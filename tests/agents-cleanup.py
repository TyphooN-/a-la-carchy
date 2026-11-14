#!/usr/bin/env python3
"""Provider-admission regressions on real native source, with JS data seams.

No live configs, credentials, services or network. This executes the rendered
Main.qml admission body in Node; full native QML/render acceptance is separate.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
STAGER_SOURCE = Path(os.environ.get('ALACARCHY_STAGER_SOURCE', str(ROOT/'extras/local-router-stats/stage_plugin.py')))
spec = importlib.util.spec_from_file_location('cleanup_stage', STAGER_SOURCE)
assert spec is not None and spec.loader is not None
stager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stager)


spec = importlib.util.spec_from_file_location('cleanup_inventory', ROOT/'tests/agents-credentials.py')
assert spec is not None and spec.loader is not None
inventory_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory_fixture)
# Explicit sibling loading also works under Python -I (no tests/ on sys.path).
PANEL_LIMITS = inventory_fixture.load('cleanup_agents_fixture', ROOT/'tests/agents_fixture.py').PANEL_LIMITS


def admission(main, records, synced=None, inventory=None):
    # Real generated helpers, not a fake displayProvider/always-true hasData.
    return inventory_fixture.consumer(records, inventory, synced, main=main)['ids']


def published_inventory():
    bridge, fixture = inventory_fixture.bridge, inventory_fixture.fixture
    rows = [fixture.entry(subject='synthetic-cleanup-' + str(i)) for i in range(3)]
    record = bridge.build_record(rows, {}, lambda *args: bridge.normalize_quota(fixture.quota(), fixture.NOW), fixture.NOW)
    with tempfile.TemporaryDirectory(prefix='cl-') as folder:
        path = Path(folder)/'scanner'
        bridge.publish_accounts(path, record)
        records = [json.loads(p.read_text()) for p in sorted(path.glob('*.json'))]
        inventory = json.loads((path/bridge.PUBLIC_INVENTORY).read_text())
    return records, inventory


class CleanupTests(unittest.TestCase):
    def render(self):
        return stager.render(SOURCE, 'fixture.agents')

    def test_native_grok_has_exactly_one_admission(self):
        ids = admission(self.render()['Main.qml'], [{'id': 'grok', 'name': 'Grok', 'ready':True, 'tierLabel':'X Premium'}])
        self.assertEqual(ids.count('grok'), 1, ids)

    def test_native_codex_is_excluded_from_local_and_synced_admission(self):
        main = self.render()['Main.qml']
        hermes, inventory = published_inventory()
        for records, synced in [(hermes + [{'id': 'codex'}], {}), (hermes, {'codex': {'providerName': 'Codex'}})]:
            with self.subTest(records=records, synced=synced):
                ids = admission(main, records, synced, inventory)
                self.assertNotIn('codex', ids)
                self.assertEqual({i for i in ids if i.startswith('codex')}, {r['id'] for r in hermes})

    def test_all_inventoried_hermes_identities_admitted_locally_and_from_sync_but_forged_third_is_not(self):
        main = self.render()['Main.qml']
        allowed, inventory = published_inventory()
        ghosts = [{'id': ident} for ident in ('codex-hermes-' + 'f'*64, 'codex-other', 'codex-hermes-01')]
        exported = inventory_fixture.consumer(allowed, inventory)['snapshot']['providers']
        for records, synced in ((allowed + ghosts, {}), ([], dict(exported, **{r['id']: {} for r in ghosts}))):
            with self.subTest(records=records, synced=synced):
                ids = admission(main, records, synced, inventory)
                self.assertEqual({i for i in ids if i.startswith('codex')}, {r['id'] for r in allowed})

    def test_limits_layout_contains_the_complete_local_status_surface(self):
        rendered = self.render()
        self.assertEqual(rendered['layout'], 'limits')
        self.assertTrue('function localRouterText(p)' in rendered['Panel.qml'], 'Local formatting function missing')
        self.assertTrue('text: visible ? root.localRouterText(section.provider) : ""' in rendered['Panel.qml'], 'Local text surface missing')
        self.assertIn('No token data', rendered['Panel.qml'])
        self.assertIn('Quota stale', rendered['Panel.qml'])
        self.assertNotIn('Quota unavailable · sign in', rendered['Panel.qml'])

    def test_supported_eager_and_lazy_height_shapes(self):
        layout, eager = stager.patch_panel(PANEL_LIMITS)
        self.assertEqual(layout, 'limits')
        self.assertIn('panel.fittedContentHeight(recordedTokenHeader.implicitHeight + Style.space(16) + column.implicitHeight)', eager)
        native = stager.source_bytes(SOURCE/'Panel.qml').decode()
        self.assertIn('content: PanelKeyCatcher {', native)
        layout, lazy = stager.patch_panel(native)
        self.assertEqual(layout, 'limits')
        self.assertIn('contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)', lazy)
        self.assertIn('implicitHeight: recordedTokenHeader.implicitHeight + Style.space(16) + column.implicitHeight', lazy)
        self.assertEqual(lazy.count('id: recordedTokenHeader'), 1)
        self.assertNotIn('Style.space(860)', lazy)

    def test_history_imports_are_unique_on_supported_panel_shapes(self):
        native = stager.source_bytes(SOURCE/'Panel.qml').decode()
        for text in (PANEL_LIMITS, native):
            with self.subTest(shape='lazy' if text is native else 'eager'):
                _, rendered = stager.patch_panel(text)
                self.assertEqual(rendered.count('import "History.js" as History'), 1)
                self.assertEqual(rendered.count('import qs.Ui as Ui'), 1)
                for malformed in (text.replace('import qs.Ui\n', ''),
                                  text.replace('import qs.Ui\n', 'import qs.Ui\nimport qs.Ui\n')):
                    with self.assertRaises(ValueError):
                        stager.patch_panel(malformed)

    def test_lazy_shape_partial_or_mixed_anchors_are_refused(self):
        native = stager.source_bytes(SOURCE/'Panel.qml').decode()
        lazy_height = 'contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(860))'
        eager_height = 'contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(860))'
        malformed = [native.replace(lazy_height, eager_height),
                     native.replace(lazy_height, lazy_height + '\n    ' + lazy_height),
                     native.replace('implicitHeight: column.implicitHeight', 'implicitHeight: 0'),
                     native.replace('content: PanelKeyCatcher {', 'PanelKeyCatcher {')]
        for text in malformed:
            with self.subTest(shape=text[:50]):
                with self.assertRaises(ValueError):
                    stager.patch_panel(text)

    def test_r7017_localized_trouble_text_is_preserved_completely(self):
        old = '      text: section.provider ? String(section.provider.authHelpText || "") : ""'
        localized = ('      // Shown for the status, so a record with no help to offer says the status rather than nothing.\n'
                     '      text: section.provider ? (root.helpDisplayText(section.provider.authHelpText) || root.statusDisplayText(section.provider.usageStatusText) || "") : ""')
        native = PANEL_LIMITS.replace(old, localized)
        layout, rendered = stager.patch_panel(native)
        self.assertEqual(layout, 'limits')
        self.assertIn(localized, rendered)
        self.assertEqual(rendered.count('text: visible ? root.localRouterText(section.provider) : ""'), 1)

    def test_trouble_block_duplicates_mixed_and_partial_are_refused(self):
        old = '      text: section.provider ? String(section.provider.authHelpText || "") : ""'
        localized = ('      // Shown for the status, so a record with no help to offer says the status rather than nothing.\n'
                     '      text: section.provider ? (root.helpDisplayText(section.provider.authHelpText) || root.statusDisplayText(section.provider.usageStatusText) || "") : ""')
        block = PANEL_LIMITS.split('    Text {', 1)[1].split('\n    }', 1)[0]
        block = '    Text {' + block + '\n    }\n'
        cases = [PANEL_LIMITS + block,
                 PANEL_LIMITS + block.replace(old, localized),
                 PANEL_LIMITS.replace(old, localized).replace('      color: root.urgent', '      color: root.dim'),
                 PANEL_LIMITS.replace(old, localized.replace('root.statusDisplayText', 'root.unprovenDisplayText')),
                 PANEL_LIMITS.replace(block, '')]
        for malformed in cases:
            with self.subTest(shape=malformed[-100:]):
                with self.assertRaises(ValueError):
                    stager.patch_panel(malformed)

    def test_partial_limits_panel_is_refused(self):
        malformed = 'import QtQuick\nItem { function otherTrouble(item) { return "" } }\n'
        with self.assertRaises(ValueError):
            stager.patch_panel(malformed)

    def test_duplicate_replacement_anchor_is_refused(self):
        with self.assertRaises(ValueError):
            stager.replace_once('anchor\nanchor', 'anchor', 'new')


if __name__ == '__main__':
    unittest.main()
