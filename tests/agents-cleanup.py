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


def admission(main, records, synced=None):
    # Only the native top-level property block ends at two-space indentation.
    anchor = '  property var enabledProviders: {'
    body = main.split(anchor, 1)[1].split('\n  }\n', 1)[0]
    enabled_body = main.split('  function providerEnabled(id) {', 1)[1].split('\n  }\n', 1)[0]
    context = {'records': records, 'synced': synced or {}}
    script = '''const ctx = ''' + json.dumps(context) + ''';
var dataRevision = 0, syncRevision = 0;
var agents = ctx.records.map(record => ({record}));
var aggregateData = {providers: ctx.synced};
var localRouterAgent = {record: {id: 'local', name: 'Local', ready: true}};
var grokAgent = {record: {id: 'grok', name: 'CUSTOM GROK', ready: false}};
function syncConfigured() { return true; }
var settings = {};
function providerEnabled(id) {''' + enabled_body + '''\n}
function displayProvider(record) { return {providerId: record.id}; }
function providerHasData(record) { return true; }
function orderedProviders(list) { return list; }
function selected() {''' + body + '''\n}
console.log(JSON.stringify(selected().map(p => p.providerId)));
'''
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required for admission behavior proof')
    with tempfile.TemporaryDirectory(prefix='agents-cleanup-') as td:
        run = subprocess.run([node, '-'], input=script, text=True, capture_output=True, timeout=5,
                             env={'PATH': os.environ.get('PATH', ''), 'HOME': td, 'LANG': 'C.UTF-8'})
    if run.returncode:
        raise AssertionError((run.returncode, run.stderr))
    return json.loads(run.stdout)


class CleanupTests(unittest.TestCase):
    def render(self):
        return stager.render(SOURCE, 'fixture.agents')

    def test_native_grok_has_exactly_one_admission(self):
        ids = admission(self.render()['Main.qml'], [{'id': 'grok', 'name': 'Grok'}])
        self.assertEqual(ids.count('grok'), 1, ids)

    def test_native_codex_is_excluded_from_local_and_synced_admission(self):
        main = self.render()['Main.qml']
        hermes = [{'id': 'codex-hermes-1'}, {'id': 'codex-hermes-2'}]
        for records, synced in [(hermes + [{'id': 'codex'}], {}), (hermes, {'codex': {'providerName': 'Codex'}})]:
            with self.subTest(records=records, synced=synced):
                ids = admission(main, records, synced)
                self.assertNotIn('codex', ids)
                self.assertEqual(sorted(i for i in ids if i.startswith('codex')), ['codex-hermes-1', 'codex-hermes-2'])

    def test_only_two_hermes_slots_admitted_locally_and_from_sync(self):
        main = self.render()['Main.qml']
        allowed = [{'id': 'codex-hermes-1'}, {'id': 'codex-hermes-2'}]
        ghosts = [{'id': ident} for ident in ('codex-hermes-3', 'codex-other', 'codex-hermes-01')]
        for records, synced in ((allowed + ghosts, {}), (allowed, {r['id']: {} for r in ghosts})):
            with self.subTest(records=records, synced=synced):
                ids = admission(main, records, synced)
                self.assertEqual(sorted(i for i in ids if i.startswith('codex')), ['codex-hermes-1', 'codex-hermes-2'])

    def test_limits_layout_contains_the_complete_local_status_surface(self):
        rendered = self.render()
        self.assertEqual(rendered['layout'], 'limits')
        self.assertTrue('function localRouterText(p)' in rendered['Panel.qml'], 'Local formatting function missing')
        self.assertTrue('text: visible ? root.localRouterText(section.provider) : ""' in rendered['Panel.qml'], 'Local text surface missing')
        self.assertTrue('No recorded days yet (not zero usage)' in rendered['Panel.qml'], 'Unknown coverage status missing')

    def test_partial_limits_panel_is_refused(self):
        malformed = 'import QtQuick\nItem { function otherTrouble(item) { return "" } }\n'
        with self.assertRaises(ValueError):
            stager.patch_panel(malformed)

    def test_duplicate_replacement_anchor_is_refused(self):
        with self.assertRaises(ValueError):
            stager.replace_once('anchor\nanchor', 'anchor', 'new')


if __name__ == '__main__':
    unittest.main()
