#!/usr/bin/env python3
"""Synthetic real quota producer/admission -> generated panel clock semantics."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError('Owned quota fixture is unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load('quota_credentials_fixture', ROOT / 'tests/agents-credentials.py')
extract = load('quota_panel_extract', ROOT / 'tests/grok-native-panel.py')


class HermesQuotaFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.now = fixture.fixture.NOW
        rows = [fixture.fixture.entry(subject='SYNTHETIC_QUOTA_CLOCK')]
        self.record = fixture.bridge.build_record(rows, {}, lambda *_: fixture.bridge.normalize_quota(
            fixture.fixture.quota(primary=60), self.now), self.now)
        self.temp = tempfile.TemporaryDirectory(prefix='quota-clock-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        destination = Path(self.temp.name) / 'scanner'
        fixture.bridge.publish_accounts(destination, self.record)
        records = [json.loads(p.read_text()) for p in destination.glob('*.json')]
        inventory = json.loads((destination / fixture.bridge.PUBLIC_INVENTORY).read_text())
        self.provider = fixture.consumer(records, inventory)['display'][0]
        self.panel = fixture.stager.render(NATIVE, 'fixture.quota')['Panel.qml']

    def windows(self, provider, times, panel=None):
        panel = self.panel if panel is None else panel
        names = ['windowIsLong', 'windowSpanMs', 'windowTitle', 'limitWindow',
                 'limitWindows', 'scopedPart', 'displayWindows']
        names += [name for name in ('grokQuotaFresh', 'hermesQuotaFresh', 'hermesQuotaText')
                  if 'function ' + name + '(' in panel]
        seed = {'functions': '\n'.join(extract.function(panel, n) for n in names),
                'provider': provider, 'times': times}
        program = """const vm=require('node:vm');
