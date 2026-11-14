#!/usr/bin/env python3
"""Explicit canonical feed: wholly synthetic SQLite, no Hermes bootstrap or live discovery."""
from contextlib import closing, contextmanager, ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT/'extras/local-router-stats/canonical_feed.py'
SCHEMA = '''
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version VALUES (31);
CREATE TABLE session_model_usage (
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 model TEXT NOT NULL, billing_provider TEXT NOT NULL DEFAULT '',
 billing_base_url TEXT NOT NULL DEFAULT '', billing_mode TEXT NOT NULL DEFAULT '',
 task TEXT NOT NULL DEFAULT '', api_call_count INTEGER NOT NULL DEFAULT 0,
 input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
 cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
 reasoning_tokens INTEGER NOT NULL DEFAULT 0, estimated_cost_usd REAL NOT NULL DEFAULT 0,
 actual_cost_usd REAL NOT NULL DEFAULT 0, cost_status TEXT, cost_source TEXT,
 first_seen REAL, last_seen REAL,
 PRIMARY KEY(session_id,model,billing_provider,billing_base_url,billing_mode,task));
CREATE TABLE sessions (id TEXT PRIMARY KEY, input_tokens INTEGER, system_prompt TEXT);
CREATE TABLE messages (content TEXT);
INSERT INTO sessions VALUES ('PRIVATE_SESSION', 999999, 'PRIVATE_PROMPT');
INSERT INTO messages VALUES ('PRIVATE_MESSAGE');
'''
COLUMNS = ('session_id','model','billing_provider','billing_base_url','billing_mode','task',
           'api_call_count','input_tokens','output_tokens','cache_read_tokens','cache_write_tokens',
           'reasoning_tokens','estimated_cost_usd','actual_cost_usd','cost_status','cost_source',
           'first_seen','last_seen')
def row(**changes) -> dict:
    values = dict(session_id='PRIVATE_SESSION', model='PRIVATE_MODEL', billing_provider='openai-codex',
                  billing_base_url='https://api.openai.com/v1', billing_mode='subscription_included',
                  task='PRIVATE_TASK', api_call_count=1, input_tokens=100, output_tokens=20,
                  cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=7,
                  estimated_cost_usd=0, actual_cost_usd=0, cost_status=None, cost_source=None,
                  first_seen=0, last_seen=1)
    values.update(changes)
    return values

def insert(db, values):
    db.execute('INSERT INTO session_model_usage VALUES (' + ','.join('?' for _ in COLUMNS) + ')',
               [values[c] for c in COLUMNS])

def sparse_main_file(path, size):
    """Extend a closed fixture with valid free pages, without writing their payloads."""
    with path.open('r+b') as stream:
        header = bytearray(stream.read(100))
        page_size = int.from_bytes(header[16:18], 'big')
        assert page_size == 4096 and not header[20]
        old_pages = int.from_bytes(header[28:32], 'big')
        assert path.stat().st_size == old_pages * page_size
        assert not int.from_bytes(header[36:40], 'big')
        assert size % page_size == 0 and size > path.stat().st_size
        pages = size // page_size
        # Leave SQLite's historical six reserved trunk entries unused.
        leaves_per_trunk = page_size // 4 - 8
        first = old_pages + 1
        stream.truncate(size)
        for trunk in range(first, pages + 1, leaves_per_trunk + 1):
            following = trunk + leaves_per_trunk + 1
            leaves = range(trunk + 1, min(following, pages + 1))
            data = bytearray(page_size)
            struct.pack_into('>II', data, 0, following if following <= pages else 0, len(leaves))
            for index, leaf in enumerate(leaves):
                struct.pack_into('>I', data, 8 + index * 4, leaf)
            stream.seek((trunk - 1) * page_size); stream.write(data)
        struct.pack_into('>III', header, 28, pages, first, pages - old_pages)
        change = int.from_bytes(header[24:28], 'big') + 1
        struct.pack_into('>I', header, 24, change)
        struct.pack_into('>I', header, 92, change)
        stream.seek(0); stream.write(header); stream.flush(); os.fsync(stream.fileno())
    return pages - old_pages

