#!/usr/bin/env python3
"""Offline adapter/managed consumer contract: synthetic inputs only."""
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(relative):
    spec = importlib.util.spec_from_file_location('fixture_' + Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(record):
    stager = load('extras/local-router-stats/stage_plugin.py')
    functions = stager.RECORDED_TOKEN_FUNCTIONS.split('  // One reactive pass')[0]
    code = functions + '\nconsole.log(JSON.stringify(recordedTokenTotalsFor([], null, ' + json.dumps(record) + ')));'
    child = subprocess.run(['node', '-e', 'const formatTokenCount = n => String(n);\n' + code],
                           capture_output=True, text=True, timeout=10)
    if child.returncode:
        raise AssertionError(child.stderr)
    return json.loads(child.stdout)


ADAPTER = 'extras/local-router-stats/hermes_accounting.py'


def usage_row(**changes):
    row = dict(session_id='private-session', model='model-x', billing_provider='openai-codex',
               billing_base_url='https://api.openai.com/v1', billing_mode='subscription_included',
               task='', api_call_count=1, input_tokens=100, output_tokens=20,
               cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=7,
               first_seen=1700000000.0, last_seen=1700200000.0)
    row.update(changes)
    return row


def export(rows):
    return {'contract': 'hermes-session-model-usage/1', 'counterSemantics': 'hermes-canonical/1',
            'rows': rows}


class ProducerTests(unittest.TestCase):
    def test_validation_projection_and_privacy(self):
        adapter = load(ADAPTER)
        record = adapter.project([usage_row(), usage_row(session_id='other', model='other')], 'default')
        self.assertEqual(record['sources'][0]['windows']['history'], 310)
        self.assertEqual(record['sources'][0]['id'], adapter.project([usage_row()], 'default')['sources'][0]['id'])
        self.assertNotEqual(record['sources'][0]['id'], adapter.project([usage_row()], 'work')['sources'][0]['id'])
        self.assertEqual(set(record['sources'][0]), {'id', 'provider', 'profile', 'attribution', 'windows'})
        for bad in [usage_row(input_tokens=True), usage_row(output_tokens=-1),
                    usage_row(input_tokens=9007199254740991), usage_row(cache_read_tokens='30'),
                    usage_row(reasoning_tokens=21), usage_row(first_seen='secret'),
                    usage_row(first_seen=1700300000), usage_row(task=None)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                adapter.project([bad], 'default')
        with self.assertRaises(ValueError):
            adapter.project([usage_row(), usage_row()], 'default')
        with self.assertRaises(ValueError):
            adapter.project([usage_row(input_tokens=9007199254740800),
                             usage_row(session_id='other', input_tokens=1000)], 'default')
        with self.assertRaises(ValueError):
            adapter.project([usage_row()], 'https://secret')
        for route in ['http://localhost:8000/v1', 'https://127.0.0.1/v1',
                      'https://[::1]/v1', 'https://[::ffff:127.0.0.1]/v1',
                      'https://10.0.0.1/v1', 'https://router.local/v1',
                      'https://localhost./v1', 'https://2130706433/v1',
                      'https://api.openai.com@localhost/v1', 'https://user:secret@api.openai.com/v1',
                      'https://api.openai.com/v1?key=secret', 'not-a-url']:
            with self.subTest(route=route):
                self.assertEqual(adapter.project([usage_row(billing_base_url=route)], 'default')['sources'], [])
        self.assertEqual(adapter.project([usage_row(billing_provider='local')], 'default')['sources'], [])
        zero = usage_row(input_tokens=0, output_tokens=0, cache_read_tokens=0,
                         cache_write_tokens=0, reasoning_tokens=0, first_seen=None, last_seen=None)
        self.assertEqual(adapter.project([zero], 'default')['sources'][0]['windows'],
                         {'today': None, 'week': None, 'history': 0})
        self.assertIsNone(adapter.project([usage_row(first_seen=None, last_seen=None)], 'default')['sources'][0]['windows']['today'])

    def test_snapshot_and_bounded_publication(self):
        import sqlite3
        import stat
        adapter = load(ADAPTER)
        with tempfile.TemporaryDirectory(prefix='hermes-snapshot-', dir=os.environ['TMPDIR']) as folder:
            folder = Path(folder)
            snapshot, output = folder / 'offline.sqlite', folder / 'usage.json'
            columns = adapter.COLUMNS
            with sqlite3.connect(snapshot) as db:
                db.execute('CREATE TABLE session_model_usage (' + ','.join(
                    f'{c} ' + ('INTEGER' if c in adapter.COUNTERS else 'REAL' if c in ('first_seen', 'last_seen') else 'TEXT')
                    for c in columns) + ')')
                for row in [usage_row(), usage_row(session_id='local-only', billing_base_url='http://localhost:8000/v1'),
                            usage_row(session_id='other', model='other', first_seen=None, last_seen=None)]:
                    db.execute('INSERT INTO session_model_usage VALUES (' + ','.join('?' for _ in columns) + ')',
                               tuple(row[c] for c in columns))
            with self.assertRaises(ValueError):
                adapter.read_snapshot(snapshot)
            snapshot.chmod(0o400)
            original = snapshot.read_bytes()
            rows = adapter.read_snapshot(snapshot)
            self.assertEqual(adapter.project(rows, 'default')['sources'][0]['windows']['history'], 310)
            self.assertEqual(snapshot.read_bytes(), original)
            env = dict(os.environ, HOME=str(folder), XDG_STATE_HOME=str(folder), XDG_CONFIG_HOME=str(folder),
                       XDG_CACHE_HOME=str(folder), PYTHONDONTWRITEBYTECODE='1')
            for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS']:
                env.pop(key, None)
            command = ['python3', str(ROOT / ADAPTER), '--sqlite-snapshot', str(snapshot),
                       '--profile', 'default', '--output', str(output)]
            child = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(summary(json.loads(output.read_text()))['total']['history'], 310)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            first = output.read_bytes()
            self.assertEqual(subprocess.run(command, env=env, capture_output=True, timeout=10).returncode, 0)
            self.assertEqual(output.read_bytes(), first)
            sidecar = Path(str(snapshot) + '-wal')
            sidecar.write_bytes(b'')
            with self.assertRaises(ValueError):
                adapter.read_snapshot(snapshot)
            sidecar.unlink()
            link = folder / 'link.sqlite'
            link.symlink_to(snapshot)
            with self.assertRaises((ValueError, OSError)):
                adapter.read_snapshot(link)
            bad = folder / 'bad.json'
            for data in [export([usage_row(), usage_row()]), {'rows': []},
                         dict(export([]), counterSemantics='raw-provider-totals')]:
                bad.write_text(json.dumps(data))
                child = subprocess.run(['python3', str(ROOT / ADAPTER), '--json', str(bad),
                                        '--profile', 'default', '--output', str(output)],
                                       env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, 2)
                self.assertNotIn('private-session', child.stderr)
                self.assertEqual(output.read_bytes(), first)
            bad.write_text('{"contract":"a","contract":"b"}')
            with self.assertRaises(ValueError):
                adapter.read_json(bad)
            bad.write_bytes(b' ' * (adapter.MAX_INPUT + 1))
            with self.assertRaises(ValueError):
                adapter.read_json(bad)
            target = folder / 'target'
            target.write_text('untouched')
            alias = folder / 'alias'
            alias.symlink_to(target)
            with self.assertRaises(ValueError):
                adapter.publish(adapter.project([], 'default'), alias)
            self.assertEqual(target.read_text(), 'untouched')
            with self.assertRaises(ValueError):
                adapter.publish({'oversized': 'x' * adapter.MAX_OUTPUT}, output)
            self.assertEqual(output.read_bytes(), first)
            self.assertFalse(list(folder.glob('.hermes-accounting-*')))
        for route in ['https://127.0.0.1.nip.io/v1', 'https://corp.company.com/v1']:
            self.assertEqual(adapter.project([usage_row(billing_base_url=route)], 'default')['sources'], [])

    def test_actual_cli_json_to_consumer(self):
        self.assertTrue((ROOT / ADAPTER).is_file(), 'owned producer module is missing')
        with tempfile.TemporaryDirectory(prefix='hermes-producer-', dir=os.environ['TMPDIR']) as folder:
            folder = Path(folder)
            source, output = folder / 'sanitized.json', folder / 'usage.json'
            source.write_text(json.dumps(export([usage_row()])))
            env = dict(os.environ, HOME=str(folder), XDG_STATE_HOME=str(folder),
                       XDG_CONFIG_HOME=str(folder), XDG_CACHE_HOME=str(folder), PYTHONDONTWRITEBYTECODE='1')
            for key in ['DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS']:
                env.pop(key, None)
            child = subprocess.run(['python3', str(ROOT / ADAPTER), '--json', str(source),
                                    '--profile', 'default', '--output', str(output)],
                                   capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr)
            record = json.loads(output.read_text())
            self.assertEqual(record['sources'][0]['windows'], {'today': None, 'week': None, 'history': 155})
            self.assertEqual(summary(record)['total']['history'], 155)
            self.assertNotIn('private-session', output.read_text())
            self.assertNotIn('api.openai.com', output.read_text())


class EndpointProvenanceTests(unittest.TestCase):
    """Explicit supported hosts, not mere public routability, establish Cloud provenance."""
    def test_global_ip_literals_cannot_supply_supported_cloud_provenance(self):
        adapter = load(ADAPTER)
        routes = ('https://8.8.8.8/v1', 'https://[2606:4700:4700::1111]/v1',
                  'https://[::ffff:8.8.8.8]/v1')
        for provider in sorted(adapter.CLOUD_PROVIDERS):
            for route in routes:
                with self.subTest(provider=provider, route=route):
                    values = usage_row(billing_provider=provider, billing_base_url=route)
                    self.assertIsNone(adapter.admitted_provider(provider, route))
                    self.assertEqual(adapter.project([values], 'default')['sources'], [])
                    self.assertEqual(adapter.canonical_snapshot([values], 'default')['sources'], [])

    def test_explicit_supported_hosts_default_routes_and_alias_stay_admitted(self):
        adapter = load(ADAPTER)
        providers = dict(adapter.CLOUD_HOSTS)
        providers['xai-oauth'] = adapter.CLOUD_HOSTS['xai']
        for provider, hosts in sorted(providers.items()):
            for route in ('', *(f'https://{host}/v1' for host in hosts)):
                with self.subTest(provider=provider, route=route):
                    values = usage_row(billing_provider=provider, billing_base_url=route)
                    expected = adapter.PROVIDER_ALIASES.get(provider, provider)
                    self.assertEqual(adapter.admitted_provider(provider, route), expected)
                    record = adapter.project([values], 'default')
                    self.assertEqual([(s['provider'], s['windows']['history']) for s in record['sources']], [(expected, 155)])
                    snapshot = adapter.canonical_snapshot([values], 'default')
                    self.assertEqual(len(snapshot['sources']), 1)
                    self.assertEqual(snapshot['sources'][0]['counts']['total_tokens'], 155)


CUMULATIVE_WORKER = r'''
from pathlib import Path
import os, resource, runpy, signal, sys
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
source, mode, input_path, output, boundary, marker = sys.argv[1:]
real_open, real_fsync, real_replace = os.open, os.fsync, os.replace
temporaries = set()
def opened(path, flags, *args, **kwargs):
    fd = real_open(path, flags, *args, **kwargs)
    name = Path(os.fsdecode(path)).name
    if flags & os.O_CREAT and name.startswith('.hermes-accounting-') and not name.endswith('.lock'):
        temporaries.add(fd)
    return fd
def reached():
    with open(marker, 'w') as stream:
        stream.write(boundary); stream.flush(); real_fsync(stream.fileno())
    if boundary == 'hold':
        print('PUBLICATION_HELD', flush=True)
        if sys.stdin.buffer.read(1) != b'g':
            raise RuntimeError('owned worker was not released')
    else:
        os.kill(os.getpid(), signal.SIGKILL)
def synced(fd):
    real_fsync(fd)
    if fd in temporaries and boundary in ('file_fsync', 'hold'):
        reached()
def replaced(src, dst, *args, **kwargs):
    if boundary == 'before_replace' and Path(os.fsdecode(src)).name.startswith('.hermes-accounting-') and Path(os.fsdecode(dst)).name == Path(output).name:
        reached()
    return real_replace(src, dst, *args, **kwargs)
os.open, os.fsync, os.replace = opened, synced, replaced
sys.argv = [source, mode, input_path, '--profile', 'default', '--output', output]
runpy.run_path(source, run_name='__main__')
'''


class CumulativePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='accounting-crash-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.adapter = load(ADAPTER)
        self.json_input = self.root/'sanitized.json'
        self.json_input.write_text(json.dumps(export([usage_row()])))
        self.snapshot = self.root/'offline.sqlite'
        columns = self.adapter.COLUMNS
        db = sqlite3.connect(self.snapshot)
        try:
            db.execute('CREATE TABLE session_model_usage (' + ','.join(
                f'{c} ' + ('INTEGER' if c in self.adapter.COUNTERS else
                          'REAL' if c in ('first_seen', 'last_seen') else 'TEXT') for c in columns) + ')')
            row = usage_row()
            db.execute('INSERT INTO session_model_usage VALUES (' + ','.join('?' for _ in columns) + ')',
                       tuple(row[c] for c in columns))
            db.commit()
        finally:
            db.close()
        self.snapshot.chmod(0o400)

    def input_for(self, mode):
        return self.json_input if mode == '--json' else self.snapshot

    def command(self, mode, output, source=None):
        return [sys.executable, '-I', '-B', str(ROOT/ADAPTER), mode,
                str(source or self.input_for(mode)), '--profile', 'default', '--output', str(output)]

    def healthy(self, mode, output):
        child = subprocess.run(self.command(mode, output), capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(output.read_text())['sources'][0]['windows']['history'], 155)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def crash_recovery(self, mode, boundary):
        output = self.root/'usage.json'
        self.healthy(mode, output)  # Identical valid input reaches the actual CLI publication.
        before = (output.read_bytes(), output.stat().st_ino)
        input_before = self.input_for(mode).read_bytes()
        for attempt in range(3):
            marker = self.root/('reached-' + str(attempt))
            child = subprocess.run([sys.executable, '-I', '-B', '-c', CUMULATIVE_WORKER,
                                    str(ROOT/ADAPTER), mode, str(self.input_for(mode)), str(output),
                                    boundary, str(marker)], capture_output=True, timeout=10)
            self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr)
            self.assertEqual(marker.read_text(), boundary, 'the named real publication boundary must be reached')
            self.assertEqual((output.read_bytes(), output.stat().st_ino), before)
            self.assertLessEqual(len(list(self.root.glob('.hermes-accounting-*'))), 1,
                                 'repeated process death must leave at most one per-target publication temporary')
            self.assertEqual(self.input_for(mode).read_bytes(), input_before)
        self.healthy(mode, output)
        self.assertFalse(list(self.root.glob('.hermes-accounting-*')), 'healthy retry must reclaim its crash leftover')
        self.assertEqual(self.input_for(mode).read_bytes(), input_before)

    def test_json_repeated_death_after_file_fsync_is_bounded_and_recovers(self):
        self.crash_recovery('--json', 'file_fsync')

    def test_json_repeated_death_before_replace_is_bounded_and_recovers(self):
        self.crash_recovery('--json', 'before_replace')

    def test_snapshot_repeated_death_after_file_fsync_is_bounded_and_recovers(self):
        self.crash_recovery('--sqlite-snapshot', 'file_fsync')

    def test_snapshot_repeated_death_before_replace_is_bounded_and_recovers(self):
        self.crash_recovery('--sqlite-snapshot', 'before_replace')

    def test_competing_writer_refuses_without_reclaiming_an_active_temporary(self):
        output = self.root/'usage.json'
        self.healthy('--json', output)
        before = (output.read_bytes(), output.stat().st_ino)
        marker = self.root/'held-boundary'
        worker = subprocess.Popen([sys.executable, '-I', '-B', '-c', CUMULATIVE_WORKER,
                                   str(ROOT/ADAPTER), '--json', str(self.json_input), str(output),
                                   'hold', str(marker)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([worker.stdout], [], [], 10)[0], 'owned publisher must reach the hold boundary')
            self.assertEqual(worker.stdout.readline(), b'PUBLICATION_HELD\n')
            self.assertEqual(marker.read_text(), 'hold')
            other = self.root/'other.json'
            other.write_text(json.dumps(export([usage_row(input_tokens=500)])))
            second = subprocess.run(self.command('--json', output, other), capture_output=True, timeout=10)
            self.assertEqual(second.returncode, 2, 'a competing cumulative writer must refuse while the target is owned')
            self.assertEqual((output.read_bytes(), output.stat().st_ino), before)
        finally:
            try:
                out, error = worker.communicate(input=b'g', timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                out, error = worker.communicate(timeout=10)
                self.fail('owned held publisher did not finish after release')
        self.assertEqual(worker.returncode, 0, error)
        self.assertFalse(list(self.root.glob('.hermes-accounting-*')))

    def test_lock_admission_rechecks_metadata_after_flock(self):
        from unittest.mock import patch
        output = self.root/'usage.json'
        self.healthy('--json', output)
        before = (output.read_bytes(), output.stat().st_ino)
        real_flock = self.adapter.fcntl.flock
        entered = []
        def changed_after_acquisition(fd, operation):
            real_flock(fd, operation)
            entered.append(fd)
            os.fchmod(fd, 0o644)  # Real fixture-only metadata change at the admission boundary.
        with patch.object(self.adapter.fcntl, 'flock', side_effect=changed_after_acquisition):
            with self.assertRaises(ValueError):
                self.adapter.publish(self.adapter.project([usage_row()], 'default'), output)
        self.assertEqual(len(entered), 1, 'the real target-lock acquisition must be exercised')
        self.assertEqual((output.read_bytes(), output.stat().st_ino), before)
        self.assertFalse(list(self.root.glob('.hermes-accounting-*')))

    def test_foreign_publication_lock_obstacles_are_preserved(self):
        for kind in ('symlink', 'hardlink', 'directory', 'fifo', 'shared-mode', 'nonempty'):
            with self.subTest(kind=kind):
                directory = self.root/('lock-' + kind)
                directory.mkdir(mode=0o700)
                output = directory/'usage.json'
                self.healthy('--json', output)
                before = (output.read_bytes(), output.stat().st_ino)
                lock = self.adapter.publication_lock_path(output)
                lock.unlink()  # The exact fixture lock; its healthy CLI owner was reaped.
                victim = directory/'unrelated'
                victim.write_bytes(b'unrelated lock fixture')
                if kind == 'symlink':
                    lock.symlink_to(victim)
                elif kind == 'hardlink':
                    os.link(victim, lock)
                elif kind == 'directory':
                    lock.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(lock, 0o600)
                else:
                    lock.write_bytes(b'not an empty lock' if kind == 'nonempty' else b'')
                    lock.chmod(0o644 if kind == 'shared-mode' else 0o600)
                obstacle = lock.lstat()
                victim_before = (victim.read_bytes(), victim.stat().st_ino, victim.stat().st_nlink)
                child = subprocess.run(self.command('--json', output), capture_output=True, timeout=10)
                self.assertEqual(child.returncode, 2)
                self.assertEqual((output.read_bytes(), output.stat().st_ino), before)
                self.assertEqual((lock.lstat().st_ino, lock.lstat().st_mode),
                                 (obstacle.st_ino, obstacle.st_mode))
                self.assertEqual((victim.read_bytes(), victim.stat().st_ino, victim.stat().st_nlink), victim_before)

    def test_reserved_temporary_input_alias_is_refused_before_effects(self):
        output = self.root/'usage.json'
        reserved = self.adapter.temporary_path(output)
        reserved.write_bytes(self.json_input.read_bytes())
        before = (reserved.read_bytes(), reserved.stat().st_ino, sorted(p.name for p in self.root.iterdir()))
        child = subprocess.run(self.command('--json', output, reserved), capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 2, 'the deterministic publication namespace must not adopt the input')
        self.assertEqual((reserved.read_bytes(), reserved.stat().st_ino,
                          sorted(p.name for p in self.root.iterdir())), before)
        self.assertFalse(output.exists())

    def test_foreign_temporary_obstacles_are_preserved_with_last_good_output(self):
        for kind in ('symlink', 'hardlink', 'directory', 'fifo', 'shared-mode'):
            with self.subTest(kind=kind):
                directory = self.root/kind
                directory.mkdir(mode=0o700)
                output = directory/'usage.json'
                self.healthy('--json', output)
                before = (output.read_bytes(), output.stat().st_ino)
                victim = directory/'unrelated'
                victim.write_bytes(b'unrelated fixture data')
                temporary = self.adapter.temporary_path(output)
                if kind == 'symlink':
                    temporary.symlink_to(victim)
                elif kind == 'hardlink':
                    os.link(victim, temporary)
                elif kind == 'directory':
                    temporary.mkdir()
                elif kind == 'fifo':
                    os.mkfifo(temporary, 0o600)
                else:
                    temporary.write_bytes(b'unowned shared-mode obstacle')
                    temporary.chmod(0o644)
                obstacle = temporary.lstat()
                victim_before = (victim.read_bytes(), victim.stat().st_ino, victim.stat().st_nlink)
                child = subprocess.run(self.command('--json', output), capture_output=True, timeout=10)
                self.assertEqual(child.returncode, 2, 'a foreign reserved obstacle must refuse publication')
                self.assertEqual((output.read_bytes(), output.stat().st_ino), before)
                self.assertEqual((temporary.lstat().st_ino, temporary.lstat().st_mode),
                                 (obstacle.st_ino, obstacle.st_mode))
                self.assertEqual((victim.read_bytes(), victim.stat().st_ino, victim.stat().st_nlink), victim_before)


class ConsumerTests(unittest.TestCase):
    def test_rejects_entire_malformed_or_local_ledger(self):
        import copy
        source = {'id': 'a' * 64, 'provider': 'openai-codex', 'profile': 'default',
                  'attribution': 'unattributed', 'windows': {'today': None, 'week': None, 'history': 42}}
        record = {'id': 'hermes-token-accounting', 'contract': 'hermes-token-accounting/1',
                  'schemaVersion': 1, 'readOnly': True, 'sources': [source]}
        for changes in [{'id': 'bad'}, {'provider': 'local'}, {'provider': 'localhost'},
                        {'profile': 'https://secret'}, {'attribution': 'credential'},
                        {'windows': {'today': None, 'week': None, 'history': -1}},
                        {'windows': {'today': None, 'week': None, 'history': 9007199254740992}},
                        {'windows': {'today': None, 'week': None, 'history': '42'}},
                        {'windows': []}, {'windows': {'history': 42}}]:
            candidate = copy.deepcopy(record)
            changed = dict(source, id='b' * 64)
            changed.update(changes)
            candidate['sources'].append(changed)
            with self.subTest(changes=changes):
                self.assertIsNone(summary(candidate)['total']['history'])
        duplicate = copy.deepcopy(record)
        duplicate['sources'].append(source)
        self.assertIsNone(summary(duplicate)['total']['history'])
        record['sources'][0]['windows'] = {'today': 0, 'week': None, 'history': 0}
        self.assertEqual(summary(record)['total']['today'], 0)
        self.assertIsNone(summary(record)['total']['week'])

    def test_owned_hermes_counts_reach_summary_without_quota_or_local_overlap(self):
        record = {'id': 'hermes-token-accounting', 'contract': 'hermes-token-accounting/1',
                  'schemaVersion': 1, 'readOnly': True, 'sources': [
                      {'id': 'a' * 64, 'provider': 'openai-codex', 'profile': 'default',
                       'attribution': 'unattributed', 'windows': {'today': None, 'week': None, 'history': 42}}]}
        result = summary(record)
        self.assertEqual(result['cloud']['history']['value'], 42)
        self.assertIsNone(result['total']['today'])
        self.assertEqual(result['total']['history'], 42)
        stager = load('extras/local-router-stats/stage_plugin.py')
        self.assertIn('hermesAccountingAgent.record', stager.RECORDED_TOKEN_FUNCTIONS)
        self.assertIn('hermes-token-accounting/usage.json', __import__('inspect').getsource(stager.transform_main))


if __name__ == '__main__':
    unittest.main(verbosity=2)