const seed=JSON.parse(require('fs').readFileSync(0,'utf8'));
const ctx={usage:{setting:(_,f)=>f}};ctx.root=ctx;
vm.createContext(ctx);vm.runInContext(seed.functions,ctx,{timeout:1000});
console.log(JSON.stringify(seed.times.map(now=>{
  ctx.nowMs=now;
  return {windows:ctx.displayWindows(seed.provider),
          text:typeof ctx.hermesQuotaText==='function'?ctx.hermesQuotaText(seed.provider):''};
})));
"""
        child = subprocess.run([fixture.NODE, '-e', program], input=json.dumps(seed), text=True,
                               capture_output=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def rendered(self, providers):
        """Evaluate the generated card's own trouble and quota Text bindings."""
        def binding(anchor):
            block = self.panel.split(anchor, 1)[1].split('\n    }\n', 1)[0]
            return re.search(r'^      text: (.+)$', block, re.M).group(1)
        note_name = 'usageNote' if 'function usageNote(item)' in self.panel else 'otherTrouble'
        names = ['supportsSignIn', 'needsSignIn', note_name, 'statusDisplayText', 'helpDisplayText', 'windowIsLong',
                 'windowSpanMs', 'windowTitle', 'limitWindow', 'limitWindows', 'scopedPart',
                 'displayWindows', 'hermesQuotaFresh', 'hermesQuotaText']
        add_providers = re.search(r'^  readonly property var addProviders: (\[\n.*?^  \])',
                                  self.panel, re.M | re.S)
        assert add_providers is not None
        if note_name == 'usageNote':
            note = fixture.stager.limits_component_body(self.panel, '  component UsageNote: Text {')
            provider = fixture.stager.limits_component_body(self.panel, '  component ProviderSection: Column {')
            message = re.search(r'^    readonly property string message: (.+)$', note, re.M).group(1)
            self.assertEqual(message, 'root.usageNote(record)')
            self.assertIn('visible: !section.multi && message !== ""', provider)
            self.assertIn('record: section.provider', provider)
            trouble_text = message.replace('record', 'section.provider')
            shown = '!section.multi && ' + trouble_text + ' !== ""'
        else:
            trouble = '      visible: !section.multi && root.otherTrouble(section.provider) !== ""\n'
            trouble_text = binding(trouble)
            shown = trouble.split('visible: ', 1)[1].strip()
        seed = {'functions': '\n'.join(extract.function(self.panel, n) for n in names),
                'addProviders': add_providers.group(1),
                'trouble': trouble_text, 'quota': binding('      objectName: "hermesQuotaStatus"\n'),
                'shown': shown, 'providers': providers, 'now': self.now * 1000}
        program = """const vm=require('node:vm');
const seed=JSON.parse(require('fs').readFileSync(0,'utf8'));
const ctx={usage:{setting:(_,f)=>f},Commons:{I18n:{t:k=>k}},nowMs:seed.now};ctx.root=ctx;
vm.createContext(ctx);vm.runInContext('var addProviders = '+seed.addProviders,ctx,{timeout:1000});
vm.runInContext(seed.functions,ctx,{timeout:1000});
console.log(JSON.stringify(seed.providers.map(p=>{
  ctx.section={provider:p,multi:false};
  return {trouble:vm.runInContext(seed.shown,ctx)?vm.runInContext(seed.trouble,ctx):'',
          quota:vm.runInContext(seed.quota,ctx),windows:ctx.displayWindows(p).map(w=>w.percent)};
})));
"""
        child = subprocess.run([fixture.NODE, '-e', program], input=json.dumps(seed), text=True,
                               capture_output=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        return json.loads(child.stdout)

    def test_unsupported_and_unknown_quota_placeholders_are_not_rendered(self):
        record = copy.deepcopy(self.record)
        record['credentials'] = fixture.bridge.unavailable_credentials(
            {provider: [{'id': 'SYNTHETIC_' + provider}] for provider in ('xai-oauth', 'copilot', 'anthropic')})
        destination = Path(self.temp.name) / 'unsupported'
        fixture.bridge.publish_accounts(destination, record)
        records = [json.loads(p.read_text()) for p in destination.glob('*.json')]
        inventory = json.loads((destination / fixture.bridge.PUBLIC_INVENTORY).read_text())
        display = fixture.consumer(records, inventory)['display']
        reasons = fixture.bridge.REASONS
        cards = [p for p in display if p['credentialProvider'] != 'openai-codex']
        self.assertEqual(sorted(p['providerName'].split(' (Hermes ')[0] for p in cards),
                         ['Anthropic', 'GitHub Copilot', 'Grok'])
        # Raw status provenance stays in each record; only the view omits the placeholder.
        self.assertTrue(all(p['usageStatusText'] == p['authHelpText'] == reasons['unsupported'] for p in cards))
        self.assertEqual(self.rendered(cards), [{'trouble': '', 'quota': '', 'windows': []}] * 3)
        zero = copy.deepcopy(self.provider)
        zero['limits'][0]['percent'] = 0
        quiet = [self.provider, zero, dict(self.provider, limitsFetchedAt=0),
                 dict(self.provider, limitsFetchedAt=(self.now + 120) * 1000)]
        self.assertEqual([(r['trouble'], r['quota'], r['windows'][:1]) for r in self.rendered(quiet)],
                         [('', '', [0.54]), ('', '', [0]), ('', '', []), ('', '', [])])
        stale = self.rendered([dict(self.provider, limitsStale=True)])[0]
        self.assertEqual((stale['trouble'], stale['quota'], stale['windows']), ('', 'Quota stale', []))
        for reason in ('expired', 'auth', 'network', 'quota'):
            with self.subTest(reason=reason):
                failed = dict(self.provider, usageStatusText=reasons[reason], authHelpText=reasons[reason])
                self.assertEqual(self.rendered([failed])[0]['trouble'], reasons[reason])
        native = dict(cards[0], providerId='claude', credentialProvider='', readOnly=False)
        self.assertEqual(self.rendered([native])[0]['trouble'], reasons['unsupported'])

    def test_reset_without_publication_never_becomes_a_measured_zero(self):
        before = copy.deepcopy(self.provider)
        reset = (self.now + 60) * 1000
        rows = self.windows(self.provider, [self.now * 1000, reset - 1, reset, reset + 1])
        self.assertEqual(rows[0]['windows'][0]['percent'], 0.54)
        self.assertEqual(rows[1]['windows'][0]['percent'], 0.54)
        for expired in rows[2:]:
            self.assertEqual(expired['windows'], [], 'An unobserved reset must withhold quotas, not invent zero')
            self.assertIn('stale', expired['text'].lower())
        self.assertEqual(self.provider, before, 'Clock projection must not rewrite authenticated samples')

    def test_measured_zero_is_retained_but_stale_future_and_invalid_samples_are_not(self):
        zero = copy.deepcopy(self.provider)
        zero['limits'][0]['percent'] = 0
        self.assertEqual(self.windows(zero, [self.now * 1000])[0]['windows'][0]['percent'], 0)
        # A retained sample is called stale; a quota never validly fetched is unknown, not a message.
        for changes, text in (({'limitsStale': True}, 'Quota stale'), ({'limitsFetchedAt': 0}, ''),
                              ({'limitsFetchedAt': (self.now + 120) * 1000}, ''),
                              ({'limitsFetchedAt': (self.now - 1801) * 1000}, 'Quota stale')):
            with self.subTest(changes=changes):
                row = self.windows(dict(self.provider, **changes), [self.now * 1000])[0]
                self.assertEqual(row['windows'], [])
                self.assertEqual(row['text'], text)
        for window in ({'percent': None}, {'percent': True}, {'percent': 2},
                       {'resetsAt': ''}, {'resetsAt': 'invalid'}):
            with self.subTest(window=window):
                p = copy.deepcopy(self.provider)
                p['limits'][0].update(window)
                self.assertEqual(self.windows(p, [self.now * 1000])[0]['windows'], [])

    def test_helper_does_not_depend_on_native_grok_support(self):
        panel = fixture.stager.transform_panel_limits((NATIVE / 'Panel.qml').read_text())
        self.assertNotIn('function grokQuotaFresh(', panel)
        rows = self.windows(self.provider, [self.now * 1000, (self.now + 60) * 1000], panel)
        self.assertEqual(rows[0]['windows'][0]['percent'], 0.54)
        self.assertEqual(rows[1]['windows'], [])

    def test_unrelated_native_provider_behavior_and_existing_grok_guard_are_preserved(self):
        p = copy.deepcopy(self.provider)
        p.update(providerId='claude', credentialProvider='')
        rows = self.windows(p, [self.now * 1000, (self.now + 60) * 1000])
        self.assertEqual(rows[0]['windows'][0]['percent'], 0.54)
        self.assertEqual(rows[1]['windows'][0]['percent'], 0)  # Stock native policy, outside this adapter.
        p['providerId'] = 'grok'
        rows = self.windows(p, [self.now * 1000, (self.now + 60) * 1000])
        self.assertEqual(rows[0]['windows'][0]['percent'], 0.54)
        self.assertEqual(rows[1]['windows'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
