#!/usr/bin/env python3
"""Opt-in canonical SQLite feed. No Hermes imports, source discovery or credential loading.

A mode=ro transaction includes committed WAL, unlike an immutable snapshot. SQLite
may take locks and create/use WAL shared memory: this is NOT a zero-side-effect read.
"""
import argparse
from contextlib import closing, contextmanager
import fcntl
from datetime import datetime, timezone as utc_timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import sqlite3
import stat
import sys
import time


def adapter_module():
    spec = importlib.util.spec_from_file_location('owned_canonical_adapter', Path(__file__).with_name('hermes_accounting.py'))
    if spec is None or spec.loader is None:
        raise ValueError('adapter unavailable')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_schema(db, adapter):
    tables = db.execute("SELECT name,type,sql FROM sqlite_schema WHERE name IN ('schema_version','session_model_usage')").fetchall()
    if (len(tables) != 2 or any(kind != 'table' or not sql or 'VIRTUAL' in sql.upper()
                                for _, kind, sql in tables)):
        raise ValueError('expected ordinary canonical tables')
    # v32 adds runtime/worker declarations; v33 only re-tags delegate metadata
    # in sessions.model_config. Neither changes this exact usage-ledger contract.
    # Admit only reviewed versions; the exact ledger shape remains mandatory.
    if db.execute('SELECT version FROM schema_version LIMIT 2').fetchall() not in ([(31,)], [(32,)], [(33,)]):
        raise ValueError('unsupported canonical schema')
    columns = db.execute('PRAGMA table_xinfo(session_model_usage)').fetchall()
    names = [*adapter.KEY_FIELDS, 'api_call_count', *adapter.COUNTERS[:5],
             'estimated_cost_usd', 'actual_cost_usd', 'cost_status', 'cost_source', 'first_seen', 'last_seen']
    expected = []
    for index, name in enumerate(names):
        kind = ('TEXT' if name in adapter.KEY_FIELDS or name in ('cost_status','cost_source')
                else 'INTEGER' if name in adapter.COUNTERS else 'REAL')
        required = int(name not in ('cost_status','cost_source','first_seen','last_seen'))
        pk = index + 1 if name in adapter.KEY_FIELDS else 0
        expected.append((name,kind,required,pk,0))
    if [(r[1],r[2].upper(),r[3],r[5],r[6]) for r in columns] != expected:
        raise ValueError('canonical ledger shape drifted')


def authorize(adapter):
    """Deny every SQL capability except the fixed read/metadata/transaction set."""
    readable = {'session_model_usage':set(adapter.COLUMNS), 'schema_version':{'version'},
                'sqlite_master':{'name','type','sql'}}
    def check(action, first, second, database, trigger):
        if trigger is not None or database not in (None,'main'):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT: return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and second in readable.get(first,set()): return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_TRANSACTION and first in ('BEGIN','ROLLBACK'): return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and (first,second) in (
                ('table_xinfo','session_model_usage'),('journal_mode',None)):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return check


# The main file also holds unrelated transcripts/free pages; it is never loaded
# wholesale. Keep a finite ceiling while selected rows/cache/memory stay bounded.
MAX_DB_BYTES = 1024 * 1024 * 1024
# Only an explicit --max-source-bytes raises that main-file ceiling, never past this
# hard maximum; sidecar and acquisition bounds are not part of the opt-in.
MAX_DB_BYTES_CEILING = 4 * 1024 * 1024 * 1024
MAX_SIDECAR_BYTES = 128 * 1024 * 1024
SIDECARS = ('-wal', '-shm', '-journal')


def acquire(path, adapter, profile, *, limit=MAX_DB_BYTES):
    """Fence source identity with an owned no-follow descriptor around SQLite's read."""
    path = Path(path)
    identity = source_identity(source_stat(path, adapter, limit=limit))
    with adapter.controlled_directory(path) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            held = os.fstat(fd)
            if held.st_size > limit or source_identity(held) != identity:
                raise ValueError('source changed before SQLite open')
            for suffix in SIDECARS:
                source_stat(Path(str(path)+suffix), adapter, optional=True, limit=MAX_SIDECAR_BYTES)
            rows = _read_ledger(path, adapter, profile)
            if source_identity(source_stat(path, adapter, limit=limit)) != identity:
                raise ValueError('source changed while SQLite read was open')
            held = os.fstat(fd)
            if held.st_size > limit or source_identity(held) != identity:
                raise ValueError('source changed while SQLite read was open')
            return rows
        finally:
            os.close(fd)


