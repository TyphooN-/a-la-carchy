#!/usr/bin/env python3
"""Read-only Hermes Codex quotas. Stdlib only; never refreshes or selects logins.

One bounded OAuth JSON snapshot, a fixed HTTPS GET per unique principal, and an
atomic private display record. Optional --usage-dir publishes two read-only
scanner records; no default scanner writes and no native account registration.
Tokens exist in memory and a private worker pipe, never argv, files or output.
"""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata

MAX_AUTH_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_ACCOUNTS = 8
MAX_TOKEN_BYTES = 32768
MAX_SUBJECT_BYTES = 1024
WORKER_TIMEOUT = 12
AUTH_NAMESPACE = 'https://api.openai.com/auth'
PLANS = {'free', 'plus', 'pro', 'team', 'business', 'enterprise', 'edu'}
REASONS = {'auth': 'Hermes login unavailable — manage credentials in Hermes',
           'expired': 'Token expired — refresh login in Hermes',
           'network': 'Quota check unavailable', 'quota': 'Quota fields unavailable',
           'initial': 'Quotas not checked yet', 'busy': 'A quota check is already running',
           'state': 'Private quota state unavailable',
           'publication': 'Quotas collected but Agents publication failed — review the explicit destination',
           'publication-recovery': 'Agents publication interrupted — preserve files for manual recovery'}


class Unavailable(Exception):
    """Only an allowlisted reason code may escape the credential boundary."""
    def __init__(self, reason):
        self.reason = reason if reason in REASONS else 'quota'
        super().__init__(REASONS[self.reason])


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        # Bounded JSON can still contain an integer beyond float range.
        return False


