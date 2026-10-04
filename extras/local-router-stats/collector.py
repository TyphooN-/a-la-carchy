#!/usr/bin/env python3
"""Best-effort llama.cpp router token sampling. Observes only; never loads a model."""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

NAMES = ('llamacpp:prompt_tokens_total', 'llamacpp:tokens_predicted_total')
LIMIT = 1024 * 1024
ROUTER_PORT = 8080
ROUTER_UNIT = 'llama-router.service'
# A stored sample this far ahead of the clock means the clock was corrected.
CLOCK_SLACK = 300
RETAINED = ' · history retained'


def counters(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in NAMES:
            continue
        if len(parts) != 2 or parts[0] in result:
            raise ValueError('ambiguous counters')
        try:
            value = Decimal(parts[1])
            if not value.is_finite() or value < 0 or value > 2**63 - 1 or value != value.to_integral_value():
                raise ValueError('invalid counter')
            result[parts[0]] = int(value)
        except InvalidOperation as exc:
            raise ValueError('invalid counter') from exc
    if set(result) != set(NAMES):
        raise ValueError('missing counters')
    return tuple(result[n] for n in NAMES)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def get(port, path):
    # Loopback only; no redirects, proxies or authentication discovery.
    port = int(port)
    if not 1 <= port <= 65535:
        raise ValueError('invalid port')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=2) as response:
            body = response.read(LIMIT + 1)
    except urllib.error.HTTPError as exc:
        exc.close()
        raise
    if len(body) > LIMIT:
        raise ValueError('response too large')
    return body.decode('utf-8')


def option(args, key):
    for i, arg in enumerate(args):
        if arg == key and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(key + '='):
            return arg.split('=', 1)[1]
    return None


def children(root_pid, proc):
    # The router spawns workers from its HTTP threads, and a child is listed
    # under the thread that forked it, so the main thread alone sees none.
    pids = set()
    for task in proc.joinpath(str(root_pid), 'task').iterdir():
        try:
            pids.update(pid for pid in task.joinpath('children').read_text().split() if pid.isdigit())
        except OSError:
            continue
    return sorted(pids, key=int)


def worker(model, root_pid, proc=Path('/proc')):
    """Return (identity, port) of the one router child serving this model.

    Only the command line is read: the alias and port the router rendered into
    it. The process environment is never opened.
    """
    status = model.get('status')
    listed = status.get('args') if isinstance(status, dict) else None
    listed = listed if isinstance(listed, list) and all(isinstance(a, str) for a in listed) else []
    port = option(listed, '--port')
    boot = proc.joinpath('sys/kernel/random/boot_id').read_text().strip()
    matches = []
    for pid in children(root_pid, proc):
        base = proc / pid
        try:
            args = base.joinpath('cmdline').read_bytes().decode().rstrip('\0').split('\0')
            child_port = option(args, '--port')
            if model['id'] not in (option(args, '--alias') or '').split(','):
                continue
            if not child_port or not child_port.isdigit() or (port and port != child_port):
                continue
            # Field 22, accounting for spaces/parentheses in process comm.
            start = base.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]
            matches.append((f'{boot}:{pid}:{start}', int(child_port)))
        except (OSError, ValueError, IndexError, UnicodeError):
            continue
    if len(matches) != 1:
        raise ValueError('worker identity unavailable')
    return matches[0]


