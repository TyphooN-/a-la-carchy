#!/usr/bin/env python3
"""Explicit offline Hermes canonical counters -> private owned accounting record.

No Hermes imports, live DB discovery, credentials, network or automatic backfill.
"""
import argparse
from contextlib import closing, contextmanager
import fcntl
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from urllib.parse import urlsplit

MAX_SAFE = 9007199254740991
MAX_ROWS = 65536
MAX_SOURCES = 4096
# Canonical identities are independent baselines, not provider aggregates.
MAX_HISTORY_SOURCES = 512
MAX_INPUT = 16 * 1024 * 1024

# usage_history.MAX_STATE_BYTES: 512 retained identities x 61 dense cells
# (31,232) + 512 events have a conservative compact-JSON envelope of 15,223,710
# journal bytes (including transaction paths) and 15,297,696 public-history bytes.
# Keep private preimages/state/exports and the public consumer at the same cap.
MAX_OUTPUT = 16 * 1024 * 1024
KEY_FIELDS = ('session_id', 'model', 'billing_provider', 'billing_base_url', 'billing_mode', 'task')
COUNTERS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
            'reasoning_tokens', 'api_call_count')
COLUMNS = KEY_FIELDS + COUNTERS + ('first_seen', 'last_seen')
# Explicit supported cloud provenance, not arbitrary input labels/secrets.
CLOUD_HOSTS = {
    'openai': ('api.openai.com',), 'openai-codex': ('api.openai.com', 'chatgpt.com'),
    'anthropic': ('api.anthropic.com',), 'openrouter': ('openrouter.ai',),
    'google': ('generativelanguage.googleapis.com',), 'gemini': ('generativelanguage.googleapis.com',),
    'groq': ('api.groq.com',), 'deepseek': ('api.deepseek.com',),
    'nous': ('inference-api.nousresearch.com',), 'xai': ('api.x.ai',),
    'mistral': ('api.mistral.ai',), 'cerebras': ('api.cerebras.ai',),
    'together': ('api.together.xyz',), 'fireworks': ('api.fireworks.ai',),
}
CLOUD_PROVIDERS = frozenset(CLOUD_HOSTS)
# Hermes OAuth labels for a cloud already in CLOUD_HOSTS. A private base URL stays dropped.
PROVIDER_ALIASES = {'xai-oauth': 'xai'}


def admitted_provider(provider, base_url):
    """Public cloud provider, or None. xai-oauth counts as xAI only on that cloud route."""
    if not isinstance(provider, str):
        return None
    provider = PROVIDER_ALIASES.get(provider, provider)
    return provider if cloud_route(provider, base_url) else None



def safe_count(value):
    if type(value) is not int or not 0 <= value <= MAX_SAFE:
        raise ValueError('invalid counter')
    return value


def cloud_route(provider, base_url):
    if provider not in CLOUD_PROVIDERS:
        return False
    if base_url == '':
        return True  # Known provider default endpoint; still no credential identity.
    try:
        if any(c.isspace() for c in base_url) or '\\' in base_url:
            return False
        route = urlsplit(base_url)
        host = route.hostname
        if (route.scheme != 'https' or not host or route.username or route.password
                or route.query or route.fragment or route.port == 0):
            return False
        host = host.rstrip('.').lower()
        try:
            address = ipaddress.ip_address(host)
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            # Public routability alone cannot establish a named provider's Cloud provenance.
            return address.is_global and host in CLOUD_HOSTS.get(provider, ())
        except ValueError:
            # Do not resolve DNS. Reject ambiguous/private/local or numeric aliases.
            return (bool(re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host))
                    and not re.fullmatch(r'[0-9.]+', host)
                    and not any(host == suffix or host.endswith('.' + suffix)
                                for suffix in ('localhost', 'local', 'internal', 'lan',
                                               'home', 'test', 'invalid', 'example'))
                    and not any(part.startswith('0x') for part in host.split('.'))
                    and host in CLOUD_HOSTS.get(provider, ()))
    except ValueError:
        return False


