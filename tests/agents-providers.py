#!/usr/bin/env python3
"""Synthetic provider menu/settings proof; never run the live TUI or collectors."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
MANAGER = REPO / 'extras/agents-clone/manage.py'


class Providers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ap-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        self.source = self.root / 'native'
        self.source.mkdir()
        (self.source / 'manifest.json').write_text(json.dumps({'id': 'omarchy.agents', 'barWidget': {'defaults': {
            'providers': {'claude': {'enabled': True}, 'fireworks': {'enabled': True}, 'kimi': {'enabled': False}}}}}))
        (self.source / 'Main.qml').write_text('function updateCommand(kind, agentIds) {\n'
            '  var providers = settings && settings.providers ? settings.providers : {}\n'
            '  for (var id in providers) {\n'
            '    if (providers[id] && providers[id].enabled === false) command.push("--except", id)\n'
            '  }\n  return command\n}\n')
        self.shell = self.home / 'shell.json'
        self.original = {'bar': {'layout': {'left': [{'id': 'custom', 'keep': 3}], 'right': [
            {'id': 'omarchy.agents', 'refreshIntervalSec': 123, 'providers': {'claude': {'color': 'preserve'}}}]}}, 'keep': True}
        self.write(self.original)
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home), 'XDG_STATE_HOME': str(self.home / 'state'),
                    'XDG_CONFIG_HOME': str(self.home / 'config'), 'TMPDIR': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8'}

    def write(self, data):
        self.shell.write_text(json.dumps(data) + '\n')
        self.shell.chmod(0o600)

    def cli(self, *args, expected=0):
        r = subprocess.run(['/usr/bin/python3', '-I', '-B', str(MANAGER), '--source', str(self.source),
                            '--shell', str(self.shell), *args], env=self.env, cwd=self.root,
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, expected, r.stdout + r.stderr)
        return r

    def status(self):
        return json.loads(self.cli('--providers').stdout)

    def test_native_status_disable_enable_preserves_other_settings(self):
        status = self.status()
        self.assertEqual(status['providers'], [
            {'id': 'claude', 'enabled': True}, {'id': 'fireworks', 'enabled': True}, {'id': 'kimi', 'enabled': False}])
        self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell', status['binding'])
        d = json.loads(self.shell.read_text())
        self.assertFalse(d['bar']['layout']['right'][0]['providers']['fireworks']['enabled'])
        self.assertEqual(d['bar']['layout']['left'], self.original['bar']['layout']['left'])
        self.assertEqual(d['bar']['layout']['right'][0]['providers']['claude'], {'color': 'preserve'})
        self.assertTrue(d['keep'])
        backups = list(self.home.glob('shell.json.backup.agents-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), self.original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        status = self.status()
        self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell', status['binding'])
        self.assertEqual(len(list(self.home.glob('shell.json.backup.agents-*'))), 1)
        self.cli('--set-provider', 'fireworks', 'enabled', '--expected-shell', self.status()['binding'])
        self.assertTrue(json.loads(self.shell.read_text())['bar']['layout']['right'][0]['providers']['fireworks']['enabled'])
        self.assertFalse((self.home / '.local').exists())

    def test_stale_display_unknown_provider_and_unsafe_files_refuse(self):
        status = self.status()
        newer = json.loads(self.shell.read_text())
        newer['newer'] = 'preserve'
        self.write(newer)
        before = self.shell.read_bytes()
        self.cli('--set-provider', 'claude', 'disabled', '--expected-shell', status['binding'], expected=1)
        self.assertEqual(self.shell.read_bytes(), before)
        self.assertEqual(list(self.home.glob('shell.json.backup.agents-*')), [])
        self.cli('--set-provider', 'not-registered', 'disabled', '--expected-shell', self.status()['binding'], expected=1)
        self.cli('--set-provider', 'claude', 'disabled', expected=2)
        linked = self.home / 'linked.json'
        self.shell.rename(linked)
        self.shell.symlink_to(linked)
        self.cli('--providers', expected=1)
        self.assertEqual(linked.read_bytes(), before)
        self.shell.unlink()
        self.shell.hardlink_to(linked)
        self.cli('--providers', expected=1)
        self.assertEqual(linked.read_bytes(), before)

    def test_managed_original_keeps_native_default_when_disabling_feature_later(self):
        # Only clone-ownership admission is stubbed; settings/marker/write paths are real.
        spec = importlib.util.spec_from_file_location('provider_manager_fixture', MANAGER)
        assert spec is not None and spec.loader is not None
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        helper = manager.load('provider_fixture', REPO / 'extras/agents-clone/providers.py')
        original = self.original['bar']['layout']['right'][0]
        row = json.loads(json.dumps(original))
        row['id'] = manager.CLONE_ID
        row['providers']['fireworks'] = {'enabled': False}
        row[manager.ENTRY_KEY] = {'original': original, 'features': ['local']}
        config = {'bar': {'layout': {'right': [row]}}}
        self.write(config)
        registry = self.root / 'registry.json'
        registry.write_text('{"files":{}}')
        paths = SimpleNamespace(shell=self.shell, source=self.source, plugins=self.root / 'plugins',
                                clone=self.root / 'clone', registry=registry)
        setattr(manager, 'clone_info', lambda *_: {'features': ('local',)})
        status = helper.control(paths, manager)
        helper.control(paths, manager, ['fireworks', 'disabled'], status['binding'])
        after = json.loads(self.shell.read_text())['bar']['layout']['right'][0]
        restored = after[manager.ENTRY_KEY]['original']['providers']
        self.assertFalse(restored.get('fireworks', {}).get('enabled', True))
        self.assertEqual(after['providers']['claude'], {'color': 'preserve'})

    def test_malformed_settings_and_native_contract_refuse_without_traceback(self):
        before = self.shell.read_bytes()
        for manifest in [[], {'id': 'omarchy.agents', 'barWidget': []},
                         {'id': 'omarchy.agents', 'barWidget': {'defaults': {'providers': []}}}]:
            with self.subTest(manifest=manifest):
                (self.source / 'manifest.json').write_text(json.dumps(manifest))
                result = self.cli('--providers', expected=1)
                self.assertNotIn('Traceback', result.stdout + result.stderr)
                self.assertEqual(self.shell.read_bytes(), before)

    def test_nonfinite_input_refused_before_backup_or_write(self):
        good = self.shell.read_bytes()
        for value in ('NaN', 'Infinity', '-Infinity', '1e400'):
            with self.subTest(value=value):
                bad = good.rstrip()[:-1] + b',"unrelated":' + value.encode() + b'}\n'
                self.shell.write_bytes(bad)
                status = subprocess.run(['/usr/bin/python3', '-I', '-B', str(MANAGER), '--source', str(self.source),
                                         '--shell', str(self.shell), '--providers'], env=self.env, cwd=self.root,
                                        capture_output=True, text=True, timeout=10)
                # Strict admission may refuse before displaying a binding. If it does not,
                # the mutation must still refuse instead of serializing a non-finite value.
                if status.returncode == 0:
                    binding = json.loads(status.stdout)['binding']
                    self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell', binding, expected=1)
                else:
                    self.assertEqual(status.returncode, 1, status.stdout + status.stderr)
                self.assertEqual(self.shell.read_bytes(), bad)
                self.assertEqual(list(self.home.glob('shell.json.backup.agents-*')), [])
        self.shell.write_bytes(good)
        manifest = self.source / 'manifest.json'
        original = manifest.read_bytes()
        for value in ('NaN', '1e400'):
            with self.subTest(native_value=value):
                manifest.write_bytes(original.rstrip()[:-1] + b',"unrelated":' + value.encode() + b'}\n')
                self.cli('--providers', expected=1)
                self.assertEqual(self.shell.read_bytes(), good)

    def test_oversized_output_refused_before_backup_or_write(self):
        spec = importlib.util.spec_from_file_location('provider_size_fixture', MANAGER)
        assert spec is not None and spec.loader is not None
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        cap = manager.MAX_CONFIG
        def intended(data):
            changed = json.loads(json.dumps(data))
            changed['bar']['layout']['right'][0]['providers']['fireworks'] = {'enabled': False}
            return (json.dumps(changed, indent=2, ensure_ascii=False) + '\n').encode()
        exact = dict(self.original, pad='')
        exact['pad'] = 'x' * (cap - len(intended(exact)))
        # Compact inputs fit the cap; only their indented replacement would exceed it.
        for label, data in [('zeros', dict(self.original, pad=[0] * 200000)),
                            ('cap+1', dict(exact, pad=exact['pad'] + 'x'))]:
            with self.subTest(case=label):
                self.write(data)
                before = self.shell.read_bytes()
                self.assertLessEqual(len(before), cap)
                self.assertGreater(len(intended(data)), cap)
                backups = set(self.home.glob('shell.json.backup.agents-*'))
                result = self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell',
                                  self.status()['binding'], expected=1)
                after = self.shell.read_bytes()
                created = set(self.home.glob('shell.json.backup.agents-*')) - backups
                self.assertEqual(after, before, f'shell.json replaced with {len(after)} bytes; {len(created)} new backup(s)')
                self.assertEqual(created, set())
                self.assertIn('Refused: updated shell.json would be too large; preserved', result.stdout)
        # Exact-cap positive control: MAX_CONFIG output bytes install and read back.
        self.write(exact)
        self.assertEqual(len(intended(exact)), cap)
        self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell', self.status()['binding'])
        self.assertEqual(self.shell.read_bytes(), intended(exact))
        self.assertEqual(self.status()['providers'][1], {'id': 'fireworks', 'enabled': False})

    def test_genuine_managed_registry_duplicate_keys_refuse(self):
        spec = importlib.util.spec_from_file_location('native_provider_source_fixture', REPO / 'tests/agents_fixture.py')
        assert spec is not None and spec.loader is not None
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        full = self.root / 'full-native'
        native.make_source(full, 'limits')
        manifest = full / 'manifest.json'
        data = json.loads(manifest.read_text())
        data.setdefault('barWidget', {}).setdefault('defaults', {}).setdefault('providers', {})['fireworks'] = {'enabled': True}
        manifest.write_text(json.dumps(data))
        main = full / 'Main.qml'
        body = main.read_text()
        if 'function updateCommand(' not in body:
            function = (self.source / 'Main.qml').read_text()
            body = body[:body.rfind('}')] + function + '\n}\n'
            main.write_text(body)
        self.source = full
        self.cli('--enable', 'local')
        # No clone/registry/ownership methods are stubbed in this case.
        status = self.status()
        self.assertEqual(json.loads(self.shell.read_text())['bar']['layout']['right'][0]['id'], 'alacarchy.agents')
        registry = self.home / '.local/share/a-la-carchy/managed-files.json'
        original = registry.read_bytes()
        files = json.loads(original)['files']
        first = next(iter(files))
        top_duplicate = '{"files":{},"files":' + json.dumps(files) + '}'
        nested_duplicate = '{"files":{' + json.dumps(first) + ':"wrong",' + json.dumps(files)[1:] + '}'
        before = self.shell.read_bytes()
        for label, malformed in [('top', top_duplicate), ('nested', nested_duplicate)]:
            with self.subTest(registry=label):
                # Each ambiguity must reach the ownership branch independently,
                # not refuse because an earlier unsafe mutation made the binding stale.
                registry.write_bytes(original)
                self.shell.write_bytes(before)
                status = self.status()
                backups = set(self.home.glob('shell.json.backup.agents-*'))
                registry.write_text(malformed)
                self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell', status['binding'], expected=1)
                self.assertEqual(self.shell.read_bytes(), before)
                self.assertEqual(set(self.home.glob('shell.json.backup.agents-*')), backups)
        registry.write_bytes(original)
        self.cli('--set-provider', 'fireworks', 'disabled', '--expected-shell', self.status()['binding'])
        self.assertFalse(json.loads(self.shell.read_text())['bar']['layout']['right'][0]['providers']['fireworks']['enabled'])

    def test_failed_write_retains_config_and_private_backup(self):
        spec = importlib.util.spec_from_file_location('provider_fault_fixture', MANAGER)
        assert spec is not None and spec.loader is not None
        manager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manager)
        helper = manager.load('provider_fault_control', REPO / 'extras/agents-clone/providers.py')
        paths = SimpleNamespace(shell=self.shell, source=self.source, plugins=self.root / 'plugins')
        status = helper.control(paths, manager)
        before = self.shell.read_bytes()
        def fail_write(*_):
            raise OSError('synthetic pre-replacement write failure')
        setattr(manager, 'write_config', fail_write)
        with self.assertRaises(OSError):
            helper.control(paths, manager, ['fireworks', 'disabled'], status['binding'])
        self.assertEqual(self.shell.read_bytes(), before)
        backups = list(self.home.glob('shell.json.backup.agents-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_installed_provider_manager_works_without_repository_access(self):
        self.cli('--install-tools', 'local')
        installed = self.home / '.local/share/a-la-carchy/agents-clone/manage.py'
        guard = r'''
import json,runpy,sys
root,script,*args=sys.argv[1:]
attempts=[]
def audit(event,values):
    if event=='open' and isinstance(values[0],str) and (values[0]==root or values[0].startswith(root+'/')):
        attempts.append('denied-original-repository-read')
        raise PermissionError('isolated repository access denied')
sys.addaudithook(audit)
try:open(root+'/README.md','rb')
except PermissionError:pass
else:raise AssertionError('denial control not exercised')
assert len(attempts)==1
attempts.clear()
sys.argv=[script,*args]
code=0
try:runpy.run_path(script,run_name='__main__')
except SystemExit as exc:code=exc.code
print('INSTALLED_PROVIDER_REPOSITORY_DENIALS='+json.dumps(attempts))
assert not attempts
raise SystemExit(code)
'''
        def call(*args):
            child = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', guard, str(REPO), str(installed),
                                    '--source', str(self.source), '--shell', str(self.shell), *args],
                                   env=self.env, cwd=self.root, capture_output=True, text=True, timeout=15)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            self.assertIn('INSTALLED_PROVIDER_REPOSITORY_DENIALS=[]', child.stdout)
            return json.loads(child.stdout.splitlines()[0])
        status = call('--providers')
        call('--set-provider', 'fireworks', 'disabled', '--expected-shell', status['binding'])
        self.assertFalse(json.loads(self.shell.read_text())['bar']['layout']['right'][0]['providers']['fireworks']['enabled'])
        self.assertTrue((installed.parent / 'providers.py').is_file())
        self.assertTrue((installed.parent / 'menu.sh').is_file())

    @unittest.skipUnless(os.environ.get('AGENTS_NATIVE_SOURCE') and os.environ.get('AGENTS_NATIVE_UPDATER')
                         and os.environ.get('AGENTS_MANAGED_SOURCE'), 'explicit native/managed source inputs required')
    def test_real_update_command_and_native_updater_skip_disabled_collectors(self):
        # Read code only. Execute the native updater under a completely synthetic OMARCHY_PATH.
        main = Path(os.environ['AGENTS_NATIVE_SOURCE']) / 'Main.qml'
        managed = Path(os.environ['AGENTS_MANAGED_SOURCE']) / 'Main.qml'
        updater = Path(os.environ['AGENTS_NATIVE_UPDATER'])
        pattern = r'^  function updateCommand\(kind, agentIds\) \{\n.*?^  \}'
        native_functions = re.findall(pattern, main.read_text(), re.M | re.S)
        managed_functions = re.findall(pattern, managed.read_text(), re.M | re.S)
        self.assertEqual(len(native_functions), 1)
        self.assertEqual(native_functions, managed_functions)
        node = shutil.which('node')
        self.assertIsNotNone(node)
        observed = {}
        for enabled in (False, True):
            self.cli('--set-provider', 'fireworks', 'enabled' if enabled else 'disabled',
                     '--expected-shell', self.status()['binding'])
            settings = json.loads(self.shell.read_text())['bar']['layout']['right'][0]
            program = 'const settings=' + json.dumps(settings) + ';\n' + native_functions[0]
            program += '\nprocess.stdout.write(JSON.stringify(updateCommand("force", ["claude", "fireworks"])));'
            result = subprocess.run([str(node), '-e', program], capture_output=True, text=True, env=self.env, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            command = json.loads(result.stdout)
            self.assertEqual(command[0], 'omarchy-agent-usage-update')
            run = self.root / ('pipeline-enabled' if enabled else 'pipeline-disabled')
            tools = run / 'bin'
            tools.mkdir(parents=True)
            mockbin = run / 'mockbin'
            mockbin.mkdir()
            calls = run / 'calls'
            for agent in ('claude', 'fireworks'):
                tool = tools / ('omarchy-agent-usage-' + agent)
                tool.write_text('#!/bin/sh\nset -eu\nmise ' + agent + '\nprintf \'{"id":"' + agent + '"}\\n\'\n')
                tool.chmod(0o700)
            mise = mockbin / 'mise'
            mise.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$MOCK_CALLS"\n')
            mise.chmod(0o700)
            copy = run / 'update.sh'
            copy.write_bytes(updater.read_bytes())
            env = dict(self.env, OMARCHY_PATH=str(run), XDG_STATE_HOME=str(run / 'state'),
                       PATH=str(mockbin) + ':' + self.env['PATH'], MOCK_CALLS=str(calls))
            child = subprocess.run(['/bin/bash', str(copy), *command[1:]], env=env, cwd=run,
                                   capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            actual = sorted(calls.read_text().splitlines())
            self.assertEqual(actual, ['claude', 'fireworks'] if enabled else ['claude'])
            observed[str(enabled)] = actual
        import hashlib
        print('NATIVE_COLLECTOR_GATING_PROOF=' + json.dumps({'calls': observed, 'no_real_mise_or_collectors': True,
              'native_main_sha256': hashlib.sha256(main.read_bytes()).hexdigest(),
              'managed_main_sha256': hashlib.sha256(managed.read_bytes()).hexdigest(),
              'native_updater_sha256': hashlib.sha256(updater.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    unittest.main(verbosity=2)
