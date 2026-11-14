#!/usr/bin/env python3
"""Generated Local consumer continuity; synthetic data, no router or ledger."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
spec = importlib.util.spec_from_file_location('recent_rates_stage', ROOT/'extras/local-router-stats/stage_plugin.py')
assert spec is not None and spec.loader is not None
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def record(at, prompt: float | None = 1200, generation: float | None = 32, worker='fixture:20:888'):
    return {'providerId': 'local', 'localLive': {
        'contract': 'local-task-rates/1', 'observedAt': at, 'staleAfterSec': 30,
        'models': [{'model': 'heretic', 'workerIdentity': worker, 'observedAt': at,
                    'state': 'loaded (activity unknown)', 'slotsState': 'unavailable',
                    'promptTokPerSec': None, 'generationTokPerSec': None,
                    'engineMetrics': {'contract': 'llamacpp-metrics-summary/1',
                        'workerIdentity': worker, 'observedAt': at, 'activeRequests': 0,
                        'promptTokPerSec': prompt, 'generationTokPerSec': generation}}]}}


def sequence(steps):
    panel = stage.render(SOURCE, 'fixture.agents')['Panel.qml']
    functions = panel.split('  // a-la-carchy: current-task live consumer\n', 1)[1].split('  // a-la-carchy: what the Local section says.', 1)[0]
    script = 'var root = {localRateNowMs: 100000, localRecentEngineRates: {}};\n' + functions + '''
var current = null, texts = [];
for (var step of STEPS) {
  root.localRateNowMs = step.now * 1000;
  if (step.record) {
    current = step.record;
    if (typeof localRememberEngineRates === "function")
      root.localRecentEngineRates = localRememberEngineRates(current);
  }
  texts.push(localLiveText(current));
}
console.log(JSON.stringify(texts));
'''.replace('STEPS', json.dumps(steps))
    with tempfile.TemporaryDirectory() as td:
        run = subprocess.run(['node', '-'], input=script, text=True, capture_output=True,
                             timeout=5, env={'HOME': td, 'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8'})
    if run.returncode:
        raise AssertionError(run.stderr)
    return json.loads(run.stdout)


def publication_event(at, now):
    panel = stage.render(SOURCE, 'fixture.agents')['Panel.qml']
    functions = panel.split('  // a-la-carchy: current-task live consumer\n', 1)[1].split('  // a-la-carchy: what the Local section says.', 1)[0]
    handler = stage.LIMITS_LIVE_FUNCTIONS.split('onLocalRatePublicationChanged:', 1)[1].split('  Timer {', 1)[0].strip()
    script = 'var root = {localRateNowMs:100000, localRecentEngineRates:{}};\n' + functions + """