def _read_ledger(path, adapter, profile):
    """Single committed view; no backup/copy, runtime constructor, migration or retry."""
    deadline = time.monotonic() + 3
    def within_deadline():
        if time.monotonic() > deadline: raise TimeoutError('canonical acquisition deadline')
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=0.1)) as db:
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 8192)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA cache_size=-2048')
        db.execute('PRAGMA mmap_size=0')
        db.execute('PRAGMA temp_store=MEMORY')
        db.set_authorizer(authorize(adapter))
        db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
        within_deadline()
        db.execute('BEGIN')
        validate_schema(db, adapter)
        if db.execute('PRAGMA journal_mode').fetchone()[0] not in ('wal','delete'):
            raise ValueError('unsupported journal mode')
        db.row_factory = sqlite3.Row
        normalized, size, count = [], 0, 0
        cursor = db.execute('SELECT ' + ','.join(adapter.COLUMNS) + ' FROM session_model_usage')
        for value in cursor:
            within_deadline()
            row = dict(value); count += 1
            # Validate types before JSON encoding (SQLite may return BLOBs).
            adapter.project([row], profile)
            size += len(json.dumps(row, separators=(',', ':'), allow_nan=False).encode())
            if count > adapter.MAX_ROWS or size > adapter.MAX_INPUT:
                raise ValueError('canonical rows or bytes exceeded bound')
            provider = adapter.admitted_provider(row['billing_provider'], row['billing_base_url'])
            if provider is None: continue
            if len(normalized) >= adapter.MAX_HISTORY_SOURCES:
                raise ValueError('too many canonical history sources')
            normalized.append(dict(session_id=digest([row[k] for k in adapter.KEY_FIELDS]),
                model='canonical-route', billing_provider=provider, billing_base_url='',
                billing_mode='', task='', first_seen=row['first_seen'], last_seen=row['last_seen'],
                **{k:row[k] for k in adapter.COUNTERS}))
        adapter.canonical_snapshot(normalized, profile)
        within_deadline()
        db.rollback()
        return normalized


def size_limit(limit):
    """An exact positive int within the hard maximum: never bool, float, text or None/unlimited."""
    if type(limit) is not int or not 0 < limit <= MAX_DB_BYTES_CEILING:
        raise ValueError('source size ceiling must be a positive integer within the hard maximum')
    return limit


def source_limit(args):
    """The explicitly selected main-file ceiling, else the unchanged default."""
    return size_limit(getattr(args, 'max_source_bytes', MAX_DB_BYTES))


def source_stat(path, adapter, *, optional=False, limit=MAX_DB_BYTES):
    size_limit(limit)
    with adapter.controlled_directory(path):
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            if optional: return None
            raise
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError('source must be bounded private singly-linked owned regular storage')
        if info.st_size > limit:
            raise ValueError('canonical sidecar byte bound reached' if optional
                             else 'canonical source byte bound reached')
        return info


def source_identity(info):
    # Live counters/WAL may advance; mtime/size are deliberately not an identity.
    # Same-process fence only: Btrfs st_dev is an anonymous per-mount number.
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink)


# Persistent association identity. statvfs f_fsid is UUID-derived (and per
# subvolume on Btrfs), so it survives reboots; it never identifies a file alone.
SOURCE_IDENTITY = 'hermes-canonical-source/2'


def stable_identity(path, adapter, identity):
    """Boot-stable source identity, read from a no-follow descriptor of `identity`."""
    with adapter.controlled_directory(path) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info, fsid = os.fstat(fd), os.fstatvfs(fd).f_fsid
        finally:
            os.close(fd)
    # Zero/absent f_fsid cannot establish an identity: refuse, never fall back to st_dev.
    if source_identity(info) != identity or type(fsid) is not int or not 0 < fsid < 1 << 64:
        raise ValueError('stable source identity unavailable')
    return {'scheme':SOURCE_IDENTITY, 'digest':digest([SOURCE_IDENTITY, str(path), fsid, *identity[1:]])}


