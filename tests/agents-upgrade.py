#!/usr/bin/env python3
"""Guarded upgrade fixtures: source/config/registry/service all disposable."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('upgrade_fixture', Path(__file__).with_name('agents-clone.py'))
f = importlib.util.module_from_spec(spec); spec.loader.exec_module(f)
spec = importlib.util.spec_from_file_location('upgrade_manage', ROOT/'extras/agents-clone/manage.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class UpgradeTests(f.Fixture):
    LAYOUT = 'limits'

    def setUp(self):
        super().setUp()
        self.ok('local', 'enable')
        self.registry = self.home/'.local/share/a-la-carchy/managed-files.json'

    def preimage(self):
        return {'version': 1, 'clone': {n: m.digest(b) for n, b in f.tree(self.clone).items()},
                'registry': m.digest(self.registry.read_bytes()), 'shell': m.digest(self.shell.read_bytes()),
                'files': {str(self.local_collector): m.digest(self.local_collector.read_bytes())}}

    def upgrader(self):
        source = ROOT/'extras/agents-clone/upgrade.py'
        self.assertTrue(source.is_file(), 'guarded upgrade implementation must exist')
        spec = importlib.util.spec_from_file_location('upgrade_under_test', source)
        u = importlib.util.module_from_spec(spec); spec.loader.exec_module(u)
        return u

    def paths(self):
        return m.Paths(type('Args', (), {'source': self.source, 'shell': self.shell, 'plugins': self.plugins})())

    def test_unused_clone_unknown_nodes_are_refused_without_content_loss(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        for kind in ('fifo', 'socket', 'empty-directory'):
            with self.subTest(kind=kind):
                node = self.clone/'unknown-user-node'
                server, directory_fd = None, None
                try:
                    if kind == 'fifo':
                        os.mkfifo(node)
                    elif kind == 'socket':
                        # A short kernel fd alias keeps AF_UNIX fixture paths bounded.
                        directory_fd = os.open(self.clone, os.O_RDONLY | os.O_DIRECTORY)
                        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        server.bind(f'/proc/self/fd/{directory_fd}/{node.name}')
                    else:
                        node.mkdir()
                    mode = node.lstat().st_mode
                    before = f.tree(self.home)
                    run = self.run_manage('local', 'enable')
                    self.assertNotEqual(run.returncode, 0, run.stdout+run.stderr)
                    self.assertNotIn('Traceback', run.stderr)
                    self.assertEqual(node.lstat().st_mode, mode)
                    self.assertEqual(f.tree(self.home), before)
                    self.assertIsNone(m.clone_info(self.clone))
                finally:
                    if server is not None:
                        server.close()
                    if directory_fd is not None:
                        os.close(directory_fd)
                    if node.is_dir():
                        node.rmdir()
                    elif node.exists():
                        node.unlink()

    def foreign_node_refuses_replacement(self, unused, name):
        if unused:
            self.ok('local', 'disable')
        self.new_omarchy('limits')
        foreign = self.clone/name
        scan, observed = os.scandir, []
        class Entry:
            def __init__(self, entry): self.entry = entry
            def __getattr__(self, name): return getattr(self.entry, name)
            def __fspath__(self): return self.entry.path
            def stat(self, *, follow_symlinks=True):
                info = self.entry.stat(follow_symlinks=follow_symlinks)
                if Path(self.entry.path) == foreign:
                    fields = list(info)
                    fields[4] = os.getuid() + 10000
                    observed.append(self.entry.path)
                    return os.stat_result(fields)
                return info
        class Scan:
            def __init__(self, path): self.context = scan(path)
            def __enter__(self): return iter(Entry(entry) for entry in self.context.__enter__())
            def __exit__(self, *args): return self.context.__exit__(*args)
        before, calls = f.tree(self.home), self.systemctl_calls()
        with patch.dict(os.environ, self.env, clear=True), patch.object(os, 'scandir', side_effect=Scan):
            if unused:
                with self.assertRaises(m.Refused):
                    m.enable(self.paths(), 'local')
            else:
                u = self.upgrader()
                with self.assertRaises(u.manage.Refused):
                    u.upgrade(self.paths())
            self.assertIsNone(m.clone_info(self.clone, m.Registry(self.registry)))
        self.assertTrue(observed, 'the fixture must exercise the foreign node UID')
        self.assertEqual(f.tree(self.home), before)
        self.assertEqual(self.systemctl_calls(), calls)

    def test_unused_rebuild_refuses_foreign_clone_directory(self):
        self.foreign_node_refuses_replacement(unused=True, name='assets')

    def test_enabled_upgrade_refuses_foreign_clone_directory(self):
        self.foreign_node_refuses_replacement(unused=False, name='assets')

    def test_unused_rebuild_refuses_foreign_clone_file(self):
        self.foreign_node_refuses_replacement(unused=True, name='assets/local.svg')

    def test_enabled_upgrade_refuses_foreign_clone_file(self):
        self.foreign_node_refuses_replacement(unused=False, name='assets/local.svg')

    def foreign_opened_file_refuses_replacement(self, unused):
        if unused:
            self.ok('local', 'disable')
        self.new_omarchy('limits')
        foreign = (self.clone/'Agent.qml').resolve()
        fstat, observed = os.fstat, []
        def opened_as_foreign(fd):
            info = fstat(fd)
            if Path(os.readlink(f'/proc/self/fd/{fd}')) == foreign:
                fields = list(info)
                fields[4] = os.getuid() + 10000
                observed.append(fd)
                return os.stat_result(fields)
            return info
        before, calls = f.tree(self.home), self.systemctl_calls()
        with patch.dict(os.environ, self.env, clear=True), patch.object(os, 'fstat', opened_as_foreign):
            if unused:
                with self.assertRaises(m.Refused):
                    m.enable(self.paths(), 'local')
            else:
                u = self.upgrader()
                with self.assertRaises(u.manage.Refused):
                    u.upgrade(self.paths())
            self.assertIsNone(m.clone_info(self.clone, m.Registry(self.registry)))
        self.assertTrue(observed, 'the fixture must exercise the opened file UID')
        self.assertEqual(f.tree(self.home), before)
        self.assertEqual(self.systemctl_calls(), calls)

    def test_unused_rebuild_refuses_foreign_opened_clone_file(self):
        self.foreign_opened_file_refuses_replacement(unused=True)

    def test_enabled_upgrade_refuses_foreign_opened_clone_file(self):
        self.foreign_opened_file_refuses_replacement(unused=False)

    def test_clone_inventory_refuses_foreign_root(self):
        real_lstat, observed = Path.lstat, []
        def foreign_root(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            if path == self.clone:
                fields = list(info)
                fields[4] = os.getuid() + 10000
                observed.append(path)
                return os.stat_result(fields)
            return info
        before = f.tree(self.home)
        with patch.object(Path, 'lstat', foreign_root):
            with self.assertRaises(m.Refused):
                m.plugin_snapshot(self.clone)
            self.assertIsNone(m.clone_info(self.clone, m.Registry(self.registry)))
        self.assertTrue(observed, 'the fixture must exercise the foreign root UID')
        self.assertEqual(f.tree(self.home), before)

    def test_native_source_readability_does_not_require_current_uid(self):
        real_lstat, real_fstat = Path.lstat, os.fstat
        def foreign_uid(info):
            fields = list(info)
            fields[4] = os.getuid() + 10000
            return os.stat_result(fields)
        def native_root(path, *args, **kwargs):
            info = real_lstat(path, *args, **kwargs)
            return foreign_uid(info) if path == self.source else info
        def native_file(fd):
            info = real_fstat(fd)
            target = Path(os.readlink(f'/proc/self/fd/{fd}'))
            return foreign_uid(info) if target.is_relative_to(self.source) else info
        with patch.dict(os.environ, self.env, clear=True):
            expected = m.clone_plan(self.paths())[1]
            with patch.object(Path, 'lstat', native_root), patch.object(os, 'fstat', native_file):
                actual = m.clone_plan(self.paths())[1]
        self.assertEqual(actual, expected, 'readable package-owned sources must remain supported')

    def failed_restore_requires_incomplete(self, unused):
        if unused:
            self.ok('local', 'disable')
        self.new_omarchy('limits')
        u, rename = self.upgrader(), os.rename
        old = f.tree(self.clone)
        registry, shell = self.registry.read_bytes(), self.shell.read_bytes()
        collector, calls = self.local_collector.read_bytes(), self.systemctl_calls()
        failures = []
        def destination_denied(source, target):
            if Path(target) == self.clone:
                failures.append(Path(source).name)
                raise OSError('fixture persistent clone destination failure')
            return rename(source, target)
        with patch.dict(os.environ, self.env, clear=True), patch.object(os, 'rename', side_effect=destination_denied):
            if unused:
                with self.assertRaises(m.Refused) as caught:
                    m.enable(self.paths(), 'local')
            else:
                with self.assertRaises(u.manage.Refused) as caught:
                    u.upgrade(self.paths())
        self.assertEqual(failures, ['plugin', 'previous'], 'both swap and restoration must have failed')
        self.assertFalse(self.clone.exists())
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.local_collector.read_bytes(), collector)
        self.assertEqual(self.systemctl_calls(), calls)
        originals = self.registry.parent.glob('agents-upgrade-backups/*/original-at-swap')
        self.assertTrue(any(f.tree(original) == old for original in originals))
        self.assertIn('rollback incomplete', str(caught.exception))
        self.assertNotIn('refused/rolled back', str(caught.exception))

    def test_unused_rebuild_failed_restore_reports_incomplete(self):
        self.failed_restore_requires_incomplete(unused=True)

    def test_enabled_upgrade_failed_restore_reports_incomplete(self):
        self.failed_restore_requires_incomplete(unused=False)

    def home_outside_backups(self):
        """HOME bytes other than the private backup and lock a refusal retains."""
        share = self.registry.parent.relative_to(self.home).as_posix()
        return {n: b for n, b in f.tree(self.home).items()
                if not n.startswith((share+'/agents-upgrade-backups/', share+'/.agents-upgrade.lock'))}

    def failed_swap_restores_original(self, unused):
        if unused:
            self.ok('local', 'disable')
        else:
            # An older registered collector makes the replaced-file rollback observable.
            older = b'# older registered fixture collector\n'
            self.local_collector.write_bytes(older)
            body = json.loads(self.registry.read_bytes())
            body['files'][str(self.local_collector)] = m.digest(older)
            self.registry.write_text(json.dumps(body, indent=2, sort_keys=True)+'\n')
        self.new_omarchy('limits')
        u, rename = self.upgrader(), os.rename
        old, before, calls = f.tree(self.clone), self.home_outside_backups(), self.systemctl_calls()
        attempts = []
        def staged_install_denied(source, target):
            if Path(target) == self.clone:
                attempts.append(Path(source).name)
                if len(attempts) == 1:
                    raise OSError('fixture staged clone install failure')
            return rename(source, target)
        with patch.dict(os.environ, self.env, clear=True), patch.object(os, 'rename', side_effect=staged_install_denied):
            if unused:
                with self.assertRaises(m.Refused) as caught:
                    m.enable(self.paths(), 'local')
            else:
                with self.assertRaises(u.manage.Refused) as caught:
                    u.upgrade(self.paths())
            self.assertIsNotNone(m.clone_info(self.clone, m.Registry(self.registry)))
        self.assertEqual(attempts, ['plugin', 'previous'], 'the swap must fail and the restoration must then run')
        self.assertEqual(f.tree(self.clone), old)
        self.assertEqual(self.home_outside_backups(), before)
        self.assertEqual(self.systemctl_calls(), calls)
        self.assertIn('refused/rolled back', str(caught.exception))
        self.assertNotIn('rollback incomplete', str(caught.exception))
        backups = self.registry.parent/'agents-upgrade-backups'
        self.assertTrue(any(f.tree(backup) == old for backup in backups.glob('*/clone')))
        self.assertFalse(list(backups.glob('*/original-at-swap')), 'a restored original must not also be parked')
        self.assertFalse(list(self.plugins.glob('.alacarchy.agents.upgrade-*')))

    def test_unused_rebuild_failed_swap_restores_original(self):
        self.failed_swap_restores_original(unused=True)

    def test_enabled_upgrade_failed_swap_restores_original(self):
        self.failed_swap_restores_original(unused=False)

    def concurrent_destination_is_preserved(self, unused, after_check):
        if unused:
            self.ok('local', 'disable')
        self.new_omarchy('limits')
        u, rename = self.upgrader(), os.rename
        old = f.tree(self.clone)
        registry, shell = self.registry.read_bytes(), self.shell.read_bytes()
        collector, calls = self.local_collector.read_bytes(), self.systemctl_calls()
        newer = {'newer-user-file': b'newer concurrent user bytes\n'} if after_check else {}
        attempts, inodes = [], []
        def another_writer():
            # An empty directory is the one node a directory rename replaces silently.
            self.clone.mkdir()
            for name, data in newer.items():
                (self.clone/name).write_bytes(data)
            inodes.append(self.clone.lstat().st_ino)
        def destination_taken(source, target):
            if Path(target) == self.clone:
                attempts.append(Path(source).name)
                if len(attempts) == 1:
                    if not after_check:
                        another_writer()
                    raise OSError('fixture staged clone install failure')
                another_writer()
            return rename(source, target)
        with patch.dict(os.environ, self.env, clear=True), patch.object(os, 'rename', side_effect=destination_taken):
            if unused:
                with self.assertRaises(m.Refused) as caught:
                    m.enable(self.paths(), 'local')
            else:
                with self.assertRaises(u.manage.Refused) as caught:
                    u.upgrade(self.paths())
        self.assertEqual(attempts, ['plugin', 'previous'] if after_check else ['plugin'])
        self.assertEqual([self.clone.lstat().st_ino], inodes, 'the concurrent node must survive')
        self.assertEqual(f.tree(self.clone), newer)
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.local_collector.read_bytes(), collector)
        self.assertEqual(self.systemctl_calls(), calls)
        originals = self.registry.parent.glob('agents-upgrade-backups/*/original-at-swap')
        self.assertTrue(any(f.tree(original) == old for original in originals))
        self.assertIn('rollback incomplete', str(caught.exception))
        self.assertNotIn('refused/rolled back', str(caught.exception))
        self.assertFalse(list(self.plugins.glob('.alacarchy.agents.upgrade-*')))

    def test_unused_rebuild_keeps_concurrent_empty_destination(self):
        self.concurrent_destination_is_preserved(unused=True, after_check=False)

    def test_enabled_upgrade_keeps_concurrent_empty_destination(self):
        self.concurrent_destination_is_preserved(unused=False, after_check=False)

    def test_unused_rebuild_keeps_destination_created_during_restore(self):
        self.concurrent_destination_is_preserved(unused=True, after_check=True)

    def test_enabled_upgrade_keeps_destination_created_during_restore(self):
        self.concurrent_destination_is_preserved(unused=False, after_check=True)

    def test_registered_clone_hardlink_is_not_accepted_as_owned(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        agent = self.clone/'Agent.qml'
        outside = self.root/'same-bytes-not-owned-by-clone'
        outside.write_bytes(agent.read_bytes())
        agent.unlink()
        os.link(outside, agent)
        before = f.tree(self.home)
        run = self.run_manage('local', 'enable')
        self.assertNotEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertEqual(agent.stat().st_nlink, 2)
        self.assertEqual(f.tree(self.home), before)
        self.assertEqual(agent.read_bytes(), outside.read_bytes())
        self.assertIsNone(m.clone_info(self.clone))

    def test_unused_rebuild_registry_failure_restores_clone_and_keeps_backup(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        registry = self.registry.read_bytes()
        shell = self.shell.read_bytes()
        collector = self.local_collector.read_bytes()
        calls = self.systemctl_calls()
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(m.Registry, 'save', side_effect=OSError('fixture registry failure')) as save:
            with self.assertRaises((OSError, m.Refused)):
                m.enable(self.paths(), 'local')
        self.assertGreater(save.call_count, 0, 'fixture must fail the actual registration write')
        self.assertEqual(f.tree(self.clone), old)
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.local_collector.read_bytes(), collector)
        self.assertFalse(self.unit.exists())
        backups = list(self.registry.parent.glob('agents-upgrade-backups/*/clone'))
        self.assertTrue(any(f.tree(backup) == old for backup in backups))
        self.assertEqual(self.systemctl_calls(), calls)

    def test_unused_rebuild_refuses_newer_clone_edit_during_staging(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        registry = self.registry.read_bytes()
        shell = self.shell.read_bytes()
        calls = self.systemctl_calls()
        sentinel = b'// newer user edit during staging\n'
        with patch.dict(os.environ, self.env, clear=True):
            paths = self.paths()
            plan = m.clone_plan(paths)
            local, write = plan[0], plan[0].write
            def copy_then_edit(source, target, rendered):
                write(source, target, rendered)
                (self.clone/'Agent.qml').write_bytes(sentinel)
            with patch.object(m, 'clone_plan', return_value=plan), \
                    patch.object(local, 'write', side_effect=copy_then_edit) as staged:
                with self.assertRaises(m.Refused):
                    m.enable(paths, 'local')
            self.assertEqual(staged.call_count, 1)
        self.assertEqual((self.clone/'Agent.qml').read_bytes(), sentinel)
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.systemctl_calls(), calls)

    def test_unused_rebuild_refuses_registry_change_during_staging(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        body = json.loads(self.registry.read_bytes())
        body['sentinel'] = 'newer registry edit'
        sentinel = (json.dumps(body)+'\n').encode()
        with patch.dict(os.environ, self.env, clear=True):
            paths = self.paths()
            plan = m.clone_plan(paths)
            local, write = plan[0], plan[0].write
            def copy_then_edit(source, target, rendered):
                write(source, target, rendered)
                self.registry.write_bytes(sentinel)
            with patch.object(m, 'clone_plan', return_value=plan), patch.object(local, 'write', side_effect=copy_then_edit):
                with self.assertRaises(m.Refused):
                    m.enable(paths, 'local')
        self.assertEqual(f.tree(self.clone), old)
        self.assertEqual(self.registry.read_bytes(), sentinel)
        self.assertFalse(self.unit.exists())

    def test_unused_rebuild_refuses_shell_change_during_staging(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        registry = self.registry.read_bytes()
        body = json.loads(self.shell.read_bytes())
        body['sentinel'] = 'newer shell edit'
        sentinel = (json.dumps(body)+'\n').encode()
        with patch.dict(os.environ, self.env, clear=True):
            paths = self.paths()
            plan = m.clone_plan(paths)
            local, write = plan[0], plan[0].write
            def copy_then_edit(source, target, rendered):
                write(source, target, rendered)
                self.shell.write_bytes(sentinel)
            with patch.object(m, 'clone_plan', return_value=plan), patch.object(local, 'write', side_effect=copy_then_edit):
                with self.assertRaises(m.Refused):
                    m.enable(paths, 'local')
        self.assertEqual(f.tree(self.clone), old)
        self.assertEqual(self.shell.read_bytes(), sentinel)
        self.assertEqual(self.registry.read_bytes(), registry)

    def test_unused_rebuild_refuses_source_change_during_staging(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        registry = self.registry.read_bytes()
        with patch.dict(os.environ, self.env, clear=True):
            paths = self.paths()
            plan = m.clone_plan(paths)
            local, write = plan[0], plan[0].write
            def copy_then_edit(source, target, rendered):
                write(source, target, rendered)
                (self.source/'README.md').write_text('newer native source during staging\n')
            with patch.object(m, 'clone_plan', return_value=plan), patch.object(local, 'write', side_effect=copy_then_edit):
                with self.assertRaises(m.Refused):
                    m.enable(paths, 'local')
        self.assertEqual(f.tree(self.clone), old)
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual((self.source/'README.md').read_text(), 'newer native source during staging\n')

    def test_unused_rebuild_preserves_postswap_fifo_and_reports_recovery(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        registry = self.registry.read_bytes()
        fifo = self.clone/'newer-user-fifo'
        def fail_after_edit():
            os.mkfifo(fifo)
            raise OSError('fixture concurrent edit and registration failure')
        with patch.dict(os.environ, self.env, clear=True), patch.object(m.Registry, 'save', side_effect=fail_after_edit):
            with self.assertRaisesRegex(m.Refused, 'rollback incomplete;.*private backup retained at'):
                m.enable(self.paths(), 'local')
        self.assertTrue(fifo.exists())
        self.assertEqual(self.registry.read_bytes(), registry)
        originals = list(self.registry.parent.glob('agents-upgrade-backups/*/original-at-swap'))
        self.assertTrue(any(f.tree(backup) == old for backup in originals))
        self.assertFalse(list(self.plugins.glob('.alacarchy.agents.upgrade-*')))

    def test_unused_rebuild_postregistration_clone_edit_prevents_success(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        registry = self.registry.read_bytes()
        shell = self.shell.read_bytes()
        sentinel = b'// newer edit while registration commits\n'
        save = m.Registry.save
        def save_then_edit(owner):
            save(owner)
            (self.clone/'Agent.qml').write_bytes(sentinel)
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(m.Registry, 'save', autospec=True, side_effect=save_then_edit):
            with self.assertRaisesRegex(m.Refused, 'rollback incomplete;.*private backup retained at'):
                m.enable(self.paths(), 'local')
        self.assertEqual((self.clone/'Agent.qml').read_bytes(), sentinel)
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual(self.shell.read_bytes(), shell)
        originals = list(self.registry.parent.glob('agents-upgrade-backups/*/original-at-swap'))
        self.assertTrue(any(f.tree(backup) == old for backup in originals))

    def test_unused_rebuild_preserves_concurrent_unsafe_registry_and_reports_backup(self):
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        before_registry = self.registry.read_bytes()
        def fail_after_edit():
            self.registry.unlink()
            os.mkfifo(self.registry)
            raise OSError('fixture concurrent registry replacement')
        with patch.dict(os.environ, self.env, clear=True), patch.object(m.Registry, 'save', side_effect=fail_after_edit):
            with self.assertRaisesRegex(m.Refused, 'rollback incomplete;.*private backup retained at'):
                m.enable(self.paths(), 'local')
        import stat
        self.assertTrue(stat.S_ISFIFO(self.registry.lstat().st_mode))
        self.assertEqual(f.tree(self.clone), old)
        backups = list(self.registry.parent.glob('agents-upgrade-backups/*/registry'))
        self.assertTrue(any(backup.read_bytes() == before_registry for backup in backups))

    def test_unused_rebuild_honors_existing_installer_lock(self):
        import fcntl
        self.ok('local', 'disable')
        self.new_omarchy('limits')
        lock_path = self.registry.with_name('.agents-upgrade.lock')
        lock_path.write_bytes(b'fixture lock contents\n')
        with lock_path.open('rb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = f.tree(self.home)
            run = self.run_manage('local', 'enable')
        self.assertNotEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertIn('another Agents upgrade is running', run.stdout)
        self.assertEqual(f.tree(self.home), before)

    def test_unused_rebuild_success_registers_clone_and_reenable_is_idempotent(self):
        self.ok('local', 'disable')
        old = f.tree(self.clone)
        self.new_omarchy('limits')
        calls = self.systemctl_calls()
        self.ok('local', 'enable')
        self.assertEqual((self.clone/'README.md').read_text(), '# Agents, a later release\n')
        files = json.loads(self.registry.read_bytes())['files']
        for name, data in f.tree(self.clone).items():
            self.assertEqual(files[str(self.clone/name)], m.digest(data), name)
        backups = list(self.registry.parent.glob('agents-upgrade-backups/*/clone'))
        self.assertTrue(any(f.tree(backup) == old for backup in backups))
        before = f.tree(self.home)
        self.ok('local', 'enable')
        self.ok('local', 'check-enabled')
        self.assertEqual(f.tree(self.home), before)
        self.assertEqual(self.systemctl_calls(), calls)

    def test_source_symlinks_refused_before_any_install_writes(self):
        outside = self.root/'outside-source'
        outside.mkdir()
        sentinel = outside/'synthetic-private.json'
        sentinel.write_bytes(b'{"synthetic_token":"NOT_A_PLUGIN_FILE"}\n')
        before = f.tree(self.home)
        for name, target in (('outside-file.json', sentinel), ('outside-directory', outside)):
            link = self.source/name
            link.symlink_to(target, target_is_directory=target.is_dir())
            run = self.run_manage('local', 'upgrade')
            self.assertNotEqual(run.returncode, 0, name)
            self.assertNotIn('Traceback', run.stderr)
            self.assertEqual(f.tree(self.home), before)
            self.assertEqual(sentinel.read_bytes(), b'{"synthetic_token":"NOT_A_PLUGIN_FILE"}\n')
            link.unlink()
        linked_source = self.root/'linked-source'
        linked_source.symlink_to(self.source, target_is_directory=True)
        u = self.upgrader()
        with patch.dict(os.environ, self.env, clear=True):
            paths = self.paths()
            paths.source = linked_source
            with self.assertRaises(u.manage.Refused):
                u.upgrade(paths)
        self.assertEqual(f.tree(self.home), before)

    def test_source_nonregular_and_hardlinks_refused_before_install_writes(self):
        before = f.tree(self.home)
        fifo = self.source/'source-fifo'
        os.mkfifo(fifo)
        run = self.run_manage('local', 'upgrade')
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(f.tree(self.home), before)
        fifo.unlink()
        external = self.root/'synthetic-external-file'
        external.write_bytes(b'not a native plugin asset\n')
        os.link(external, self.source/'hardlinked-file')
        run = self.run_manage('local', 'upgrade')
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(f.tree(self.home), before)
        self.assertEqual(external.read_bytes(), b'not a native plugin asset\n')

    def test_registered_manifest_cannot_redeclare_edited_agent_ownership(self):
        u = self.upgrader()
        with patch.dict(os.environ, self.env, clear=True):
            u.upgrade(self.paths())
        agent = self.clone/'Agent.qml'
        agent.write_bytes(b'// unknown user replacement\n')
        manifest = self.clone/'manifest.json'
        body = json.loads(manifest.read_bytes())
        body['omarchy']['managedFiles']['Agent.qml'] = m.digest(agent.read_bytes())
        manifest.write_text(json.dumps(body, indent=2)+'\n')
        before = f.tree(self.home)
        with patch.dict(os.environ, self.env, clear=True):
            for approval in (None, self.preimage()):
                with self.assertRaises(u.manage.Refused):
                    u.upgrade(self.paths(), approval)
        self.assertEqual(f.tree(self.home), before)

    def test_preimage_cannot_authorize_manifest_listed_extra_file(self):
        extra = self.clone/'unknown-asset.txt'
        extra.write_bytes(b'unknown user asset\n')
        manifest = self.clone/'manifest.json'
        body = json.loads(manifest.read_bytes())
        body['omarchy']['managedFiles'][extra.name] = m.digest(extra.read_bytes())
        manifest.write_text(json.dumps(body, indent=2)+'\n')
        before = f.tree(self.home)
        u = self.upgrader()
        with patch.dict(os.environ, self.env, clear=True), self.assertRaises(u.manage.Refused):
            u.upgrade(self.paths(), self.preimage())
        self.assertEqual(f.tree(self.home), before)

    def test_initial_staging_registers_the_complete_trusted_clone(self):
        registry = json.loads(self.registry.read_bytes())['files']
        for name, data in f.tree(self.clone).items():
            self.assertEqual(registry.get(str(self.clone/name)), m.digest(data), name)

    def test_legacy_preimage_needs_a_separate_trusted_baseline(self):
        baseline = {n: m.digest(b) for n, b in f.tree(self.clone).items()}
        registry = json.loads(self.registry.read_bytes())
        prefix = str(self.clone)+'/'
        registry['files'] = {n: h for n, h in registry['files'].items() if not n.startswith(prefix)}
        self.registry.write_text(json.dumps(registry)+'\n')
        (self.clone/'Main.qml').write_text((self.clone/'Main.qml').read_text()+'\n// known task edit\n')
        clone_before = f.tree(self.clone)
        before = f.tree(self.home)
        approval = self.preimage()
        u = self.upgrader()
        with patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(u.manage.Refused):
                u.upgrade(self.paths(), approval)
            self.assertEqual(f.tree(self.home), before)
            approval.update(version=2, clone_baseline=baseline)
            result = u.upgrade(self.paths(), approval)
        self.assertEqual(f.tree(Path(result['backup'])/'clone'), clone_before)
        self.assertEqual(self.shell.read_bytes(), before[self.shell.relative_to(self.home).as_posix()])

    def test_installed_tools_upgrade_without_the_repository(self):
        import subprocess
        run = self.run_manage('local', 'install-tools')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        share = self.home/'.local/share/a-la-carchy'
        installed = share/'agents-clone/manage.py'
        for relative in ('agents-clone/manage.py', 'agents-clone/upgrade.py', 'local-router-stats/stage_plugin.py',
                         'local-router-stats/history_consumer.js', 'grok-usage/stage_plugin.py', 'hermes-codex-usage/collector.py'):
            self.assertEqual((share/relative).read_bytes(), (ROOT/'extras'/relative).read_bytes())
        shell = self.shell.read_bytes()
        self.new_omarchy('limits')
        run = subprocess.run([sys.executable, '-B', str(installed), '--source', str(self.source), '--shell',
                              str(self.shell), '--plugins', str(self.plugins), '--upgrade', 'local'],
                             cwd=self.root, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.systemctl_calls(), [])
        self.assertEqual((self.clone/'README.md').read_text(), '# Agents, a later release\n')
        registry = json.loads(self.registry.read_text())['files']
        self.assertEqual(registry[str(installed)], m.digest(installed.read_bytes()))

    def test_installed_accounting_cli_with_repository_access_denied(self):
        from contextlib import closing
        import shutil
        import sqlite3
        import stat
        import subprocess
        # Install from a disposable checkout, then remove it. The actual CLI
        # below also denies reads of the original repository, not just cwd or
        # PYTHONPATH fallthrough. Only stdlib and installed siblings may load.
        checkout = self.root/'installer-checkout'
        extras = checkout/'extras'
        dependencies = ('local-router-stats/hermes_accounting.py',
                        'local-router-stats/usage_history.py')
        for relative in set(m.TOOL_FILES) | set(dependencies):
            target = extras/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/'extras'/relative, target)
        command = [sys.executable, '-I', '-B', str(extras/'agents-clone/manage.py'),
                   '--source', str(self.source), '--shell', str(self.shell),
                   '--plugins', str(self.plugins), '--install-tools', 'local']
        run = subprocess.run(command, cwd=self.root, env=self.env,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        shutil.rmtree(checkout)
        self.assertFalse(checkout.exists())
        share = self.home/'.local/share/a-la-carchy'
        installed = share/'local-router-stats/hermes_accounting.py'
        output_dir = self.state/'omarchy/hermes-token-accounting'
        output_dir.mkdir(parents=True, mode=0o700)
        usage, state, history = (output_dir/name for name in ('usage.json', 'state.json', 'history.json'))
        guarded_cli = r'''
import os, runpy, sys
forbidden, removed, script, *args = sys.argv[1:]
assert sys.flags.isolated and sys.dont_write_bytecode
assert not os.path.exists(removed)
assert 'PYTHONPATH' not in os.environ
roots = tuple(os.path.realpath(p) for p in (forbidden, removed))
def deny_repository(event, values):
    if event in ('open', 'os.listdir', 'os.scandir', 'os.chdir') and values:
        path = values[0]
        if isinstance(path, (str, bytes, os.PathLike)):
            path = os.path.realpath(os.fsdecode(path))
            if any(path == root or path.startswith(root + os.sep) for root in roots):
                raise PermissionError('repository access denied by installed CLI fixture')
sys.addaudithook(deny_repository)
try:
    open(os.path.join(forbidden, 'extras/agents-clone/manage.py'), 'rb')
except PermissionError:
    pass
else:
    raise AssertionError('repository read guard was not exercised')
sys.argv = [script, *args]
runpy.run_path(script, run_name='__main__')
'''
        calls = []
        def cli(*args):
            argv = [sys.executable, '-I', '-B', '-c', guarded_cli,
                    str(ROOT), str(checkout), str(installed), *map(str, args)]
            child = subprocess.run(argv, cwd=self.root, env=self.env,
                                   capture_output=True, text=True, timeout=15)
            calls.append({'command': argv, 'exit': child.returncode,
                          'stdout': child.stdout, 'stderr': child.stderr})
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
            self.assertEqual(child.stdout + child.stderr, '', 'successful installed CLI is quiet')
        row = dict(session_id='PRIVATE_INSTALLED_SESSION', model='PRIVATE_INSTALLED_MODEL',
                   billing_provider='openai-codex', billing_base_url='https://api.openai.com/v1',
                   billing_mode='subscription_included', task='PRIVATE_INSTALLED_TASK',
                   input_tokens=100, output_tokens=20, cache_read_tokens=30, cache_write_tokens=5,
                   reasoning_tokens=7, api_call_count=1, first_seen=0, last_seen=1)
        source = self.root/'canonical.json'
        source.write_text(json.dumps({'contract': 'hermes-session-model-usage/1',
                                     'counterSemantics': 'hermes-canonical/1', 'rows': [row]}))
        before_json = source.read_bytes()
        options = ['--profile', 'default', '--output', usage, '--history-state', state,
                   '--history-output', history, '--timezone', 'UTC']
        cli('--json', source, *options, '--observed-at', '2024-03-04T10:00:00Z')
        baseline = json.loads(history.read_text())
        self.assertEqual(baseline['coverage']['status'], 'baseline-only')
        self.assertEqual(baseline['buckets'], dict(day=[], week=[], month=[], year=[]))
        self.assertEqual(json.loads(usage.read_text())['sources'][0]['windows'],
                         {'today': None, 'week': None, 'history': 155})
        later = dict(row, input_tokens=110, output_tokens=24, cache_read_tokens=33,
                     cache_write_tokens=7, reasoning_tokens=8, api_call_count=2)
        snapshot = self.root/'canonical.sqlite'
        columns = tuple(row)
        with closing(sqlite3.connect(snapshot)) as db, db:
            db.execute('CREATE TABLE session_model_usage (' + ','.join(
                name + (' INTEGER' if name.endswith('_tokens') or name == 'api_call_count'
                        else ' REAL' if name in ('first_seen', 'last_seen') else ' TEXT')
                for name in columns) + ')')
            db.execute('INSERT INTO session_model_usage VALUES (' + ','.join('?' for _ in columns) + ')',
                       tuple(later[name] for name in columns))
        snapshot.chmod(0o400)
        before_sqlite = snapshot.read_bytes()
        cli('--sqlite-snapshot', snapshot, *options, '--observed-at', '2024-03-04T10:10:00Z')
        public = json.loads(history.read_text())
        accounting = json.loads(usage.read_text())
        self.assertEqual(public['contract'], 'hermes-observed-history/1')
        self.assertEqual(public['generation'], baseline['generation'] + 1)
        self.assertEqual(public['coverage']['status'], 'partial-observed')
        self.assertTrue(public['coverage']['missingHistory'])
        expected = dict(input_tokens=10, output_tokens=4, cache_read_tokens=3, cache_write_tokens=2,
                        reasoning_tokens=1, api_call_count=1, prompt_tokens=15, total_tokens=19)
        self.assertEqual(public['coverage']['observed'], expected)
        for grain, key in (('day', '2024-03-04'), ('week', '2024-03-04'),
                           ('month', '2024-03'), ('year', '2024')):
            self.assertEqual(public['buckets'][grain], [{'key': key, 'sources': [{
                'id': public['sources'][0]['id'], 'counts': expected, 'coveredSeconds': 600, 'intervals': 1}]}])
        self.assertEqual(accounting['sources'][0]['windows'], {'today': None, 'week': None, 'history': 174})
        self.assertEqual(source.read_bytes(), before_json)
        self.assertEqual(snapshot.read_bytes(), before_sqlite)
        files = {p.name: p.read_bytes() for p in (usage, state, history)}
        cli('--recover-history', '--output', usage, '--history-state', state, '--history-output', history)
        self.assertEqual({p.name: p.read_bytes() for p in (usage, state, history)}, files)
        registry = json.loads(self.registry.read_text())['files']
        for relative in dependencies:
            path = share/relative
            self.assertEqual(path.read_bytes(), (ROOT/'extras'/relative).read_bytes())
            self.assertEqual(registry[str(path)], m.digest(path.read_bytes()))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        for path in (usage, state, history):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        published = usage.read_text() + history.read_text()
        for private in ('PRIVATE_INSTALLED_SESSION', 'PRIVATE_INSTALLED_MODEL',
                        'PRIVATE_INSTALLED_TASK', 'api.openai.com'):
            self.assertNotIn(private, published)
        self.assertEqual(self.systemctl_calls(), [])
        print('INSTALLED_ACCOUNTING_PROOF=' + json.dumps({
            'repository_checkout_removed': str(checkout), 'repository_read_denied': str(ROOT),
            'calls': calls, 'baseline_history': baseline, 'accounting': accounting, 'history': public,
            'installed_sha256': {relative: registry[str(share/relative)] for relative in dependencies},
            'output_sha256': {name: m.digest(data) for name, data in files.items()},
            'input_unchanged': {'json': m.digest(before_json), 'sqlite': m.digest(before_sqlite)},
            'service_calls': self.systemctl_calls()}, sort_keys=True))

    def test_accounting_helpers_update_registered_versions_and_keep_backups(self):
        dependencies = ('hermes_accounting.py', 'usage_history.py')
        self.ok('local', 'install-tools')
        folder = self.home/'.local/share/a-la-carchy/local-router-stats'
        registry = json.loads(self.registry.read_text())
        older = {}
        for name in dependencies:
            path = folder/name
            older[name] = ('# older registered ' + name + '\n').encode()
            path.write_bytes(older[name])
            path.chmod(0o600)
            registry['files'][str(path)] = m.digest(older[name])
        self.registry.write_text(json.dumps(registry) + '\n')
        shell, clone = self.shell.read_bytes(), f.tree(self.clone)
        self.ok('local', 'install-tools')
        registered = json.loads(self.registry.read_text())['files']
        for name in dependencies:
            path = folder/name
            expected = (ROOT/'extras/local-router-stats'/name).read_bytes()
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(registered[str(path)], m.digest(expected))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, 'existing helper modes are preserved')
        backups = self.registry.parent/'agents-upgrade-backups'
        self.assertTrue(any(all(data in [p.read_bytes() for p in (backup/'files').glob('*')]
                                    for data in older.values()) for backup in backups.glob('tools-*')))
        before = f.tree(self.home)
        self.ok('local', 'install-tools')
        self.assertEqual(f.tree(self.home), before, 'identical bundle is an exact no-op')
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(f.tree(self.clone), clone)
        self.assertEqual(self.systemctl_calls(), [])

    def test_accounting_helper_manual_edits_refuse_the_whole_bundle(self):
        folder = self.home/'.local/share/a-la-carchy/local-router-stats'
        for registered in (False, True):
            if registered:
                self.ok('local', 'install-tools')
            for name in ('hermes_accounting.py', 'usage_history.py'):
                with self.subTest(name=name, registered=registered):
                    path = folder/name
                    original = path.read_bytes() if path.exists() else None
                    path.write_bytes(b'# unrecognized manual accounting edit\n')
                    before = f.tree(self.home)
                    run = self.run_manage('local', 'install-tools')
                    self.assertNotEqual(run.returncode, 0, run.stdout + run.stderr)
                    self.assertIn('modified or unregistered installed tool/unit', run.stdout)
                    self.assertNotIn('Traceback', run.stderr)
                    self.assertEqual(f.tree(self.home), before, 'refusal must precede any bundle write')
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)
        self.assertEqual(self.systemctl_calls(), [])

    def test_accounting_dependency_install_registry_failure_restores_preimages(self):
        u = self.upgrader()
        folder = self.home/'.local/share/a-la-carchy/local-router-stats'
        adapter, history = folder/'hermes_accounting.py', folder/'usage_history.py'
        adapter.write_bytes(b'# older registered accounting helper\n')
        adapter.chmod(0o600)
        registry = json.loads(self.registry.read_text())
        registry['files'][str(adapter)] = m.digest(adapter.read_bytes())
        self.registry.write_text(json.dumps(registry) + '\n')
        self.assertFalse(history.exists())
        before = self.home_outside_backups()
        atomic, written = u.manage.atomic_write, []
        def fail_registration(path, data, mode):
            if path == self.registry:
                raise OSError('fixture accounting dependency registration failure')
            written.append(path)
            return atomic(path, data, mode)
        with patch.dict(os.environ, self.env, clear=True), \
                patch.object(u.manage, 'atomic_write', side_effect=fail_registration):
            paths = self.paths()
            replacements = [(paths.tools/name, m.checkout_bytes(name)) for name in m.TOOL_FILES]
            with self.assertRaisesRegex(u.manage.Refused, 'tools refused/rolled back;'):
                u.install_files(paths, replacements)
        self.assertIn(adapter, written, 'fixture must replace the registered adapter before failing')
        self.assertIn(history, written, 'fixture must install the missing sibling before failing')
        self.assertEqual(self.home_outside_backups(), before)
        self.assertEqual(adapter.stat().st_mode & 0o777, 0o600)
        self.assertFalse(history.exists())
        self.assertEqual(self.systemctl_calls(), [])
        self.assertTrue(any(self.registry.parent.glob('agents-upgrade-backups/tools-*/receipt.json')))

    def test_installed_bridge_unit_supplies_explicit_scanner_and_stays_inactive(self):
        import shlex
        import subprocess
        spec = importlib.util.spec_from_file_location('bridge_unit_fixture', ROOT/'tests/hermes-codex-usage.py')
        assert spec is not None and spec.loader is not None
        fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
        hermes_home = Path(self.env['HERMES_HOME'])
        hermes_home.mkdir(parents=True, exist_ok=True)
        auth = hermes_home/'auth.json'
        auth.write_text(json.dumps({'credential_pool': {'openai-codex': [fixture.entry(subject='auth0|expired1', expires=1),
                                                                       fixture.entry(subject='auth0|expired2', expires=1)]}}))
        auth.chmod(0o600)
        original = auth.read_bytes()
        run = self.run_manage('local', 'install-bridge')
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        unit = self.home/'.config/systemd/user/hermes-codex-usage.service'
        command = shlex.split(next(line.split('=', 1)[1] for line in unit.read_text().splitlines() if line.startswith('ExecStart=')))
        scanner = self.state/'omarchy/agents/usage'
        self.assertEqual(command[command.index('--usage-dir')+1], str(scanner))
        self.assertEqual(command[command.index('--hermes-home')+1], str(hermes_home))
        self.assertIn('/a-la-carchy/hermes-codex-usage/collector.py', command[2])
        run = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = json.loads(auth.read_text())['credential_pool']['openai-codex']
        bridge_spec = importlib.util.spec_from_file_location('installed_bridge_contract', ROOT/'extras/hermes-codex-usage/collector.py')
        assert bridge_spec is not None and bridge_spec.loader is not None
        bridge = importlib.util.module_from_spec(bridge_spec); bridge_spec.loader.exec_module(bridge)
        expected = {'codex-hermes-' + bridge.identity(row['access_token'])['key'] + '.json' for row in rows}
        self.assertEqual({p.name for p in scanner.glob('*.json')}, expected)
        inventory = json.loads((scanner/bridge.PUBLIC_INVENTORY).read_text())
        self.assertEqual({e['id'] + '.json' for e in inventory['entries']}, expected)
        self.assertEqual(auth.read_bytes(), original)
        self.assertEqual(self.systemctl_calls(), [])
        self.assertFalse((unit.parent/'timers.target.wants').exists())
        self.assertTrue(unit.with_suffix('.timer').is_file())

    def test_install_tools_refuses_unknown_edits_before_writing(self):
        share = self.home/'.local/share/a-la-carchy'
        edited = share/'agents-clone/upgrade.py'
        edited.parent.mkdir(parents=True)
        edited.write_text('# unknown user program\n')
        before = f.tree(self.home)
        run = self.run_manage('local', 'install-tools')
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(f.tree(self.home), before)

    def test_tool_bootstrap_rollback_on_registry_failure(self):
        u = self.upgrader()
        target = self.home/'.local/share/a-la-carchy/agents-clone/new-tool.py'
        before_registry = self.registry.read_bytes()
        atomic = u.manage.atomic_write
        def fail(path, data, mode):
            if path == self.registry: raise OSError('fixture registry failure')
            return atomic(path, data, mode)
        with patch.dict(os.environ, self.env, clear=True), patch.object(u.manage, 'atomic_write', side_effect=fail):
            with self.assertRaises(u.manage.Refused): u.install_files(self.paths(), [(target, b'new tool\n')])
        self.assertFalse(target.exists())
        self.assertEqual(self.registry.read_bytes(), before_registry)
        self.assertTrue(any(self.registry.parent.glob('agents-upgrade-backups/tools-*/receipt.json')))

    def test_tool_bootstrap_concurrent_edit_preserved(self):
        u = self.upgrader()
        target = self.home/'.local/share/a-la-carchy/agents-clone/new-tool.py'
        atomic = u.manage.atomic_write
        def change(path, data, mode):
            result = atomic(path, data, mode)
            if path == target: target.write_bytes(b'concurrent user edit\n')
            return result
        with patch.dict(os.environ, self.env, clear=True), patch.object(u.manage, 'atomic_write', side_effect=change):
            with self.assertRaises(u.manage.Refused) as error:
                u.install_files(self.paths(), [(target, b'new tool\n')])
        self.assertIn('rollback incomplete', str(error.exception))
        self.assertEqual(target.read_bytes(), b'concurrent user edit\n')

    def test_bridge_unit_escapes_systemd_expansion_and_rejects_controls(self):
        paths = self.paths()
        paths.bridge_home = self.root/'profile %path $value'
        data = m.bridge_units(paths)[0][1]
        self.assertIn(b'%%path $$value', data)
        paths.bridge_home = self.root/'bad\npath'
        with self.assertRaises(m.Refused): m.bridge_units(paths)

    def test_explicit_upgrade_rebuilds_enabled_clone_without_service_or_layout_changes(self):
        shell = self.shell.read_bytes()
        self.new_omarchy('limits')
        run = self.run_manage('local', 'upgrade')
        self.assertEqual(run.returncode, 0, run.stdout+run.stderr)
        self.assertEqual((self.clone/'README.md').read_text(), '# Agents, a later release\n')
        self.assertEqual(self.shell.read_bytes(), shell)
        self.assertEqual(self.systemctl_calls(), [])
        self.assertIsNotNone(m.clone_info(self.clone))
        files = json.loads(self.registry.read_text())['files']
        for name, data in f.tree(self.clone).items():
            self.assertEqual(files[str(self.clone/name)], m.digest(data))
        clone = f.tree(self.clone)
        self.ok('local', 'upgrade')
        self.assertEqual(f.tree(self.clone), clone)

    def test_known_manual_preimage_is_preserved_then_upgraded(self):
        (self.clone/'Main.qml').write_text((self.clone/'Main.qml').read_text()+'\n// authorized old task patch\n')
        self.local_collector.write_bytes(b'# authorized old task collector\n')
        before = f.tree(self.clone)
        preimage = self.preimage()
        u = self.upgrader()
        with patch.dict(os.environ, self.env, clear=True):
            result = u.upgrade(self.paths(), preimage)
        self.assertIsNotNone(m.clone_info(self.clone))
        backup = Path(result['backup'])
        self.assertEqual(f.tree(backup/'clone'), before)
        self.assertEqual((backup/'files/0').read_bytes(), b'# authorized old task collector\n')
        self.assertEqual(self.local_collector.read_bytes(), (ROOT/'extras/local-router-stats/collector.py').read_bytes())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)

    def test_unrecognized_edit_and_stale_preimage_are_preserved(self):
        u = self.upgrader()
        preimage = self.preimage()
        (self.clone/'Main.qml').write_text('// later user edit\n')
        before = f.tree(self.home)
        with patch.dict(os.environ, self.env, clear=True):
            for expected in (None, preimage):
                with self.assertRaises(u.manage.Refused): u.upgrade(self.paths(), expected)
        self.assertEqual(f.tree(self.home), before)

    def test_manual_preimage_cannot_claim_other_native_files_or_unregistered_collectors(self):
        u = self.upgrader()
        (self.clone/'Agent.qml').write_text('// foreign native edit\n')
        before = f.tree(self.home)
        with patch.dict(os.environ, self.env, clear=True), self.assertRaises(u.manage.Refused):
            u.upgrade(self.paths(), self.preimage())
        self.assertEqual(f.tree(self.home), before)

    def test_registry_failure_rolls_back_clone_collectors_and_preserves_backup(self):
        u = self.upgrader()
        self.new_omarchy('limits')
        before = f.tree(self.clone)
        registry = self.registry.read_bytes()
        shell = self.shell.read_bytes()
        atomic = u.manage.atomic_write
        def fail(path, data, mode):
            if path == self.registry: raise OSError('fixture failure')
            return atomic(path, data, mode)
        with patch.dict(os.environ, self.env, clear=True), patch.object(u.manage, 'atomic_write', side_effect=fail):
            with self.assertRaises(u.manage.Refused): u.upgrade(self.paths())
        self.assertEqual(f.tree(self.clone), before)
        self.assertEqual(self.registry.read_bytes(), registry)
        self.assertEqual(self.shell.read_bytes(), shell)

    def test_concurrent_registry_edit_is_not_overwritten(self):
        u = self.upgrader()
        self.new_omarchy('limits')
        old = f.tree(self.clone)
        write = u.manage.atomic_write
        edited = b'{"version":1,"files":{},"sentinel":"concurrent"}\n'
        def change(path, data, mode):
            out = write(path, data, mode)
            if path == self.local_collector: self.registry.write_bytes(edited)
            return out
        with patch.dict(os.environ, self.env, clear=True), patch.object(u.manage, 'atomic_write', side_effect=change):
            with self.assertRaises(u.manage.Refused): u.upgrade(self.paths())
        self.assertEqual(self.registry.read_bytes(), edited)
        self.assertEqual(f.tree(self.clone), old)

    def test_symlink_and_fifo_refused_before_writes(self):
        u = self.upgrader()
        collector = self.local_collector.read_bytes()
        self.local_collector.unlink()
        sentinel = self.root/'outside'; sentinel.write_bytes(collector)
        for kind in ('symlink', 'fifo'):
            if kind == 'symlink': self.local_collector.symlink_to(sentinel)
            else: os.mkfifo(self.local_collector)
            with patch.dict(os.environ, self.env, clear=True), self.assertRaises(u.manage.Refused):
                u.upgrade(self.paths())
            self.assertEqual(sentinel.read_bytes(), collector)
            self.local_collector.unlink()
        self.assertEqual(self.systemctl_calls(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