Object.defineProperty(globalThis,'localRecentEngineRates',{get:()=>root.localRecentEngineRates,set:v=>root.localRecentEngineRates=v});
Object.defineProperty(globalThis,'localRatePublication',{get:()=>root.localRatePublication});
root.localRecentEngineRates=localRememberEngineRates(SEED);
root.localRatePublication=PUBLICATION;
Date.now=()=>NOW;
HANDLER
console.log(JSON.stringify({cache:root.localRecentEngineRates,clock:root.localRateNowMs,text:localLiveText(root.localRatePublication)}));
""".replace('SEED', json.dumps(record(100))).replace('PUBLICATION', json.dumps(record(at, None, None))).replace('NOW', str(now * 1000)).replace('HANDLER', handler)
    child = subprocess.run(['node', '-'], input=script, text=True, capture_output=True, timeout=5)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


class RecentMetrics(unittest.TestCase):
    def test_publication_between_display_ticks_retains_prior_phases(self):
        result = publication_event(105, 105.5)
        self.assertIn('prompt 1200 tok/s · generation 32 tok/s', result['text'])
        self.assertEqual(result['clock'], 105500)
        self.assertEqual(len(result['cache']), 1)
        entry = next(iter(result['cache'].values()))
        self.assertEqual(entry['prompt']['observedAt'], 100)
        self.assertEqual(entry['generation']['observedAt'], 100)

    def test_publication_signal_does_not_admit_a_genuinely_future_sample(self):
        result = publication_event(106, 105.5)
        self.assertEqual(result['cache'], {})
        self.assertNotIn('tok/s', result['text'])

    def test_engine_scope_appears_once_without_recent_prefix(self):
        texts = sequence([{'now': 100, 'record': record(100, 255.82, 21.82)},
                          {'now': 105, 'record': record(105, None, None)}])
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(text.lower().count('engine averages'), 1)
                self.assertNotIn('Recent', text)
                self.assertIn('\nprompt 255.82 tok/s · generation 21.82 tok/s\n', text)
                self.assertIn('not current-task rates', text)

    def test_short_missing_phase_keeps_recent_values_readable(self):
        texts = sequence([{'now': 100, 'record': record(100)},
                          {'now': 105, 'record': record(105, None, None)},
                          {'now': 110}])
        self.assertIn('prompt 1200 tok/s · generation 32 tok/s', texts[0])
        for text in texts[1:]:
            self.assertIn('prompt 1200 tok/s · generation 32 tok/s', text)
            self.assertIn('0 processing (metrics)', text)
            self.assertNotRegex(text, r'(?<![0-9.])0(?:\.0+)? tok/s')


    def test_missing_samples_do_not_renew_each_phase(self):
        steps = [{'now': 100, 'record': record(100)},
                 {'now': 110, 'record': record(110, None, 40)}]
        steps += [{'now': at, 'record': record(at, None, None)} for at in (125, 145, 159)]
        steps += [{'now': 160}, {'now': 169}, {'now': 170}]
        texts = sequence(steps)
        self.assertIn('prompt 1200 tok/s', texts[-4])
        self.assertIn('prompt — · generation 40 tok/s', texts[-3])
        self.assertIn('generation 40 tok/s', texts[-2])
        self.assertNotIn('tok/s', texts[-1])
        self.assertIn('prompt — · generation —', texts[-1])

    def test_stopped_publication_expires_without_another_update(self):
        texts = sequence([{'now': 100, 'record': record(100)},
                          {'now': 105, 'record': record(105, None, None)},
                          {'now': 135}])
        self.assertIn('prompt 1200 tok/s · generation 32 tok/s', texts[1])
        self.assertIn('stale', texts[-1])
        self.assertNotIn('tok/s', texts[-1])

    def test_worker_change_and_unload_discard_old_measurements(self):
        for reset in (record(105, None, None, worker='fixture:21:999'),
                      {'providerId': 'local', 'localLive': {'contract': 'local-task-rates/1',
                       'observedAt': 105, 'staleAfterSec': 30, 'models': []}}):
            with self.subTest(reset=reset):
                texts = sequence([{'now': 100, 'record': record(100)},
                                  {'now': 105, 'record': reset},
                                  {'now': 110, 'record': record(110, None, None)}])
                self.assertNotIn('tok/s', texts[-1])

    def test_malformed_publication_cannot_seed_a_later_recent_value(self):
        for field, bad in (('promptTokPerSec', True), ('generationTokPerSec', '10'),
                           ('activeRequests', -1), ('workerIdentity', 'other')):
            broken = record(105)
            broken['localLive']['models'][0]['engineMetrics'][field] = bad
            texts = sequence([{'now': 100, 'record': record(100)},
                              {'now': 105, 'record': broken},
                              {'now': 110, 'record': record(110, None, None)}])
            self.assertNotIn('tok/s', texts[1])
            self.assertNotIn('tok/s', texts[2])

    def test_alias_change_does_not_reuse_other_model_measurements(self):
        other = record(105, None, None)
        other['localLive']['models'][0]['model'] = 'another-model'
        texts = sequence([{'now': 100, 'record': record(100)},
                          {'now': 105, 'record': other},
                          {'now': 110, 'record': record(110, None, None)}])
        self.assertNotIn('tok/s', texts[1])
        self.assertNotIn('tok/s', texts[2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
