#!/usr/bin/env python3
"""Offline tests using the installed Grok status-line contract; no API calls."""
import copy
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'extras/grok-usage/collector.py'
spec = importlib.util.spec_from_file_location('grok_collector', SCRIPT)
assert spec is not None and spec.loader is not None
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
SID = '12345678-1234-1234-1234-123456789abc'


def load(name, path):
    module_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


stager = load('grok_stage', ROOT/'extras/grok-usage/stage_plugin.py')
fixture = load('agents_fixture', Path(__file__).with_name('agents_fixture.py'))


def payload(values=(0, 0, 0, 0), model='grok-test'):
    return {'schema_version': 1, 'session_id': SID, 'model': {'id': model},
            'context_window': {'session_usage': dict(zip(c.FIELDS, values)),
                               'session_input_tokens': values[0]+values[2]+values[3],
                               'session_output_tokens': values[1]}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = c.Ledger(self.root/'db.sqlite')
        self.addCleanup(self.ledger.db.close)
        self.now = dt.datetime(2026, 10, 2, 12).timestamp()

    def observe(self, values=(0, 0, 0, 0), model='grok-test', now=None):
        return self.ledger.observe(c.parse(payload(values, model)), self.now if now is None else now)

    def record(self, now=None):
        return self.ledger.record(self.now if now is None else now)

    def test_counter_splits(self):
        self.observe(); self.observe((10, 3, 7, 2), now=self.now+5)
        self.assertEqual(self.record()['todayTotalTokens'], 22)
        self.assertEqual(self.record()['modelUsage']['grok-test']['cacheReadInputTokens'], 7)

    def test_first_observation_is_baseline(self):
        self.observe((1000, 200, 300, 100))
        self.assertEqual(self.record()['todayTotalTokens'], 0)

    def test_first_call_after_known_empty_session(self):
        p = payload(); del p['context_window']['session_usage']
        self.ledger.observe(c.parse(p), self.now)
        self.observe((10, 3, 2, 1), now=self.now+5)
        self.assertEqual(self.record()['todayTotalTokens'], 16)

    def test_missing_usage_is_not_zero(self):
        p = payload(); p['context_window'] = {}
        self.assertIsNone(c.parse(p))
        self.assertFalse(self.record()['hasLocalStats'])
        self.assertEqual(self.record()['recentDays'], [])

    def test_duplicates_do_not_double_count(self):
        self.observe(); self.observe((10, 2, 0, 0), now=self.now+5)
        self.observe((10, 2, 0, 0), now=self.now+10)
        self.assertEqual(self.record()['todayTotalTokens'], 12)

    def test_model_change_is_unattributed(self):
        self.observe(); self.observe((10, 2, 0, 0), model='grok-other', now=self.now+5)
        self.assertEqual(self.record()['todayTokensByModel']['Unattributed (model changed between observations)'], 12)

    def test_regression_rebaselines_without_erasing_spend(self):
        self.observe(); self.observe((10, 2, 0, 0), now=self.now+5)
        self.observe((1, 1, 0, 0), now=self.now+10)
        self.observe((4, 2, 0, 0), now=self.now+15)
        self.assertEqual(self.record()['todayTotalTokens'], 16)

    def test_cross_day_gap_is_not_backfilled(self):
        self.observe(); later = self.now+86400
        self.observe((1000, 200, 0, 0), now=later)
        self.observe((1004, 201, 0, 0), now=later+5)
        self.assertEqual(self.record(later+5)['todayTotalTokens'], 5)

    def test_contiguous_midnight_interval(self):
        midnight = dt.datetime(2026, 10, 3).timestamp()
        self.observe(now=midnight-5)
        self.observe((10, 2, 0, 0), now=midnight+5)
        self.assertEqual(self.record(midnight+5)['todayTotalTokens'], 12)

    def test_clock_regression_is_rejected(self):
        self.observe()
        with self.assertRaises(ValueError): self.observe(now=self.now-1)

    def test_retention(self):
        self.observe()
        self.observe(now=self.now+365*86400)
        self.assertEqual(len(self.record(self.now+365*86400)['recentDays']), 1)

    def test_invalid_counters(self):
        for value in [True, -1, 1.5, '3', c.MAX+1]:
            p = payload(); p['context_window']['session_usage']['input_tokens'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): c.parse(p)

    def test_schema_and_identity_validation(self):
        for key, value in [('schema_version', True), ('schema_version', 0), ('session_id', 'not-a-uuid')]:
            p = payload(); p[key] = value
            with self.assertRaises(ValueError): c.parse(p)

    def test_inconsistent_totals(self):
        p = payload((3, 1, 2, 1)); p['context_window']['session_input_tokens'] = 100
        with self.assertRaises(ValueError): c.parse(p)

    def test_no_quota_invention(self):
        r = self.record()
        self.assertEqual(r['limits'][0]['percent'], -1)
        self.assertFalse(r['ready'])
        self.assertEqual(r['tierLabel'], '')

    def test_atomic_publication(self):
        output = self.root/'grok.json'
        self.assertTrue(c.publish(output, self.record()))
        self.assertFalse(c.publish(output, self.record()))
        self.assertEqual(json.loads(output.read_text())['id'], 'grok')
        self.assertEqual(list(self.root.glob('.grok-*')), [])

    def test_cli_ignores_private_metadata(self):
        output = self.root/'grok.json'
        p = payload(); p['cwd'] = 'PRIVATE_SENTINEL'; p['session_name'] = 'PRIVATE_SENTINEL'
        args = [sys.executable, str(SCRIPT), '--hook', '--state-dir', str(self.root/'state'), '--output', str(output)]
        r = subprocess.run(args, input=json.dumps(p), text=True, capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('PRIVATE_SENTINEL', r.stdout+r.stderr+output.read_text())
        self.assertNotIn('PRIVATE_SENTINEL', (self.root/'state/tokens.sqlite3').read_bytes().decode('latin1'))

    def test_invalid_input_retains_history_without_logging_input(self):
        output = self.root/'grok.json'
        args = [sys.executable, str(SCRIPT), '--hook', '--state-dir', str(self.root/'state'), '--output', str(output)]
        r = subprocess.run(args, input='PRIVATE_SENTINEL', text=True, capture_output=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn('PRIVATE_SENTINEL', r.stdout+r.stderr+output.read_text())
        self.assertIn('history retained', json.loads(output.read_text())['usageStatusText'])
        self.assertIn('—', r.stdout)

    def test_simultaneous_observers(self):
        self.observe()
        other = c.Ledger(self.root/'db.sqlite'); self.addCleanup(other.db.close)
        observation = c.parse(payload((10, 2, 0, 0)))
        self.ledger.observe(observation, self.now+5)
        other.observe(observation, self.now+5)
        self.assertEqual(self.record()['todayTotalTokens'], 12)

    # ------------------------------------------------------------ stager
    def test_stager_adds_the_observed_tab_to_the_tabs_layout(self):
        source = fixture.make_source(self.root/'source', 'tabs')
        before = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        target = self.root/'staged/agents'
        stager.stage(source, target, 'fixture.agents')
        self.assertEqual({p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}, before)
        manifest = json.loads((target/'manifest.json').read_text())
        self.assertEqual(manifest['name'], 'Agents + Local + Grok tokens')
        self.assertEqual(manifest['barWidget']['displayName'], 'Agents + Local + Grok tokens')
        self.assertEqual(manifest['omarchy']['alaCarchy'], 'local-router-stats/1;grok-observed-stats/1')
        self.assertEqual(manifest['omarchy']['alaCarchyLayout'], 'tabs')
        main, panel = (target/'Main.qml').read_text(), (target/'Panel.qml').read_text()
        for snippet in ['name !== "local.json" && name !== "grok.json"', '/omarchy/grok-usage/agents/grok.json', 'id: grokObservedAgent',
                        'result.push(displayProvider(grokObservedRecord))', 'if (syncedId === "local" || syncedId === "grok") continue',
                        'if (String(record.id) === "local" || String(record.id) === "grok") continue',
                        'if (providerId === "local" || providerId === "grok") return null']:
            self.assertEqual(main.count(snippet), 1, snippet)
        for snippet in ['(p.providerId === "local" || p.providerId === "grok") ? rows : rows.slice(0, 4)',
                        '(p.providerId === "local" || p.providerId === "grok") ? id : usage.friendlyModelName(id),', '"Observed uncached input "']:
            self.assertEqual(panel.count(snippet), 1, snippet)
        self.assertEqual([p.name for p in target.parent.iterdir()], ['agents'], 'no staging leftovers')

    def test_stager_never_shadows_a_first_party_grok_collector(self):
        for layout in ('limits', 'tabs'):
            with self.subTest(layout=layout):
                source = fixture.make_source(self.root/layout, layout, native_grok=True)
                with self.assertRaises(stager.NativeGrok): stager.render(source, 'fixture.agents')
                with self.assertRaises(stager.NativeGrok): stager.stage(source, self.root/'never', 'fixture.agents')
                self.assertFalse((self.root/'never').exists())

    def test_stager_refuses_layouts_it_cannot_patch_and_existing_targets(self):
        source = fixture.make_source(self.root/'source', 'limits', native_grok=False)
        with self.assertRaises(ValueError) as caught: stager.render(source, 'fixture.agents')
        self.assertNotIsInstance(caught.exception, stager.NativeGrok)
        tabs = fixture.make_source(self.root/'tabs', 'tabs')
        (self.root/'taken').mkdir()
        with self.assertRaises(ValueError): stager.stage(tabs, self.root/'taken', 'fixture.agents')
        self.assertEqual(list((self.root/'taken').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
