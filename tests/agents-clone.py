#!/usr/bin/env python3
"""Local/Grok Agents-clone integration in an isolated HOME.

Never touches the live shell, router, Grok, systemd or package-owned files:
systemctl is a logging mock on PATH and collectors run only in
render/initialize/hook mode. The manager's logic runs against synthetic Agents
sources for each supported panel layout (deterministic, see agents_fixture.py);
a separate class copies the really installed plugin read-only into the fixture
to prove the installed Omarchy is still supported.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MANAGE = ROOT/'extras/agents-clone/manage.py'
STOCK = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
_spec = importlib.util.spec_from_file_location('agents_fixture', Path(__file__).with_name('agents_fixture.py'))
agents_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agents_fixture)
NATIVE_GROK = 'already shows Grok usage (limits and tokens) natively'
SYSTEMCTL = r'''#!/bin/bash
printf '%s\n' "$*" >> "$FIXTURE/systemctl.log"
state() { cat "$FIXTURE/unit-$1" 2>/dev/null || printf '%s' "$2"; }
case "$1 $2" in
  "--user is-active") s=$(state active inactive); printf '%s\n' "$s"; [[ "$s" == active ]] ;;
  "--user is-enabled") s=$(state enabled disabled); printf '%s\n' "$s"; [[ "$s" == enabled ]] ;;
  "--user disable")
    [[ -e "$FIXTURE/disable-fails" ]] && exit 1
    printf inactive > "$FIXTURE/unit-active"; printf disabled > "$FIXTURE/unit-enabled" ;;
  *) printf 'unexpected\n' >> "$FIXTURE/unexpected.log"; exit 97 ;;
esac
'''


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


class Fixture(unittest.TestCase):
    """Isolated HOME plus one Agents source; subclasses pick the source."""
    LAYOUT = 'tabs'

    def make_source(self):
        agents_fixture.make_source(self.source, self.LAYOUT)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root/'home'
        self.source = self.root/'omarchy/shell/plugins/agents'
        self.make_source()
        self.pristine_source = tree(self.source)
        self.plugins = self.home/'.config/omarchy/plugins'
        self.plugins.mkdir(parents=True)
        self.shell = self.home/'.config/omarchy/shell.json'
        self.layout = {'bar': {'layout': {
            'left': [{'id': 'omarchy.menu'}],
            'center': [{'id': 'alacarchy.supersonic', '_alaCarchySupersonicOriginal': None}],
            'right': [{'id': 'omarchy.tray'}, {'id': 'omarchy.agents', 'refreshIntervalSec': 300,
                                                'providers': {'claude': {'enabled': False}}}, {'id': 'omarchy.power'}]}},
            'version': 1, 'other': {'keep': True}}
        self.write_shell(self.layout)
        self.grok = self.home/'.grok'
        self.grok.mkdir()
        self.grok_config = self.grok/'config.toml'
        self.grok_original = '# user settings\nmodel = "grok-4"\n\n[ui]\ntheme = "dark"\n'
        self.grok_config.write_text(self.grok_original)
        bin_dir = self.root/'bin'
        bin_dir.mkdir()
        (bin_dir/'systemctl').write_text(SYSTEMCTL)
        (bin_dir/'systemctl').chmod(0o755)
        for name in ('omarchy-shell', 'omarchy-plugin-enable', 'omarchy-plugin-clone', 'grok', 'llama-server', 'curl'):
            (bin_dir/name).write_text('#!/bin/sh\necho "$0" >> "$FIXTURE/unexpected.log"\nexit 97\n')
            (bin_dir/name).chmod(0o755)
        self.env = {'LANG': 'C.UTF-8', 'TMPDIR': str(self.root),
                    'XDG_CONFIG_HOME': str(self.home/'.config'), 'XDG_DATA_HOME': str(self.home/'.local/share'),
                    'XDG_CACHE_HOME': str(self.home/'.cache'), 'HERMES_HOME': str(self.root/'synthetic-hermes'),
                    'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(self.root/'no-bus')}
        self.env.update(HOME=str(self.home), XDG_STATE_HOME=str(self.home/'.local/state'), FIXTURE=str(self.root),
                        PATH=str(bin_dir) + ':' + os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1',
                        XDG_RUNTIME_DIR=str(self.root/'runtime'), http_proxy='http://127.0.0.1:9', https_proxy='http://127.0.0.1:9')
        self.state = self.home/'.local/state'
        self.clone = self.plugins/'alacarchy.agents'
        self.unit = self.home/'.config/systemd/user/local-router-stats.service'
        self.local_collector = self.home/'.local/share/a-la-carchy/local-router-stats/collector.py'
        self.grok_collector = self.home/'.local/share/a-la-carchy/grok-usage/collector.py'
        self.local_record = self.state/'omarchy/local-router/agents/local.json'
        self.grok_record = self.state/'omarchy/grok-usage/agents/grok.json'

    def tearDown(self):
        self.assertFalse((self.root/'unexpected.log').exists(), 'unexpected command escaped the fixture')

    def write_shell(self, data):
        self.shell.parent.mkdir(parents=True, exist_ok=True)
        self.shell.write_text(json.dumps(data, indent=2) + '\n')

    def run_manage(self, feature, mode, source=None):
        return subprocess.run([sys.executable, str(MANAGE), '--source', str(source or self.source), '--shell', str(self.shell),
                               '--plugins', str(self.plugins), '--' + mode, feature],
                              capture_output=True, text=True, env=self.env, timeout=120)

    def ok(self, feature, mode):
        run = self.run_manage(feature, mode)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn('Traceback', run.stderr)
        return run

    def refused(self, feature, mode):
        run = self.run_manage(feature, mode)
        self.assertNotEqual(run.returncode, 0, run.stdout)
        self.assertNotIn('Traceback', run.stderr + run.stdout)
        return run

    def state_of(self, feature):
        return (self.run_manage(feature, 'check-enabled').returncode == 0, self.run_manage(feature, 'check-disabled').returncode == 0)

    def entry(self):
        rows = [r for s in json.loads(self.shell.read_text())['bar']['layout'].values() for r in s if r['id'] == 'alacarchy.agents']
        return rows[0] if rows else None

    def systemctl_calls(self):
        log = self.root/'systemctl.log'
        return log.read_text().splitlines() if log.exists() else []

    def snapshot_local(self):
        return {p: p.read_bytes() for p in (self.local_collector, self.unit, self.local_record)}

    def snapshot_grok(self):
        return {p: p.read_bytes() for p in (self.grok_collector, self.grok_config, self.grok_record)}

    def manifest(self):
        return json.loads((self.clone/'manifest.json').read_text())

    def assert_nothing_changed(self, before_shell):
        self.assertEqual(self.shell.read_bytes(), before_shell)
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        for path in (self.unit, self.local_collector, self.grok_collector, self.local_record, self.grok_record):
            self.assertFalse(path.exists(), path)

    def new_omarchy(self, layout=None):
        """Replace the packaged source, as an Omarchy update would."""
        shutil.rmtree(self.source)
        agents_fixture.make_source(self.source, layout or self.LAYOUT)
        (self.source/'README.md').write_text('# Agents, a later release\n')


class TabsLayout(Fixture):
    """Omarchy 4.0.4 panel layout without packaged Grok: Local and Grok tabs."""

    # ------------------------------------------------------------ flows
    def exercise(self, first, second, disable_first):
        self.ok(first, 'enable')
        self.assertEqual(self.state_of(first), (True, False))
        self.assertEqual(self.state_of(second), (False, True))
        clone_bytes = tree(self.clone)
        firsts = self.snapshot_local() if first == 'local' else self.snapshot_grok()
        self.ok(second, 'enable')
        self.assertEqual(tree(self.clone), clone_bytes, 'second feature must not restage the shared clone')
        self.assertEqual(self.snapshot_local() if first == 'local' else self.snapshot_grok(), firsts)
        self.assertEqual(self.state_of('local'), (True, False))
        self.assertEqual(self.state_of('grok'), (True, False))
        self.assertEqual(self.entry()['_alaCarchyAgents']['features'], ['grok', 'local'])
        survivor = second if disable_first == first else first
        kept = self.snapshot_local() if survivor == 'local' else self.snapshot_grok()
        self.ok(disable_first, 'disable')
        self.assertEqual(self.state_of(disable_first), (False, True))
        self.assertEqual(self.state_of(survivor), (True, False))
        self.assertEqual(self.snapshot_local() if survivor == 'local' else self.snapshot_grok(), kept)
        self.assertEqual(tree(self.clone), clone_bytes)
        self.assertEqual(self.entry()['_alaCarchyAgents']['features'], [survivor])
        self.ok(survivor, 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        self.assertEqual(tree(self.clone), clone_bytes, 'plugin files are kept on disable')
        for feature in ('local', 'grok'):
            self.assertEqual(self.state_of(feature), (False, True))
            before = self.shell.read_bytes()
            self.ok(feature, 'disable')
            self.assertEqual(self.shell.read_bytes(), before)

    def test_local_then_grok_disable_local_first(self):
        self.exercise('local', 'grok', 'local')

    def test_local_then_grok_disable_grok_first(self):
        self.exercise('local', 'grok', 'grok')

    def test_grok_then_local_disable_grok_first(self):
        self.exercise('grok', 'local', 'grok')

    def test_grok_then_local_disable_local_first(self):
        self.exercise('grok', 'local', 'local')

    def test_repeated_cycles_are_idempotent_and_reuse_clone(self):
        self.ok('local', 'enable')
        clone_bytes = tree(self.clone)
        enabled = self.shell.read_bytes()
        self.ok('local', 'enable')
        self.assertEqual(self.shell.read_bytes(), enabled)
        self.ok('grok', 'enable')
        grok_bytes = self.grok_config.read_bytes()
        self.ok('grok', 'enable')
        self.assertEqual(self.grok_config.read_bytes(), grok_bytes)
        self.assertEqual(len(list(self.grok.glob('config.toml.backup.*'))), 1)
        for _ in range(2):
            self.ok('local', 'disable'); self.ok('grok', 'disable')
            self.ok('grok', 'enable'); self.ok('local', 'enable')
        self.assertEqual(tree(self.clone), clone_bytes)
        self.ok('grok', 'disable'); self.ok('local', 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)

    def test_entry_settings_preserved_and_edits_kept(self):
        self.ok('local', 'enable')
        entry = self.entry()
        self.assertEqual(entry['refreshIntervalSec'], 300)
        self.assertEqual(entry['providers'], {'claude': {'enabled': False}})
        self.assertEqual(list(entry)[0], 'id')
        config = json.loads(self.shell.read_text())
        right = config['bar']['layout']['right']
        self.assertEqual([r['id'] for r in right], ['omarchy.tray', 'alacarchy.agents', 'omarchy.power'])
        right[1]['refreshIntervalSec'] = 600  # a later per-entry edit in the shell
        self.write_shell(config)
        self.ok('local', 'disable')
        restored = json.loads(self.shell.read_text())['bar']['layout']['right'][1]
        self.assertEqual(restored, {'id': 'omarchy.agents', 'refreshIntervalSec': 600, 'providers': {'claude': {'enabled': False}}})

    def test_no_stock_entry_appends_then_removes(self):
        self.layout['bar']['layout']['right'] = [{'id': 'omarchy.tray'}]
        self.write_shell(self.layout)
        self.ok('grok', 'enable')
        self.assertEqual(json.loads(self.shell.read_text())['bar']['layout']['right'][-1]['id'], 'alacarchy.agents')
        self.ok('grok', 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)

    # -------------------------------------------------------- refusals
    def test_custom_plugin_at_managed_id_refused(self):
        self.clone.mkdir()
        (self.clone/'manifest.json').write_text('{"id":"alacarchy.agents","omarchy":{"clonedFrom":"omarchy.agents"}}')
        (self.clone/'Panel.qml').write_text('custom')
        custom = tree(self.clone)
        before = self.shell.read_bytes()
        for feature in ('local', 'grok'):
            self.assertIn('not an unmodified managed clone', self.refused(feature, 'enable').stdout)
            self.assertEqual(self.state_of(feature), (False, True))
        self.assertEqual(tree(self.clone), custom)
        self.assert_nothing_changed(before)

    def test_user_agents_clone_in_bar_refused(self):
        mine = self.plugins/'fixture.agents'
        mine.mkdir()
        (mine/'manifest.json').write_text(json.dumps({'id': 'fixture.agents', 'omarchy': {'clonedFrom': 'omarchy.agents'}}))
        self.layout['bar']['layout']['right'][1] = {'id': 'fixture.agents', 'custom': True}
        self.write_shell(self.layout)
        before = self.shell.read_bytes()
        for feature in ('local', 'grok'):
            self.assertIn('another Agents clone', self.refused(feature, 'enable').stdout)
        self.assertFalse(self.clone.exists())
        self.assert_nothing_changed(before)

    def test_modified_managed_clone_refused_but_disable_restores(self):
        self.ok('local', 'enable')
        panel = self.clone/'Panel.qml'
        panel.write_text(panel.read_text() + '\n// user edit\n')
        edited = tree(self.clone)
        self.assertEqual(self.state_of('local'), (False, False))
        self.refused('grok', 'enable')
        self.assertFalse(self.grok_collector.exists())
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        self.ok('local', 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)
        self.assertEqual(tree(self.clone), edited)

    def test_invalid_and_ambiguous_layouts_refused(self):
        stock = {'id': 'omarchy.agents'}
        managed = {'id': 'alacarchy.agents', '_alaCarchyAgents': {'original': None, 'features': ['local']}}
        layout = lambda *rows: json.dumps({'bar': {'layout': {'right': list(rows)}}}).encode()
        # (shell.json bytes, disable refused?) -- enable must refuse in every case.
        cases = [(b'{bad json', True), (json.dumps({'bar': {'layout': {'right': 'nope'}}}).encode(), True),
                 (layout(stock, dict(stock)), False), (layout(stock, managed), False),
                 (layout({'id': 'alacarchy.agents'}), True),
                 (layout({'id': 'alacarchy.agents', '_alaCarchyAgents': {'original': None, 'features': ['bogus']}}), True),
                 (layout({'id': 'alacarchy.agents', '_alaCarchyAgents': {'original': {'id': 'other.widget'}, 'features': ['local']}}), True),
                 (layout(managed, dict(managed)), True)]
        for data, disable_refused in cases:
            with self.subTest(data=data[:70]):
                for feature in ('local', 'grok'):
                    self.shell.write_bytes(data)
                    self.refused(feature, 'enable')
                    self.assertEqual(self.shell.read_bytes(), data)
                    if disable_refused:
                        self.refused(feature, 'disable')
                        self.assertEqual(self.shell.read_bytes(), data)
                self.assertFalse(self.clone.exists())
                self.assertEqual(self.grok_config.read_text(), self.grok_original)
        # Disabling with only stock duplicates has nothing of ours to touch.
        self.shell.write_bytes(layout(stock, dict(stock)))
        self.ok('local', 'disable')
        self.assertEqual(self.shell.read_bytes(), layout(stock, dict(stock)))

    def test_symlinked_shell_config_refused(self):
        real = self.root/'real-shell.json'
        self.shell.rename(real)
        self.shell.symlink_to(real)
        before = real.read_bytes()
        self.refused('local', 'enable')
        self.assertEqual(real.read_bytes(), before)
        self.assertTrue(self.shell.is_symlink())
        self.assertFalse(self.clone.exists())

    def test_unsupported_source_stages_nothing(self):
        (self.source/'Main.qml').write_text('import QtQuick\nItem {}\n')
        before = self.shell.read_bytes()
        self.assertIn('unsupported', self.refused('local', 'enable').stdout)
        self.assertFalse(self.clone.exists())
        self.assertEqual([p.name for p in self.plugins.iterdir()], [])
        self.assertEqual(self.shell.read_bytes(), before)

    # ------------------------------------------------------------- grok
    def test_grok_round_trip_is_byte_exact_and_minimal(self):
        self.ok('grok', 'enable')
        text = self.grok_config.read_text()
        self.assertTrue(text.startswith(self.grok_original))
        parsed = tomllib.loads(text)
        command = parsed['ui'].pop('status_line')
        self.assertEqual(parsed, tomllib.loads(self.grok_original))
        self.assertEqual(command['type'], 'command')
        self.assertEqual(command['refresh_interval'], 5)
        self.assertEqual(command['command'], '/usr/bin/python3 %s --hook --state-dir %s' % (self.grok_collector, self.state/'omarchy/grok-usage'))
        backups = list(self.grok.glob('config.toml.backup.*'))
        self.assertEqual([b.read_text() for b in backups], [self.grok_original])
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.ok('grok', 'disable')
        self.assertEqual(self.grok_config.read_text(), self.grok_original)

    def test_hook_command_publishes_like_grok_would(self):
        self.ok('grok', 'enable')
        record = json.loads(self.grok_record.read_text())
        self.assertEqual(record['usageStatusText'], 'Waiting for Grok token metadata · quota unavailable')
        self.assertEqual(record['limits'], [{'key': 'quota', 'label': 'Subscription quota', 'percent': -1}])
        command = tomllib.loads(self.grok_config.read_text())['ui']['status_line']['command']
        payload = {'schema_version': 1, 'session_id': '12345678-1234-1234-1234-123456789abc', 'model': {'id': 'grok-test'},
                   'context_window': {'session_usage': {'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 3,
                                                        'cache_creation_input_tokens': 2}, 'session_input_tokens': 15, 'session_output_tokens': 5}}
        run = subprocess.run(['sh', '-c', command], input=json.dumps(payload) + '\n', capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(run.stdout.startswith('Grok · observed tokens'))
        record = json.loads(self.grok_record.read_text())
        self.assertEqual(record['usageStatusText'], 'Observed tokens · subscription quota unavailable')
        self.assertEqual(record['todayTotalTokens'], 0, 'first observation is a baseline, not backfill')

    def test_grok_config_absent_is_created_private_and_removed(self):
        self.grok_config.unlink()
        self.grok_original = None
        self.ok('grok', 'enable')
        self.assertEqual(self.grok_config.stat().st_mode & 0o777, 0o600)
        self.assertIn('[ui.status_line]', self.grok_config.read_text())
        self.ok('grok', 'disable')
        self.assertFalse(self.grok_config.exists())

    def test_existing_status_line_preserved(self):
        for original in ('[ui.status_line]\ntype = "builtin"\n', '[ui]\nstatus_line = { type = "command", command = "~/.grok/mine.sh" }\n',
                         'ui = { theme = "dark" }\n', 'ui = "flat"\n'):
            with self.subTest(original=original):
                self.grok_config.write_text(original)
                before = self.shell.read_bytes()
                self.refused('grok', 'enable')
                self.assertEqual(self.grok_config.read_text(), original)
                self.assertEqual(self.shell.read_bytes(), before)
                self.assertFalse(self.clone.exists())
                self.assertFalse(self.grok_collector.exists())
                self.assertEqual(list(self.grok.glob('config.toml.backup.*')), [])

    def test_invalid_toml_and_missing_grok_refused(self):
        self.grok_config.write_text('not = [valid\n')
        before = self.shell.read_bytes()
        self.refused('grok', 'enable')
        self.assertEqual(self.grok_config.read_text(), 'not = [valid\n')
        shutil.rmtree(self.grok)
        self.assertIn('~/.grok missing', self.refused('grok', 'enable').stdout)
        self.assertEqual(self.shell.read_bytes(), before)
        self.assertFalse(self.clone.exists())

    def test_edited_block_is_preserved_on_disable(self):
        self.ok('grok', 'enable')
        edited = self.grok_config.read_text().replace('refresh_interval = 5', 'refresh_interval = 30')
        self.grok_config.write_text(edited)
        shell = self.shell.read_bytes()
        self.assertEqual(self.state_of('grok'), (False, False))
        self.assertIn('edited', self.refused('grok', 'disable').stdout)
        self.assertEqual(self.grok_config.read_text(), edited)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertTrue(self.grok_record.exists())

    def test_rewritten_config_without_markers_is_not_claimed(self):
        self.ok('grok', 'enable')
        rewritten = '\n'.join(l for l in self.grok_config.read_text().splitlines() if not l.startswith('# ')) + '\n'
        self.grok_config.write_text(rewritten)
        self.assertEqual(self.state_of('grok'), (False, False))
        self.ok('grok', 'disable')  # nothing of ours is identifiable; it is left alone
        self.assertEqual(self.grok_config.read_text(), rewritten)
        self.assertFalse(self.grok_record.exists())
        self.assertEqual(self.state_of('grok'), (False, True))

    def test_layout_failure_rolls_back_new_grok_hook(self):
        spec = importlib.util.spec_from_file_location('manage_under_test', MANAGE)
        manage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manage)
        args = mock.Mock(source=self.source, shell=self.shell, plugins=self.plugins)
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch.object(manage, 'write_config', side_effect=manage.Refused('shell.json changed during the update')):
            paths = manage.Paths(args)
            with self.assertRaises(manage.Refused):
                manage.enable(paths, 'grok')
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)

    # ------------------------------------------------------------ local
    def test_enable_local_never_starts_or_contacts_anything(self):
        run = self.ok('local', 'enable')
        self.assertEqual(self.systemctl_calls(), [])
        self.assertIn('Collection is NOT running', run.stdout)
        record = json.loads(self.local_record.read_text())
        self.assertEqual(record['usageStatusText'], 'Activation pending · not collecting')
        self.assertEqual(record['todayTotalTokens'], 0)
        self.assertTrue(all(day['recorded'] is False for day in record['recentDays']))
        self.assertEqual(self.unit.read_bytes(), (ROOT/'extras/local-router-stats/local-router-stats.service').read_bytes())
        self.assertEqual(self.local_collector.read_bytes(), (ROOT/'extras/local-router-stats/collector.py').read_bytes())
        self.assertFalse((self.home/'.config/systemd/user/default.target.wants').exists())

    def test_disable_local_stops_only_our_running_service(self):
        self.ok('local', 'enable')
        (self.root/'unit-active').write_text('active'); (self.root/'unit-enabled').write_text('enabled')
        self.ok('local', 'disable')
        self.assertIn('--user disable --now local-router-stats.service', self.systemctl_calls())
        self.assertFalse(self.unit.exists())
        self.assertTrue(self.local_collector.exists(), 'collector program kept')
        self.assertTrue((self.state/'omarchy/local-router/tokens.sqlite3').exists(), 'history kept')

    def test_disable_local_service_failures_preserve_everything(self):
        self.ok('local', 'enable')
        (self.root/'unit-active').write_text('active')
        (self.root/'disable-fails').touch()
        shell = self.shell.read_bytes()
        self.refused('local', 'disable')
        self.assertTrue(self.unit.exists())
        self.assertTrue(self.local_record.exists())
        self.assertEqual(self.shell.read_bytes(), shell)
        (self.root/'disable-fails').unlink()
        (self.root/'unit-active').write_text('')  # unreadable manager state
        self.assertIn('could not read', self.refused('local', 'disable').stdout)
        self.assertTrue(self.unit.exists())

    def test_foreign_unit_and_collector_preserved(self):
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text('[Service]\nExecStart=/bin/true\n')
        before = self.shell.read_bytes()
        self.assertIn('differs', self.refused('local', 'enable').stdout)
        self.assertEqual(self.unit.read_text(), '[Service]\nExecStart=/bin/true\n')
        self.assertFalse(self.clone.exists())
        self.assertEqual(self.shell.read_bytes(), before)
        self.assertIn('not ours', self.refused('local', 'disable').stdout)
        self.assertEqual(self.unit.read_text(), '[Service]\nExecStart=/bin/true\n')
        self.assertEqual(self.systemctl_calls(), [])

    def test_upgrade_replaces_only_registered_bytes(self):
        self.ok('local', 'enable')
        old = b'# previous managed collector version\n'
        self.local_collector.write_bytes(old)
        registry = self.home/'.local/share/a-la-carchy/managed-files.json'
        data = json.loads(registry.read_text())
        data['files'][str(self.local_collector)] = hashlib.sha256(old).hexdigest()
        registry.write_text(json.dumps(data))
        self.ok('local', 'enable')
        self.assertEqual(self.local_collector.read_bytes(), (ROOT/'extras/local-router-stats/collector.py').read_bytes())
        self.local_collector.write_bytes(b'# user modified\n')
        self.refused('local', 'enable')
        self.assertEqual(self.local_collector.read_bytes(), b'# user modified\n')

    def test_staged_clone_contract(self):
        self.ok('local', 'enable')
        manifest = json.loads((self.clone/'manifest.json').read_text())
        self.assertEqual(manifest['id'], 'alacarchy.agents')
        self.assertEqual(manifest['omarchy']['clonedFrom'], 'omarchy.agents')
        self.assertEqual(manifest['omarchy']['alaCarchy'], 'local-router-stats/1;grok-observed-stats/1')
        files = {p.relative_to(self.clone).as_posix() for p in self.clone.rglob('*') if p.is_file()} - {'manifest.json'}
        self.assertEqual(set(manifest['omarchy']['managedFiles']), files)
        main = (self.clone/'Main.qml').read_text()
        self.assertIn('/omarchy/local-router/agents/local.json', main)
        self.assertIn('/omarchy/grok-usage/agents/grok.json', main)
        self.assertIn('name !== "local.json" && name !== "grok.json"', main)
        self.assertEqual([p.name for p in self.plugins.iterdir()], ['alacarchy.agents'], 'no hidden staging leftovers')
        self.assertEqual(tree(self.source), self.pristine_source, 'packaged source untouched')
        self.assertEqual(manifest['name'], 'Agents + Local + Grok tokens')
        self.assertEqual(manifest['omarchy']['alaCarchyLayout'], 'tabs')
        self.assertRegex(manifest['omarchy']['alaCarchySource'], r'^[0-9a-f]{64}$')

    def test_check_modes_are_quiet(self):
        for feature in ('local', 'grok'):
            for mode in ('check-enabled', 'check-disabled'):
                run = self.run_manage(feature, mode)
                self.assertEqual(run.stdout + run.stderr, '')

    # ------------------------------------------------- clone provenance
    def test_marker_alone_never_makes_a_clone_ours(self):
        """Forged provenance: right marker and manifest shape, wrong or missing digests."""
        self.ok('local', 'enable')
        self.ok('local', 'disable')
        genuine = self.manifest()
        shutil.rmtree(self.clone)
        before = self.shell.read_bytes()
        kept = tree(self.home)  # what an enable/disable round trip legitimately leaves behind
        forgeries = {
            'marker without digests': {k: v for k, v in genuine['omarchy'].items() if k != 'managedFiles'},
            'empty digest list': genuine['omarchy'] | {'managedFiles': {}},
            'wrong digests': genuine['omarchy'] | {'managedFiles': dict.fromkeys(genuine['omarchy']['managedFiles'], '0' * 64)},
            'path escape': genuine['omarchy'] | {'managedFiles': genuine['omarchy']['managedFiles'] | {'../outside': '0' * 64}},
            'unknown mark': genuine['omarchy'] | {'alaCarchy': 'local-router-stats/999'},
        }
        for label, meta in forgeries.items():
            with self.subTest(label):
                self.clone.mkdir()
                self.addCleanup(shutil.rmtree, self.clone, ignore_errors=True)
                for name in ('Main.qml', 'Panel.qml', 'Agent.qml'):
                    (self.clone/name).write_text('// my own plugin\n')
                (self.clone/'manifest.json').write_text(json.dumps(genuine | {'omarchy': meta}))
                mine = tree(self.clone)
                try:
                    for feature in ('local', 'grok'):
                        self.assertIn('not an unmodified managed clone', self.refused(feature, 'enable').stdout)
                        self.assertEqual(self.state_of(feature), (False, True))
                    self.assertEqual(tree(self.clone), mine)
                    self.assertEqual(self.shell.read_bytes(), before)
                    self.assertEqual(self.grok_config.read_text(), self.grok_original)
                finally:
                    shutil.rmtree(self.clone)
                self.assertEqual(tree(self.home), kept, 'a refusal writes nothing')

    def test_extra_or_missing_files_make_the_clone_foreign(self):
        self.ok('local', 'enable')
        (self.clone/'Mine.qml').write_text('// added by the user\n')
        self.assertEqual(self.state_of('local'), (False, False))
        self.assertIn('not an unmodified managed clone', self.refused('grok', 'enable').stdout)
        (self.clone/'Mine.qml').unlink()
        self.assertEqual(self.state_of('local'), (True, False))
        (self.clone/'README.md').unlink()
        self.assertEqual(self.state_of('local'), (False, False))
        self.refused('grok', 'enable')
        self.assertEqual(self.grok_config.read_text(), self.grok_original)

    def test_symlink_inside_clone_is_refused(self):
        self.ok('local', 'enable')
        target = self.root/'elsewhere.qml'
        target.write_bytes((self.clone/'Agent.qml').read_bytes())
        (self.clone/'Agent.qml').unlink()
        (self.clone/'Agent.qml').symlink_to(target)
        self.assertEqual(self.state_of('local'), (False, False))
        self.refused('grok', 'enable')
        self.assertTrue((self.clone/'Agent.qml').is_symlink())

    # ------------------------------------------------ Omarchy updates
    def test_unused_clone_is_rebuilt_from_a_newer_source(self):
        self.ok('local', 'enable')
        old = self.manifest()['omarchy']['alaCarchySource']
        self.ok('local', 'disable')
        self.new_omarchy()
        run = self.ok('grok', 'enable')
        self.assertIn('Rebuilt alacarchy.agents', run.stdout)
        self.assertNotEqual(self.manifest()['omarchy']['alaCarchySource'], old)
        self.assertEqual((self.clone/'README.md').read_text(), '# Agents, a later release\n')
        self.assertEqual(self.state_of('grok'), (True, False))
        self.assertEqual([p.name for p in self.plugins.iterdir()], ['alacarchy.agents'], 'no hidden staging leftovers')
        # Same source again: nothing is rebuilt.
        self.ok('grok', 'disable')
        clone_bytes = tree(self.clone)
        self.assertNotIn('Rebuilt', self.ok('local', 'enable').stdout)
        self.assertEqual(tree(self.clone), clone_bytes)

    def test_clone_in_use_is_never_rebuilt(self):
        self.ok('local', 'enable')
        clone_bytes = tree(self.clone)
        self.new_omarchy()
        run = self.ok('grok', 'enable')
        self.assertIn('built from a different Omarchy Agents version', run.stdout)
        self.assertNotIn('Rebuilt', run.stdout)
        self.assertEqual(tree(self.clone), clone_bytes)
        self.assertEqual(self.state_of('local'), (True, False))
        self.assertEqual(self.state_of('grok'), (True, False))

    def test_modified_unused_clone_is_not_rebuilt(self):
        self.ok('local', 'enable')
        self.ok('local', 'disable')
        (self.clone/'Panel.qml').write_text((self.clone/'Panel.qml').read_text() + '// my edit\n')
        edited = tree(self.clone)
        self.new_omarchy()
        before = self.shell.read_bytes()
        self.assertIn('not an unmodified managed clone', self.refused('local', 'enable').stdout)
        self.assertEqual(tree(self.clone), edited)
        self.assertEqual(self.shell.read_bytes(), before)

    def test_unsupported_update_keeps_unused_clone_and_refuses(self):
        self.ok('local', 'enable')
        self.ok('local', 'disable')
        clone_bytes = tree(self.clone)
        (self.source/'Panel.qml').write_text('import QtQuick\nItem {}\n')
        before = self.shell.read_bytes()
        self.assertIn('unsupported', self.refused('local', 'enable').stdout)
        self.assertEqual(tree(self.clone), clone_bytes)
        self.assertEqual(self.shell.read_bytes(), before)
        self.assertEqual([p.name for p in self.plugins.iterdir()], ['alacarchy.agents'])

    def test_update_to_native_grok_keeps_enabled_tabs_then_drops_grok(self):
        """Tabs enabled before Omarchy began collecting Grok itself keep working and stay removable."""
        self.ok('local', 'enable')
        self.ok('grok', 'enable')
        clone_bytes = tree(self.clone)
        self.new_omarchy('limits')
        self.assertEqual(self.state_of('local'), (True, False))
        self.assertEqual(self.state_of('grok'), (True, False))
        self.ok('grok', 'disable')
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        self.assertEqual(tree(self.clone), clone_bytes, 'a clone in use is never rebuilt')
        self.assertIn(NATIVE_GROK, self.refused('grok', 'enable').stdout)
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        self.ok('local', 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)
        self.assertIn('Rebuilt', self.ok('local', 'enable').stdout)
        self.assertEqual(self.manifest()['omarchy']['alaCarchy'], 'local-router-stats/1')
        self.assertIn(NATIVE_GROK, self.refused('grok', 'enable').stdout)
        self.assertEqual(self.state_of('grok'), (False, True))
        self.assertEqual(self.state_of('local'), (True, False))


class LimitsLayoutNativeGrok(Fixture):
    """Limits-first panel that collects Grok itself (omarchy-dev): Local only."""
    LAYOUT = 'limits'

    def test_local_round_trip(self):
        run = self.ok('local', 'enable')
        self.assertIn('Staged alacarchy.agents (Agents + Local)', run.stdout)
        self.assertEqual(self.state_of('local'), (True, False))
        self.assertEqual(self.entry()['_alaCarchyAgents']['features'], ['local'])
        self.assertEqual(self.systemctl_calls(), [])
        enabled = self.shell.read_bytes()
        self.ok('local', 'enable')
        self.assertEqual(self.shell.read_bytes(), enabled)
        self.ok('local', 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)
        self.assertEqual(self.state_of('local'), (False, True))
        self.assertEqual(tree(self.source), self.pristine_source, 'packaged source untouched')

    def test_staged_clone_is_local_only_and_leaves_grok_native(self):
        self.ok('local', 'enable')
        manifest = self.manifest()
        self.assertEqual(manifest['id'], 'alacarchy.agents')
        self.assertEqual(manifest['name'], 'Agents + Local')
        self.assertEqual(manifest['omarchy']['clonedFrom'], 'omarchy.agents')
        self.assertEqual(manifest['omarchy']['alaCarchy'], 'local-router-stats/1')
        self.assertEqual(manifest['omarchy']['alaCarchyLayout'], 'limits')
        self.assertIn('grok', manifest['barWidget']['defaults']['providers'], 'packaged Grok support is kept')
        files = {p.relative_to(self.clone).as_posix() for p in self.clone.rglob('*') if p.is_file()} - {'manifest.json'}
        self.assertEqual(set(manifest['omarchy']['managedFiles']), files)
        main = (self.clone/'Main.qml').read_text()
        panel = (self.clone/'Panel.qml').read_text()
        self.assertIn('/omarchy/local-router/agents/local.json', main)
        self.assertIn('name.slice(-5) === ".json" && name !== "local.json" && name !== "codex.json") ids.push', main)
        for text in (main, panel):
            self.assertNotIn('grok', text.lower(), 'the first-party Grok record is not filtered, renamed or replaced')
        self.assertIn('function localRouterText(p)', panel)
        self.assertIn('if (item && item.providerId === "local") return ""', panel)
        self.assertIn('if (p.providerId === "local") continue', panel)
        self.assertEqual([p.name for p in self.plugins.iterdir()], ['alacarchy.agents'], 'no hidden staging leftovers')

    def test_grok_tab_refused_where_omarchy_collects_grok(self):
        before = self.shell.read_bytes()
        run = self.refused('grok', 'enable')
        self.assertIn(NATIVE_GROK, run.stdout)
        self.assertFalse(self.clone.exists(), 'a refusal stages nothing')
        self.assertEqual(list(self.plugins.iterdir()), [])
        self.assert_nothing_changed(before)
        self.assertEqual(list(self.grok.glob('config.toml.backup.*')), [])
        self.assertEqual(self.state_of('grok'), (False, True))
        # ... and equally with the Local tab already in place.
        self.ok('local', 'enable')
        clone_bytes, shell, local = tree(self.clone), self.shell.read_bytes(), self.snapshot_local()
        self.assertIn(NATIVE_GROK, self.refused('grok', 'enable').stdout)
        self.assertEqual(tree(self.clone), clone_bytes)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.snapshot_local(), local)
        self.assertEqual(self.grok_config.read_text(), self.grok_original)
        self.assertFalse(self.grok_collector.exists())
        self.assertFalse(self.grok_record.exists())
        self.ok('grok', 'disable')  # nothing of ours to remove: a no-op, not an error
        self.assertEqual(self.shell.read_bytes(), shell)

    def test_unknown_layout_without_native_grok_still_refuses_the_grok_tab(self):
        shutil.rmtree(self.source)
        agents_fixture.make_source(self.source, 'limits', native_grok=False)
        before = self.shell.read_bytes()
        self.assertIn('does not support this Agents panel layout', self.refused('grok', 'enable').stdout)
        self.assertFalse(self.clone.exists())
        self.assert_nothing_changed(before)
        self.ok('local', 'enable')
        self.assertEqual(self.state_of('local'), (True, False))

    def test_local_service_handling_is_layout_independent(self):
        self.ok('local', 'enable')
        (self.root/'unit-active').write_text('active'); (self.root/'unit-enabled').write_text('enabled')
        self.ok('local', 'disable')
        self.assertIn('--user disable --now local-router-stats.service', self.systemctl_calls())
        self.assertFalse(self.unit.exists())


@unittest.skipUnless((STOCK/'Main.qml').is_file(), 'installed Omarchy Agents plugin required')
class InstalledSource(Fixture):
    """The plugin Omarchy really installed here, copied read-only into the fixture.

    A failure in this class (and only this class) means an Omarchy update
    changed the Agents plugin beyond what the stagers support.
    """

    def make_source(self):
        shutil.copytree(STOCK, self.source)

    def native_grok(self):
        providers = json.loads((STOCK/'manifest.json').read_text()).get('barWidget', {}).get('defaults', {}).get('providers', {})
        return 'grok' in providers

    def test_installed_plugin_is_supported_for_local(self):
        run = self.ok('local', 'enable')
        self.assertEqual(self.state_of('local'), (True, False))
        manifest = self.manifest()
        self.assertIn(manifest['omarchy']['alaCarchyLayout'], ('limits', 'tabs'))
        self.assertIn('Staged alacarchy.agents', run.stdout)
        self.assertIn('/omarchy/local-router/agents/local.json', (self.clone/'Main.qml').read_text())
        self.assertEqual((self.clone/'Agent.qml').read_bytes(), (STOCK/'Agent.qml').read_bytes())
        self.assertEqual(tree(self.source), tree(STOCK), 'source copy untouched')
        self.ok('local', 'disable')
        self.assertEqual(json.loads(self.shell.read_text()), self.layout)

    def test_local_icons_follow_native_dark_and_light_surface_convention(self):
        self.ok('local', 'enable')
        hashes = self.manifest()['omarchy']['managedFiles']
        icons = []
        for name in ('local.svg', 'local-light.svg'):
            path = self.clone/'assets'/name
            self.assertTrue(path.is_file(), 'Local provider mark is missing: ' + name)
            data = path.read_bytes()
            self.assertIn(b'viewBox="0 0 24 24"', data)
            self.assertEqual(hashes['assets/' + name], hashlib.sha256(data).hexdigest())
            icons.append(data)
        self.assertNotEqual(icons[0], icons[1], 'light and dark surfaces need contrasting marks')
        # The dev-channel Grok marks and collector admission stay first-party.
        for path in (self.source/'assets').iterdir():
            self.assertEqual((self.clone/'assets'/path.name).read_bytes(), path.read_bytes())
        self.assertEqual(tree(self.source), tree(STOCK), 'package source was changed')

    def test_installed_plugin_grok_tab_matches_what_omarchy_ships(self):
        if self.native_grok():
            before = self.shell.read_bytes()
            self.assertIn(NATIVE_GROK, self.refused('grok', 'enable').stdout)
            self.assertFalse(self.clone.exists())
            self.assert_nothing_changed(before)
        else:
            self.ok('grok', 'enable')
            self.assertEqual(self.state_of('grok'), (True, False))
            self.ok('grok', 'disable')
            self.assertEqual(self.grok_config.read_text(), self.grok_original)
            self.assertEqual(json.loads(self.shell.read_text()), self.layout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