def source_association(previous, stable, legacy):
    """Verified source fields for the next record; refuses any changed or unknown identity."""
    if previous is None:
        return {'sourceBinding':stable['digest'], 'sourceIdentity':stable}
    token = previous.get('sourceBinding')
    if type(token) is not str or len(token) != 64 or token.strip('0123456789abcdef'):
        raise ValueError('malformed source association')
    if 'sourceIdentity' in previous:
        if previous['sourceIdentity'] != stable:
            raise ValueError('source association changed')
    elif token != legacy:
        # A legacy digest includes st_dev: upgrade only an exact current match,
        # never by guessing a former device number.
        raise ValueError('legacy source association differs')
    # The association token is unchanged across the upgrade for downstream consumers.
    return {'sourceBinding':token, 'sourceIdentity':stable}


def preflight(args, adapter):
    limit = source_limit(args)
    source = Path(args.source_db)
    outputs = [Path(p) for p in (args.canonical_output, args.history_state, args.history_output, args.output)]
    locks = [outputs[0].with_name(outputs[0].name+'.lock'), outputs[1].with_name(outputs[1].name+'.lock')]
    sidecars = [Path(str(source)+s) for s in SIDECARS]
    targets = [*outputs, *locks, *(adapter.temporary_path(p) for p in outputs),
               *(adapter.publication_lock_path(p) for p in outputs)]
    # All internal cleanup paths join the alias set BEFORE SQLite open/lock creation.
    adapter.reject_aliases(source, *sidecars, *targets)
    for path in targets: adapter.fingerprint(path)
    identity = source_identity(source_stat(source, adapter, limit=limit))
    for path in sidecars: source_stat(path, adapter, optional=True, limit=MAX_SIDECAR_BYTES)
    return identity, locks[0]


