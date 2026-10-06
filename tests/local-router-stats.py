#!/usr/bin/env python3
"""Offline fixtures: never accesses the real router or desktop."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]/'extras/local-router-stats'
def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE/(name+'.py'))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
c = load('collector')
p = load('stage_plugin')
_spec = importlib.util.spec_from_file_location('agents_fixture', Path(__file__).with_name('agents_fixture.py'))
fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(Path(root).rglob('*')) if path.is_file()}


def epoch(day, hour=12):
    return dt.datetime(2026, 10, day, hour).timestamp()


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ledger = c.Ledger(self.root/'stats.sqlite3')

    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()

    def test_counter_validation(self):
        self.assertEqual(c.counters('llamacpp:prompt_tokens_total 1e2\nllamacpp:tokens_predicted_total 4'), (100, 4))
        for bad in ['NaN', 'Infinity', '-1', '0.5', str(2**63)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                c.counters(f'llamacpp:prompt_tokens_total {bad}\nllamacpp:tokens_predicted_total 4')
        for bad in ['', 'llamacpp:prompt_tokens_total 1', 'llamacpp:prompt_tokens_total 1\nllamacpp:prompt_tokens_total 2\nllamacpp:tokens_predicted_total 4']:
            with self.assertRaises(ValueError): c.counters(bad)

    def test_first_sample_is_baseline_not_backfill(self):
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2)), (0, 0))
        record = self.ledger.record(epoch(2), 'sampled')
        self.assertEqual(record['todayTotalTokens'], 0)
        self.assertIsNone(record['recentDays'][0]['messageCount'])
        self.assertFalse(record['recentDays'][0]['recorded'])
        self.assertTrue(record['recentDays'][-1]['recorded'])

    def test_incremental_and_duplicate_poll(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+5), (20, 5))
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+10), (0, 0))
        self.assertEqual(self.ledger.record(epoch(2)+10, 'sampled')['todayTotalTokens'], 25)

    def test_worker_restart_even_with_larger_counters(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'worker2', (900, 100), epoch(2)+5), (0, 0))
        self.assertEqual(self.ledger.observe('heretic', 'worker2', (910, 103), epoch(2)+10), (10, 3))

    def test_counter_regression_rebaselines_both(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (50, 30), epoch(2)+5), (0, 0))

    def test_calendar_and_model_breakdown(self):
        for model in ['heretic', 'qwopus']:
            self.ledger.observe(model, model, (0, 0), epoch(2))
        self.ledger.observe('heretic', 'heretic', (12, 5), epoch(2)+5)
        # A day-long gap cannot be attributed to one calendar day. Rebaseline
        # before measuring a fresh, dated interval on the second day.
        self.ledger.observe('qwopus', 'qwopus', (30, 9), epoch(3))
        self.ledger.observe('qwopus', 'qwopus', (60, 18), epoch(3)+5)
        record = self.ledger.record(epoch(3)+5, 'sampled')
        self.assertEqual(record['todayTotalTokens'], 39)
        self.assertEqual(record['recentDays'][-2]['messageCount'], 17)
        self.assertEqual(record['modelUsage']['heretic']['inputTokens'], 12)
        self.assertEqual(len(record['recentDays']), 7)
        self.assertEqual(record['limits'], [])
        self.assertFalse(record['hasPromptStats'])

    def test_persistence(self):
        self.ledger.observe('heretic', 'worker1', (0, 0), epoch(2))
        self.ledger.observe('heretic', 'worker1', (20, 5), epoch(2)+5)
        self.ledger.db.close()
        self.ledger = c.Ledger(self.root/'stats.sqlite3')
        self.assertEqual(self.ledger.record(epoch(2), 'sampled')['todayTotalTokens'], 25)
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (30, 7), epoch(2)+10), (10, 2))

    def test_invalid_event_no_change(self):
        for model, vals in [('', (1, 2)), ('bad\nmodel', (1, 2)), ('heretic', (-1, 2)), ('heretic', (True, 2))]:
            with self.assertRaises(ValueError): self.ledger.observe(model, 'pid', vals, epoch(2))
        self.assertFalse(self.ledger.record(epoch(2), 'pending')['hasLocalStats'])

    def test_out_of_order_sample(self):
        self.ledger.observe('heretic', 'worker1', (100, 20), epoch(2))
        with self.assertRaises(ValueError): self.ledger.observe('heretic', 'worker1', (200, 30), epoch(2)-1)
        self.assertEqual(self.ledger.observe('heretic', 'worker1', (120, 25), epoch(2)+5), (20, 5))

    def test_no_loaded_models_no_metrics_or_systemctl(self):
        with patch.object(c, 'get', return_value=json.dumps({'data':[{'id':'heretic','status':{'value':'unloaded'}}]})) as get, patch.object(c.subprocess, 'run') as run:
            self.assertIn('No loaded model', c.sample(self.ledger, epoch(2)))
            get.assert_called_once_with(c.ROUTER_PORT, '/models')
            run.assert_not_called()

    def test_metrics_use_worker_listener_and_identity_checked(self):
        listing = json.dumps({'data':[{'id':'model/alias','status':{'value':'loaded'}}]})
        metrics = 'llamacpp:prompt_tokens_total 100\nllamacpp:tokens_predicted_total 20'
        class Result: stdout = '123\n'
        with patch.object(c, 'get', side_effect=[listing, metrics]) as get, patch.object(c, 'worker', return_value=('stable', 9001)), patch.object(c.subprocess, 'run', return_value=Result()):
            c.sample(self.ledger, epoch(2))
            self.assertEqual(get.call_args_list[1].args, (9001, '/metrics'))
        with patch.object(c, 'get', side_effect=[listing, metrics]), patch.object(c, 'worker', side_effect=[('old', 9001), ('new', 9001)]), patch.object(c.subprocess, 'run', return_value=Result()):
            self.assertIn('ValueError', c.sample(self.ledger, epoch(2)+5))
            self.assertEqual(self.ledger.record(epoch(2)+5, 'sampled')['todayTotalTokens'], 0)

    def test_atomic_publication_and_no_prompt_fields(self):
        record = self.ledger.record(epoch(2), 'pending')
        target = self.root/'local.json'
        target.write_text('old')
        c.publish(target, record)
        self.assertEqual(json.loads(target.read_text()), record)
        self.assertEqual(list(self.root.glob('.local-*')), [])
        for key in ['prompt', 'messages', 'response', 'credentials', 'api_key']:
            self.assertNotIn(key, record)

    def test_ui_patch_fails_closed(self):
        with self.assertRaises(ValueError): p.transform_main('unsupported')
        with self.assertRaises(ValueError): p.transform_panel('unsupported')
        with self.assertRaises(ValueError): p.stage(self.root, self.root, 'typhoon.agents')

    def test_worker_identity_and_environment_redaction(self):
        proc = self.root/'proc'
        (proc/'10/task/10').mkdir(parents=True)
        (proc/'10/task/10/children').write_text('')
        (proc/'10/task/11').mkdir()
        (proc/'10/task/11/children').write_text('20')
        (proc/'20').mkdir()
        (proc/'20/cmdline').write_bytes(b'llama-server\0--alias\0heretic\0--port\09001\0')
        # No environ file exists: the observer must not inspect environments.
        (proc/'20/stat').write_text('20 (llama server) ' + ' '.join(['S'] + ['0']*18 + ['888']))
        (proc/'sys/kernel/random').mkdir(parents=True)
        (proc/'sys/kernel/random/boot_id').write_text('fixture-boot')
        identity, port = c.worker({'id':'heretic','status':{}}, 10, proc)
        self.assertEqual((identity, port), ('fixture-boot:20:888', 9001))
        with self.assertRaises(ValueError): c.worker({'id':'other','status':{}}, 10, proc)

    def test_undated_cross_day_gap_is_not_backfilled(self):
        self.ledger.observe('heretic', 'pid', (0, 0), epoch(2))
        self.assertEqual(self.ledger.observe('heretic', 'pid', (1000, 500), epoch(3)), (0, 0))
        self.assertEqual(self.ledger.observe('heretic', 'pid', (1010, 502), epoch(3)+5), (10, 2))
        self.assertEqual(self.ledger.record(epoch(3)+5, 'sampled')['todayTotalTokens'], 12)

    def test_metrics_disabled_is_explicit_and_preserves_history(self):
        import urllib.error
        listing = json.dumps({'data':[{'id':'heretic','status':{'value':'loaded'}}]})
        from email.message import Message
        error = urllib.error.HTTPError('http://fake/DO_NOT_LOG', 501, 'DO_NOT_LOG', Message(), None)
        self.addCleanup(error.close)
        with patch.object(c, 'get', side_effect=[listing, error]), patch.object(c, 'worker', return_value=('stable', 9001)), patch.object(c, 'router_pid', return_value=123):
            status = c.sample(self.ledger, epoch(2))
            self.assertIn('Metrics disabled', status)
            self.assertNotIn('DO_NOT_LOG', status)
            self.assertFalse(self.ledger.record(epoch(2), status)['hasLocalStats'])

    def test_retention_and_model_bound(self):
        self.ledger.observe('old', 'pid', (0, 0), epoch(2))
        future = epoch(2) + 366*86400
        self.ledger.observe('new', 'pid2', (0, 0), future)
        self.assertEqual(list(self.ledger.record(future, 'sampled')['modelUsage']), ['new'])
        with self.ledger.db:
            self.ledger.db.executemany('INSERT INTO snapshots VALUES (?,?,?,?,?)', [(f'm{i}', f'p{i}', 0, 0, future) for i in range(255)])
        with self.assertRaises(ValueError): self.ledger.observe('overflow', 'pid3', (0, 0), future+5)
        self.assertNotIn('overflow', self.ledger.record(future, 'sampled')['modelUsage'])

    def test_writer_lock_and_render_only_cli(self):
        import fcntl, subprocess, sys
        state = self.root/'state'
        state.mkdir()
        args = [sys.executable, str(BASE/'collector.py'), '--state-dir', str(state), '--render-only']
        with (state/'collector.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run(args, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Another local-router collector', result.stderr)
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['usageStatusText'], 'Activation pending · not collecting')

    def test_redirects_are_refused(self):
        self.assertIsNone(c.NoRedirect().redirect_request(None, None, None))

    TABS = ['dayRow.day && dayRow.day.recorded === false ? "—"', 'return p && p.providerId === "local" ? rows : rows.slice(0, 4)',
            'name: p && p.providerId === "local" ? id : usage.friendlyModelName(id),', '" · cached input not counted"',
            'if (day && day.recorded === false) return "Not recorded — not zero usage"']
    LIMITS = ['if (item && item.providerId === "local") return ""', 'if (p.providerId === "local") continue',
              'function localRouterText(p)', 'visible: !!section.provider && section.provider.providerId === "local"',
              'text: visible ? root.localRouterText(section.provider) : ""', '(not zero usage)', 'cached input not counted']
    MAIN = ['name.slice(-5) === ".json" && name !== "local.json" && name !== "codex.json") ids.push', '/omarchy/local-router/agents/local.json',
            'id: localRouterAgent', 'result.push(displayProvider(localRouterRecord))', 'if (syncedId === "local") continue',
            'if (String(record.id) === "local") continue', 'if (providerId === "local") return null']

    def assert_staged(self, source, target, layout):
        before = tree(source)
        p.stage(source, target, 'fixture.agents')
        self.assertEqual(tree(source), before, 'packaged source untouched')
        main, panel = (target/'Main.qml').read_text(), (target/'Panel.qml').read_text()
        for snippet in self.MAIN:
            self.assertEqual(main.count(snippet), 1, snippet)
        for snippet in (self.TABS if layout == 'tabs' else self.LIMITS):
            self.assertIn(snippet, panel)
        manifest = json.loads((target/'manifest.json').read_text())
        self.assertEqual(manifest['id'], 'fixture.agents')
        self.assertEqual(manifest['name'], 'Agents + Local')
        self.assertEqual(manifest['barWidget']['displayName'], 'Agents + Local')
        self.assertEqual(manifest['omarchy'], {'clonedFrom': 'omarchy.agents', 'alaCarchy': 'local-router-stats/1',
                                               'alaCarchyLayout': layout, 'alaCarchySource': p.fingerprint(source)})
        self.assertEqual((source/'Agent.qml').read_bytes(), (target/'Agent.qml').read_bytes())
        after = tree(target)
        self.assertEqual(set(after), set(before) | {'assets/local.svg', 'assets/local-light.svg'})
        for name, data in before.items():
            if name not in ('Main.qml', 'Panel.qml', 'manifest.json'):
                self.assertEqual(after[name], data, 'native file changed: ' + name)
        for name, color in (('local.svg', b'#f5f5f5'), ('local-light.svg', b'#202020')):
            self.assertIn(b'viewBox="0 0 24 24"', after['assets/' + name])
            self.assertIn(color, after['assets/' + name])
        self.assertEqual([path.name for path in target.parent.iterdir() if path.name.startswith('.')], [], 'no staging leftovers')

    def test_both_supported_panel_layouts_are_patched(self):
        for layout in ('tabs', 'limits'):
            with self.subTest(layout=layout):
                source = fixture.make_source(self.root/layout/'source', layout)
                self.assertEqual(p.patch_panel((source/'Panel.qml').read_text())[0], layout)
                self.assert_staged(source, self.root/layout/'staged/agents', layout)

    def test_limits_layout_keeps_local_out_of_the_subscription_summary(self):
        source = fixture.make_source(self.root/'source', 'limits')
        panel = p.render(source, 'fixture.agents')['Panel.qml']
        loop = panel.split('var p = providers[i]\n', 1)[1]
        self.assertTrue(loop.startswith('      if (p.providerId === "local") continue'), 'Local is skipped before any token is summed')
        trouble = panel.split('function otherTrouble(item) {\n', 1)[1]
        self.assertTrue(trouble.startswith('    if (item && item.providerId === "local") return ""'))

    def test_ambiguous_or_unknown_panels_are_refused_without_writing(self):
        source = fixture.make_source(self.root/'source', 'tabs')
        both = fixture.PANEL_TABS + fixture.PANEL_LIMITS
        listing = sorted(path.name for path in self.root.iterdir())
        for label, panel in [('both layouts', both), ('unknown', 'import QtQuick\nItem {}\n'),
                             ('doubled anchor', fixture.PANEL_TABS.replace('    return rows.slice(0, 4)', '    return rows.slice(0, 4)\n    return rows.slice(0, 4)'))]:
            with self.subTest(label):
                (source/'Panel.qml').write_text(panel)
                with self.assertRaises(ValueError): p.patch_panel(panel)
                with self.assertRaises(ValueError): p.render(source, 'fixture.agents')
                with self.assertRaises(ValueError): p.stage(source, self.root/'never', 'fixture.agents')
                self.assertFalse((self.root/'never').exists())
                self.assertEqual(sorted(path.name for path in self.root.iterdir()), listing, 'no staging leftovers')
        (source/'Panel.qml').write_text(fixture.PANEL_TABS)
        (source/'Main.qml').write_text(fixture.MAIN.replace('  Instantiator {', '  Loader {'))
        with self.assertRaises(ValueError): p.render(source, 'fixture.agents')

    def test_render_validates_identity_and_writes_nothing(self):
        source = fixture.make_source(self.root/'source', 'limits')
        before = tree(self.root)
        rendered = p.render(source, 'fixture.agents')
        self.assertEqual(tree(self.root), before)
        self.assertEqual(rendered['layout'], 'limits')
        for bad in ('omarchy.agents', 'agents', '../escape.agents', 'fixture.agents/x', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError): p.render(source, bad)
        manifest = json.loads((source/'manifest.json').read_text())
        (source/'manifest.json').write_text(json.dumps(manifest | {'id': 'someone.else'}))
        with self.assertRaises(ValueError): p.render(source, 'fixture.agents')
        (source/'manifest.json').write_text('[]')
        with self.assertRaises(ValueError): p.render(source, 'fixture.agents')

    def test_fingerprint_follows_the_packaged_bytes(self):
        source = fixture.make_source(self.root/'source', 'limits')
        first = p.fingerprint(source)
        self.assertEqual(p.fingerprint(source), first)
        self.assertRegex(first, r'^[0-9a-f]{64}$')
        (source/'assets/claude.svg').write_text('<svg/>')
        second = p.fingerprint(source)
        self.assertNotEqual(second, first)
        (source/'assets/claude.svg').rename(source/'assets/other.svg')
        self.assertNotEqual(p.fingerprint(source), second, 'a renamed file is a different source')

    def test_native_providers(self):
        self.assertEqual(p.native_providers(json.loads((fixture.make_source(self.root/'a', 'tabs')/'manifest.json').read_text())), {'claude', 'codex', 'fireworks'})
        self.assertIn('grok', p.native_providers(json.loads((fixture.make_source(self.root/'b', 'limits')/'manifest.json').read_text())))
        for odd in ({}, [], None, {'barWidget': []}, {'barWidget': {'defaults': {'providers': ['grok']}}}):
            self.assertEqual(p.native_providers(odd), set())

    def test_installed_native_ui_contract(self):
        source = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
        if not (source/'Panel.qml').is_file(): self.skipTest('native Omarchy source not installed')
        layout = p.patch_panel((source/'Panel.qml').read_text())[0]
        self.assert_staged(source, self.root/'installed/agents', layout)


if __name__ == '__main__': unittest.main(verbosity=2)