def load(path=FEED):
    spec = importlib.util.spec_from_file_location('synthetic_canonical_feed', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

STAT_NAMED = ('st_mode','st_ino','st_dev','st_nlink','st_uid','st_gid','st_size')
def shifted(info, **delta):
    values = list(info[:os.stat_result.n_sequence_fields])
    for name, change in delta.items(): values[STAT_NAMED.index(name)] += change
    return os.stat_result(values, {n:getattr(info,n) for n in dir(info) if n.startswith('st_') and n not in STAT_NAMED})

@contextmanager
def remounted(**delta):
    """Every stat result moves consistently, as after a reboot renumbers Btrfs anonymous st_dev."""
    real = {name:getattr(os,name) for name in ('stat','lstat','fstat')}
    euid = os.geteuid
    with ExitStack() as stack:
        for name, function in real.items():
            stack.enter_context(patch.object(os,name,lambda *a,function=function,**kw: shifted(function(*a,**kw),**delta)))
        if 'st_uid' in delta: stack.enter_context(patch.object(os,'geteuid',lambda: euid()+delta['st_uid']))
        yield

@contextmanager
def filesystem_id(change):
    """The source descriptor reports another statvfs f_fsid (other filesystem/subvolume or none)."""
    real = os.fstatvfs
    def view(fd):
        info = real(fd)
        return os.statvfs_result(list(info[:os.statvfs_result.n_sequence_fields]),{'f_fsid':change(info.f_fsid)})
    with patch.object(os,'fstatvfs',view): yield

def stable_identity(path):
    """Independent hermes-canonical-source/2 oracle: no st_dev in the persistent identity."""
    info = Path(path).stat(follow_symlinks=False)
    value = ['hermes-canonical-source/2', str(path), os.statvfs(path).f_fsid,
             info.st_ino, info.st_uid, info.st_mode, info.st_nlink]
    return {'scheme':'hermes-canonical-source/2',
            'digest':hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()}

def legacy_binding(path, st_dev_offset=0):
    """Reviewed pre-upgrade feed/1 sourceBinding: pathname + st_dev/inode/uid/mode/nlink digest."""
    info = Path(path).stat(follow_symlinks=False)
    value = [str(path), info.st_dev+st_dev_offset, info.st_ino, info.st_uid, info.st_mode, info.st_nlink]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

SYNTHETIC_ROOTS = set()
def sqlite_fixture_guard(event, values):
    if event == 'sqlite3.connect':
        from urllib.parse import urlsplit, unquote
        value = os.fsdecode(values[0])
        path = unquote(urlsplit(value).path) if value.startswith('file:') else value
        if path != ':memory:' and not any(Path(path).resolve().is_relative_to(root) for root in SYNTHETIC_ROOTS):
            raise PermissionError('in-process SQLite fixture boundary')
sys.addaudithook(sqlite_fixture_guard)
try: sqlite3.connect('/forbidden-in-process-fixture.db')
except PermissionError: pass
else: raise AssertionError('in-process SQLite guard not exercised')

# Every fresh CLI process enforces the SQLite fixture boundary BEFORE connection.
GUARD = r'''
import os, runpy, sys, resource
# The fixture parent can create bounded sparse source files above 32 MiB, but
# the actual CLI retains the original write ceiling independently of that setup.
resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024*1024, 32*1024*1024))
root, script, *args = sys.argv[1:]
root = os.path.realpath(root)
def guard(event, values):
    if event == 'sqlite3.connect':
        from urllib.parse import urlsplit, unquote
        value = os.fsdecode(values[0])
        path = unquote(urlsplit(value).path) if value.startswith('file:') else value
        if path != ':memory:' and not os.path.realpath(path).startswith(root + os.sep):
            raise PermissionError('synthetic SQLite boundary')
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise PermissionError('offline fixture')
    if event == 'import' and str(values[0]).split('.')[0] in ('hermes_state', 'hermes_bootstrap', 'hermes_cli'):
        raise PermissionError('no runtime bootstrap')
sys.addaudithook(guard)
try:
    import sqlite3
    sqlite3.connect('/forbidden-canonical-fixture.db')
except PermissionError:
    pass
else:
    raise AssertionError('SQLite guard not exercised')
sys.argv = [script, *args]
runpy.run_path(script, run_name='__main__')
'''

class FeedTests(unittest.TestCase):
    def setUp(self):
        for key in ('HOME','HERMES_HOME','XDG_CONFIG_HOME','XDG_STATE_HOME','XDG_RUNTIME_DIR'):
            self.assertIn('/ag-feed-', os.environ[key], 'use mandatory private runner')
        self.temp = tempfile.TemporaryDirectory(prefix='canonical-feed-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        SYNTHETIC_ROOTS.add(self.root)
        self.addCleanup(SYNTHETIC_ROOTS.discard,self.root)
        self.dbpath = self.root/'synthetic.db'
        self.out = self.root/'private'; self.out.mkdir(mode=0o700)
        self.canonical, self.state, self.history, self.usage = (self.out/n for n in
            ('canonical.json','state.json','history.json','usage.json'))
        self.calls = []

    def database(self, wal=False):
        # Explicit synthetic path guard before opening even the fixture writer.
        self.assertTrue(self.dbpath.resolve().is_relative_to(self.root))
        db = sqlite3.connect(self.dbpath)
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        self.dbpath.chmod(0o600)
        if wal: db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_autocheckpoint=0')
        insert(db, row()); db.commit()
        for side in self.root.glob('synthetic.db-*'): side.chmod(0o600)
        return db

    def options(self, at='2024-03-04T10:00:00Z'):
        return ['--source-db', str(self.dbpath), '--profile', 'default', '--timezone', 'UTC',
                '--canonical-output', str(self.canonical), '--history-state', str(self.state),
                '--history-output', str(self.history), '--output', str(self.usage), '--observed-at', at]

    def cli(self, options=None, script=FEED):
        command = [sys.executable,'-I','-B','-c', GUARD,str(self.root),str(script),
                   *(options if options is not None else self.options())]
        child = subprocess.run(command,
                               capture_output=True, text=True, timeout=20)
        self.calls.append({'command':command,'script_sha256':hashlib.sha256(Path(script).read_bytes()).hexdigest(),
                           'exit':child.returncode,'stdout':child.stdout,'stderr':child.stderr})
        return child

    def tearDown(self):
        if self.calls:
            print('CANONICAL_CLI_CALLS_PROOF='+json.dumps({'test':self.id(),'calls':self.calls},sort_keys=True))

    def arguments(self):
        import argparse
        values = self.options()
        return argparse.Namespace(**{values[i][2:].replace('-', '_'): Path(values[i+1])
            if values[i] in ('--source-db','--canonical-output','--history-state','--history-output','--output')
            else values[i+1] for i in range(0,len(values),2)})

    def test_preopen_alias_and_private_source_guards(self):
        self.database()
        feed = load(); adapter = feed.adapter_module()
        args = self.arguments()
        paths = [self.canonical,self.state,self.history,self.usage]
        aliases = [*paths, *(adapter.temporary_path(p) for p in paths),
                   *(adapter.publication_lock_path(p) for p in paths),
                   self.state.with_name(self.state.name+'.lock'), self.canonical.with_name(self.canonical.name+'.lock')]
        for alias_slot, alias in enumerate(aliases):
            with self.subTest(alias_slot=alias_slot):
                original = self.dbpath.read_bytes()
                alias.write_bytes(original); alias.chmod(0o600)
                args.source_db = alias
                before = {p.name:p.read_bytes() for p in self.out.iterdir()}
                opened = []
                def forbidden(*a, **kw): opened.append(a); raise AssertionError('SQLite opened before alias refusal')
                with patch.object(feed.sqlite3,'connect',side_effect=forbidden), self.assertRaises(ValueError):
                    feed.run(args)
                self.assertEqual(opened,[])
                self.assertEqual({p.name:p.read_bytes() for p in self.out.iterdir()},before)
                alias.unlink()
        args.source_db = self.dbpath
        alias = self.out/'hardlink.json'; os.link(self.dbpath,alias)
        args.canonical_output = alias
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('hardlink opened')), self.assertRaises(ValueError):
            feed.run(args)
        alias.unlink(); args.canonical_output = self.canonical
        for suffix in ('-wal','-shm','-journal'):
            side = Path(str(self.dbpath)+suffix)
            side.symlink_to(self.root/'do-not-touch')
            with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('sidecar opened')), self.assertRaises((ValueError,OSError)):
                feed.run(args)
            side.unlink()
        linked = self.root/'linked.db'; linked.symlink_to(self.dbpath); args.source_db = linked
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('symlink opened')), self.assertRaises((ValueError,OSError)):
            feed.run(args)
        args.source_db = self.dbpath; self.dbpath.chmod(0o644)
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('nonprivate opened')), self.assertRaises(ValueError):
            feed.run(args)

    def test_schema32_upgrade_retains_identity_and_observed_history(self):
        db = self.database()
        self.assertEqual(self.cli().returncode, 0)
        binding = self.binding()
        # Hermes v32 records additive runtime/worker schema declarations only;
        # it has no canonical-usage data migration and leaves the ledger intact.
        db.execute('UPDATE schema_version SET version=32')
        db.execute('CREATE TABLE runtime_epoch (id INTEGER)')
        db.execute('ALTER TABLE sessions ADD COLUMN runtime_generation INTEGER NOT NULL DEFAULT 0')
        db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+11, output_tokens=output_tokens+2')
        db.commit()
        source = self.dbpath.read_bytes()
        child = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(self.binding(), binding)
        self.assertEqual(self.observed(), 13)
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows']['history'], 168)
        self.assertEqual(self.dbpath.read_bytes(), source, 'feed must not migrate or modify source')

    def test_schema33_upgrade_retains_identity_and_observed_history(self):
        db = self.database()
        self.assertEqual(self.cli().returncode, 0)
        binding = self.binding()
        # v33 changes delegate metadata in sessions.model_config, not this ledger.
        # Model that unrelated metadata change; never run Hermes migrations here.
        db.execute('UPDATE schema_version SET version=33')
        db.execute('ALTER TABLE sessions ADD COLUMN model_config TEXT')
        db.execute('UPDATE sessions SET model_config=?', (json.dumps({'_delegate_from':'PRIVATE_PARENT'}),))
        db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+11, output_tokens=output_tokens+2')
        db.commit()
        source = self.dbpath.read_bytes()
        child = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(self.binding(), binding)
        self.assertEqual(self.observed(), 13)
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows']['history'], 168)
        self.assertEqual(self.dbpath.read_bytes(), source, 'feed must not migrate or modify source')
        for path in (self.canonical,self.state,self.history,self.usage):
            self.assertNotIn('PRIVATE_PARENT',path.read_text())

    def test_unrecognized_or_ambiguous_schema_versions_preserve_last_good(self):
        db = self.database()
        self.assertEqual(self.cli().returncode, 0)
        before = self.files()
        for versions in ([], [30], [34], [31, 32], [32, 32], [31, 33], [33, 33]):
            with self.subTest(versions=versions):
                db.execute('DELETE FROM schema_version')
                db.executemany('INSERT INTO schema_version VALUES (?)', ((v,) for v in versions))
                db.commit()
                source = self.dbpath.read_bytes()
                child = self.cli(self.options('2024-03-04T10:10:00Z'))
                self.assertEqual(child.returncode, 2)
                self.assertEqual(self.files(), before)
                self.assertEqual(self.dbpath.read_bytes(), source)

    def test_schema32_shape_drift_preserves_last_good(self):
        db = self.database()
        self.assertEqual(self.cli().returncode, 0)
        before = self.files()
        db.execute('UPDATE schema_version SET version=32')
        db.execute('ALTER TABLE session_model_usage ADD COLUMN unsupported TEXT')
        db.commit()
        source = self.dbpath.read_bytes()
        child = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(child.returncode, 2)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.dbpath.read_bytes(), source)

    def test_schema33_shape_drift_preserves_last_good(self):
        db = self.database()
        self.assertEqual(self.cli().returncode, 0)
        before = self.files()
        db.execute('UPDATE schema_version SET version=33')
        db.execute('ALTER TABLE session_model_usage ADD COLUMN unsupported TEXT')
        db.commit()
        source = self.dbpath.read_bytes()
        child = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(child.returncode, 2)
        self.assertIn('E_SCHEMA',child.stderr)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.dbpath.read_bytes(), source)

    def test_schema_drift_and_nonordinary_sources_preserve_last_good(self):
        db = self.database()
        feed = load(); args = self.arguments()
        feed.run(args)
        before = {p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
        # A same-shaped VIEW is not a canonical ledger, even at version31.
        db.execute('ALTER TABLE session_model_usage RENAME TO original_usage')
        db.execute('CREATE VIEW session_model_usage AS SELECT * FROM original_usage'); db.commit()
        with self.assertRaises((ValueError,sqlite3.Error)):
            feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)
        db.execute('DROP VIEW session_model_usage'); db.execute('ALTER TABLE original_usage RENAME TO session_model_usage')
        db.execute('ALTER TABLE session_model_usage ADD COLUMN unsupported TEXT'); db.commit()
        with self.assertRaises((ValueError,sqlite3.Error)):
            feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)

    def test_source_binding_and_destination_preimages(self):
        db = self.database(); feed = load(); args = self.arguments(); feed.run(args)
        before = {p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
        db.close()
        other = self.root/'replacement.db'; other.write_bytes(self.dbpath.read_bytes()); other.chmod(0o600)
        original = self.root/'original.db'; self.dbpath.rename(original); other.rename(self.dbpath)
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('replacement opened')), self.assertRaises(ValueError):
            feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)
        self.dbpath.unlink(); original.rename(self.dbpath)
        acquire = feed.acquire
        def edit(*a, **kw):
            rows = acquire(*a, **kw)
            self.canonical.write_bytes(b'PRIVATE_USER_EDIT\n')
            return rows
        with patch.object(feed,'acquire',side_effect=edit), self.assertRaises(ValueError):
            feed.run(args)
        self.assertEqual(self.canonical.read_bytes(),b'PRIVATE_USER_EDIT\n')
        for p in (self.state,self.history,self.usage): self.assertEqual(p.read_bytes(),before[p.name])

    def files(self):
        return {p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}

    def observed(self):
        return json.loads(self.history.read_text())['coverage']['observed'].get('total_tokens')

    def binding(self, record=None):
        record = json.loads(self.canonical.read_text()) if record is None else record
        return {k:record.get(k) for k in ('sourceBinding','sourceIdentity','configBinding')}

    def republish(self, adapter, record):
        adapter.publish(record,self.canonical,expected=adapter.fingerprint(self.canonical))
        return self.files()

    def legacy_publication(self, adapter, st_dev_offset=0):
        """The same association as published before the identity upgrade (no sourceIdentity)."""
        record = json.loads(self.canonical.read_text()); record.pop('sourceIdentity',None)
        record['sourceBinding'] = legacy_binding(self.dbpath,st_dev_offset)
        return record, self.republish(adapter,record)

    def test_device_number_only_change_keeps_stable_association(self):
        db = self.database(); feed = load(); args = self.arguments(); feed.run(args)
        first, expected, info = json.loads(self.canonical.read_text()), stable_identity(self.dbpath), self.dbpath.stat()
        # Observed live sequence 33 -> 34 -> 33, then an arbitrary later renumbering.
        for step, (minute, offset) in enumerate(((10,1),(20,0),(30,7)), 1):
            with self.subTest(st_dev_offset=offset):
                db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10'); db.commit()
                args.observed_at = '2024-03-04T10:%d:00Z' % minute
                with remounted(st_dev=offset):
                    self.assertEqual(os.stat(self.dbpath).st_dev,info.st_dev+offset)
                    self.assertEqual(stable_identity(self.dbpath),expected)
                    feed.run(args)
                self.assertEqual(self.binding(),{'sourceBinding':expected['digest'],'sourceIdentity':expected,
                                                 'configBinding':first['configBinding']})
                self.assertEqual(self.observed(),10*step)
        for secret in (str(self.dbpath),str(os.statvfs(self.dbpath).f_fsid),str(info.st_ino)):
            self.assertNotIn(secret,self.canonical.read_text())
        print('STABLE_IDENTITY_PROOF='+json.dumps({'st_dev_offsets':[1,0,7],'observed_total_tokens':self.observed(),
            'scheme':expected['scheme']},sort_keys=True))

    def test_exact_legacy_binding_upgrades_without_rebinding_consumers(self):
        db = self.database(); feed = load(); adapter = feed.adapter_module(); args = self.arguments(); feed.run(args)
        legacy, _ = self.legacy_publication(adapter)
        calibration = load(ROOT/'extras/local-router-stats/quota_calibration.py')
        def association():
            at = calibration.instant(json.loads(self.canonical.read_text())['observedAt'])
            return calibration.canonical_snapshot(adapter,self.canonical,'default',at)[0]
        before = association()
        db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10'); db.commit()
        upgraded = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(upgraded.returncode,0,upgraded.stderr)
        expected = {'sourceBinding':legacy['sourceBinding'],'sourceIdentity':stable_identity(self.dbpath),
                    'configBinding':legacy['configBinding']}
        self.assertEqual(self.binding(),expected)
        self.assertEqual(association(),before,'upgrade must not look like a changed feed association')
        self.assertEqual(self.observed(),10)
        # Once upgraded, a reboot-only device change no longer refuses.
        db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+5'); db.commit()
        args.observed_at = '2024-03-04T10:20:00Z'
        with remounted(st_dev=1): feed.run(args)
        self.assertEqual(self.binding(),expected)
        self.assertEqual(self.observed(),15)
        self.assertEqual(association(),before)

    def test_true_source_filesystem_inode_owner_mode_path_replacement_refused(self):
        db = self.database(); feed = load(); args = self.arguments(); feed.run(args); db.close()
        first = self.binding()
        args.observed_at = '2024-03-04T10:10:00Z'
        before = self.files()
        @contextmanager
        def inode():
            copy, original = self.root/'copy.db', self.root/'original.db'
            copy.write_bytes(self.dbpath.read_bytes()); copy.chmod(0o600)
            self.dbpath.rename(original); copy.rename(self.dbpath)
            try: yield
            finally: self.dbpath.unlink(); original.rename(self.dbpath)
        @contextmanager
        def mode():
            self.dbpath.chmod(0o400)
            try: yield
            finally: self.dbpath.chmod(0o600)
        @contextmanager
        def moved():
            other = self.root/'moved.db'; self.dbpath.rename(other); args.source_db = other
            try: yield
            finally: other.rename(self.dbpath); args.source_db = self.dbpath
        @contextmanager
        def rebooted_replacement():
            with remounted(st_dev=1), inode(): yield
        for name, change in (('filesystem',lambda: filesystem_id(lambda value: value ^ 1)),
                             ('subvolume',lambda: filesystem_id(lambda value: value ^ (1 << 32))),
                             ('inode',inode),('owner',lambda: remounted(st_uid=1)),('mode',mode),
                             ('path',moved),('reboot+inode',rebooted_replacement)):
            with self.subTest(replacement=name):
                with change(), patch.object(feed.sqlite3,'connect',side_effect=AssertionError(name+' replacement opened')):
                    with self.assertRaises(ValueError): feed.run(args)
                self.assertEqual(self.files(),before)
        feed.run(args)
        self.assertEqual(self.binding(),first)
        self.assertEqual(first['sourceIdentity'],stable_identity(self.dbpath))

    def test_malformed_unknown_or_unavailable_identity_refuses(self):
        self.database(); feed = load(); adapter = feed.adapter_module(); args = self.arguments(); feed.run(args)
        good, stable = json.loads(self.canonical.read_text()), stable_identity(self.dbpath)
        args.observed_at = '2024-03-04T10:10:00Z'
        def without(key): return lambda record: record.pop(key)
        def setting(key, value): return lambda record: record.__setitem__(key,value)
        variants = {'unknown scheme':setting('sourceIdentity',dict(stable,scheme='hermes-canonical-source/3')),
                    'legacy scheme':setting('sourceIdentity',dict(stable,scheme='hermes-canonical-source/1')),
                    'uppercase digest':setting('sourceIdentity',dict(stable,digest=stable['digest'].upper())),
                    'short digest':setting('sourceIdentity',dict(stable,digest=stable['digest'][:63])),
                    'extra field':setting('sourceIdentity',dict(stable,st_dev=0)),
                    'missing digest':setting('sourceIdentity',{'scheme':stable['scheme']}),
                    'null identity':setting('sourceIdentity',None),
                    'string identity':setting('sourceIdentity',stable['digest']),
                    'list identity':setting('sourceIdentity',[stable['scheme'],stable['digest']]),
                    'missing binding':without('sourceBinding'),'null binding':setting('sourceBinding',None),
                    'uppercase binding':setting('sourceBinding',good['sourceBinding'].upper()),
                    'nonhex binding':setting('sourceBinding','g'*64),
                    'short binding':setting('sourceBinding',good['sourceBinding'][:63]),
                    'numeric binding':setting('sourceBinding',7)}
        for name, mutate in variants.items():
            with self.subTest(identity=name):
                record = json.loads(json.dumps(good)); mutate(record)
                before = self.republish(adapter,record)
                with patch.object(feed.sqlite3,'connect',side_effect=AssertionError(name+' opened')), self.assertRaises(ValueError):
                    feed.run(args)
                self.assertEqual(self.files(),before)
        self.republish(adapter,good); before = self.files()
        for name, value in (('zero',lambda _: 0),('negative',lambda _: -1),('overflow',lambda _: 1 << 64),
                            ('text',lambda value: str(value)),('float',lambda value: float(value))):
            with self.subTest(fsid=name):
                with filesystem_id(value), patch.object(feed.sqlite3,'connect',side_effect=AssertionError('no fsid opened')):
                    with self.assertRaises(ValueError): feed.run(args)
                self.assertEqual(self.files(),before)
        # A first association also fails closed rather than falling back to st_dev.
        fresh = self.root/'fresh'; fresh.mkdir(mode=0o700)
        for name in ('canonical_output','history_state','history_output','output'):
            setattr(args,name,fresh/(name+'.json'))
        with filesystem_id(lambda _: 0), self.assertRaises(ValueError): feed.run(args)
        self.assertEqual([p.name for p in fresh.iterdir()],['canonical_output.json.lock'])
        self.assertEqual(self.binding(good),{'sourceBinding':stable['digest'],'sourceIdentity':stable,
                                             'configBinding':good['configBinding']})

    def test_legacy_mismatch_or_failed_upgrade_preserves_last_good(self):
        db = self.database(); feed = load(); adapter = feed.adapter_module(); args = self.arguments()
        with patch.object(feed,'adapter_module',return_value=adapter):
            feed.run(args)
            db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10'); db.commit()
            args.observed_at = '2024-03-04T10:10:00Z'
            legacy, before = self.legacy_publication(adapter)
            publish = adapter.publish
            def fail(record, path, **kw):
                if Path(path) == self.canonical: raise OSError('synthetic upgrade publication failure')
                return publish(record,path,**kw)
            with patch.object(adapter,'publish',side_effect=fail), self.assertRaises(OSError): feed.run(args)
            self.assertEqual(self.files(),before)
            db.execute('BEGIN EXCLUSIVE')
            self.assertEqual(self.cli(self.options(args.observed_at)).returncode,2)
            db.rollback()
            self.assertEqual(self.files(),before)
            # A legacy digest from another boot's device is refused without guessing that device.
            former, before = self.legacy_publication(adapter,-1)
            seen = []; real = feed.digest
            def spy(value): seen.append(value); return real(value)
            with patch.object(feed,'digest',side_effect=spy), \
                    patch.object(feed.sqlite3,'connect',side_effect=AssertionError('mismatched legacy opened')):
                with self.assertRaises(ValueError): feed.run(args)
            info = self.dbpath.stat()
            self.assertEqual([v for v in seen if isinstance(v,list) and v[:1] == [str(self.dbpath)]],
                             [[str(self.dbpath),info.st_dev,info.st_ino,info.st_uid,info.st_mode,info.st_nlink]])
            self.assertEqual(self.files(),before)
            # When the current device matches the complete legacy binding again, it upgrades once.
            with remounted(st_dev=-1): feed.run(args)
            self.assertEqual(self.binding(),{'sourceBinding':former['sourceBinding'],
                'sourceIdentity':stable_identity(self.dbpath),'configBinding':former['configBinding']})
            self.assertEqual(self.observed(),10)
            committed = self.files(); feed.run(args)
            self.assertEqual(self.files(),committed,'same-instant retry after the upgrade is idempotent')
            self.assertNotEqual(legacy['sourceBinding'],former['sourceBinding'])

    def test_configuration_is_validated_before_source_open(self):
        self.database(); feed = load(); args = self.arguments()
        for name, value in (('profile','private/path'),('timezone','not/a/timezone'),
                            ('observed_at','2024-01-01'),('observed_at','secret')):
            with self.subTest(name=name,value=value):
                previous = getattr(args,name); setattr(args,name,value)
                with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('invalid config opened')), self.assertRaises(ValueError):
                    feed.run(args)
                setattr(args,name,previous)
                self.assertEqual(list(self.out.iterdir()),[])

    def test_stream_bytes_and_time_bounds_leave_publications_unchanged(self):
        db = self.database(); feed = load(); args = self.arguments(); adapter = feed.adapter_module()
        feed.run(args)
        before = {p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
        db.execute("UPDATE session_model_usage SET model=?", ('x'*2000,)); db.commit()
        args.observed_at = '2024-03-04T10:10:00Z'
        with patch.object(feed,'adapter_module',return_value=adapter), patch.object(adapter,'MAX_INPUT',1000):
            with self.assertRaises(ValueError): feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)
        ticks = iter(range(0,10000,10))
        with patch.object(feed.time,'monotonic',side_effect=lambda:next(ticks)), self.assertRaises((ValueError,sqlite3.Error,TimeoutError)):
            feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)

    def test_deadline_expires_even_for_small_ledger(self):
        self.database(); feed = load(); adapter = feed.adapter_module()
        ticks = iter(range(0,10000,10))
        with patch.object(feed.time,'monotonic',side_effect=lambda:next(ticks)):
            with self.assertRaises((TimeoutError,sqlite3.Error)):
                feed.acquire(self.dbpath,adapter,'default')
        self.assertFalse(self.canonical.exists())

    def test_fixed_sql_allowlist_blocks_transcripts_and_writes(self):
        self.database(); feed = load(); seen = []
        original = sqlite3.connect
        class Audited(sqlite3.Connection):
            def set_authorizer(conn, authorizer_callback):
                super().set_authorizer(authorizer_callback)
                for statement in ('SELECT content FROM messages', 'SELECT system_prompt FROM sessions',
                                  'SELECT actual_cost_usd FROM session_model_usage',
                                  'DELETE FROM session_model_usage', 'PRAGMA user_version=10',
                                  "ATTACH DATABASE ':memory:' AS extra"):
                    try: conn.execute(statement)
                    except sqlite3.Error: seen.append(statement)
                    else: raise AssertionError('nonallowlisted SQL accepted: '+statement)
        with patch.object(feed.sqlite3,'connect',side_effect=lambda *a,**kw:original(*a,**kw,factory=Audited)):
            feed.run(self.arguments())
        self.assertEqual(len(seen),6,'source acquisition never installed its SQL allowlist')

    def test_wal_concurrent_writer_is_one_committed_view(self):
        db = self.database(wal=True); feed = load(); adapter = feed.adapter_module()
        original = sqlite3.connect; commits = []
        class Concurrent(sqlite3.Connection):
            def execute(conn, sql, *params):
                result = super().execute(sql,*params)
                if sql.startswith('SELECT name,type,sql'):
                    db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10'); db.commit(); commits.append(True)
                return result
        with patch.object(feed.sqlite3,'connect',side_effect=lambda *a,**kw:original(*a,**kw,factory=Concurrent)):
            acquired = feed.acquire(self.dbpath,adapter,'default')
        self.assertEqual(len(commits),1)
        self.assertEqual(acquired[0]['input_tokens'],100)
        self.assertEqual(feed.acquire(self.dbpath,adapter,'default')[0]['input_tokens'],110)

    def test_cli_signal_shutdown_is_quiet_and_nonzero(self):
        self.database()
        code = '''
import importlib.util,os,signal,sys
p=sys.argv[1]
spec=importlib.util.spec_from_file_location('feed_signal_fixture',p)
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
f.run=lambda args: os.kill(os.getpid(),signal.SIGTERM)
raise SystemExit(f.main(sys.argv[2:]))
'''
        child=subprocess.run([sys.executable,'-I','-B','-c',code,str(FEED),*self.options()],
                             capture_output=True,text=True,timeout=5)
        self.assertEqual(child.returncode,2,'signal should abort cleanly without claiming success')
        self.assertNotIn('Traceback',child.stderr)
        self.assertEqual(child.stderr, 'Canonical accounting feed refused [E_INTERRUPTED]; last-good publications retained.\n')
        self.assertEqual(list(self.out.iterdir()),[])

    def test_cli_installs_finite_wall_and_address_space_limits(self):
        code='''
import importlib.util,resource,signal,sys
spec=importlib.util.spec_from_file_location('feed_limits_fixture',sys.argv[1])
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
def inspect(args):
    bound=resource.getrlimit(resource.RLIMIT_AS)[0]
    assert 0 < bound <= 256*1024*1024, ('unbounded address space',bound)
    seconds=signal.getitimer(signal.ITIMER_REAL)[0]
    assert 0 < seconds <= 10, ('unbounded CLI lifetime',seconds)
f.run=inspect
raise SystemExit(f.main(sys.argv[2:]))
'''
        child=subprocess.run([sys.executable,'-I','-B','-c',code,str(FEED),*self.options()],
                             capture_output=True,text=True,timeout=5)
        self.assertEqual(child.returncode,0,child.stderr)

    def test_missing_busy_corrupt_source_and_recovery(self):
        db=self.database(); self.assertEqual(self.cli().returncode,0)
        files=(self.canonical,self.state,self.history,self.usage)
        before={p.name:p.read_bytes() for p in files}
        db.execute('BEGIN EXCLUSIVE')
        child=self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(child.returncode,2)
        self.assertEqual({p.name:p.read_bytes() for p in files},before)
        db.rollback(); db.close()
        saved=self.root/'saved.db'; self.dbpath.rename(saved)
        self.assertEqual(self.cli(self.options('2024-03-04T10:10:00Z')).returncode,2)
        self.assertFalse(self.dbpath.exists(),'mode=ro never creates missing source')
        saved.rename(self.dbpath)
        data=self.dbpath.read_bytes(); self.dbpath.write_bytes(b'PRIVATE_MALFORMED_SOURCE')
        child=self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(child.returncode,2); self.assertNotIn('PRIVATE_MALFORMED_SOURCE',child.stderr)
        self.assertEqual({p.name:p.read_bytes() for p in files},before)
        self.dbpath.write_bytes(data)
        self.assertEqual(self.cli(self.options('2024-03-04T10:10:00Z')).returncode,0)
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows']['history'],155)

    def test_malformed_row_rowcount_and_sidecar_bounds(self):
        db=self.database(); feed=load(); args=self.arguments(); feed.run(args)
        before={p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
        args.observed_at='2024-03-04T10:10:00Z'
        for name,value in (('input_tokens',-1),('output_tokens',1),('cache_read_tokens','PRIVATE_BAD_COUNTER'),
                           ('first_seen',2),('model','x'*2049)):
            with self.subTest(field=name):
                old=db.execute('SELECT '+name+' FROM session_model_usage').fetchone()[0]
                db.execute('UPDATE session_model_usage SET '+name+'=?',(value,)); db.commit()
                with self.assertRaises((ValueError,sqlite3.Error)): feed.run(args)
                self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)
                db.execute('UPDATE session_model_usage SET '+name+'=?',(old,)); db.commit()
        srow=row(session_id='PRIVATE_LOCAL',billing_base_url='http://localhost:9000')
        insert(db,srow); db.commit()
        adapter=feed.adapter_module()
        with patch.object(feed,'adapter_module',return_value=adapter),patch.object(adapter,'MAX_ROWS',1):
            with self.assertRaises(ValueError):feed.run(args)
        for suffix in ('-wal','-shm','-journal'):
            p=Path(str(self.dbpath)+suffix); p.touch(mode=0o600)
            with p.open('r+b') as stream: stream.truncate(feed.MAX_SIDECAR_BYTES+1)
            with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('oversized sidecar opened')),self.assertRaises(ValueError):
                feed.run(args)
            p.unlink()
        with self.dbpath.open('r+b') as stream: stream.truncate(feed.MAX_DB_BYTES+1)
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('oversized database opened')),self.assertRaises(ValueError):
            feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)

    def test_postcommit_failure_recovery_and_canonical_failure(self):
        db=self.database(); feed=load(); adapter=feed.adapter_module(); args=self.arguments()
        with patch.object(feed,'adapter_module',return_value=adapter):
            feed.run(args)
            before={p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
            db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+19');db.commit()
            args.observed_at='2024-03-04T10:10:00Z'
            publish=adapter.publish
            def fail_history(record,path,**kw):
                if Path(path)==self.history:raise OSError('synthetic export failure')
                return publish(record,path,**kw)
            with patch.object(adapter,'publish',side_effect=fail_history),self.assertRaises(OSError):feed.run(args)
            self.assertNotEqual(self.state.read_bytes(),before[self.state.name],'durable journal commits first')
            self.assertEqual(self.history.read_bytes(),before[self.history.name])
            adapter.recover_history(state_path=self.state,history_output=self.history,output=self.usage)
            actual=json.loads(self.history.read_text())
            self.assertEqual(actual['coverage']['observed']['total_tokens'],19)
            committed={p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
            feed.run(args)
            self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},committed)
            args.observed_at='2024-03-04T10:20:00Z'
            def fail_canonical(record,path,**kw):
                if Path(path)==self.canonical:raise OSError('synthetic canonical failure')
                return publish(record,path,**kw)
            with patch.object(adapter,'publish',side_effect=fail_canonical),self.assertRaises(OSError):feed.run(args)
            self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},committed)
            stale=adapter.temporary_path(self.canonical);stale.write_bytes(b'synthetic stale payload');stale.chmod(0o600)
            feed.run(args);self.assertFalse(stale.exists())
            self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'],19)

    def test_contention_and_source_replacement_during_read(self):
        import fcntl
        self.database();feed=load();args=self.arguments();feed.run(args)
        before={p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
        lock=self.canonical.with_name(self.canonical.name+'.lock')
        with lock.open('rb') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('contended feed opened')),self.assertRaises(OSError):feed.run(args)
        acquire=feed.acquire
        def replace(*a,**kw):
            rows=acquire(*a,**kw)
            other=self.root/'source-replacement.db';other.write_bytes(self.dbpath.read_bytes());other.chmod(0o600)
            os.replace(other,self.dbpath)
            return rows
        with patch.object(feed,'acquire',side_effect=replace),self.assertRaises(ValueError):feed.run(args)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)

    def test_stale_temp_and_publication_aliases_are_not_cleanup_authority(self):
        self.database();feed=load();args=self.arguments();adapter=feed.adapter_module()
        stale=adapter.temporary_path(self.canonical)
        target=self.root/'private-unrelated';target.write_bytes(b'PRIVATE_UNRELATED');target.chmod(0o600)
        for kind in ('symlink','hardlink'):
            if kind=='symlink':stale.symlink_to(target)
            else:os.link(target,stale)
            with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('unsafe temp opened')),self.assertRaises((ValueError,OSError)):feed.run(args)
            self.assertEqual(target.read_bytes(),b'PRIVATE_UNRELATED');self.assertTrue(stale.exists());stale.unlink()

    def test_acquisition_api_rejects_symlinks_and_sidecars_before_sqlite(self):
        self.database();feed=load();adapter=feed.adapter_module()
        linked=self.root/'source-link.db';linked.symlink_to(self.dbpath)
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('symlink opened by acquisition')),self.assertRaises((ValueError,OSError)):
            feed.acquire(linked,adapter,'default')
        side=Path(str(self.dbpath)+'-wal');side.symlink_to(self.root/'unknown')
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('sidecar opened by acquisition')),self.assertRaises((ValueError,OSError)):
            feed.acquire(self.dbpath,adapter,'default')

    def test_clocked_cli_without_observed_at(self):
        self.database()
        child=self.cli(self.options()[:-2])
        self.assertEqual(child.returncode,0,child.stderr)
        observed=json.loads(self.history.read_text())['observedAt']
        self.assertRegex(observed,r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(json.loads(self.history.read_text())['coverage']['status'],'baseline-only')

    def test_parsed_canonical_bytes_must_match_acquired_counters(self):
        db=self.database();feed=load();adapter=feed.adapter_module();args=self.arguments()
        with patch.object(feed,'adapter_module',return_value=adapter):
            feed.run(args)
            before={p.name:p.read_bytes() for p in (self.state,self.history,self.usage)}
            db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10');db.commit()
            args.observed_at='2024-03-04T10:10:00Z'
            read=adapter.read_json
            def edit_then_read(path):
                data=json.loads(Path(path).read_text());data['rows'][0]['input_tokens']+=1000
                Path(path).write_text(json.dumps(data))
                return read(path)
            with patch.object(adapter,'read_json',side_effect=edit_then_read),self.assertRaises(ValueError):feed.run(args)
            self.assertEqual({p.name:p.read_bytes() for p in (self.state,self.history,self.usage)},before)
            self.assertEqual(json.loads(self.canonical.read_text())['rows'][0]['input_tokens'],1110)

    def test_first_state_write_failure_retains_replay_instant(self):
        self.database();feed=load();adapter=feed.adapter_module();args=self.arguments()
        publish=adapter.publish
        def fail(record,path,**kw):
            if Path(path)==self.state:raise OSError('synthetic first commit failure')
            return publish(record,path,**kw)
        with patch.object(feed,'adapter_module',return_value=adapter),patch.object(adapter,'publish',side_effect=fail):
            with self.assertRaises(OSError):feed.run(args)
        saved=json.loads(self.canonical.read_text())
        self.assertEqual(saved.get('observedAt'),'2024-03-04T10:00:00Z','canonical replay must retain its observation instant')
        self.assertFalse(self.state.exists());self.assertFalse(self.usage.exists());self.assertFalse(self.history.exists())
        result=adapter.main(['--json',str(self.canonical),'--profile','default','--timezone','UTC',
            '--observed-at',saved['observedAt'],'--history-state',str(self.state),'--history-output',str(self.history),
            '--output',str(self.usage)])
        self.assertEqual(result,0)
        self.assertEqual(json.loads(self.history.read_text())['coverage']['status'],'baseline-only')

    def test_blob_sqlite_values_are_quiet_refusals(self):
        db=self.database()
        db.execute('UPDATE session_model_usage SET model=?',(sqlite3.Binary(b'PRIVATE_BLOB'),));db.commit()
        result=self.cli()
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertNotIn('Traceback',result.stderr);self.assertNotIn('PRIVATE_BLOB',result.stderr)
        self.assertFalse(self.canonical.exists());self.assertFalse(self.state.exists())

    def test_sparse_larger_main_file_cli_pipeline(self):
        db = self.database(); db.close()
        # Valid unused capacity can remain after unrelated transcript storage shrinks.
        # The logical size matches the refused shape; no real database is opened.
        size = 612311040
        free_pages = sparse_main_file(self.dbpath, size)
        with closing(sqlite3.connect(self.dbpath)) as fixture:
            self.assertEqual(fixture.execute('PRAGMA quick_check').fetchall(), [('ok',)])
            self.assertEqual(fixture.execute('PRAGMA freelist_count').fetchone()[0], free_pages)
            self.assertEqual(fixture.execute('PRAGMA page_count').fetchone()[0] * 4096, size)
        allocated = self.dbpath.stat().st_blocks * 512
        self.assertLess(allocated, 2 * 1024 * 1024, 'fixture must stay physically sparse')
        print('SPARSE_MAIN_FIXTURE_PROOF='+json.dumps({'logical_bytes':size,'allocated_bytes':allocated,
            'free_pages':free_pages,'quick_check':'ok','source':'private synthetic fixture'}))
        first = self.cli(); self.assertEqual(first.returncode, 0, first.stderr)
        baseline = json.loads(self.history.read_text())
        self.assertEqual(baseline['coverage']['status'], 'baseline-only')
        self.assertEqual(baseline['coverage']['observed']['total_tokens'], 0)
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows'],
                         {'today':None,'week':None,'history':155})
        with closing(sqlite3.connect(self.dbpath)) as fixture:
            fixture.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10, output_tokens=output_tokens+4, '
                            'cache_read_tokens=cache_read_tokens+3, cache_write_tokens=cache_write_tokens+2, '
                            'reasoning_tokens=reasoning_tokens+1, api_call_count=api_call_count+1')
            fixture.commit()
        later = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(later.returncode, 0, later.stderr)
        history = json.loads(self.history.read_text())
        self.assertEqual(history['coverage']['observed'], dict(input_tokens=10,output_tokens=4,
            cache_read_tokens=3,cache_write_tokens=2,reasoning_tokens=1,api_call_count=1,prompt_tokens=15,total_tokens=19))
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows']['history'], 174)
        for path in (self.canonical,self.state,self.history,self.usage):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for secret in ('PRIVATE_SESSION','PRIVATE_MODEL','PRIVATE_TASK','api.openai.com','PRIVATE_PROMPT','PRIVATE_MESSAGE'):
                self.assertNotIn(secret,path.read_text())
        self.assertEqual(self.dbpath.stat().st_size, size)
        self.assertLess(self.dbpath.stat().st_blocks * 512, 2 * 1024 * 1024)
        print('SPARSE_MAIN_PIPELINE_PROOF='+json.dumps({'baseline_status':baseline['coverage']['status'],
            'observed':history['coverage']['observed'],'cli_exits':[call['exit'] for call in self.calls],
            'output_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (self.canonical,self.state,self.history,self.usage)}},sort_keys=True))

    def test_beyond_main_file_cap_preserves_last_good_before_sqlite(self):
        db = self.database(); db.close()
        self.assertEqual(self.cli().returncode, 0)
        feed = load(); args = self.arguments()
        self.assertEqual(feed.MAX_DB_BYTES, 1024 * 1024 * 1024)
        files = (self.canonical,self.state,self.history,self.usage)
        before = {p.name:p.read_bytes() for p in files}
        with self.dbpath.open('r+b') as stream: stream.truncate(feed.MAX_DB_BYTES + 1)
        with patch.object(feed.sqlite3,'connect',side_effect=AssertionError('oversized database opened')) as connect:
            with self.assertRaises(ValueError): feed.run(args)
        self.assertEqual(connect.call_count, 0, 'refuse before any attempted SQLite connection')
        result = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, 'Canonical accounting feed refused [E_DB_SIZE]; last-good publications retained.\n')
        self.assertEqual({p.name:p.read_bytes() for p in files}, before)
        print('BEYOND_MAIN_CAP_PROOF='+json.dumps({'main_cap':feed.MAX_DB_BYTES,
            'logical_bytes':self.dbpath.stat().st_size,'attempted_sqlite_opens':connect.call_count,
            'last_good_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in before.items()},
            'cli_exits':[call['exit'] for call in self.calls]},sort_keys=True))

    def test_raw_rows_provider_totals_and_history_identities_are_distinct(self):
        db = self.database(); adapter = load().adapter_module()
        # Scanned local rows consume row/byte bounds but no cloud history identities.
        for index in range(256):
            insert(db,row(session_id='PRIVATE_LOCAL_'+str(index),billing_base_url='http://localhost:8000/v1'))
        db.commit()
        first = self.cli(); self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(len(json.loads(self.canonical.read_text())['rows']), 1)
        # Provider totals aggregate; comparison/reset baselines retain all canonical identities.
        for index in range(127): insert(db,row(session_id='PRIVATE_CLOUD_'+str(index)))
        db.commit()
        later = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(later.returncode, 0, later.stderr)
        self.assertEqual(len(json.loads(self.canonical.read_text())['rows']), 128)
        sources = json.loads(self.usage.read_text())['sources']
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['attribution'], 'unattributed')
        self.assertEqual(sources[0]['windows']['history'], 128 * 155)
        many = [row(session_id='PRIVATE_CLOUD_'+str(index)) for index in range(129)]
        self.assertEqual(len(adapter.project(many,'default')['sources']), 1)
        self.assertEqual(len(adapter.canonical_snapshot(many,'default')['sources']),129)
        beyond = [row(session_id='PRIVATE_OVER_'+str(index)) for index in range(adapter.MAX_HISTORY_SOURCES+1)]
        with self.assertRaisesRegex(ValueError,'too many observed history sources'):
            adapter.canonical_snapshot(beyond,'default')
        insert(db,row(session_id='PRIVATE_CLOUD_127')); db.commit()
        self.assertEqual(self.cli(self.options('2024-03-04T10:20:00Z')).returncode,0)
        self.assertEqual(len(json.loads(self.history.read_text())['sources']),129)
        for index in range(128,adapter.MAX_HISTORY_SOURCES-1):
            insert(db,row(session_id='PRIVATE_CLOUD_'+str(index)))
        db.commit()
        maximum=self.cli(self.options('2024-03-04T10:30:00Z'))
        self.assertEqual(maximum.returncode,0,maximum.stderr)
        files = (self.canonical,self.state,self.history,self.usage)
        before = {p.name:p.read_bytes() for p in files}
        insert(db,row(session_id='PRIVATE_CLOUD_OVER')); db.commit()
        result = self.cli(self.options('2024-03-04T10:40:00Z'))
        self.assertEqual(result.returncode, 2)
        self.assertEqual({p.name:p.read_bytes() for p in files}, before)
        print('ROW_SOURCE_DISTINCTION_PROOF='+json.dumps({'scanned_rows_at_success':adapter.MAX_HISTORY_SOURCES+256,
            'excluded_local_rows':256,'admitted_history_identities':adapter.MAX_HISTORY_SOURCES,
            'legacy_128_and_129_admitted':True,'provider_aggregate_sources':1,
            'refused_cloud_identities':adapter.MAX_HISTORY_SOURCES+1,'history_policy':'per canonical identity; finite extension',
            'cli_exits':[call['exit'] for call in self.calls]},sort_keys=True))

    def test_deployed_sanitized_key_source_id_is_pinned(self):
        self.database()
        adapter = load().adapter_module()
        self.assertEqual(adapter.KEY_FIELDS, ('session_id','model','billing_provider',
                                            'billing_base_url','billing_mode','task'))
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        sources = json.loads(self.history.read_text())['sources']
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['id'], '2371b9fcae224d69bad6e1f559aecfb667559d83c803afbdfaf91d2d90ec840c')
        self.assertNotEqual(sources[0]['id'], '52206b2a985ad16697b6f0b43b864a4007c196958bebda9f3328e2d2f69f891c',
                            'a managed refresh must not restore the obsolete prefixed identity oracle')

    def test_many_cloud_identities_cli_independent_resets_to_consumer(self):
        import shutil
        from copy import deepcopy
        db = self.database()
        db.execute('DELETE FROM session_model_usage')
        cloud = [row(session_id='PRIVATE_CLOUD_'+str(i), model='PRIVATE_MODEL_'+str(i % 7),
                     task='PRIVATE_TASK_'+str(i % 3),
                     billing_provider='anthropic' if i % 2 else 'openai-codex',
                     billing_base_url='https://api.anthropic.com' if i % 2 else 'https://api.openai.com/v1',
                     input_tokens=100+i) for i in range(160)]
        local = [row(session_id='PRIVATE_LOCAL_'+str(i), billing_base_url='http://localhost:8000/v1')
                 for i in range(37)]
        adapter = load().adapter_module()
        fields = adapter.COUNTERS
        zero = dict.fromkeys(fields, 0)
        expected, statuses, previous = {}, {}, {}
        times = ['2024-02-29T23:40:00Z', '2024-02-29T23:45:00Z', '2024-02-29T23:50:00Z',
                 '2024-03-01T00:05:00Z', '2024-03-02T00:10:00Z', '2024-03-02T00:15:00Z']
        outputs = []
        for sample, when in enumerate(times):
            if sample == 1:
                for i, value in enumerate(cloud):
                    if i % 3 == 1:
                        for field, delta in zip(fields, (10, 4, 3, 2, 1, 1)): value[field] += delta
                    elif i % 3 == 2:
                        value.update(input_tokens=1, output_tokens=1, cache_read_tokens=0,
                                     cache_write_tokens=0, reasoning_tokens=0, api_call_count=0)
                cloud.append(row(session_id='PRIVATE_NEW', input_tokens=9))
            elif sample > 1:
                for value in cloud:
                    for field, delta in zip(fields, (3, 2, 1, 1, 1, 1)): value[field] += delta
            active = cloud[1:] if sample == 3 else cloud
            db.execute('DELETE FROM session_model_usage')
            for value in [*active, *local]: insert(db, value)
            db.commit()
            child = self.cli(self.options(when))
            self.assertEqual(child.returncode, 0, 'legitimate 160+ CLOUD identities must reach journal: '+child.stderr)
            public = json.loads(self.history.read_text())
            self.assertEqual(len(json.loads(self.canonical.read_text())['rows']), len(active))
            self.assertEqual(len(public['sources']), len(cloud))
            present = {value['session_id'] for value in active}
            # Independent fixture arithmetic; never aggregate identities before comparison.
            for value in active:
                key = value['session_id']
                expected.setdefault(key, dict(zero)); statuses.setdefault(key, {})
                old = previous.get(key)
                delta = {f: value[f]-old[f] for f in fields} if old else dict(zero)
                kind = ('baseline' if key not in previous else 'reappeared' if old is None else
                        'reset' if any(v < 0 for v in delta.values()) else
                        'gap' if sample == 4 else 'measured_zero' if not sum(delta[f] for f in fields[:4]) else 'delta')
                statuses[key][kind] = statuses[key].get(kind, 0)+1
                if kind in ('delta','measured_zero','gap'):
                    for f in fields: expected[key][f] += delta[f]
            for key in set(previous)-present:
                if previous[key] is not None: statuses[key]['missing'] = statuses[key].get('missing',0)+1
            previous = {**{key:None for key in previous}, **{v['session_id']:deepcopy(v) for v in active}}
            # Pin the deployed feed's two existing hashes, without inventing a
            # profile/domain prefix that would reidentify every retained source.
            for value in cloud:
                raw_key = [value[k] for k in adapter.KEY_FIELDS]
                inner = hashlib.sha256(json.dumps(raw_key,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                identity = [inner,'canonical-route',value['billing_provider'],'','','']
                sid = hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                source = next(s for s in public['sources'] if s['id'] == sid)
                counted = dict(expected[value['session_id']])
                counted.update(prompt_tokens=sum(counted[f] for f in ('input_tokens','cache_read_tokens','cache_write_tokens')),
                               total_tokens=sum(counted[f] for f in fields[:4]))
                self.assertEqual(source['coverage']['observed'], counted)
                self.assertEqual(source['coverage']['events'], {k:statuses[value['session_id']].get(k,0)
                                 for k in ('baseline','delta','measured_zero','reset','missing','reappeared','gap')})
                self.assertEqual(source['attribution'], 'unattributed')
                self.assertEqual(source['present'], value['session_id'] in present)
            provider_totals = {}
            for value in active:
                provider = value['billing_provider']
                provider_totals[provider] = provider_totals.get(provider,0)+sum(value[f] for f in fields[:4])
            cumulative = json.loads(self.usage.read_text())['sources']
            self.assertEqual({s['provider']:s['windows']['history'] for s in cumulative}, provider_totals)
            self.assertTrue(all(s['attribution']=='unattributed' and s['windows']['today'] is None
                                and s['windows']['week'] is None for s in cumulative))
            for path in (self.canonical,self.state,self.history,self.usage):
                self.assertEqual(path.stat().st_mode & 0o777,0o600)
                self.assertNotIn('PRIVATE_',path.read_text())
            consumer = FEED.with_name('history_consumer.js')
            program = consumer.read_text()+'''\nconst text=require('fs').readFileSync(0,'utf8');
const report=parseHistory(text);const view=selectHistory(report,{sources:report.data?report.data.sources.map(s=>s.id):[]});
console.log(JSON.stringify({state:report.state,sources:view.sources.length,selected:view.selectedSources.length,error:view.selectionError,summaries:report.summaries}));'''
            node = shutil.which('node'); self.assertIsNotNone(node)
            consumed = subprocess.run([node,'-e',program],input=self.history.read_text(),text=True,
                                      capture_output=True,timeout=10)
            self.assertEqual(consumed.returncode,0,consumed.stderr)
            parsed = json.loads(consumed.stdout)
            self.assertEqual(parsed['state'],'baseline' if sample == 0 else 'observed')
            self.assertEqual(parsed['selected'],len(cloud)); self.assertFalse(parsed['error'])
            if sample == 0: self.assertTrue(all(s['value'] is None for s in parsed['summaries']))
            outputs.append({'at':when,'cloud_active':len(active),'cloud_lifetime':len(cloud),
                            'excluded_local':len(local),'provider_totals':provider_totals,
                            'observed':public['coverage']['observed'],'consumer':parsed,
                            'output_bytes':{p.name:p.stat().st_size for p in (self.canonical,self.state,self.history,self.usage)}})
        self.assertGreater(public['coverage']['prunedEvents'],0)
        self.assertGreater(public['coverage']['unallocated']['day']['total_tokens'],0)
        print('CAPACITY_REPRESENTATIVE_PROOF='+json.dumps({'observations':outputs,
              'scope':'six real CLI observations; 160 initial/161 lifetime distinct CLOUD; 37 Local excluded; all per-ID counters/events checked'},sort_keys=True))

    def test_full_identity_capacity_sustained_serialized_cli_consumer(self):
        import shutil
        db = self.database(); db.execute('DELETE FROM session_model_usage')
        unit = 10**11
        cloud = [row(session_id='PRIVATE_MAX_'+str(i), input_tokens=unit, output_tokens=unit,
                     cache_read_tokens=unit, cache_write_tokens=unit, reasoning_tokens=unit,
                     api_call_count=unit) for i in range(512)]
        times = ['2023-01-02T00:00:00Z','2023-01-02T00:05:00Z',
                 '2024-02-05T00:00:00Z','2024-02-05T00:05:00Z']
        for sample, when in enumerate(times):
            if sample:
                for value in cloud:
                    for field in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens',
                                  'reasoning_tokens','api_call_count'): value[field] += unit
            db.execute('DELETE FROM session_model_usage')
            for value in cloud: insert(db,value)
            db.commit()
            result = self.cli(self.options(when))
            if result.returncode:
                args = self.arguments(); args.observed_at = when
                try: load().run(args)
                except ValueError as error:
                    print('CAPACITY_ADMISSION_FAILURE_PROOF='+json.dumps({'observation':sample,'reason':str(error)}))
            self.assertEqual(result.returncode,0,'full 512-identity legitimate sustained history: '+result.stderr)
        history = json.loads(self.history.read_text()); state = json.loads(self.state.read_text())
        self.assertEqual(len(state['sources']),512)
        self.assertEqual(sum(len(b['sources']) for bs in state['buckets'].values() for b in bs),4096)
        self.assertEqual(len(state['events']),512)
        self.assertEqual(history['coverage']['observed']['total_tokens'],512*3*4*unit)
        for grain in ('day','week','month','year'):
            self.assertEqual(history['coverage']['pruned'][grain]['total_tokens'],0,
                             'both recorded buckets remain inside each grain retention')
            self.assertEqual(history['coverage']['unallocated'][grain]['total_tokens'],512*4*unit)
        self.assertGreater(self.history.stat().st_size,2*1024*1024,'legitimate wire fixture must discriminate old reader bound')
        self.assertGreater(self.state.stat().st_size,2*1024*1024,'legitimate journal must discriminate old state/target bound')
        consumer = FEED.with_name('history_consumer.js')
        program=consumer.read_text()+'''\nconst r=parseHistory(require('fs').readFileSync(0,'utf8'));
const v=selectHistory(r,{sources:r.data?r.data.sources.map(s=>s.id):[]});
console.log(JSON.stringify({state:r.state,selected:v.selectedSources.length,error:v.selectionError,summaries:r.summaries}));'''
        node=shutil.which('node'); assert node
        result=subprocess.run([node,'-e',program],input=self.history.read_text(),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        parsed=json.loads(result.stdout)
        self.assertEqual(parsed['state'],'observed');self.assertEqual(parsed['selected'],512)
        self.assertFalse(parsed['error']);self.assertEqual([s['value'] for s in parsed['summaries']],[512*4*unit]*4)
        before={p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)}
        again=self.cli(self.options(times[-1]));self.assertEqual(again.returncode,0,again.stderr)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)
        insert(db,row(session_id='PRIVATE_MAX_OVER'));db.commit()
        refused=self.cli(self.options('2024-02-05T00:10:00Z'))
        self.assertEqual(refused.returncode,2)
        self.assertEqual({p.name:p.read_bytes() for p in (self.canonical,self.state,self.history,self.usage)},before)
        print('CAPACITY_MAX_PROOF='+json.dumps({'identities':512,'rejected':513,'retained_cells':4096,'retained_events':512,
            'output_bytes':{name:len(data) for name,data in before.items()},'consumer':parsed,
            'cli_exits':[c['exit'] for c in self.calls],'last_good_preserved':True},sort_keys=True))

    def test_identity_lifetime_and_counter_overflow_preserve_last_good(self):
        db=self.database(); db.execute('DELETE FROM session_model_usage')
        feed=load(); adapter=feed.adapter_module(); maximum=adapter.MAX_HISTORY_SOURCES
        for i in range(maximum): insert(db,row(session_id='PRIVATE_LIFETIME_'+str(i)))
        db.commit(); self.assertEqual(self.cli().returncode,0)
        # Establish measured-zero facts, not just discardable undated baselines.
        self.assertEqual(self.cli(self.options('2024-03-04T10:05:00Z')).returncode,0)
        retained=json.loads(self.state.read_text())
        self.assertEqual(sum(len(b['sources']) for bs in retained['buckets'].values() for b in bs),2048)
        files=(self.canonical,self.state,self.history,self.usage)
        before={p.name:p.read_bytes() for p in files}
        db.execute('DELETE FROM session_model_usage')
        for i in range(maximum-1): insert(db,row(session_id='PRIVATE_LIFETIME_'+str(i)))
        insert(db,row(session_id='PRIVATE_REPLACEMENT'))
        db.commit()
        args=self.arguments(); args.observed_at='2024-03-04T10:10:00Z'
        with self.assertRaisesRegex(ValueError,'retained history source capacity reached'): feed.run(args)
        self.assertEqual(self.cli(self.options(args.observed_at)).returncode,2)
        self.assertEqual({p.name:p.read_bytes() for p in files},before)
        db.execute('DELETE FROM session_model_usage')
        # Both individual-vector and provider-sum overflow remain hard failures.
        for case, values in [('vector',[row(input_tokens=adapter.MAX_SAFE)]),
                             ('sum',[row(session_id=str(i),input_tokens=adapter.MAX_SAFE//2+1,
                                  output_tokens=0,cache_read_tokens=0,cache_write_tokens=0,reasoning_tokens=0)
                                     for i in range(2)])]:
            for value in values: insert(db,value)
            db.commit()
            with self.subTest(case=case):
                self.assertEqual(self.cli(self.options(args.observed_at)).returncode,2)
                self.assertEqual({p.name:p.read_bytes() for p in files},before)
            db.execute('DELETE FROM session_model_usage')
        # Persisted source capacity, including absent IDs, is independently guarded.
        state=json.loads(self.state.read_text())
        extra=dict(next(iter(state['sources'].values())),id='f'*64)
        state['sources']['f'*64]=extra
        with self.assertRaisesRegex(ValueError,'too many persisted sources'):
            adapter.history_module().validate_state(state)
        print('CAPACITY_FAILURE_PROOF='+json.dumps({'active_bound':maximum,'retained_union':maximum+1,
            'counter_overflows':['individual','provider sum'],'persisted_bound':maximum,
            'last_good_sha256':{name:hashlib.sha256(data).hexdigest() for name,data in before.items()},
            'cli_exits':[call['exit'] for call in self.calls]},sort_keys=True))

    def test_unreferenced_identity_retires_through_actual_cli_without_backfill(self):
        db=self.database();db.execute('DELETE FROM session_model_usage')
        for i in range(512):insert(db,row(session_id='PRIVATE_RETIRE_'+str(i)))
        db.commit();self.assertEqual(self.cli().returncode,0)
        initial=json.loads(self.state.read_text())
        self.assertNotIn('retired',initial)
        db.execute('DELETE FROM session_model_usage WHERE session_id=?',('PRIVATE_RETIRE_511',))
        insert(db,row(session_id='PRIVATE_RETIRE_NEW'));db.commit()
        admitted=self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(admitted.returncode,0,admitted.stderr)
        after=json.loads(self.state.read_text())
        self.assertEqual(len(after['sources']),512)
        self.assertEqual(len(set(initial['sources'])-set(after['sources'])),1)
        self.assertEqual(len(set(after['sources'])-set(initial['sources'])),1)
        self.assertEqual(after['retired']['sources'],1)
        self.assertEqual(after['coverage']['events']['baseline'],513)
        self.assertEqual(after['coverage']['observed']['total_tokens'],0)
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'],0)

    def test_exact_selected_byte_cap_then_one_more_preserves_last_good(self):
        db=self.database(); feed=load(); adapter=feed.adapter_module()
        self.assertEqual(adapter.MAX_INPUT,16*1024*1024)
        self.assertEqual(self.cli().returncode,0)
        # SQLite REAL affinity is represented exactly as acquisition will encode it.
        selected=lambda value: {key:(float(value[key]) if key in ('first_seen','last_seen') else value[key])
                                for key in adapter.COLUMNS}
        encoded=lambda value: len(json.dumps(selected(value),separators=(',',':'),allow_nan=False).encode())
        used=encoded(row())
        batch=[]
        while True:
            value=row(session_id='PRIVATE_BYTES_'+str(len(batch)),model='x'*2048,
                      billing_base_url='http://localhost:8000/v1',billing_mode='x'*2048,task='x'*2048)
            if used+encoded(value)+1024 > adapter.MAX_INPUT: break
            batch.append(value);used+=encoded(value)
        last=row(session_id='PRIVATE_BYTES_TAIL',model='x',billing_base_url='http://localhost:8000/v1',
                 billing_mode='',task='')
        remaining=adapter.MAX_INPUT-used-encoded(last)
        self.assertGreaterEqual(remaining,0)
        for key in ('task','billing_mode','model'):
            padding=min(2048-len(last[key]),remaining)
            last[key]+='x'*padding; remaining-=padding
        # A small empty-identity row consumes the remainder if the tail exceeds three fields.
        self.assertEqual(remaining,0)
        self.assertEqual(used+encoded(last),adapter.MAX_INPUT)
        for value in [*batch,last]: insert(db,value)
        db.commit()
        exact=self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(exact.returncode,0,exact.stderr)
        files=(self.canonical,self.state,self.history,self.usage)
        before={p.name:p.read_bytes() for p in files}
        key=next(key for key in ('model','task','billing_mode') if len(last[key])<2048)
        db.execute('UPDATE session_model_usage SET '+key+'=? WHERE session_id=?',
                   (last[key]+'x',last['session_id']));db.commit()
        result=self.cli(self.options('2024-03-04T10:20:00Z'))
        self.assertEqual(result.returncode,2)
        self.assertEqual({p.name:p.read_bytes() for p in files},before)
        print('CAPACITY_SELECTED_BYTES_PROOF='+json.dumps({'exact':adapter.MAX_INPUT,
            'refused':adapter.MAX_INPUT+1,'scanned_rows':len(batch)+2,'cloud_identities':1,
            'local_rows_count_toward_bytes':True,'last_good_preserved':True,
            'cli_exits':[call['exit'] for call in self.calls]},sort_keys=True))

    def test_many_identity_publication_failures_and_real_cli_recovery(self):
        db=self.database();db.execute('DELETE FROM session_model_usage')
        for i in range(160): insert(db,row(session_id='PRIVATE_RECOVERY_'+str(i)))
        db.commit();self.assertEqual(self.cli().returncode,0)
        feed=load();adapter=feed.adapter_module();args=self.arguments()
        files=(self.canonical,self.state,self.history,self.usage)
        before={p.name:p.read_bytes() for p in files}
        db.execute('UPDATE session_model_usage SET input_tokens=input_tokens+10');db.commit()
        args.observed_at='2024-03-04T10:10:00Z'
        publish=adapter.publish
        with patch.object(feed,'adapter_module',return_value=adapter):
            for boundary in (self.canonical,self.state):
                def fail(record,path,**kwargs):
                    if Path(path)==boundary: raise OSError('synthetic named precommit failure')
                    return publish(record,path,**kwargs)
                with patch.object(adapter,'publish',side_effect=fail),self.assertRaises(OSError): feed.run(args)
                self.assertEqual({p.name:p.read_bytes() for p in files[1:]},
                                 {p.name:before[p.name] for p in files[1:]})
                if boundary==self.canonical: self.assertEqual(self.canonical.read_bytes(),before[self.canonical.name])
            # Retry the saved sample once, then fail only its public history export.
            def fail_history(record,path,**kwargs):
                if Path(path)==self.history: raise OSError('synthetic named postcommit failure')
                return publish(record,path,**kwargs)
            with patch.object(adapter,'publish',side_effect=fail_history),self.assertRaises(OSError): feed.run(args)
        journal=json.loads(self.state.read_text())
        self.assertEqual(journal['generation'],2)
        self.assertEqual(journal['coverage']['observed']['total_tokens'],160*10)
        self.assertEqual(self.history.read_bytes(),before[self.history.name])
        recovery=['--recover-history','--history-state',str(self.state),'--history-output',str(self.history),
                  '--output',str(self.usage)]
        recovered=self.cli(recovery,script=FEED.with_name('hermes_accounting.py'))
        self.assertEqual(recovered.returncode,0,recovered.stderr)
        good={p.name:p.read_bytes() for p in files}
        self.assertEqual(json.loads(self.history.read_text())['coverage']['observed']['total_tokens'],160*10)
        self.assertEqual(self.cli(self.options(args.observed_at)).returncode,0)
        self.assertEqual({p.name:p.read_bytes() for p in files},good)
        print('CAPACITY_RECOVERY_PROOF='+json.dumps({'cloud_identities':160,'injected_named_boundaries':
            ['canonical precommit','state precommit','history postcommit'],'real_recovery_cli_exit':recovered.returncode,
            'generation':2,'observed':160*10,'same_instant_retry_unchanged':True,
            'cli_exits':[call['exit'] for call in self.calls]},sort_keys=True))

    def test_wal_pipeline(self):
        self.assertTrue(FEED.is_file(), 'explicit canonical SQLite feed is missing')
        db = self.database(wal=True)
        insert(db, row(session_id='PRIVATE_LOCAL', billing_base_url='http://localhost:8000/v1', input_tokens=700))
        insert(db, row(session_id='PRIVATE_AUX', task='PRIVATE_AUX_TASK', input_tokens=10,
                       output_tokens=2, cache_read_tokens=3, cache_write_tokens=1, reasoning_tokens=1))
        db.commit()
        first = self.cli(); self.assertEqual(first.returncode, 0, first.stderr)
        baseline = json.loads(self.history.read_text())
        self.assertEqual(baseline['coverage']['status'], 'baseline-only')
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows'],
                         {'today':None,'week':None,'history':171})
        db.execute("UPDATE session_model_usage SET input_tokens=input_tokens+10, output_tokens=output_tokens+4, "
                   "cache_read_tokens=cache_read_tokens+3, cache_write_tokens=cache_write_tokens+2, "
                   "reasoning_tokens=reasoning_tokens+1, api_call_count=api_call_count+1 WHERE session_id='PRIVATE_SESSION'")
        db.commit()
        later = self.cli(self.options('2024-03-04T10:10:00Z'))
        self.assertEqual(later.returncode, 0, later.stderr)
        history = json.loads(self.history.read_text())
        self.assertEqual(history['coverage']['observed'], dict(input_tokens=10,output_tokens=4,
            cache_read_tokens=3,cache_write_tokens=2,reasoning_tokens=1,api_call_count=1,prompt_tokens=15,total_tokens=19))
        self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows']['history'],190)
        for path in (self.canonical,self.state,self.history,self.usage):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            for secret in ('PRIVATE_SESSION','PRIVATE_MODEL','PRIVATE_TASK','PRIVATE_LOCAL','PRIVATE_AUX',
                           'localhost','api.openai.com','PRIVATE_PROMPT','PRIVATE_MESSAGE'):
                self.assertNotIn(secret,path.read_text())
        canonical = json.loads(self.canonical.read_text())
        self.assertEqual(canonical['contract'],'hermes-session-model-usage/1')
        self.assertEqual(canonical['counterSemantics'],'hermes-canonical/1')
        self.assertEqual(len(canonical['rows']),2)
        print('CANONICAL_PIPELINE_PROOF='+json.dumps({'calls':self.calls,'history':history,
            'usage':json.loads(self.usage.read_text()),'hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.canonical,self.state,self.history,self.usage)}, 'synthetic_wal':True},sort_keys=True))

class CloudEndpointFeedTests(unittest.TestCase):
    """Unsupported IP endpoints must be filtered before endpoint provenance is erased."""
    setUp = FeedTests.setUp
    tearDown = FeedTests.tearDown
    database = FeedTests.database
    options = FeedTests.options
    cli = FeedTests.cli

    def test_supported_hosts_only_reach_canonical_history_rollup_and_consumer(self):
        import datetime
        import time
        db = self.database()
        db.execute('UPDATE session_model_usage SET api_call_count=0,input_tokens=0,output_tokens=0,'
                   'cache_read_tokens=0,cache_write_tokens=0,reasoning_tokens=0')
        zero = dict(api_call_count=0, input_tokens=0, output_tokens=0, cache_read_tokens=0,
                    cache_write_tokens=0, reasoning_tokens=0)
        insert(db, row(model='PRIVATE_DEFAULT', billing_base_url='', **zero))
        for index, route in enumerate(('https://8.8.8.8/v1', 'https://[2606:4700:4700::1111]/v1',
                                       'https://[::ffff:8.8.8.8]/v1')):
            insert(db, row(model='PRIVATE_IP_'+str(index), billing_base_url=route, **zero))
        db.commit()
        before = self.dbpath.read_bytes()
        first = self.cli(self.options('2024-03-04T10:00:00Z'))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.dbpath.read_bytes(), before)
        baseline_ids = [s['id'] for s in json.loads(self.history.read_text())['sources']]
        with self.subTest(boundary='baseline_canonical'):
            self.assertEqual(len(json.loads(self.canonical.read_text())['rows']), 2)
        db.execute('UPDATE session_model_usage SET input_tokens=17,api_call_count=2 WHERE model=?', ('PRIVATE_MODEL',))
        db.execute('UPDATE session_model_usage SET input_tokens=19,api_call_count=3 WHERE model=?', ('PRIVATE_DEFAULT',))
        db.execute('UPDATE session_model_usage SET input_tokens=31,api_call_count=7 WHERE model LIKE ?', ('PRIVATE_IP_%',))
        db.commit()
        before = self.dbpath.read_bytes()
        second = self.cli(self.options('2024-03-04T10:01:00Z'))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.dbpath.read_bytes(), before, 'the source ledger must remain untouched')
        history = json.loads(self.history.read_text())
        with self.subTest(boundary='canonical_and_cumulative'):
            self.assertEqual(len(json.loads(self.canonical.read_text())['rows']), 2)
            self.assertEqual(json.loads(self.usage.read_text())['sources'][0]['windows']['history'], 36)
        with self.subTest(boundary='dated_history'):
            self.assertEqual(len(history['sources']), 2)
            self.assertEqual(history['coverage']['observed']['total_tokens'], 36)
            self.assertEqual(history['coverage']['observed']['api_call_count'], 5)
        self.assertEqual([s['id'] for s in history['sources']], baseline_ids,
                         'supported source identities must not change when counters increase')
        # Load the existing real Node consumer helper without leaking its test TZ.
        try:
            with patch.dict(os.environ, {}):
                helpers = load(ROOT/'extras/local-router-stats/test_unified_rollup.py')
                os.environ['TZ'] = 'UTC'; time.tzset()
                now = datetime.datetime.fromisoformat('2024-03-04T10:02:00+00:00').timestamp()
                ledger = helpers.collector.Ledger(self.root/'rollup.sqlite3')
                self.addCleanup(ledger.db.close)
                self.assertEqual(ledger.observe('synthetic-local', 'worker', (0, 0), now-1), (0, 0))
                self.assertEqual(ledger.observe('synthetic-local', 'worker', (1, 0), now), (1, 0))
                output = self.root/'rollup.json'
                publisher = helpers.collector.RollupPublisher(ledger, self.history, output, collecting=False)
                publisher.tick(now)
                self.assertIsNone(publisher.failure)
                self.assertEqual(publisher.feed['state'], 'admitted', 'valid official/default routes must reach publication')
                record = json.loads(output.read_text())
                cloud = next(s['id'] for s in record['sources'] if s['kind'] == 'cloud')
                result = helpers.run_node('''
                    const report = H.parseRollup(payload.text);
                    out.state = report.state; out.tokens = []; out.calls = [];
                    for (const grain of ['day', 'week', 'month', 'year']) {
                        const opts = {sources:[payload.cloud], grain};
                        const tokens = H.selectHistory(report, {...opts, metric:'total_tokens'});
                        const calls = H.selectHistory(report, {...opts, metric:'api_call_count'});
                        if (tokens.selectionError || calls.selectionError) throw Error('selection failed');
                        out.tokens.push(tokens.bars.at(-1).value);
                        out.calls.push(calls.bars.at(-1).value);
                    }
                ''', text=output.read_text(), cloud=cloud)
                with self.subTest(boundary='rollup_and_actual_consumer'):
                    self.assertEqual(result, {'state': 'observed', 'tokens': [36]*4, 'calls': [5]*4})
                    self.assertEqual(record['days']['2024-03-04']['total'], 37,
                                     'Local stays separate and is added exactly once')
                print('ENDPOINT_PROVENANCE_PIPELINE='+json.dumps({'canonical_rows':len(json.loads(self.canonical.read_text())['rows']),
                      'observed_tokens':history['coverage']['observed']['total_tokens'],
                      'consumer':result, 'source_unchanged':self.dbpath.read_bytes()==before}, sort_keys=True))
        finally:
            time.tzset()


if __name__ == '__main__': unittest.main(verbosity=2)