def read_json(path, maximum):
    """No links, FIFO blocking, oversized reads or auth-library side effects."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > maximum:
            raise Unavailable('auth')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > maximum:
                raise Unavailable('auth')
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise Unavailable('auth')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise Unavailable('auth')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise Unavailable('auth') from None


def read_pool(path):
    store = read_json(Path(path), MAX_AUTH_BYTES)
    pool = store.get('credential_pool')
    if not isinstance(pool, dict):
        raise Unavailable('auth')
    rows = pool.get('openai-codex')
    if not isinstance(rows, list) or len(rows) > MAX_ACCOUNTS or any(not isinstance(r, dict) for r in rows):
        raise Unavailable('auth')
    return rows


def header_value(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_.:@-]{1,256}', value))


def opaque_subject(value):
    # Subject is hashed, NEVER put into a header or a display record. Auth0's
    # pipe-separated subjects are valid; header syntax is the wrong contract.
    return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= MAX_SUBJECT_BYTES
            and not any(c.isspace() or unicodedata.category(c)[0] in 'CZ' for c in value))


def identity(token):
    """Unverified claims are identity hints ONLY; the server authenticates GETs.

    Workspace plus subject distinguishes members who share one workspace.
    Headers derive from that SAME JWT, including residency; no pool selection.
    """
    try:
        if not isinstance(token, str) or len(token) > MAX_TOKEN_BYTES or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
            raise ValueError
        encoded = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        auth = claims.get(AUTH_NAMESPACE, {})
        workspace, subject = auth.get('chatgpt_account_id'), claims.get('sub')
        expiry = claims.get('exp')
        residency = auth.get('chatgpt_data_residency') or auth.get('chatgpt_compute_residency') or ''
        if not header_value(workspace) or not opaque_subject(subject) or not number(expiry) or \
                (residency and not header_value(residency)):
            raise ValueError
        key = hashlib.sha256(json.dumps([workspace, subject], separators=(',', ':')).encode()).hexdigest()
        return {'key': key, 'account': workspace, 'residency': residency, 'expires': expiry}
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
        raise Unavailable('auth') from None


def iso(epoch):
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def duration_label(seconds):
    if seconds == 604800: return '7-day window', 'Weekly'
    if seconds % 86400 == 0: return f'{seconds // 86400}-day window', f'{seconds // 86400}-day'
    if seconds % 3600 == 0: return f'{seconds // 3600}-hour window', f'{seconds // 3600}-hour'
    if seconds % 60 == 0: return f'{seconds // 60}-minute window', f'{seconds // 60}-minute'
    return f'{seconds}-second window', f'{seconds}-second'


def normalize_window(raw, now):
    if not isinstance(raw, dict): raise Unavailable('quota')
    percent, seconds = raw.get('used_percent'), raw.get('limit_window_seconds')
    if not number(percent) or not 0 <= percent <= 100 or type(seconds) is not int or not 0 < seconds <= 366*86400:
        raise Unavailable('quota')
    reset = raw.get('reset_at')
    if reset is None and 'reset_after_seconds' in raw:
        after = raw['reset_after_seconds']
        if not number(after) or not 0 <= after <= 366*86400: raise Unavailable('quota')
        reset = now + after
    if reset is not None and (not number(reset) or not 0 < reset <= 4102444800):
        raise Unavailable('quota')
    label, title = duration_label(seconds)
    return {'label': label, 'title': title, 'percent': percent/100, 'resetAt': iso(reset) if reset else ''}


def normalize_quota(body, now):
    if not isinstance(body, dict) or not isinstance(body.get('rate_limit'), dict): raise Unavailable('quota')
    rate = body['rate_limit']
    # Position never determines the label or duration. Fail closed on malformed
    # advertised windows instead of silently presenting a partial allowance.
    windows = [rate.get(k) for k in ('primary_window', 'secondary_window') if rate.get(k) is not None]
    if not windows: raise Unavailable('quota')
    windows.sort(key=lambda w: w.get('limit_window_seconds', 0) if isinstance(w, dict) and number(w.get('limit_window_seconds')) else 0)
    limits = [normalize_window(w, now) for w in windows]
    plan = body.get('plan_type')
    return {'limits': limits, 'plan': plan if isinstance(plan, str) and plan in PLANS else ''}


def http_quota(token, ident, now):
    """Fixed host/path, verified TLS, no proxy environment and no redirects."""
    connection = http.client.HTTPSConnection('chatgpt.com', timeout=4, context=ssl.create_default_context())
    try:
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
                   'User-Agent': 'a-la-carchy-hermes-codex-usage/1', 'ChatGPT-Account-ID': ident['account']}
        if ident['residency']: headers['x-openai-internal-codex-residency'] = ident['residency']
        deadline = time.monotonic() + 8
        connection.request('GET', '/backend-api/wham/usage', headers=headers)
        response = connection.getresponse()
        if response.status in (401, 403): raise Unavailable('expired')
        if response.status != 200: raise Unavailable('network')
        length = response.getheader('Content-Length')
        if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES): raise Unavailable('quota')
        chunks, size = [], 0
        while True:
            if time.monotonic() >= deadline: raise Unavailable('network')
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk: break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES: raise Unavailable('quota')
            chunks.append(chunk)
        return normalize_quota(json.loads(b''.join(chunks)), now)
    except Unavailable:
        raise
    except (OSError, ValueError, http.client.HTTPException, RecursionError):
        raise Unavailable('network') from None
    finally:
        connection.close()


def fetch(token, ident):
    # A separate worker gives DNS/TLS/body reads a real overall deadline, even
    # for a stalled resolver or a peer that drip-feeds bytes. No secret in argv
    # or environment; stdout contains only the allowlisted normalized result.
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--quota-worker'],
                                input=json.dumps({'token': token}), text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=WORKER_TIMEOUT)
        data = json.loads(result.stdout)
        if result.returncode or 'error' in data: raise Unavailable(data.get('error', 'network'))
        return data
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Unavailable('network') from None


def sanitize_old(raw):
    """Even a modified prior record cannot smuggle arbitrary fields/strings."""
    if not isinstance(raw, dict) or not re.fullmatch(r'[a-f0-9]{64}', str(raw.get('id', ''))): return None
    limits = raw.get('limits')
    if not isinstance(limits, list) or len(limits) > 2: return None
    cleaned = []
    for w in limits:
        if not isinstance(w, dict) or not number(w.get('percent')) or not 0 <= w['percent'] <= 1: return None
        label, title, reset = w.get('label'), w.get('title'), w.get('resetAt')
        if not isinstance(label, str) or not re.fullmatch(r'[0-9]{1,8}-(?:day|hour|minute|second) window', label): return None
        seconds = int(label.split('-')[0]) * {'day':86400, 'hour':3600, 'minute':60, 'second':1}[label.split('-')[1].split()[0]]
        if not 0 < seconds <= 366*86400: return None
        if reset:
            if not isinstance(reset, str) or len(reset) > 35: return None
            try: reset = iso(dt.datetime.fromisoformat(reset.replace('Z', '+00:00')).timestamp())
            except (ValueError, TypeError, OSError, OverflowError): return None
        elif reset != '': return None
        clean_label, clean_title = duration_label(seconds)
        cleaned.append({'label': clean_label, 'title': clean_title, 'percent': w['percent'], 'resetAt': reset})
    stamp = raw.get('fetchedAt', 0)
    if not number(stamp) or not 0 <= stamp <= 4102444800000: return None
    plan = raw.get('plan')
    return {'id': raw['id'], 'limits': cleaned, 'fetchedAt': stamp, 'plan': plan if isinstance(plan, str) and plan in PLANS else ''}


def skeleton(status=''):
    return {'schemaVersion': 1, 'id': 'hermes-codex', 'name': 'Codex · Hermes', 'readOnly': True,
            'ready': True, 'tierLabel': 'Read-only quotas', 'hasLocalStats': False, 'hasPromptStats': False,
            'limits': [], 'accounts': [], 'usageStatusText': status, 'authHelpText': status}


def build_record(rows, previous, probe, now):
    if len(rows) > MAX_ACCOUNTS: raise Unavailable('auth')
    old = {}
    previous_accounts = previous.get('accounts', []) if isinstance(previous, dict) else []
    if isinstance(previous_accounts, list):
        for raw in previous_accounts[:MAX_ACCOUNTS]:
            clean = sanitize_old(raw)
            if clean: old[clean['id']] = clean
    selected = {}
    for ordinal, row in enumerate(rows, 1):
        token = row.get('access_token')
        try: ident = identity(token)
        except Unavailable:
            ident = {'key': hashlib.sha256(('invalid-pool-slot:' + str(ordinal)).encode()).hexdigest(), 'expires': 0}
        key = ident['key']
        if key not in selected:
            selected[key] = (ordinal, token, ident)
        elif ident['expires'] > selected[key][2]['expires']:
            selected[key] = (selected[key][0], token, ident)
    record = skeleton()
    for key, (ordinal, token, ident) in selected.items():
        account = {'id': key, 'label': 'Hermes account ' + str(ordinal), 'readOnly': True,
                   'active': False, 'limits': [], 'fetchedAt': 0, 'stale': True, 'plan': '',
                   'usageStatusText': '', 'authHelpText': ''}
        try:
            if 'account' not in ident: raise Unavailable('auth')
            if ident['expires'] <= now: raise Unavailable('expired')
            result = probe(token, ident)
            # Revalidate the normalized worker contract before publication.
            clean = sanitize_old({'id': key, **result, 'fetchedAt': int(now*1000)})
            if not clean or not clean['limits']: raise Unavailable('quota')
            account.update(clean); account['stale'] = False
        except Unavailable as exc:
            if key in old: account.update(old[key])
            account['usageStatusText'] = REASONS[exc.reason]
            account['authHelpText'] = REASONS[exc.reason]
        record['accounts'].append(account)
    if not record['accounts']:
        record['usageStatusText'] = record['authHelpText'] = 'No Codex credentials in this Hermes pool'
    return record


def private_state(state):
    if state.is_symlink() or any(p.is_symlink() for p in state.absolute().parents): raise Unavailable('state')
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise Unavailable('state')


def publish(state, record):
    state = Path(state); private_state(state)
    target = state/'usage.json'
    if target.is_symlink() or (target.exists() and not target.is_file()): raise Unavailable('state')
    data = (json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    if len(data) > MAX_RECORD_BYTES: raise Unavailable('state')
    fd, name = tempfile.mkstemp(prefix='.usage-', dir=state)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name): os.unlink(name)
    return True


PUBLICATION_MANIFEST = '.hermes-codex-publication'
PUBLICATION_LOCK = '.hermes-codex-publication.lock'


def account_records(record):
    """Only two display slots; account hashes follow current pool order."""
    accounts = record.get('accounts')
    if not isinstance(accounts, list) or len(accounts) > 2: raise Unavailable('publication')
    result = {}
    for i, raw in enumerate(accounts, 1):
        clean = sanitize_old(raw)
        if not clean: raise Unavailable('publication')
        status = raw.get('usageStatusText', '')
        if status not in ('', *REASONS.values()): raise Unavailable('publication')
        name = 'codex-hermes-' + str(i)
        value = {'schemaVersion': 1, 'id': name, 'name': f'Codex (Hermes {i})',
                 'principalId': clean['id'], 'readOnly': True, 'ready': True,
                 'hasLocalStats': False, 'hasPromptStats': False,
                 'tierLabel': clean['plan'] or 'Hermes',
                 'limits': [{**{k: w[k] for k in ('label', 'title', 'percent')},
                             'resetsAt': w['resetAt']} for w in clean['limits']],
                 'limitsStale': raw.get('stale') is not False,
                 'limitsFetchedAt': clean['fetchedAt'],
                 'usageStatusText': status, 'authHelpText': status}
        result[name + '.json'] = (json.dumps(value, allow_nan=False, separators=(',', ':')) + '\n').encode()
    return result


def publication_directory(path):
    """Walk no-follow directory FDs, including parents; never resolve a link."""
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in Path(path).absolute().parts[1:]:
            if part in ('.', '..'): raise Unavailable('publication')
            try: os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError: pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd); fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
            raise Unavailable('publication')
        return fd
    except BaseException:
        os.close(fd)
        raise


def publication_read(fd, name):
    try: file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    except FileNotFoundError: return None
    with os.fdopen(file_fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > MAX_RECORD_BYTES:
            raise Unavailable('publication')
        data = stream.read(MAX_RECORD_BYTES+1)
    if len(data) > MAX_RECORD_BYTES: raise Unavailable('publication')
    return data


def publication_temp(fd, data):
    name = '.codex-' + os.urandom(16).hex()
    file_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    try:
        with os.fdopen(file_fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    except BaseException:
        os.unlink(name, dir_fd=fd)
        raise
    return name


def publish_accounts(destination, record):
    """Private atomic files, ownership hashes, checked rollback, no auth data.

    A batch is NOT an atomic multi-file transaction for scanner readers. All
    files are prepared first; ordinary partial failures roll back. A process
    death or a non-cooperating concurrent writer fails closed on the next run.
    Unregistered legacy/manual records are preserved, never adopted by name.
    """
    desired = account_records(record)
    fd = None
    try:
        fd = publication_directory(destination)
        lock_fd = os.open(PUBLICATION_LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=fd)
        with os.fdopen(lock_fd, 'wb') as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise Unavailable('publication')
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest_before = publication_read(fd, PUBLICATION_MANIFEST)
            owned = {}
            if manifest_before is not None:
                meta = json.loads(manifest_before)
                owned = meta.get('files') if isinstance(meta, dict) and meta.get('version') == 1 else None
                if not isinstance(owned, dict) or any(not re.fullmatch(r'codex-hermes-[1-8]\.json', n)
                        or not isinstance(h, str) or not re.fullmatch(r'[a-f0-9]{64}', h) for n, h in owned.items()):
                    raise Unavailable('publication')
            before = {n: publication_read(fd, n) for n in sorted(set(owned) | set(desired))}
            for n, data in before.items():
                if data is not None and owned.get(n) != hashlib.sha256(data).hexdigest():
                    raise Unavailable('publication')
            manifest = (json.dumps({'version': 1, 'files': {n: hashlib.sha256(d).hexdigest() for n, d in desired.items()}},
                                   sort_keys=True, separators=(',', ':')) + '\n').encode()
            before[PUBLICATION_MANIFEST] = manifest_before
            changes = desired | {PUBLICATION_MANIFEST: manifest}
            staged, applied = {}, []
            try:
                for n, data in changes.items(): staged[n] = publication_temp(fd, data)
                for n in sorted(set(owned) | set(desired)) + [PUBLICATION_MANIFEST]:
                    if publication_read(fd, n) != before[n]: raise Unavailable('publication')
                    if n in changes:
                        os.replace(staged[n], n, src_dir_fd=fd, dst_dir_fd=fd)
                        del staged[n]
                    elif before[n] is not None: os.unlink(n, dir_fd=fd)
                    else: continue
                    applied.append(n)
                os.fsync(fd)
            except (OSError, Unavailable):
                for n in reversed(applied):
                    if publication_read(fd, n) != changes.get(n): raise Unavailable('publication-recovery')
                    if before[n] is None: os.unlink(n, dir_fd=fd)
                    else:
                        temp = publication_temp(fd, before[n])
                        try: os.replace(temp, n, src_dir_fd=fd, dst_dir_fd=fd)
                        finally:
                            try: os.unlink(temp, dir_fd=fd)
                            except FileNotFoundError: pass
                os.fsync(fd)
                raise Unavailable('publication') from None
            finally:
                for temp in staged.values(): os.unlink(temp, dir_fd=fd)
        return True
    except Unavailable: raise
    except (OSError, ValueError, TypeError, RecursionError): raise Unavailable('publication') from None
    finally:
        if fd is not None: os.close(fd)


def run_once(home, state, initialize=False, usage_dir=None):
    private_state(state)
    fd = os.open(state/'collector.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'wb') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode): raise Unavailable('state')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        if initialize:
            if not (state/'usage.json').exists(): publish(state, skeleton(REASONS['initial']))
            return True
        try: previous = read_json(state/'usage.json', MAX_RECORD_BYTES)
        except Unavailable: previous = {}
        now = time.time()
        try:
            record = build_record(read_pool(home/'auth.json'), previous, fetch, now)
        except Unavailable:
            record = skeleton(REASONS['auth'])
            raw_accounts = previous.get('accounts', [])
            for raw in (raw_accounts[:MAX_ACCOUNTS] if isinstance(raw_accounts, list) else []):
                clean = sanitize_old(raw)
                if clean:
                    record['accounts'].append(clean | {'label': 'Hermes account ' + str(len(record['accounts'])+1),
                        'readOnly': True, 'active': False, 'stale': True,
                        'usageStatusText': REASONS['auth'], 'authHelpText': REASONS['auth']})
        publish(state, record)

        if usage_dir is not None: publish_accounts(usage_dir, record)
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home', type=Path, default=Path(os.environ.get('HERMES_HOME') or Path.home()/'.hermes'))
    parser.add_argument('--state-dir', type=Path, default=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'omarchy/hermes-codex')
    parser.add_argument('--initialize', action='store_true', help='Publish an unknown placeholder; no auth read or network')
    parser.add_argument('--usage-dir', type=Path, help='Explicit Agents scanner destination; omitted means private state only')
    parser.add_argument('--quota-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.quota_worker:
            packet = json.loads(sys.stdin.read(MAX_TOKEN_BYTES+128))
            token = packet.get('token')
            result = http_quota(token, identity(token), time.time())
            print(json.dumps(result, allow_nan=False))
        else:
            run_once(args.hermes_home, args.state_dir, args.initialize, args.usage_dir)
        return 0
    except Unavailable as exc:
        if args.quota_worker: print(json.dumps({'error': exc.reason}))
        else: print(REASONS[exc.reason], file=sys.stderr)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        if args.quota_worker: print('{"error":"network"}')
        else: print('Quota bridge unavailable', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