@contextmanager
def feed_lock(path, adapter):
    with adapter.controlled_directory(path) as directory:
        fd = os.open(path.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            info = os.fstat(fd); adapter.private_regular(info)
            if info.st_size: raise ValueError('invalid feed lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)


def run(args):
    limit = source_limit(args)
    adapter = adapter_module()
    history = adapter.history_module()
    history.label(args.profile)
    history.calendar_zone(args.timezone)
    if args.observed_at is not None: history.instant(args.observed_at)
    identity, lock = preflight(args, adapter)
    with feed_lock(lock, adapter):
        # Recheck after acquisition: another cooperating feed cannot race this sample.
        if preflight(args, adapter)[0] != identity:
            raise ValueError('source replaced before acquisition')
        source = Path(args.source_db)
        stable = stable_identity(source, adapter, identity)
        preimage = adapter.fingerprint(args.canonical_output)
        binding = {'feed':'hermes-canonical-feed/1',
                   'configBinding':digest([args.profile,args.timezone,*(str(p) for p in
                       (args.canonical_output,args.history_state,args.history_output,args.output))])}
        previous = None
        if preimage is not None:
            previous = json.loads(adapter.read_bytes(args.canonical_output, limit=adapter.MAX_OUTPUT),
                                  object_pairs_hook=adapter.unique_object)
            if (not isinstance(previous, dict) or any(previous.get(k) != v for k,v in binding.items()) or
                    previous.get('contract') != 'hermes-session-model-usage/1' or
                    previous.get('counterSemantics') != 'hermes-canonical/1' or
                    adapter.fingerprint(args.canonical_output) != preimage):
                raise ValueError('source or configuration association changed')
        elif adapter.fingerprint(args.history_state) is not None:
            raise ValueError('existing history without feed association')
        binding.update(source_association(previous, stable, digest([str(source),*identity])))
        state, state_preimage = adapter.read_history_state(history, args.history_state)
        rows = acquire(source, adapter, args.profile, limit=limit)
        if source_identity(source_stat(source, adapter, limit=limit)) != identity:
            raise ValueError('source replaced during acquisition')
        for suffix in SIDECARS:
            source_stat(Path(str(source)+suffix), adapter, optional=True, limit=MAX_SIDECAR_BYTES)
        observed_at = args.observed_at or datetime.now(utc_timezone.utc).replace(microsecond=0).isoformat()
        # All semantic input/history checks run before canonical publication.
        expected_snapshot = adapter.canonical_snapshot(rows,args.profile)
        history.observe(state, expected_snapshot, observed_at=observed_at, timezone=args.timezone)
        if adapter.fingerprint(args.history_state) != state_preimage:
            raise ValueError('history changed during acquisition')
        record = {'contract':'hermes-session-model-usage/1', 'counterSemantics':'hermes-canonical/1',
                  **binding, 'observedAt':history.stamp(history.instant(observed_at)), 'rows':rows}
        adapter.publish(record, args.canonical_output, expected=preimage)
        expected_digest = hashlib.sha256(adapter.payload_bytes(record)).hexdigest()
        if adapter.preimage_digest(adapter.fingerprint(args.canonical_output)) != expected_digest:
            raise ValueError('canonical publication readback differs')
        # Exercise the EXISTING explicit export adapter, not an alternative history writer.
        snapshot = adapter.canonical_snapshot(adapter.read_json(args.canonical_output), args.profile)
        if (snapshot != expected_snapshot or
                adapter.preimage_digest(adapter.fingerprint(args.canonical_output)) != expected_digest):
            raise ValueError('parsed canonical export differs from acquired source')
        return adapter.record_history(snapshot, state_path=args.history_state, history_output=args.history_output,
                                      output=args.output, observed_at=observed_at, timezone=args.timezone)


@contextmanager
def cli_limits():
    """Linux CLI process limits; acquisition also has connection-local bounds."""
    limits = resource.getrlimit(resource.RLIMIT_AS)
    ceiling = min(v for v in (256*1024*1024,*limits) if v != resource.RLIM_INFINITY)
    handlers = {sig:signal.getsignal(sig) for sig in (signal.SIGALRM,signal.SIGTERM)}
    def interrupted(signum, frame): raise TimeoutError('canonical feed interrupted')
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    try:
        resource.setrlimit(resource.RLIMIT_AS,(ceiling,limits[1]))
        for sig in handlers: signal.signal(sig,interrupted)
        signal.setitimer(signal.ITIMER_REAL,10)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,*previous_timer)
        for sig,handler in handlers.items(): signal.signal(sig,handler)
        resource.setrlimit(resource.RLIMIT_AS,limits)


def byte_count(text):
    """CLI ceiling: ASCII decimal digits only (no sign, separator, exponent or unit)."""
    if not (text.isascii() and text.isdigit()):
        raise ValueError('expected decimal digits')
    return size_limit(int(text))


def refusal_code(error):
    """Stable diagnostics from exception classes or exact owned messages, never payloads."""
    if isinstance(error, BlockingIOError):
        return 'E_LOCK_BUSY'
    if isinstance(error, (KeyboardInterrupt, TimeoutError)):
        return 'E_INTERRUPTED'
    if isinstance(error, sqlite3.Error):
        return 'E_SQLITE'
    if isinstance(error, OSError):
        return 'E_IO'
    if isinstance(error, MemoryError):
        return 'E_MEMORY'
    if isinstance(error, (OverflowError, RecursionError)):
        return 'E_INPUT_LIMIT'
    known = {
        'retained history source capacity reached': 'E_SOURCE_CAP',
        'too many canonical history sources': 'E_SOURCE_CAP',
        'canonical source byte bound reached': 'E_DB_SIZE',
        'canonical sidecar byte bound reached': 'E_SIDECAR_SIZE',
        'canonical rows or bytes exceeded bound': 'E_INPUT_LIMIT',
        'expected ordinary canonical tables': 'E_SCHEMA',
        'unsupported canonical schema': 'E_SCHEMA',
        'canonical ledger shape drifted': 'E_SCHEMA',
        'unsupported journal mode': 'E_SCHEMA',
        'backward or conflicting repeated observation': 'E_CLOCK_BACKWARD',
    }
    message = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
    return known.get(message, 'E_VALIDATION')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db', type=Path, required=True, help='explicit canonical SQLite source; never discovered')
    parser.add_argument('--profile', required=True, help='safe public provenance, not credential identity')
    parser.add_argument('--timezone', required=True, help='explicit IANA calendar timezone')
    parser.add_argument('--canonical-output', type=Path, required=True, help='private sanitized canonical export')
    parser.add_argument('--history-state', type=Path, required=True)
    parser.add_argument('--history-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--observed-at', help='optional explicit RFC3339 instant; otherwise UTC observation clock')
    parser.add_argument('--max-source-bytes', type=byte_count, default=MAX_DB_BYTES,
                        help='explicit opt-in main SQLite file byte ceiling (default 1 GiB, at most 4 GiB); '
                             'sidecar bounds are unchanged')
    args = parser.parse_args(argv)
    try:
        with cli_limits(): run(args)
    except (ValueError, OSError, sqlite3.Error, OverflowError, RecursionError, MemoryError, KeyboardInterrupt) as error:
        print('Canonical accounting feed refused [' + refusal_code(error)
              + ']; last-good publications retained.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__': raise SystemExit(main())