def router_pid():
    result = subprocess.run(['systemctl', '--user', 'show', ROUTER_UNIT, '-p', 'MainPID', '--value'], capture_output=True, text=True, timeout=3, check=True)
    pid = int(result.stdout.strip())
    if pid <= 0:
        raise ValueError('router unavailable')
    return pid


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS snapshots (
                model TEXT PRIMARY KEY, epoch TEXT NOT NULL,
                input INTEGER NOT NULL, output INTEGER NOT NULL, sampled REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS daily (
                day TEXT NOT NULL, model TEXT NOT NULL, input INTEGER NOT NULL,
                output INTEGER NOT NULL, PRIMARY KEY(day, model));
            CREATE TABLE IF NOT EXISTS coverage (day TEXT PRIMARY KEY, first REAL NOT NULL, last REAL NOT NULL);
        ''')

    def observe(self, model, epoch, values, now, boundary_gap=25):
        if not isinstance(model, str) or not model or len(model) > 256 or any(ord(c) < 32 for c in model):
            raise ValueError('invalid model id')
        if not epoch or len(epoch) > 256:
            raise ValueError('invalid worker identity')
        if len(values) != 2 or any(type(v) is not int or not 0 <= v <= 2**63 - 1 for v in values):
            raise ValueError('invalid counters')
        day = dt.datetime.fromtimestamp(now).strftime('%Y-%m-%d')
        cutoff_day = str(dt.datetime.fromtimestamp(now).date() - dt.timedelta(days=364))
        cutoff = dt.datetime.combine(dt.date.fromisoformat(cutoff_day), dt.time()).timestamp()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old = self.db.execute('SELECT epoch,input,output,sampled FROM snapshots WHERE model=?', (model,)).fetchone()
            if old and now <= old[3]:
                # A small backwards step is waited out so nothing is lost; a
                # sample stored far in the future would block collection.
                if old[3] - now <= CLOCK_SLACK:
                    raise ValueError('non-monotonic sample time')
                old = None
            self.db.execute('DELETE FROM daily WHERE day < ?', (cutoff_day,))
            self.db.execute('DELETE FROM coverage WHERE day < ?', (cutoff_day,))
            self.db.execute('DELETE FROM snapshots WHERE sampled < ?', (cutoff,))
            if old and old[3] < cutoff:
                old = None
            known = self.db.execute('SELECT 1 FROM snapshots WHERE model=?', (model,)).fetchone()
            if not known and self.db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] >= 256:
                raise ValueError('retained model bound exceeded')
            delta = (0, 0)
            if old and old[0] == epoch and values[0] >= old[1] and values[1] >= old[2]:
                # Within one calendar day the interval is dated exactly. Across
                # midnight only a short interval is credited to the new day; a
                # longer one (collector downtime) cannot be dated and is a gap.
                same_day = dt.datetime.fromtimestamp(old[3]).strftime('%Y-%m-%d') == day
                if same_day or now - old[3] <= boundary_gap:
                    delta = (values[0] - old[1], values[1] - old[2])
            # First sample, new process, counter regression or undatable gap:
            # establish a new baseline. Never attribute lifetime counts to
            # today's calendar date.
            self.db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)', (model, epoch, *values, now))
            self.db.execute('INSERT INTO daily VALUES (?,?,?,?) ON CONFLICT(day,model) DO UPDATE SET input=input+excluded.input, output=output+excluded.output', (day, model, *delta))
            self.db.execute('INSERT INTO coverage VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET last=excluded.last', (day, now, now))
        return delta

    def record(self, now, status):
        today = dt.datetime.fromtimestamp(now).date()
        rows = self.db.execute('SELECT day,model,input,output FROM daily ORDER BY day,model')
        recorded = {day for (day,) in self.db.execute('SELECT day FROM coverage')}
        models, totals, today_models = {}, {}, {}
        for day, model, inp, out in rows:
            bucket = models.setdefault(model, {'inputTokens': 0, 'outputTokens': 0})
            bucket['inputTokens'] += inp
            bucket['outputTokens'] += out
            totals[day] = totals.get(day, 0) + inp + out
            if day == str(today):
                today_models[model] = inp + out
        days = []
        for offset in range(6, -1, -1):
            day = str(today - dt.timedelta(days=offset))
            days.append({'date': day, 'messageCount': totals.get(day) if day in recorded else None,
                         'recorded': day in recorded})
        # Every field is stable between samples that observe no change, so the
        # record is republished (and the panel redrawn) only when it differs.
        return {'id': 'local', 'name': 'Local', 'ready': True, 'limits': [],
                'tierLabel': 'llama.cpp router', 'usageStatusText': status,
                'authHelpText': 'Sampled evaluated input + generated output on this machine; cached input is not counted. First samples are baselines. Model unloads, downtime and day-boundary sampling can leave gaps. Not full request accounting.',
                'hasPromptStats': False, 'hasLocalStats': bool(recorded),
                'todayTotalTokens': totals.get(str(today), 0), 'todayTokensByModel': today_models,
                'recentDays': days, 'modelUsage': models, 'totalPrompts': 0, 'totalSessions': 0,
                'activeDays': sum(v > 0 for v in totals.values())}


def sample(ledger, now, boundary_gap=25):
    try:
        listing = json.loads(get(ROUTER_PORT, '/models'))
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError):
        return 'Router unreachable' + RETAINED
    models = listing.get('data') if isinstance(listing, dict) else None
    if not isinstance(models, list) or len(models) > 256 or not all(isinstance(m, dict) for m in models):
        raise ValueError('invalid model listing')
    loaded = [m for m in models if isinstance(m.get('status'), dict) and m['status'].get('value') == 'loaded']
    if not loaded:
        return 'No loaded model' + RETAINED
    if not all(isinstance(m.get('id'), str) for m in loaded):
        raise ValueError('invalid model listing')
    try:
        pid = router_pid()
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'Router service unavailable' + RETAINED
    sampled, disabled, failure = 0, 0, None
    for model in loaded:
        try:
            # The counters come from the worker's own loopback listener, not
            # the router: nothing there can load a model, be proxied or logged.
            identity, port = worker(model, pid)
            values = counters(get(port, '/metrics'))
            if worker(model, pid) != (identity, port):
                raise ValueError('worker changed during sample')
            ledger.observe(model['id'], identity, values, now, boundary_gap)
            sampled += 1
        except urllib.error.HTTPError as exc:
            # llama.cpp answers 501 when the worker runs without --metrics.
            if exc.code == 501:
                disabled += 1
            else:
                failure = failure or type(exc).__name__
        except Exception as exc:
            # Errors can contain URLs or source bodies. Keep only their class,
            # not their text or model launch arguments.
            failure = failure or type(exc).__name__
    if sampled == len(loaded):
        return f'Sampled tokens · {sampled} model(s) · cache excluded'
    # The missing prerequisite is named rather than folded into a generic error.
    if disabled:
        if sampled:
            return f'Sampled {sampled} of {len(loaded)} models · metrics disabled on {disabled}'
        return 'Metrics disabled · start llama.cpp with --metrics' + RETAINED
    if sampled:
        return f'Sampled {sampled} of {len(loaded)} models · {len(loaded) - sampled} unavailable ({failure})'
    return f'Collection unavailable ({failure})' + RETAINED


def publish(path, record):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.local-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=state_home/'omarchy/local-router')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--publish', action='store_true', help='Write the record the patched Agents clone reads (outside the native usage directory)')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--render-only', action='store_true', help='No network or service inspection; export stored history')
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()
    if not 2 <= args.interval <= 3600:
        parser.error('interval must be between 2 and 3600 seconds')
    if args.publish:
        if args.output:
            parser.error('--publish and --output are mutually exclusive')
        args.output = state_home/'omarchy/local-router/agents/local.json'
        args.output.parent.mkdir(parents=True, exist_ok=True)
    elif args.output and args.output.resolve().parent == (state_home/'omarchy/agents/usage').resolve():
        # The stock Agents widget treats every record there as syncable.
        parser.error('refusing to publish into the native Agents usage directory')
    args.state_dir.mkdir(parents=True, exist_ok=True)
    lock = (args.state_dir/'collector.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        parser.exit(1, 'Another local-router collector owns this state directory.\n')
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    ledger = Ledger(args.state_dir/'tokens.sqlite3')
    published = None
    try:
        while True:
            now = time.time()
            status = 'Activation pending · not collecting'
            if not args.render_only:
                try:
                    status = sample(ledger, now, 3 * args.interval + 10)
                except Exception as exc:
                    # Errors can contain credentials, URLs or source bodies. Persist
                    # only their class, not their text or model launch arguments.
                    status = f'Collection unavailable ({type(exc).__name__})' + RETAINED
            record = ledger.record(now, status)
            if not args.output:
                print(json.dumps(record, allow_nan=False), flush=True)
            elif record != published:
                publish(args.output, record)
                published = record
            if not args.run:
                break
            time.sleep(args.interval)
    finally:
        ledger.db.close()
        lock.close()


if __name__ == '__main__':
    main()