def project(rows, profile, *, _history_sources=None):
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile):
        raise ValueError('invalid profile label')
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError('invalid rows')
    totals, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict) or any(field not in row for field in COLUMNS):
            raise ValueError('missing usage columns')
        for field in KEY_FIELDS:
            value = row[field]
            if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
                raise ValueError('invalid route identity')
        if not row['session_id'] or not row['model']:
            raise ValueError('missing usage identity')
        key = tuple(row[field] for field in KEY_FIELDS)
        if key in seen:
            raise ValueError('duplicate usage identity')
        seen.add(key)
        for field in COUNTERS:
            safe_count(row[field])
        if row['reasoning_tokens'] > row['output_tokens']:
            raise ValueError('reasoning is an output subset')
        for field in ('first_seen', 'last_seen'):
            stamp = row[field]
            if stamp is not None and (type(stamp) not in (int, float) or not math.isfinite(stamp)
                                      or not 0 <= stamp <= 253402300799):
                raise ValueError('invalid usage timestamp')
        if (row['first_seen'] is not None and row['last_seen'] is not None
                and row['first_seen'] > row['last_seen']):
            raise ValueError('reversed usage timestamps')
        # Canonical Hermes input excludes cache buckets. Reasoning is already output.
        total = safe_count(sum(row[field] for field in COUNTERS[:4]))
        provider = admitted_provider(row['billing_provider'], row['billing_base_url'])
        if provider is None:
            continue
        totals[provider] = safe_count(totals.get(provider, 0) + total)
        if _history_sources is not None:
            if len(_history_sources) >= MAX_HISTORY_SOURCES:
                raise ValueError('too many observed history sources')
            # Preserve the existing incoming KEY_FIELDS fingerprint, with no
            # profile/domain prefix. canonical_feed supplies normalized fields;
            # changing this fingerprint would invalidate retained source IDs.
            identity = json.dumps(list(key), sort_keys=True, separators=(',', ':'), allow_nan=False)
            counts = {field: row[field] for field in COUNTERS}
            counts.update(prompt_tokens=safe_count(sum(row[field] for field in
                                                      ('input_tokens', 'cache_read_tokens', 'cache_write_tokens'))),
                          total_tokens=total)
            entry = {'id': hashlib.sha256(identity.encode()).hexdigest(),
                     'provider': provider, 'profile': profile,
                     'attribution': 'unattributed', 'counts': counts}
            # Preserve optional lifetime metadata for compatibility only. It never
            # dates a baseline or participates in the stable snapshot digest.
            if row['first_seen'] is not None and row['last_seen'] is not None:
                entry['firstSeen'] = row['first_seen']
                entry['lastSeen'] = row['last_seen']
            _history_sources.append(entry)
    safe_count(sum(totals.values()))
    if len(totals) > MAX_SOURCES:
        raise ValueError('too many sources')
    sources = []
    for provider, total in sorted(totals.items()):
        identity = json.dumps(['hermes-token-accounting/1', profile, provider], separators=(',', ':'))
        sources.append({'id': hashlib.sha256(identity.encode()).hexdigest(), 'provider': provider,
                        'profile': profile, 'attribution': 'unattributed',
                        'windows': {'today': None, 'week': None, 'history': total}})
    return {'id': 'hermes-token-accounting', 'contract': 'hermes-token-accounting/1',
            'schemaVersion': 1, 'readOnly': True, 'sources': sources}


def canonical_snapshot(rows, profile):
    """Validated per-canonical-source counters; no raw identity survives."""
    sources = []
    record = project(rows, profile, _history_sources=sources)
    return {'profile': profile, 'record': record, 'sources': sorted(sources, key=lambda source: source['id'])}


