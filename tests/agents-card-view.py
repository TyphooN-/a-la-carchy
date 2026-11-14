#!/usr/bin/env python3
"""Presentation-only Hermes cards: generated helpers, synthetic data only."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NATIVE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load('card_view_stage', ROOT / 'extras/local-router-stats/stage_plugin.py')
extract = load('card_view_extract', ROOT / 'tests/grok-native-panel.py')


class Cards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = stage.render(NATIVE, 'fixture.cards')['Panel.qml']
        names = ['windowIsLong', 'windowSpanMs', 'windowTitle', 'limitWindow', 'limitWindows',
                 'scopedPart', 'displayWindows', 'hermesQuotaFresh', 'providerHeading',
                 'providerCardVisible', 'reorderProvider']
        cls.functions = '\n'.join(extract.function(cls.panel, n) for n in names)
        names_match = re.search(r'^  readonly property var hermesDisplayNames: (\{.*?^  \})', cls.panel, re.M | re.S)
        assert names_match is not None
        cls.name_body = names_match.group(1)
        cls.node = shutil.which('node')
        assert cls.node, 'Node is required; do not silently skip presentation proof'

    def evaluate(self, providers, inventory=None, clock=None, selected=0, dy=0):
        seed = {'functions': self.functions, 'nameBody': self.name_body, 'providers': providers,
                'inventory': inventory or {}, 'now': clock or 1800000000000, 'selected': selected, 'dy': dy}
        code = '''const vm=require('node:vm');
const s=JSON.parse(require('fs').readFileSync(0,'utf8'));
const ctx={nowMs:s.now,providers:s.providers,calls:[],dragIndex:-1,usage:{setting:(_,f)=>f,hermesInventoryIndex:s.inventory}};
ctx.root=ctx;ctx.Qt={callLater:fn=>fn()};ctx.providerIndexOfKey=()=>s.selected;ctx.pointAt=()=>{};ctx.lineStop=()=>"provider";
ctx.usage.moveProvider=(id,to)=>ctx.calls.push({id,to});
vm.createContext(ctx);vm.runInContext(s.functions,ctx,{timeout:1000});
ctx.hermesDisplayNames=vm.runInContext('(function()'+s.nameBody+')()',ctx,{timeout:1000});
const before=JSON.stringify([s.providers,s.inventory]);
const rows=s.providers.map(p=>({visible:ctx.providerCardVisible(p),name:ctx.providerHeading(p)}));
ctx.reorderProvider(s.dy);
console.log(JSON.stringify({rows,calls:ctx.calls,unchanged:before===JSON.stringify([s.providers,s.inventory])}));'''
        node = self.node
        assert node is not None
        p = subprocess.run([node, '-e', code], input=json.dumps(seed), text=True,
                           capture_output=True, timeout=5)
        self.assertEqual(p.returncode, 0, p.stderr)
        result = json.loads(p.stdout)
        self.assertTrue(result['unchanged'], 'Presentation must not rewrite raw identities, samples or token counters')
        return result

    def provider(self, suffix='a', **changes):
        now = 1800000000000
        reset = datetime.datetime.fromtimestamp((now + 60000) / 1000, datetime.timezone.utc).isoformat()
        p = {'providerId': 'codex-hermes-' + suffix * 64, 'providerName': 'raw credential ' + suffix,
             'credentialProvider': 'openai-codex', 'readOnly': True, 'limitsStale': False,
             'limitsFetchedAt': now, 'limits': [{'label': '1-minute window', 'percent': 0, 'resetsAt': reset}],
             'tokenCounters': {'today': 12345}}
        return dict(p, **changes)

    def test_only_renderable_hermes_bars_and_native_cards_remain(self):
        good = self.provider()
        bad = [dict(good, limits=[]), dict(good, limitsStale=True), dict(good, limitsFetchedAt=0),
               dict(good, limitsFetchedAt=1800000060001)]
        for value in (None, True, '0', -1, 2):
            bad.append(dict(good, limits=[dict(good['limits'][0], percent=value)]))
        native = {'providerId': 'local', 'providerName': 'Local', 'credentialProvider': '', 'readOnly': True, 'limits': []}
        rows = self.evaluate([good, *bad, native])['rows']
        self.assertTrue(rows[0]['visible'], 'Measured zero is a real visible bar')
        self.assertTrue(rows[-1]['visible'], 'Native Local is not a Hermes credential card')
        self.assertTrue(all(not row['visible'] for row in rows[1:-1]))
        self.assertFalse(self.evaluate([good], clock=1800000060000)['rows'][0]['visible'])

    def test_numbering_uses_full_validated_inventory_not_visible_order(self):
        providers = [self.provider(c) for c in 'abc']
        inventory = {p['providerId']: {'id': p['providerId'], 'provider': 'openai-codex',
                     'name': 'Codex (Hermes ' + c * 8 + ')'} for c, p in zip('abc', providers)}
        providers[1]['limits'] = []
        result = self.evaluate(list(reversed(providers)), dict(reversed(list(inventory.items()))))
        self.assertEqual([row['name'] for row in result['rows']],
                         ['Codex 3 (Hermes)', 'Codex 2 (Hermes)', 'Codex 1 (Hermes)'])
        self.assertEqual([row['visible'] for row in result['rows']], [True, False, True])
        self.assertNotIn('quotaCalibrationStatus', self.panel)
        self.assertIn('hermesCaptionHover.hovered', self.panel)
        self.assertIn('text: root.quotaCalibrationText(section.provider)', self.panel)
        supported = ('  readonly property var providers: usage.enabledProviders',
                     '  readonly property var providers: usage.subscribedProviders',
                     '  readonly property var providers: dragProviders !== null ? dragProviders : usage.panelProviders')
        native_bindings = [line for line in (NATIVE/'Panel.qml').read_text().splitlines() if line in supported]
        self.assertEqual(len(native_bindings), 1, 'one complete supported native provider binding required')
        self.assertIn(native_bindings[0], self.panel, 'preserve the native raw provider population; do not filter the backing list')

    def test_native_headings_do_not_inherit_hermes_display_names(self):
        providers = [{'providerId': name, 'providerName': 'Native ' + name,
                      'readOnly': False, 'credentialProvider': '', 'limits': []}
                     for name in ('constructor', 'toString', '__proto__')]
        result = self.evaluate(providers)
        self.assertEqual([row.get('name') for row in result['rows']],
                         [p['providerName'] for p in providers])
        self.assertTrue(all(row['visible'] for row in result['rows']))

    def test_keyboard_reorder_skips_hidden_indices_without_dropping_raw_order(self):
        providers = [self.provider('a'), self.provider('b', limits=[]), self.provider('c')]
        forward = self.evaluate(providers, selected=0, dy=1)
        self.assertEqual(forward['calls'], [{'id': providers[0]['providerId'], 'to': 2}])
        reverse = self.evaluate(providers, selected=2, dy=-1)
        self.assertEqual(reverse['calls'], [{'id': providers[2]['providerId'], 'to': 0}])
        self.assertEqual(self.evaluate(providers, selected=0, dy=0)['calls'], [])
        self.assertIn('if (!root.providerCardVisible(providers[p])) continue', self.panel)
        if 'import "../panels/elsewhen/Model.js" as Reorder' in (NATIVE/'Panel.qml').read_text():
            self.assertIn('if (!dragDomainCurrent()) { resetProviderDrag(); return }', self.panel)
            self.assertIn('dragVisibleIndices[slot]', self.panel)
            self.assertIn('onDragDomainValidChanged: if (!dragDomainValid) resetProviderDrag()', self.panel)
        else:
            self.assertIn('if (item && item.visible && y >= item.y', self.panel)


if __name__ == '__main__':
    unittest.main(verbosity=2)