def history_module():
    # Resolve only the sibling source, never an installed Hermes module.
    import importlib.util
    spec = importlib.util.spec_from_file_location('owned_usage_history', Path(__file__).with_name('usage_history.py'))
    if spec is None or spec.loader is None:
        raise ValueError('history module unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload_bytes(record):
    payload = (json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(payload) > MAX_OUTPUT:
        raise ValueError('output exceeded bound')
    return payload


@contextmanager
def controlled_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.parent.resolve(strict=True) != path.parent:
        raise ValueError('history destinations require absolute no-symlink directories')
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        details = os.fstat(directory)
        if details.st_uid != os.geteuid() or details.st_mode & 0o022:
            raise ValueError('history directory must be owned and not writable by others')
        yield directory
    finally:
        os.close(directory)


def private_regular(details):
    if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.geteuid() or
            details.st_nlink != 1 or details.st_mode & 0o077 or details.st_size > MAX_OUTPUT):
        raise ValueError('history target must be a bounded private singly-linked owned regular file')


def fingerprint(path):
    """Bounded private preimage, including inode/metadata for in-process race checks."""
    path = Path(path)
    with controlled_directory(path) as directory:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return None
        try:
            before = os.fstat(fd)
            private_regular(before)
            with os.fdopen(os.dup(fd), 'rb') as stream:
                data = stream.read(MAX_OUTPUT + 1)
            after = os.fstat(fd)
            target = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink', 'st_uid')
            if (len(data) != before.st_size or any(getattr(before, field) != getattr(after, field) or
                                                 getattr(after, field) != getattr(target, field) for field in fields)):
                raise ValueError('history target preimage changed')
            return dict(sha256=hashlib.sha256(data).hexdigest(), **{field: getattr(after, field) for field in fields})
        finally:
            os.close(fd)


def preimage_digest(preimage):
    return preimage['sha256'] if preimage is not None else None


def temporary_path(output):
    output = Path(output)
    name = '.hermes-accounting-' + hashlib.sha256(str(output).encode()).hexdigest()[:24] + '.tmp'
    return output.with_name(name)


def publication_lock_path(output):
    output = Path(output)
    name = '.hermes-publish-' + hashlib.sha256(str(output).encode()).hexdigest()[:24] + '.lock'
    return output.with_name(name)


@contextmanager
def publication_lock(output):
    lock = publication_lock_path(output)
    with controlled_directory(lock) as directory:
        fd = os.open(lock.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            details = os.fstat(fd)
            private_regular(details)
            if details.st_size:
                raise ValueError('invalid publication lock file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = os.stat(lock.name, dir_fd=directory, follow_symlinks=False)
            private_regular(current)
            if current.st_size or any(getattr(details, field) != getattr(current, field)
                                      for field in ('st_dev', 'st_ino', 'st_mode', 'st_size',
                                                    'st_mtime_ns', 'st_ctime_ns', 'st_nlink', 'st_uid')):
                raise ValueError('publication lock changed')
            yield directory, fd
        finally:
            os.close(fd)


def sync_private(path):
    path = Path(path)
    with controlled_directory(path) as directory:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            private_regular(os.fstat(fd))
            os.fsync(fd)
            os.fsync(directory)
        finally:
            os.close(fd)


def reject_aliases(*paths):
    resolved, identities = set(), set()
    for path in paths:
        path = Path(path)
        name = path.resolve()
        if name in resolved:
            raise ValueError('input, state and exports must differ')
        resolved.add(name)
        try:
            details = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        identity = (details.st_dev, details.st_ino)
        if identity in identities:
            raise ValueError('hardlinked input/state/export aliases')
        identities.add(identity)


def storage_paths(state_path, history_output, output, *, input_path=None):
    paths = {'state': Path(state_path), 'history': Path(history_output), 'accounting': Path(output)}
    lock = paths['state'].with_name(paths['state'].name + '.lock')
    targets = [*paths.values(), lock, *(temporary_path(path) for path in paths.values()),
               *(publication_lock_path(path) for path in paths.values())]
    if input_path is not None:
        targets.append(input_path)
    reject_aliases(*targets)
    for path in paths.values():
        fingerprint(path)
    return paths, lock


@contextmanager
def history_lock(state_path, history_output, output):
    paths, lock = storage_paths(state_path, history_output, output)
    with controlled_directory(lock) as directory:
        fd = os.open(lock.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            details = os.fstat(fd)
            private_regular(details)
            if details.st_size:
                raise ValueError('invalid history lock file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(fd)
            os.fsync(directory)
            yield paths
        finally:
            os.close(fd)


def read_history_state(history, path):
    preimage = fingerprint(path)
    if preimage is None:
        return None, None
    state = json.loads(read_bytes(path, limit=history.MAX_STATE_BYTES), object_pairs_hook=unique_object)
    history.validate_state(state)
    if 'transaction' not in state or fingerprint(path) != preimage:
        raise ValueError('invalid or changing durable history journal')
    return state, preimage


def export_records(history, state):
    return {'accounting': history.accounting_record(state['sources'].values(), state['profile']),
            'history': history.project_history(state)}


def recover_exports(history, state, paths):
    transaction = state.get('transaction')
    if not isinstance(transaction, dict):
        raise ValueError('no committed export transaction')
    records, preimages = export_records(history, state), {}
    for key, record in records.items():
        target = transaction['targets'][key]
        expected = hashlib.sha256(payload_bytes(record)).hexdigest()
        if target['path'] != str(paths[key]) or target['payload'] != expected:
            raise ValueError('recovery targets or payload changed')
        preimages[key] = fingerprint(paths[key])
        if preimage_digest(preimages[key]) not in (target['preimage'], expected):
            raise ValueError('publication preimage differs; journal retained')
    for key, record in records.items():
        expected = transaction['targets'][key]['payload']
        if preimage_digest(preimages[key]) != expected:
            publish(record, paths[key], expected=preimages[key])
        sync_private(paths[key])
        if preimage_digest(fingerprint(paths[key])) != expected:
            raise ValueError('publication readback differs; journal retained')


def recover_history(*, state_path, history_output, output):
    """Idempotently replay the last committed exports, without a new sample."""
    history = history_module()
    with history_lock(state_path, history_output, output) as paths:
        state, _ = read_history_state(history, paths['state'])
        if state is None:
            raise ValueError('no owned history journal to recover')
        recover_exports(history, state, paths)
        return state


def record_history(snapshot, *, state_path, history_output, output, observed_at, timezone):
    """Write-ahead state commit, then replayable private atomic exports.

    Pre-commit failures leave counters unadvanced: retry the same supplied
    snapshot/instant. Post-commit failures retain the delta and export preimages
    in state; recovery/retry replays exactly once before any subsequent commit.
    """
    history = history_module()
    with history_lock(state_path, history_output, output) as paths:
        state, preimage = read_history_state(history, paths['state'])
        # Validate new input/time/counts before attempting even pending exports.
        updated = history.observe(state, snapshot, observed_at=observed_at, timezone=timezone)
        records = export_records(history, updated)
        for record in records.values():
            payload_bytes(record)
        if state is not None:
            recover_exports(history, state, paths)
            if updated['generation'] == state['generation']:
                return state
        updated['transaction'] = {'generation': updated['generation'], 'targets': {
            key: {'path': str(paths[key]), 'preimage': preimage_digest(fingerprint(paths[key])),
                  'payload': hashlib.sha256(payload_bytes(record)).hexdigest()}
            for key, record in records.items()}}
        history.validate_state(updated)
        publish(updated, paths['state'], expected=preimage)
        if preimage_digest(fingerprint(paths['state'])) != hashlib.sha256(payload_bytes(updated)).hexdigest():
            raise ValueError('state commit readback differs')
        recover_exports(history, updated, paths)
        return updated


def read_bytes(path, immutable=False, *, limit=MAX_INPUT):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('input must be a bounded regular file')
        # A sidecar name is refused even as a dangling or foreign symlink: lexists, not exists.
        if immutable and (before.st_mode & 0o222 or any(
                os.path.lexists(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal'))):
            raise ValueError('snapshot must be read-only without sidecars')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        fence = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (len(data) > limit or len(data) != before.st_size
                or any(getattr(before, field) != getattr(after, field) for field in fence)):
            raise ValueError('input changed or exceeded bound')
        return data
    finally:
        os.close(fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def read_json(path):
    data = json.loads(read_bytes(path), object_pairs_hook=unique_object)
    if (not isinstance(data, dict) or data.get('contract') != 'hermes-session-model-usage/1'
            or data.get('counterSemantics') != 'hermes-canonical/1' or 'rows' not in data):
        raise ValueError('unsupported export contract')
    return data['rows']


def snapshot_authorizer():
    """Deny every SQL capability except reading the fixed schema/usage columns."""
    readable = {'session_model_usage': set(COLUMNS), 'sqlite_master': {'name', 'type', 'sql'}}

    def check(action, first, second, database, trigger):
        if trigger is not None or database not in (None, 'main'):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and second in readable.get(first, set()):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and (first, second) == ('table_xinfo', 'session_model_usage'):
            return sqlite3.SQLITE_OK
        # Functions, other pragmas, attach, writes and transactions are outside the
        # offline snapshot contract. The authorizer does not see generated-column
        # expressions, so read_snapshot refuses hidden/generated columns by shape.
        return sqlite3.SQLITE_DENY
    return check


def read_snapshot(path):
    """Rows of a bounded standalone offline snapshot; the pathname is never opened by SQLite.

    Bytes are read through a no-follow descriptor (refusing writable files, sidecars
    and any input over MAX_INPUT), fenced by inode/size/time before and after, then
    deserialized into a private in-memory query-only connection with an authorizer,
    a value-length limit, a progress deadline and a MAX_ROWS + 1 fetch bound.
    """
    data = read_bytes(path, immutable=True)
    if not data.startswith(b'SQLite format 3\x00') or data[18:20] != b'\x01\x01':
        raise ValueError('unsupported snapshot format; export a standalone rollback-mode copy')
    with closing(sqlite3.connect(':memory:')) as db:
        db.deserialize(data)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_INPUT)
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
        db.set_authorizer(snapshot_authorizer())
        table = db.execute("SELECT type, sql FROM sqlite_schema WHERE name='session_model_usage'").fetchall()
        if (len(table) != 1 or table[0][0] != 'table' or not table[0][1]
                or 'VIRTUAL' in table[0][1].upper()):
            raise ValueError('expected ordinary session_model_usage table')
        # Stored values are bounded by the deserialized bytes; generated columns
        # would compute values on read, so they are refused rather than evaluated.
        if any(column[6] for column in db.execute('PRAGMA table_xinfo(session_model_usage)')):
            raise ValueError('generated or hidden snapshot columns are refused')
        db.row_factory = sqlite3.Row
        result = db.execute('SELECT ' + ','.join(COLUMNS) + ' FROM session_model_usage').fetchmany(MAX_ROWS + 1)
        if len(result) > MAX_ROWS:
            raise ValueError('too many usage rows')
        return [dict(row) for row in result]


def read_snapshot_vacuumed(path, *, vacuum_into=None):
    """Retired oversized-database reader; kept only so stale callers fail closed.

    It used to open the operator pathname through an unescaped SQLite URI, follow
    symlinks and skip the read-only/sidecar/row-bound checks. There is no pathname,
    URI or VACUUM INTO fallback: export a standalone snapshot within MAX_INPUT.
    """
    if vacuum_into is not None:
        raise ValueError('VACUUM INTO copies are not supported')
    return read_snapshot(path)


_NO_PREIMAGE = object()


def publish(record, output, *, expected=_NO_PREIMAGE):
    output = Path(output)
    payload = payload_bytes(record)
    temporary = temporary_path(output).name
    with publication_lock(output) as (directory, lock_fd):
        directory_identity = os.fstat(directory)
        lock_identity = os.fstat(lock_fd)
        try:
            target = os.stat(output.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(target.st_mode):
                raise ValueError('output target must be a regular file')
        required = fingerprint(output) if expected is _NO_PREIMAGE else expected
        if fingerprint(output) != required:
            raise ValueError('publication preimage changed')
        # All entrypoints use this target's one reserved private temporary. The
        # shared target lock prevents reclaiming a cooperating active writer.
        if fingerprint(output.parent / temporary) is not None:
            os.unlink(temporary, dir_fd=directory)
        owned = None
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as stream:
                details = os.fstat(stream.fileno())
                private_regular(details)
                owned = (details.st_dev, details.st_ino)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != owned:
                owned = None
                raise ValueError('publication temporary changed')
            current_directory = os.stat(output.parent, follow_symlinks=False)
            current_lock = os.stat(publication_lock_path(output).name, dir_fd=directory, follow_symlinks=False)
            lock_fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink', 'st_uid')
            if (any(getattr(current_lock, field) != getattr(lock_identity, field) for field in lock_fields)
                    or (current_directory.st_dev, current_directory.st_ino) !=
                       (directory_identity.st_dev, directory_identity.st_ino)
                    or fingerprint(output) != required):
                raise ValueError('publication directory, lock or preimage changed')
            # Advisory cooperation plus observed preimage pins, not atomic CAS
            # against non-cooperating same-UID pathname replacement.
            os.replace(temporary, output.name, src_dir_fd=directory, dst_dir_fd=directory)
            owned = None
            os.fsync(directory)
        finally:
            if owned is not None:
                try:
                    current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if (current.st_dev, current.st_ino) == owned:
                        os.unlink(temporary, dir_fd=directory)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--json', type=Path, help='explicit sanitized canonical export')
    source.add_argument('--sqlite-snapshot', type=Path, help='explicit read-only standalone offline snapshot')
    source.add_argument('--recover-history', action='store_true', help='replay last committed exports; no observation')
    parser.add_argument('--profile', help='required for snapshots; safe public provenance label, not account identity')
    parser.add_argument('--output', required=True, type=Path, help='absolute path in an existing controlled directory')
    parser.add_argument('--history-state', type=Path, help='opt-in owned observation ledger')
    parser.add_argument('--history-output', type=Path, help='opt-in public history projection')
    parser.add_argument('--observed-at', help='explicit RFC3339 observation instant with offset')
    parser.add_argument('--timezone', help='explicit IANA calendar timezone')
    args = parser.parse_args(argv)
    try:
        if args.recover_history:
            if not args.history_state or not args.history_output or any((args.profile, args.observed_at, args.timezone)):
                raise ValueError('recovery requires only explicit state and export destinations')
            recover_history(state_path=args.history_state, history_output=args.history_output, output=args.output)
            return 0
        if args.profile is None:
            raise ValueError('snapshot profile required')
        path = args.json or args.sqlite_snapshot
        options = (args.history_state, args.history_output, args.observed_at, args.timezone)
        if any(options):
            if not all(options):
                raise ValueError('history requires all explicit options')
            # Include internal lock/cleanup targets before reading input or writing anything.
            storage_paths(args.history_state, args.history_output, args.output, input_path=path)
        else:
            reject_aliases(path, args.output, temporary_path(args.output), publication_lock_path(args.output))
        # An oversized snapshot is refused by the bounded reader; it never selects
        # a different reader that would open the pathname as a database.
        rows = read_json(path) if args.json else read_snapshot(path)
        if any(options):
            record_history(canonical_snapshot(rows, args.profile), state_path=args.history_state,
                           history_output=args.history_output, output=args.output,
                           observed_at=args.observed_at, timezone=args.timezone)
        else:
            publish(project(rows, args.profile), args.output)
    except (ValueError, OSError, sqlite3.Error, OverflowError, RecursionError):
        print('Hermes accounting rejected input or publication.', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
