#!/usr/bin/env python3
"""Best-effort llama.cpp router token sampling. Observes only; never loads a model."""
import argparse
import datetime as dt
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import signal
import stat
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

NAMES = ('llamacpp:prompt_tokens_total', 'llamacpp:tokens_predicted_total')
LIMIT = 1024 * 1024
ROUTER_PORT = 8080
ROUTER_UNIT = 'llama-router.service'
# A stored sample this far ahead of the clock means the clock was corrected.
CLOCK_SLACK = 300
RETAINED = ' · history retained'
MAX_MODELS = 16
MAX_SLOTS = 256
MAX_RATE_GAP = 30
MAX_SAFE = 2**53 - 1

# Unified dated roll-up: one calendar shared by the panel's history and the bar.
ROLLUP_CONTRACT = 'omarchy-unified-token-rollup/1'
ROLLUP_RETENTION_DAYS = 365
# A stopped collector's "today" must expire rather than stay current forever.
ROLLUP_TTL = 300
ROLLUP_REFRESH = 60
# Collector liveness is written at most this often; a longer silence is an outage.
UPTIME_WRITE = 60
UPTIME_GAP = 180
# How far from a day's edge a recording may start or stop and still span that day.
LOCAL_DAY_SLACK = 120
# Cloud is sampled about once a minute and midnight-crossing intervals stay undated.
CLOUD_DAY_SLACK = 600
# A stopped canonical feed is distinct from a fresh measurement of zero.
CLOUD_FEED_TTL = 600
HISTORY_CONTRACT = 'hermes-observed-history/1'
# usage_history.MAX_STATE_BYTES: the dense 512-identity x 61-bucket projection.
HISTORY_LIMIT = 16 * 1024 * 1024
# Serialized ASCII JSON must fit history_consumer.parseRollup transport.
PUBLICATION_LIMIT = 4 * 1024 * 1024
HISTORY_DAY_BUCKETS = 31
HISTORY_SOURCES = 512
# Cloud routes only. The Hermes adapter drops loopback/Local routes before it
# publishes, which is what keeps Cloud + router samples free of double counting;
# any other provider is refused here, not summed.
CLOUD_PROVIDERS = frozenset(('openai', 'openai-codex', 'anthropic', 'openrouter', 'google', 'gemini', 'groq',
                             'deepseek', 'nous', 'xai', 'mistral', 'cerebras', 'together', 'fireworks'))
CLOUD_COUNTS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
                'reasoning_tokens', 'api_call_count')
IANA_ZONE = re.compile(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*')
HISTORY_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')
HISTORY_INSTANT = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')
SOURCE_ID = re.compile(r'[a-f0-9]{64}')


def integer(value, maximum=MAX_SAFE):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError('invalid numeric state')
    return value


def slots(raw):
    """Maintained Local slot projection: drop bodies/params before sampling."""
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_SLOTS:
        raise ValueError('invalid slots')
    result, seen = [], set()
    for row in raw:
        if not isinstance(row, dict) or type(row.get('is_processing')) is not bool:
            raise ValueError('invalid slot state')
        sid = integer(row.get('id'), MAX_SLOTS - 1)
        if sid in seen:
            raise ValueError('duplicate slot')
        seen.add(sid)
        item = {'id': sid, 'active': row['is_processing']}
        for source, target in (('id_task', 'task'), ('n_ctx', 'capacity'), ('n_prompt_tokens', 'used'),
                               ('n_prompt_tokens_processed', 'input'), ('n_prompt_tokens_cache', 'cache')):
            item[target] = integer(row[source]) if source in row else None
        token = row.get('next_token')
        if token is not None:
            if not isinstance(token, list) or len(token) != 1 or not isinstance(token[0], dict):
                raise ValueError('invalid token state')
            item['output'] = integer(token[0].get('n_decoded'))
        else:
            item['output'] = None
        result.append(item)
    return result


class LiveSampler:
    """One previous observation per model, never engine-average throughput."""
    def __init__(self):
        self.previous = {}

    def retain(self, models):
        self.previous = {k: v for k, v in self.previous.items() if k in models}

    def observe(self, model, identity, rows, metrics, now):
        old = self.previous.pop(model, None)
        if (not isinstance(model, str) or not model or len(model) > 256 or any(ord(c) < 32 for c in model)
                or not isinstance(identity, str) or not identity or len(identity) > 256
                or any(ord(c) < 32 for c in identity)
                or type(now) not in (int, float) or not math.isfinite(now) or not 0 < now <= 253402300799):
            raise ValueError('invalid live identity or time')
        self.previous = {k: v for k, v in self.previous.items() if 0 <= now - v[1] <= MAX_RATE_GAP}
        if len(self.previous) >= MAX_MODELS:
            raise ValueError('live model bound exceeded')
        if not isinstance(rows, list) or not 0 < len(rows) <= MAX_SLOTS:
            raise ValueError('invalid projected slots')
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or type(row.get('active')) is not bool:
                raise ValueError('invalid projected slot state')
            sid = integer(row.get('id'), MAX_SLOTS - 1)
            if sid in seen:
                raise ValueError('duplicate projected slot')
            seen.add(sid)
            for key in ('task', 'input', 'output', 'used', 'capacity', 'cache'):
                if row.get(key) is not None:
                    integer(row[key])
        active = [r for r in rows if r['active']]
        current = {r['id']: (r['task'], r['input'], r['output']) for r in active}
        span = now - old[1] if old else 0
        stable = bool(active) and old and old[0] == identity and 0.1 <= span <= MAX_RATE_GAP and set(old[2]) == set(current)
        inp, out = 0, 0
        if stable:
            for sid, values in current.items():
                previous = old[2][sid]
                if (values[0] is None or values[0] != previous[0]
                        or any(v is None for v in values[1:] + previous[1:])
                        or values[1] < previous[1] or values[2] < previous[2]):
                    stable = False
                    break
                inp += values[1] - previous[1]
                out += values[2] - previous[2]
        rates = dict(old[3]) if stable else {}
        for key, delta in (('prompt', inp), ('generation', out)):
            if stable and delta > 0 and delta <= MAX_SAFE:
                rates[key] = (round(delta / span, 2), now, span)
            elif key in rates and now - rates[key][1] >= MAX_RATE_GAP:
                del rates[key]
        self.previous[model] = (identity, now, current, rates)
        def total(key):
            values = [r[key] for r in rows]
            return sum(values) if all(v is not None for v in values) and sum(values) <= MAX_SAFE else None
        result = {'model': model, 'workerIdentity': identity, 'observedAt': now,
                  'taskIdentity': [[sid, values[0]] for sid, values in sorted(current.items())],
                  'state': 'processing' if active else 'idle', 'activeRequests': len(active),
                  'slotCount': len(rows), 'contextUsed': total('used'), 'contextCapacity': total('capacity'),
                  'rateWindowSec': round(span, 3) if stable else None,
                  'rateScope': 'sampled wall-clock, current slot/tasks', 'slotsState': 'available'}
        for key in ('prompt', 'generation'):
            value = rates.get(key)
            result[key + 'TokPerSec'] = value[0] if value else None
            result[key + 'ObservedAt'] = value[1] if value else None
            result[key + 'WindowSec'] = value[2] if value else None
            result[key + 'Identity'] = [identity, result['taskIdentity']] if value else None
        return result


def engine_metrics(text, identity, now):
    """Project already fetched gauges; never claim task-bound/instantaneous rates.

    The engine's phase gauges average recorded prompt batches/completed generation
    work. They are not counter deltas divided by the polling interval. A zero
    phase gauge has no throughput observation, whereas zero processing is valid.
    Optional malformed gauges withhold this summary, not retained token history.
    """
    if (type(text) is not str or len(text) > LIMIT
            or type(identity) is not str or not 0 < len(identity) <= 256
            or any(ord(ch) < 32 for ch in identity)
            or type(now) not in (int, float) or not math.isfinite(now)
            or not 0 < now <= 253402300799):
        return None
    names = {'llamacpp:prompt_tokens_seconds': 'promptTokPerSec',
             'llamacpp:predicted_tokens_seconds': 'generationTokPerSec',
             'llamacpp:requests_processing': 'activeRequests'}
    values = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in names:
            continue
        key = names[parts[0]]
        if (len(parts) != 2 or key in values
                or re.fullmatch(r'[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', parts[1]) is None):
            return None
        try:
            number = Decimal(parts[1])
        except InvalidOperation:
            return None
        if not number.is_finite() or number < 0 or number > MAX_SAFE:
            return None
        if key == 'activeRequests':
            if number != number.to_integral_value() or number > MAX_SLOTS:
                return None
            values[key] = int(number)
        else:
            rate = float(number)
            if number > 0 and rate == 0:
                return None
            values[key] = rate if rate > 0 else None
    if not values:
        return None
    return {'contract': 'llamacpp-metrics-summary/1', 'workerIdentity': identity,
            'observedAt': now, 'rateScope': 'engine-reported averages; not current-task rates',
            **{key: values.get(key) for key in names.values()}}


def counters(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in NAMES:
            continue
        if len(parts) != 2 or parts[0] in result:
            raise ValueError('ambiguous counters')
        if re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', parts[1]) is None:
            raise ValueError('invalid counter')
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


# One maintained build/source association, not a version/path heuristic or a
# reproducible-build claim. See SLOT_ADMISSION.md for the retained build, patch,
# parser and queue proof. Every request still verifies the actual process below.
RUNTIME_SOURCE = 'bed0a856606ee4a24a164066f73d2379447033f5'
RUNTIME_ARTIFACTS = {
    'llama-server': {'bytes': 15976, 'sha256': 'fcaa81d940b6fadb3befb692ab4c953040478dd907421a9e5efa4234e6e95498'},
    'libllama-server-impl.so': {'bytes': 7414824, 'sha256': '5c25fa7d3e3c2d7767497de597c5e5e6a503a3869fd7b72b1cecca1e3c816f76'},
    'libllama-common.so.0.5.0': {'bytes': 6717264, 'sha256': 'c59970234617d898c2907b80373acacbc2e7f0733c6515f5a61796b895ea791f'},
    'libllama.so.0.5.0': {'bytes': 4872592, 'sha256': 'cf17dadc6aeeda0b7c81348e1633b81ff4d373164ea5ac199da26e54af82ed07'},
    'libggml.so.0.25.3': {'bytes': 55360, 'sha256': 'dae60428bf79abbdca751d5596aacbcba154a330e4549b7bf1a5f77592202dac'},
    'libggml-base.so.0.25.3': {'bytes': 984272, 'sha256': '9927add557dc9f7588a41dddfe8255b122f4ee4799815c1a34e956a0c793d9c5'},
    'libggml-cpu.so.0.25.3': {'bytes': 1442776, 'sha256': '9523387dffc251e287b397b3ad265279d473272afc4bd7e46c96ae27da7925db'},
    'libggml-hip.so.0.25.3': {'bytes': 63777632, 'sha256': 'fe0e857ed7989f1a9a16a18a94d1eac8cec0380708c7ddf0a3685a1d7980f17f'},
    'libmtmd.so.0.5.0': {'bytes': 2135984, 'sha256': '79f6a45861a63f458829c2d4c3d63c751475720c5d2d24f84e300ec48415da56'},
}
RUNTIME_REQUIRED = frozenset(('libllama-server-impl.so', 'libllama-common.so.0.5.0',
                              'libllama.so.0.5.0', 'libggml.so.0.25.3', 'libggml-base.so.0.25.3'))
_RUNTIME_HASHES = {}

# Value-aware subset of common/arg.cpp at RUNTIME_SOURCE (819-869). Unknown
# names/arity refuse admission. Names normalize underscores, values never do.
# Unlike the older reference collector, reject attached values: this pin's
# parser consumes split values only. Sleep's sole disabled value is -1, not 0.
WORKER_ARG_SCHEMA = (
    ('--alias', 1, ('-a',)), ('--port', 1, ()), ('--host', 1, ()),
    ('--sleep-idle-seconds', 1, ()), ('--path', 1, ()), ('--model', 1, ('-m',)),
    ('--tags', 1, ()), ('--threads', 1, ('-t',)), ('--threads-batch', 1, ('-tb',)),
    ('--ctx-size', 1, ('-c',)), ('--parallel', 1, ('-np',)),
    ('--batch-size', 1, ('-b',)), ('--ubatch-size', 1, ('-ub',)),
    ('--cache-ram', 1, ('-cram',)), ('--cache-type-k', 1, ('-ctk',)),
    ('--cache-type-v', 1, ('-ctv',)), ('--flash-attn', 1, ('-fa',)),
    ('--gpu-layers', 1, ('-ngl', '--n-gpu-layers')), ('--split-mode', 1, ('-sm',)),
    ('--tensor-split', 1, ('-ts',)), ('--lora', 1, ()), ('--lora-scaled', 1, ()),
    ('--control-vector', 1, ()), ('--control-vector-scaled', 1, ()),
    ('--control-vector-layer-range', 2, ()),
    *((name, 0, ()) for name in ('--jinja', '--no-jinja', '--metrics', '--slots', '--no-slots',
                               '--cache-prompt', '--no-cache-prompt', '--cache-idle-slots',
                               '--no-cache-idle-slots', '--context-shift', '--no-context-shift')),
)
WORKER_ARG_NAMES = {name: (key, arity) for key, arity, aliases in WORKER_ARG_SCHEMA
                    for name in (key, *aliases)}


def worker_options(args, executable=False):
    if (not isinstance(args, list) or len(args) > 512
            or not all(isinstance(a, str) and a and '\0' not in a and len(a) <= 8192 for a in args)):
        raise ValueError('invalid worker arguments')
    i = 0
    if executable or (args and not args[0].startswith('-')):
        if not args or Path(args[0]).name != 'llama-server':
            raise ValueError('unknown worker executable')
        i = 1
    result = {}
    while i < len(args):
        name = args[i]
        if name.startswith('--'):
            name = name.replace('_', '-')
        if name not in WORKER_ARG_NAMES:
            raise ValueError('unsupported worker argument')
        key, arity = WORKER_ARG_NAMES[name]
        i += 1
        if i + arity > len(args):
            raise ValueError('missing worker value')
        values = tuple(args[i:i + arity])
        i += arity
        result.setdefault(key, []).append(values)
    for key in ('--alias', '--port', '--host', '--sleep-idle-seconds', '--slots'):
        if len(result.get(key, [])) > 1:
            raise ValueError('ambiguous worker option')
    if '--no-slots' in result:
        raise ValueError('worker slots disabled')
    return result


def safety_option(options, key):
    values = options.get(key, [])
    value = values[0][0] if values else None
    if value is not None:
        if key == '--port' and (not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 65535):
            raise ValueError('invalid worker port')
        if key == '--alias' and (value.startswith('-') or any(not a or a != a.strip() or len(a) > 256
                or any(ord(ch) < 32 for ch in a) for a in value.split(','))):
            raise ValueError('invalid worker alias')
        if key == '--host' and value != '127.0.0.1':
            raise ValueError('worker host is not literal loopback')
        if key == '--sleep-idle-seconds' and value != '-1':
            raise ValueError('worker sleep is not explicitly disabled')
    return value


def bounded_metadata(path, cap=2 * 1024 * 1024):
    with path.open('rb') as stream:
        raw = stream.read(cap + 1)
    if len(raw) > cap:
        raise ValueError('worker metadata exceeds bound')
    return raw


def file_stamp(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def runtime_bytes(path, expected):
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != expected['bytes'] or before.st_size > 128 * 1024 * 1024:
        raise ValueError('unqualified runtime bytes')
    stamp = file_stamp(before)
    key = (str(path), stamp, expected['sha256'])
    if key not in _RUNTIME_HASHES:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            if file_stamp(os.fstat(stream.fileno())) != stamp:
                raise ValueError('runtime file changed')
            remaining = before.st_size
            while remaining:
                raw = stream.read(min(remaining, 1024 * 1024))
                if not raw:
                    raise ValueError('runtime file truncated')
                digest.update(raw); remaining -= len(raw)
            if stream.read(1) or file_stamp(os.fstat(stream.fileno())) != stamp:
                raise ValueError('runtime file changed')
        if digest.hexdigest() != expected['sha256']:
            raise ValueError('unqualified runtime bytes')
        if len(_RUNTIME_HASHES) >= 64:
            _RUNTIME_HASHES.clear()
        _RUNTIME_HASHES[key] = True
    if file_stamp(path.stat()) != stamp:
        raise ValueError('runtime path changed')
    return stamp


def runtime_mappings(entry):
    result = []
    for line in bounded_metadata(entry/'maps').decode().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not Path(fields[5]).name.startswith(('libllama', 'libggml', 'libmtmd')):
            continue
        span, permissions, offset, device, inode, path = fields
        if path.endswith(' (deleted)') or not path.startswith('/') or Path(path).name not in RUNTIME_ARTIFACTS:
            raise ValueError('unqualified engine mapping')
        major, minor = (int(v, 16) for v in device.split(':'))
        result.append((span, os.makedev(major, minor), int(inode), path))
        if len(result) > 256:
            raise ValueError('too many engine mappings')
    return result


def qualified_runtime(entry):
    # /exe is the kernel-held executable, not argv[0] or a version-looking path.
    executable = runtime_bytes(entry/'exe', RUNTIME_ARTIFACTS['llama-server'])
    mappings = runtime_mappings(entry)
    found = {}
    for span, device, inode, name in mappings:
        path = Path(name)
        disk = path.stat()
        if disk.st_ino != inode:
            raise ValueError('engine mapping inode changed')
        if disk.st_dev != device:
            # Btrfs subvolume devices differ from maps. As in the maintained
            # serving validator, only a readable kernel-held map_files object
            # establishes the association; equal inode/path alone is not proof.
            mapped = (entry/'map_files'/span).stat()
            if (mapped.st_dev, mapped.st_ino) != (disk.st_dev, disk.st_ino):
                raise ValueError('engine mapped object unavailable')
        stamp = runtime_bytes(path, RUNTIME_ARTIFACTS[path.name])
        if stamp != file_stamp(disk):
            raise ValueError('engine mapping changed')
        if path.name in found and found[path.name] != stamp:
            raise ValueError('ambiguous engine libraries')
        found[path.name] = stamp
    if not RUNTIME_REQUIRED <= found.keys():
        raise ValueError('necessary engine mappings missing')
    # Recheck all disk/object relationships after the last hash, including the
    # Btrfs kernel-held disambiguation. A matching maps text is insufficient.
    for span, device, inode, name in mappings:
        disk = Path(name).stat()
        if file_stamp(disk) != found[Path(name).name]:
            raise ValueError('engine mapping changed')
        if disk.st_dev != device:
            mapped = (entry/'map_files'/span).stat()
            if (mapped.st_dev, mapped.st_ino) != (disk.st_dev, disk.st_ino):
                raise ValueError('engine mapped object unavailable')
    if runtime_mappings(entry) != mappings or file_stamp((entry/'exe').stat()) != executable:
        raise ValueError('engine process mappings changed')


def worker_listener(entry, proc, port):
    namespace = (entry/'ns/net').stat()
    if not (entry/'ns/net').samefile(proc/'self/ns/net'):
        raise ValueError('worker network namespace unavailable')
    matches = []
    for line in bounded_metadata(proc/'net/tcp').decode().splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != '0A':
            continue
        address, raw_port = fields[1].split(':')
        if int(raw_port, 16) == port and address in ('0100007F', '00000000'):
            matches.append((address, fields[9]))
    if len(matches) != 1 or matches[0][0] != '0100007F' or not matches[0][1].isdigit():
        raise ValueError('worker loopback listener unavailable')
    descriptors = list((entry/'fd').iterdir())
    if len(descriptors) > 4096:
        raise ValueError('worker descriptor bound exceeded')
    if not any(os.readlink(fd) == 'socket:[' + matches[0][1] + ']' for fd in descriptors):
        raise ValueError('worker does not own listener')
    return file_stamp(namespace), matches[0]


def slots_worker(model, root_pid, proc):
    status = model.get('status')
    listed = worker_options(status.get('args', []) if isinstance(status, dict) else [])
    port = safety_option(listed, '--port')
    alias = safety_option(listed, '--alias')
    safety_option(listed, '--host'); safety_option(listed, '--sleep-idle-seconds')
    if alias is not None and model['id'] not in alias.split(','):
        raise ValueError('listed worker alias mismatch')
    boot = bounded_metadata(proc/'sys/kernel/random/boot_id', 128).decode().strip()
    if not boot or any(ord(ch) < 32 for ch in boot):
        raise ValueError('worker boot identity unavailable')
    pids = children(root_pid, proc)
    if len(pids) > 256:
        raise ValueError('worker child bound exceeded')
    matches = []
    for pid in pids:
        entry = proc/pid
        raw = bounded_metadata(entry/'cmdline', 65536)
        args = worker_options(raw.decode().rstrip('\0').split('\0'), executable=True)
        child_alias = safety_option(args, '--alias')
        child_port = safety_option(args, '--port')
        if model['id'] not in (child_alias or '').split(','):
            continue
        if child_port is None or (port is not None and int(port) != int(child_port)):
            raise ValueError('worker port mismatch')
        safety_option(args, '--host')
        if safety_option(args, '--sleep-idle-seconds') is None:
            raise ValueError('worker sleep policy missing')
        fields = bounded_metadata(entry/'stat', 8192).decode().rsplit(')', 1)[1].split()
        if len(fields) < 20 or fields[0] in ('Z', 'X') or fields[1] != str(root_pid) or not fields[19].isdigit():
            raise ValueError('worker generation unavailable')
        matches.append((entry, raw, fields[19], int(child_port)))
    if len(matches) != 1:
        raise ValueError('worker identity unavailable')
    entry, raw, start, port = matches[0]
    listener = worker_listener(entry, proc, port)
    qualified_runtime(entry)
    after = bounded_metadata(entry/'stat', 8192).decode().rsplit(')', 1)[1].split()
    if (len(after) < 20 or after[0] in ('Z', 'X') or after[1] != str(root_pid) or after[19] != start
            or bounded_metadata(entry/'cmdline', 65536) != raw or children(root_pid, proc) != pids
            or bounded_metadata(proc/'sys/kernel/random/boot_id', 128).decode().strip() != boot
            or worker_listener(entry, proc, port) != listener):
        raise ValueError('worker changed during admission')
    return f'{boot}:{entry.name}:{start}', port


def worker(model, root_pid, proc=Path('/proc'), for_slots=False):
    """Return (identity, port) of the one router child serving this model.

    History reads only alias/port arguments, never process environments. Slots
    additionally require byte-associated executable/mappings, safe lexical sleep
    policy, listener ownership and a stable worker generation.
    """
    if for_slots:
        try:
            return slots_worker(model, root_pid, proc)
        except (OSError, UnicodeError, IndexError, KeyError) as exc:
            raise ValueError('engine sleep/parser provenance unqualified') from exc
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
            child_alias = option(args, '--alias')
            try:
                projected = worker_options(args, executable=True)
                child_port = safety_option(projected, '--port')
                child_alias = safety_option(projected, '--alias')
            except ValueError:
                # Metrics-only compatibility for argv outside the bounded
                # schema. This fallback is never reachable for slot admission.
                pass
            if model['id'] not in (child_alias or '').split(','):
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


def local_date(moment):
    return dt.datetime.fromtimestamp(moment).date()


def day_span(day):
    """Host-calendar [start, end) of a day key in epoch seconds; 23/25-hour days included."""
    date = dt.date.fromisoformat(day)
    return (dt.datetime.combine(date, dt.time()).timestamp(),
            dt.datetime.combine(date + dt.timedelta(days=1), dt.time()).timestamp())


def utc_stamp(moment):
    return dt.datetime.fromtimestamp(int(moment), dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def named_zone(name):
    if (not isinstance(name, str) or not IANA_ZONE.fullmatch(name) or name in ('localtime', 'posixrules')
            or name.startswith(('posix/', 'right/'))):
        raise ValueError('invalid calendar timezone')
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, OSError) as exc:
        raise ValueError('unavailable calendar timezone') from exc


def host_zone_label():
    """Display label only: Local days are dated by the host clock, whatever it is called."""
    name = os.environ.get('TZ', '').lstrip(':')
    if not IANA_ZONE.fullmatch(name):
        target = os.path.realpath('/etc/localtime')
        name = target.split('zoneinfo/', 1)[1] if 'zoneinfo/' in target else ''
    return name if IANA_ZONE.fullmatch(name) else 'host-local'


def zones_agree(name, now):
    """Whether an IANA calendar and the host clock date every instant of the year alike.

    Local days come from the host clock and Cloud days from the producer's zone,
    so they may only share a date key when both calendars agree. Weekly probes
    catch zones that match today but change their clocks on different dates.
    """
    zone = named_zone(name)
    for week in range(-26, 27):
        moment = now + week * 7 * 86400
        if dt.datetime.fromtimestamp(moment).astimezone().utcoffset() != dt.datetime.fromtimestamp(moment, zone).utcoffset():
            return False
    return True


def rollup_valid_until(now):
    """The published "today" ends at TTL or its first real date change, whichever is first.

    Scanning finds a date change even where a backward clock change repeats a
    day. The last instant known to still be today is the conservative answer.
    """
    today = local_date(now)
    moment, limit = int(now), int(now) + ROLLUP_TTL
    while moment + 30 <= limit:
        if local_date(moment + 30) != today:
            return moment
        moment += 30
    return limit


def unique_members(pairs):
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)):
        raise ValueError('duplicate JSON member')
    return dict(pairs)


def refuse_constant(_):
    raise ValueError('non-finite JSON number')


def read_history(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > HISTORY_LIMIT:
            raise ValueError('unusable history file')
        raw = stream.read(HISTORY_LIMIT + 1)
    if len(raw) > HISTORY_LIMIT:
        raise ValueError('unusable history file')
    return json.loads(raw, object_pairs_hook=unique_members, parse_constant=refuse_constant)


def history_counts(value):
    if not isinstance(value, dict) or set(value) != {*CLOUD_COUNTS, 'prompt_tokens', 'total_tokens'}:
        raise ValueError('invalid history counters')
    if any(type(number) is not int or not 0 <= number <= MAX_SAFE for number in value.values()):
        raise ValueError('invalid history counters')
    if (value['reasoning_tokens'] > value['output_tokens']
            or value['prompt_tokens'] != value['input_tokens'] + value['cache_read_tokens'] + value['cache_write_tokens']
            or value['total_tokens'] != value['prompt_tokens'] + value['output_tokens']):
        raise ValueError('inconsistent history counters')
    return value


def cloud_days(history):
    """Per-day, per-provider sums from one hermes-observed-history/1 projection.

    Hundreds of per-session sources reduce to one row per provider. That row is
    all the calendar needs, and it outlives the bucket the projection prunes.
    """
    if (not isinstance(history, dict) or history.get('contract') != HISTORY_CONTRACT
            or history.get('schemaVersion') != 1 or history.get('readOnly') is not True):
        raise ValueError('unsupported history')
    zone, observed = history.get('timezone'), history.get('observedAt')
    named_zone(zone)
    if not isinstance(observed, str) or not HISTORY_INSTANT.fullmatch(observed):
        raise ValueError('invalid history time')
    observed_at = dt.datetime.strptime(observed, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).timestamp()
    sources, buckets = history.get('sources'), history.get('buckets')
    if not isinstance(sources, list) or len(sources) > HISTORY_SOURCES or not isinstance(buckets, dict):
        raise ValueError('invalid history shape')
    providers = {}
    for source in sources:
        identity = source.get('id') if isinstance(source, dict) else None
        if (not isinstance(identity, str) or not SOURCE_ID.fullmatch(identity) or identity in providers
                or source.get('provider') not in CLOUD_PROVIDERS):
            raise ValueError('invalid or non-cloud history source')
        providers[identity] = source['provider']
    day_buckets = buckets.get('day')
    if not isinstance(day_buckets, list) or len(day_buckets) > HISTORY_DAY_BUCKETS:
        raise ValueError('invalid history buckets')
    days, previous = {}, ''
    for bucket in day_buckets:
        key = bucket.get('key') if isinstance(bucket, dict) else None
        if not isinstance(key, str) or not HISTORY_DAY.fullmatch(key) or key <= previous:
            raise ValueError('invalid history day')
        dt.date.fromisoformat(key)
        previous = key
        cells = bucket.get('sources')
        if not isinstance(cells, list) or not cells or len(cells) > HISTORY_SOURCES:
            raise ValueError('invalid history day')
        seen, rows = set(), {}
        for cell in cells:
            identity = cell.get('id') if isinstance(cell, dict) else None
            if identity not in providers or identity in seen:
                raise ValueError('unknown history cell')
            seen.add(identity)
            counts, covered = history_counts(cell.get('counts')), cell.get('coveredSeconds')
            if type(covered) is not int or not 0 <= covered <= MAX_SAFE:
                raise ValueError('invalid history coverage')
            row = rows.setdefault(providers[identity], {**dict.fromkeys(CLOUD_COUNTS, 0), 'covered': 0})
            for field in CLOUD_COUNTS:
                row[field] += counts[field]
                if row[field] > MAX_SAFE:
                    raise ValueError('history counter overflow')
            row['covered'] = max(row['covered'], covered)
        days[key] = rows
    return zone, observed_at, days


def cloud_cell(row):
    prompt = row['input_tokens'] + row['cache_read_tokens'] + row['cache_write_tokens']
    return {**{field: row[field] for field in CLOUD_COUNTS}, 'prompt_tokens': prompt, 'total_tokens': prompt + row['output_tokens']}


def checked_rollup_aggregates(record):
    """Bound the consumer's complete retained aggregation domain, without mutation.

    Nonnegative retained sums dominate every period/source/selection subset.
    API calls have their own bound; missing components remain unknown, not zero
    in the published cells. This scratch sum is admission only, never a metric.
    """
    totals = dict.fromkeys((*CLOUD_COUNTS, 'prompt_tokens', 'total_tokens'), 0)
    for day in record['days'].values():
        for cells in (day['byModel'], day['byProvider']):
            for cell in cells.values():
                for field in totals:
                    value = cell.get(field)
                    if value is not None:
                        combined = totals[field] + integer(value)
                        if combined > MAX_SAFE:
                            raise ValueError('roll-up retained counter overflow')
                        totals[field] = combined
    return record


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
            CREATE TABLE IF NOT EXISTS uptime (day TEXT PRIMARY KEY, first REAL NOT NULL, last REAL NOT NULL, gap REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS cloud_daily (
                day TEXT NOT NULL, provider TEXT NOT NULL, timezone TEXT NOT NULL,
                input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
                reasoning_tokens INTEGER NOT NULL, api_call_count INTEGER NOT NULL,
                covered_seconds INTEGER NOT NULL, PRIMARY KEY(day, provider));
        ''')
        self.beat = None

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
            peer_at = self.db.execute('SELECT MAX(sampled) FROM snapshots WHERE epoch=? AND model<>?', (epoch, model)).fetchone()[0]
            if peer_at is not None:
                # A worker has one durable alias owner. Listing reordering or
                # alias membership changes must not revive overlapping old
                # baselines. Rebaseline conservatively; retain daily history.
                if now <= peer_at and peer_at - now <= CLOCK_SLACK:
                    raise ValueError('non-monotonic sample time')
                old = None
                # Keep the model's retention slot; deleting it would unbound
                # historical aliases. Empty epoch cannot be an incoming ID.
                self.db.execute("UPDATE snapshots SET epoch='' WHERE epoch=? AND model<>?", (epoch, model))
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
            previous = self.db.execute('SELECT input,output FROM daily WHERE day=? AND model=?', (day, model)).fetchone() or (0, 0)
            try:
                accumulated = tuple(integer(integer(previous[i], 2**63 - 1) + delta[i], 2**63 - 1)
                                    for i in range(2))
            except ValueError as exc:
                raise ValueError('invalid accumulated counters') from exc
            # Validate Python integers before SQLite can promote overflowing sums
            # to REAL. Any refusal rolls back snapshot, coverage and pruning too.
            self.db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?)', (model, epoch, *values, now))
            self.db.execute('INSERT INTO daily VALUES (?,?,?,?) ON CONFLICT(day,model) DO UPDATE SET input=excluded.input, output=excluded.output', (day, model, *accumulated))
            self.db.execute('INSERT INTO coverage VALUES (?,?,?) ON CONFLICT(day) DO UPDATE SET first=min(first,excluded.first), last=max(last,excluded.last)', (day, now, now))
        return delta

    def record(self, now, status):
        live = status if isinstance(status, dict) else None
        status = 'Live activity sampled · history retained' if live else status
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
        record = {'id': 'local', 'name': 'Local', 'ready': True, 'limits': [],
                'tierLabel': 'llama.cpp router', 'usageStatusText': status,
                'authHelpText': 'Sampled evaluated input + generated output on this machine; cached input is not counted. First samples are baselines. Model unloads, downtime and day-boundary sampling can leave gaps. Not full request accounting.',
                'hasPromptStats': False, 'hasLocalStats': bool(recorded),
                'todayTotalTokens': totals.get(str(today), 0), 'todayTokensByModel': today_models,
                'recentDays': days, 'modelUsage': models, 'totalPrompts': 0, 'totalSessions': 0,
                'activeDays': sum(v > 0 for v in totals.values())}
        record['localLive'] = live
        record['readOnly'] = True
        start = today - dt.timedelta(days=today.weekday())
        week_rows = list(self.db.execute('SELECT input,output FROM daily WHERE day BETWEEN ? AND ?', (str(start), str(today))))
        covered = sorted(day for day in recorded if str(start) <= day <= str(today))
        inp = sum(row[0] for row in week_rows) if covered else None
        out = sum(row[1] for row in week_rows) if covered else None
        record['calendarWeek'] = {'startDate': str(start), 'endDate': str(today),
                                  'timezone': time.tzname[time.localtime(now).tm_isdst],
                                  'inputTokens': inp, 'outputTokens': out,
                                  'totalTokens': sum(row[0] + row[1] for row in week_rows) if covered else None,
                                  'recordedDays': len(covered), 'elapsedDays': today.weekday() + 1,
                                  'coverage': 'partial' if covered else 'unavailable'}
        return record

    def prune_rollup(self, now):
        cutoff = str(local_date(now) - dt.timedelta(days=ROLLUP_RETENTION_DAYS - 1))
        self.db.execute('DELETE FROM uptime WHERE day < ?', (cutoff,))
        self.db.execute('DELETE FROM cloud_daily WHERE day < ?', (cutoff,))

    def heartbeat(self, now):
        """Collector liveness by day: an outage reads as partial, an idle router as measured zero."""
        day = str(local_date(now))
        if self.beat and self.beat[0] == day and 0 <= now - self.beat[1] < UPTIME_WRITE:
            return
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT first,last,gap FROM uptime WHERE day=?', (day,)).fetchone()
            if row is None:
                self.db.execute('INSERT INTO uptime VALUES (?,?,?,0)', (day, now, now))
                self.prune_rollup(now)
            else:
                silence = now - row[1]
                self.db.execute('UPDATE uptime SET first=?, last=?, gap=? WHERE day=?',
                                (min(row[0], now), max(row[1], now), row[2] + (silence if silence > UPTIME_GAP else 0), day))
        self.beat = (day, now)

    def merge_cloud(self, history, now):
        """Retain each day's Cloud sums from a projection; returns the feed's state.

        The projection prunes old buckets within days, so the last value seen is
        kept here. The token row follows the largest observed token sum. API
        calls retain their own maximum: a measured-zero token interval can grow
        that independent counter. Observations are never added across replays,
        and each retained day remains a lower bound after a source reset.
        """
        zone, observed_at, days = cloud_days(history)
        latest = str(dt.datetime.fromtimestamp(now + CLOCK_SLACK, named_zone(zone)).date())
        if observed_at > now + CLOCK_SLACK or any(day > latest for day in days):
            return {'state': 'future'}
        if not zones_agree(zone, now):
            return {'state': 'misaligned', 'timezone': zone}
        feed = {'state': 'admitted', 'timezone': zone, 'observedAt': utc_stamp(observed_at)}
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.prune_rollup(now)
            for day, rows in days.items():
                for provider, row in rows.items():
                    old = self.db.execute('SELECT timezone,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,covered_seconds,api_call_count '
                                          'FROM cloud_daily WHERE day=? AND provider=?', (day, provider)).fetchone()
                    covered, api_calls = row['covered'], row['api_call_count']
                    if old and old[0] == zone:
                        covered, api_calls = max(covered, old[5]), max(api_calls, old[6])
                        if sum(old[1:5]) > sum(row[field] for field in CLOUD_COUNTS[:4]):
                            self.db.execute('UPDATE cloud_daily SET covered_seconds=?,api_call_count=? WHERE day=? AND provider=?',
                                            (covered, api_calls, day, provider))
                            continue
                    self.db.execute('INSERT OR REPLACE INTO cloud_daily VALUES (?,?,?,?,?,?,?,?,?,?)',
                                    (day, provider, zone,
                                     *(api_calls if field == 'api_call_count' else row[field] for field in CLOUD_COUNTS), covered))
            # Validate the prospective combined view before committing retained
            # rows or pruning. A refusal rolls back the whole optional ingest.
            self.rollup(now, feed)
        return feed

    def rollup(self, now, feed):
        """One dated Cloud + Local record. A source with no record for a date is null, never zero."""
        today = local_date(now)
        window = (str(today - dt.timedelta(days=ROLLUP_RETENTION_DAYS - 1)), str(today))
        samples = {row[0]: row[1:] for row in self.db.execute('SELECT day,first,last FROM coverage WHERE day BETWEEN ? AND ?', window)}
        beats = {row[0]: row[1:] for row in self.db.execute('SELECT day,first,last,gap FROM uptime WHERE day BETWEEN ? AND ?', window)}
        models, clouds, zones = {}, {}, {}
        for day, model, inp, out in self.db.execute('SELECT day,model,input,output FROM daily WHERE day BETWEEN ? AND ? AND input+output>0 ORDER BY day,model', window):
            models.setdefault(day, {})[model] = {'input_tokens': inp, 'output_tokens': out, 'total_tokens': inp + out}
        names = ('provider', 'timezone', *CLOUD_COUNTS, 'covered')
        for day, *row in self.db.execute('SELECT day,provider,timezone,' + ','.join(CLOUD_COUNTS) + ',covered_seconds FROM cloud_daily '
                                         'WHERE day BETWEEN ? AND ? ORDER BY day,provider', window):
            row = dict(zip(names, row))
            zone = row['timezone']
            # Only rows on the host's calendar may share a date key with Local.
            if zone not in zones:
                try:
                    zones[zone] = zones_agree(zone, now)
                except ValueError:
                    zones[zone] = False
            if zones[zone]:
                clouds.setdefault(day, {})[row['provider']] = row
        providers = sorted({provider for rows in clouds.values() for provider in rows})
        days = {}
        for day in sorted({*samples, *beats, *clouds}):
            start, end = day_span(day)
            expected = min(end, now) - start
            local = None
            if day in samples or day in beats:
                # Legacy coverage rows may have been inverted by a clock rollback.
                # Normalize the view only: never rewrite retained samples on render.
                spans = [(min(span[:2]), max(span[:2]))
                         for span in (samples.get(day), beats.get(day)) if span]
                first, last = min(span[0] for span in spans), max(span[1] for span in spans)
                gap = beats[day][2] if day in beats else 0
                local = {'partial': bool(first - start > LOCAL_DAY_SLACK or min(end, now) - last > LOCAL_DAY_SLACK or gap > 0),
                         'basis': 'collector' if day in beats else 'samples', 'first': utc_stamp(first), 'last': utc_stamp(last),
                         'coveredSeconds': int(max(0, min(last, end) - max(first, start) - gap)), 'gapSeconds': int(gap),
                         'daySeconds': int(end - start)}
            cloud = None
            if day in clouds:
                cells = {provider: {'coveredSeconds': row['covered'], 'partial': row['covered'] < expected - CLOUD_DAY_SLACK}
                         for provider, row in clouds[day].items()}
                covered = max(cell['coveredSeconds'] for cell in cells.values())
                cloud = {'partial': covered < expected - CLOUD_DAY_SLACK, 'coveredSeconds': covered,
                         'daySeconds': int(end - start), 'byProvider': cells}
            by_model = models.get(day, {}) if local else {}
            by_provider = {provider: cloud_cell(row) for provider, row in clouds.get(day, {}).items()}
            total = (sum(row['total_tokens'] for row in by_model.values())
                     + sum(row['total_tokens'] for row in by_provider.values()))
            if total > MAX_SAFE:
                raise ValueError('roll-up counter overflow')
            days[day] = {'total': total, 'byModel': by_model, 'byProvider': by_provider,
                         'coverage': {'partial': bool(local is None or local['partial'] or (cloud['partial'] if cloud else bool(providers))),
                                      'local': local, 'cloud': cloud}}
        def source(kind, provider):
            identity = json.dumps([ROLLUP_CONTRACT, kind, provider], separators=(',', ':'))
            return {'id': hashlib.sha256(identity.encode()).hexdigest(), 'kind': kind, 'provider': provider}
        return checked_rollup_aggregates({'contract': ROLLUP_CONTRACT, 'schemaVersion': 1, 'readOnly': True,
                'timezone': next((zone for zone, agreed in sorted(zones.items()) if agreed), host_zone_label()),
                'today': str(today), 'cloudFeed': feed,
                'sources': [source('cloud', provider) for provider in providers] + [source('local', 'local')],
                'days': days})


class RollupPublisher:
    """Publishes the roll-up beside local.json. Nothing here can stop sampling."""

    def __init__(self, ledger, history, output, collecting):
        self.ledger, self.history, self.output, self.collecting = ledger, history, output, collecting
        self.stamp, self.feed = None, {'state': 'missing'}
        self.published, self.published_at, self.failure = None, 0, None

    def ingest(self, now):
        try:
            info = os.stat(self.history)
        except FileNotFoundError:
            self.stamp, self.feed = None, {'state': 'missing'}
            return
        except OSError:
            # An optional unreadable Cloud source cannot suppress Local output.
            self.stamp, self.feed = None, {'state': 'unusable'}
            return
        stamp = (info.st_ino, info.st_mtime_ns, info.st_size)
        if stamp == self.stamp:
            return
        try:
            feed = self.ledger.merge_cloud(read_history(self.history), now)
        except (ValueError, OSError, RecursionError, TypeError, sqlite3.Error):
            # Keep retained days, publish Local, and retry this same projection.
            # A transient read/transaction failure must not consume its stamp.
            self.stamp, self.feed = None, {'state': 'unusable'}
            return
        self.stamp, self.feed = stamp, feed

    def tick(self, now):
        try:
            if self.collecting:
                self.ledger.heartbeat(now)
            self.ingest(now)
            feed = self.feed
            if feed['state'] == 'admitted':
                observed = dt.datetime.fromisoformat(feed['observedAt'].replace('Z', '+00:00')).timestamp()
                if now - observed > CLOUD_FEED_TTL:
                    # Do not consume or alter the cached projection: it still owns
                    # the retained dated facts, but no longer establishes liveness.
                    feed = {**feed, 'state': 'stale'}
            # The admission/heartbeat paths still run every tick. Build the dated
            # view only when its output can actually be published; do not scan a
            # year of history eleven times merely to discard it at the cadence gate.
            if (not self.published or str(local_date(now)) != self.published['today']
                    or feed != self.published['cloudFeed']
                    or not 0 <= now - self.published_at < ROLLUP_REFRESH):
                record = self.ledger.rollup(now, feed)
                state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
                try:
                    record = merge_claude_days(record, claude_token_days(state_home/'omarchy/agents/usage/claude.json'))
                except (ValueError, OSError, TypeError):
                    pass
                publish(self.output, {**record, 'observedAt': utc_stamp(now), 'validUntil': utc_stamp(rollup_valid_until(now))})
                self.published, self.published_at = record, now
            self.failure = None
        except Exception as exc:
            # Errors can contain paths or usage data: report the class, once per kind.
            if type(exc).__name__ != self.failure:
                self.failure = type(exc).__name__
                print('Unified roll-up unavailable (' + self.failure + ')', file=sys.stderr, flush=True)


def sample(ledger, now, boundary_gap=25, sampler=None, clock=None):
    """Shared historical/live polling; slot requests require separate admission.

    Without a sampler retain the legacy status API. main() retains a sampler
    across polls. Tests mock get/worker; no offline fixture sends HTTP.
    """
    observed = clock or (lambda: now)
    live = {'contract': 'local-task-rates/1', 'observedAt': now, 'staleAfterSec': MAX_RATE_GAP,
            'status': 'Live activity unavailable', 'models': []}
    def finish(status):
        if sampler is None:
            return status
        live['observedAt'] = observed()
        live['status'] = status
        return live
    try:
        listing = json.loads(get(ROUTER_PORT, '/models'))
    except urllib.error.HTTPError:
        if sampler is not None: sampler.retain([])
        raise
    except (urllib.error.URLError, OSError):
        if sampler is not None: sampler.retain([])
        return finish('Router unreachable' + RETAINED)
    models = listing.get('data') if isinstance(listing, dict) else None
    if not isinstance(models, list) or len(models) > 256 or not all(isinstance(m, dict) for m in models):
        if sampler is not None: sampler.retain([])
        raise ValueError('invalid model listing')
    states = [m.get('status', {}).get('value') if isinstance(m.get('status'), dict) else None for m in models]
    if any(not isinstance(s, str) or s not in ('loaded', 'unloaded', 'loading', 'sleeping', 'downloading', 'downloaded') for s in states):
        if sampler is not None: sampler.retain([])
        return finish('Model listing unavailable · activity unknown' + RETAINED)
    loaded = [m for m in models if m['status']['value'] == 'loaded']
    ids = [m.get('id') for m in loaded]
    if (len(ids) > MAX_MODELS or any(not isinstance(m, str) or not m or len(m) > 256 or any(ord(ch) < 32 for ch in m) for m in ids)
            or len(set(ids)) != len(ids)):
        if sampler is not None: sampler.retain([])
        raise ValueError('invalid loaded model listing')
    if sampler is not None: sampler.retain(ids)
    if not loaded:
        transitions = [s for s in ('loading', 'sleeping', 'downloading') if s in states]
        return finish('Model state · ' + ' · '.join(transitions) + ' · activity unknown' if transitions else 'No loaded model' + RETAINED)
    try:
        pid = router_pid()
    except (OSError, ValueError, subprocess.SubprocessError):
        if sampler is not None: sampler.retain([])
        return finish('Router service unavailable' + RETAINED)
    sampled, disabled, failure = 0, 0, None
    seen_workers = set()
    for model in loaded:
        alias = model['id']
        row = {'model': alias, 'state': 'loaded (activity unknown)', 'activeRequests': None,
               'slotsState': 'unavailable', 'promptTokPerSec': None, 'generationTokPerSec': None}
        if sampler is not None: live['models'].append(row)
        try:
            identity, port = worker(model, pid)
            if identity in seen_workers:
                row['state'] = 'shared worker (counted once)'
                if sampler is not None: sampler.previous.pop(alias, None)
                continue
            seen_workers.add(identity)
            try:
                metric_text = get(port, '/metrics')
                values = counters(metric_text)
                metrics_at = observed()
                if worker(model, pid) != (identity, port):
                    raise ValueError('worker changed during sample')
                ledger.observe(alias, identity, values, metrics_at, boundary_gap)
                if sampler is not None:
                    row['workerIdentity'] = identity
                    row['observedAt'] = metrics_at
                    row['engineMetrics'] = engine_metrics(metric_text, identity, metrics_at)
                sampled += 1
            except urllib.error.HTTPError as exc:
                if exc.code == 501: disabled += 1
                else: failure = failure or type(exc).__name__
            if sampler is not None:
                try:
                    if worker(model, pid, for_slots=True) != (identity, port):
                        raise ValueError('worker changed before slots')
                    rows = slots(json.loads(get(port, '/slots')))
                    slots_at = observed()
                    if worker(model, pid, for_slots=True) != (identity, port):
                        raise ValueError('worker changed after slots')
                    row.update(sampler.observe(alias, identity, rows, {}, slots_at))
                except Exception:
                    sampler.previous.pop(alias, None)
                    row['slotsReason'] = 'Safe slot admission or observation unavailable'
        except Exception as exc:
            failure = failure or type(exc).__name__
            if sampler is not None:
                sampler.previous.pop(alias, None)
                row['state'] = 'unavailable'
    if sampler is not None:
        independent = [m for m in live['models'] if m['state'] != 'shared worker (counted once)']
        processing = sum(m['state'] == 'processing' for m in independent)
        unknown = sum(m['state'] not in ('processing', 'idle') for m in independent)
        status = str(len(loaded)) + ' loaded model(s) · '
        status += ('activity unknown' if processing == 0 else 'at least ' + str(processing) + ' processing') + ' · ' + str(unknown) + ' unknown' if unknown else str(processing) + ' processing'
        return finish(status)
    if sampled > 0 and sampled == len(seen_workers):
        return f'Sampled tokens · {sampled} model(s) · cache excluded'
    if disabled:
        if sampled:
            return f'Sampled {sampled} of {len(loaded)} models · metrics disabled on {disabled}'
        return 'Metrics disabled · start llama.cpp with --metrics' + RETAINED
    if sampled:
        return f'Sampled {sampled} of {len(loaded)} models · {len(loaded) - sampled} unavailable ({failure})'
    return f'Collection unavailable ({failure})' + RETAINED


def claude_token_days(path):
    """Positive dated Claude Code totals explicitly declared as tokens.

    Require recentDaysUnit == 'tokens'; absence of activity markers is not units.
    A bad file contributes nothing. Zeros stay absent, not fabricated cloud coverage.
    """
    try:
        data = read_history(path)
    except (OSError, ValueError, RecursionError, TypeError):
        return {}
    days = data.get('recentDays') if isinstance(data, dict) and data.get('recentDaysUnit') == 'tokens' else None
    if not isinstance(days, list) or len(days) > 366:
        return {}
    native, seen = {}, set()
    for row in days:
        if not isinstance(row, dict) or 'sessionCount' in row or 'toolCallCount' in row:
            return {}
        date, count = row.get('date'), row.get('messageCount')
        if not isinstance(date, str) or not HISTORY_DAY.fullmatch(date) or date in seen:
            return {}
        seen.add(date)
        if row.get('recorded') is False:
            continue
        if type(count) is not int or not 0 <= count <= MAX_SAFE:
            return {}
        if count:
            native[date] = count
    return native


def rollup_source(kind, provider):
    identity = json.dumps([ROLLUP_CONTRACT, kind, provider], separators=(',', ':'))
    return {'id': hashlib.sha256(identity.encode()).hexdigest(), 'kind': kind, 'provider': provider}


def merge_claude_days(record, days):
    """Add Claude's dated totals as anthropic. Skip days the roll-up does not already have.

    If Hermes already published anthropic, leave it. Adding the CLI on top would double-count.
    """
    if not days or not isinstance(record, dict):
        return record
    if 'anthropic' in {row.get('provider') for row in record.get('sources', [])}:
        return record
    eligible = {day: tokens for day, tokens in days.items()
                if day <= record['today'] and day in record['days']
                and 'anthropic' not in record['days'][day]['byProvider']}
    if not eligible:
        return record
    # The caller retains the original roll-up if this optional merge fails.
    # Check every sum before changing either its inventory or dated cells.
    for day, tokens in eligible.items():
        if type(tokens) is not int or not 0 < tokens <= MAX_SAFE:
            raise ValueError('invalid Claude token total')
        if record['days'][day]['total'] + tokens > MAX_SAFE:
            raise ValueError('roll-up counter overflow')
    # The consumer also sums periods, each retained source, and all sources.
    # This total-only source changes no other metric. Bound its retained token
    # contribution before mutation so period/source/global sums remain safe.
    if (sum(rec['total'] for rec in record['days'].values())
            + sum(eligible.values()) > MAX_SAFE):
        raise ValueError('roll-up retained counter overflow')
    record['sources'].append(rollup_source('cloud', 'anthropic'))
    for day, tokens in eligible.items():
        rec = record['days'][day]
        # The CLI establishes only a dated total, never its metric breakdown.
        rec['byProvider']['anthropic'] = {
            'input_tokens': None, 'output_tokens': None, 'cache_read_tokens': None, 'cache_write_tokens': None,
            'reasoning_tokens': None, 'api_call_count': None, 'prompt_tokens': None, 'total_tokens': tokens}
        rec['total'] += tokens
        cover = rec['coverage']['cloud']
        cell = {'coveredSeconds': 0, 'partial': True}
        if cover is None:
            local = rec['coverage']['local'] or {}
            rec['coverage']['cloud'] = {'partial': True, 'coveredSeconds': 0,
                                        'daySeconds': local.get('daySeconds', 86400), 'byProvider': {'anthropic': cell}}
        else:
            cover['byProvider']['anthropic'] = cell
            cover['partial'] = True
    # Adding a Cloud source changes missing-coverage semantics on *every* day,
    # including Local-only dates that received no Claude cell. Keep the producer
    # consistent with the consumer's validateRollup coverage contract.
    for rec in record['days'].values():
        local, cover = rec['coverage']['local'], rec['coverage']['cloud']
        rec['coverage']['partial'] = bool(local is None or local['partial']
                                          or (cover['partial'] if cover is not None else True))
    return record


class PublicationLimitError(ValueError):
    """A bounded publication refusal, retryable by the continuous collector."""


class PublicationTemporaryError(OSError):
    """Something other than this publisher's own crash leftover holds the temporary name."""


def publication_temporary(path):
    """The single sibling temporary for a target, the same across crashes and path spellings."""
    path = Path(path)
    return path.with_name('.local-' + hashlib.sha256(os.fsencode(path.name)).hexdigest()[:24] + '.tmp')


def publish(path, record):
    path = Path(path)
    payload = json.dumps(record, allow_nan=False).encode()
    if len(payload) > PUBLICATION_LIMIT:
        raise PublicationLimitError('publication exceeded byte bound')
    temporary = publication_temporary(path).name
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    created = False
    try:
        try:
            stale = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            # A crash before the rename leaves exactly this file. Replace only what
            # this function creates; never follow, truncate or delete anything else.
            if (not stat.S_ISREG(stale.st_mode) or stale.st_uid != os.geteuid() or stale.st_nlink != 1
                    or stale.st_mode & 0o077 or stale.st_size > PUBLICATION_LIMIT):
                raise PublicationTemporaryError('unsafe publication temporary')
            os.unlink(temporary, dir_fd=directory)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
        current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (written.st_dev, written.st_ino):
            created = False
            raise PublicationTemporaryError('publication temporary changed')
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        # The data was synced before the rename; sync the directory entry itself too.
        os.fsync(directory)
    finally:
        if created:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)


class PublicationAliasError(ValueError):
    """An output or its deterministic temporary aliases an input or state path."""


def validate_publication_paths(state_dir, output, rollup_output, history_input):
    """Reserve names and existing inodes before any mkdir, lock, read or SQLite open.

    This is an admission-time alias check, not a filesystem compare-and-swap
    against a concurrent same-UID writer. Missing future sidecars are reserved too.
    """
    state_dir = Path(state_dir)
    ledger = state_dir/'tokens.sqlite3'
    protected = [ledger, *(ledger.with_name(ledger.name + suffix)
                            for suffix in ('-wal', '-shm', '-journal')),
                 state_dir/'collector.lock', Path(history_input)]

    def keys(path):
        path = Path(path)
        canonical = path.resolve(strict=False)
        try:
            info = path.stat()
        except FileNotFoundError:
            identity = None
        else:
            identity = (info.st_dev, info.st_ino)
        return canonical, identity

    names, identities = set(), set()
    for path in protected:
        canonical, identity = keys(path)
        names.add(canonical)
        if identity is not None:
            identities.add(identity)
    for output_path in (output, rollup_output):
        if output_path is None:
            continue
        for path in (Path(output_path), publication_temporary(output_path)):
            canonical, identity = keys(path)
            if canonical in names or (identity is not None and identity in identities):
                raise PublicationAliasError('publication path aliases protected storage')
            names.add(canonical)
            if identity is not None:
                identities.add(identity)


def main():
    state_home = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=state_home/'omarchy/local-router')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--publish', action='store_true', help='Write the record the patched Agents clone reads (outside the native usage directory)')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--render-only', action='store_true', help='No network or service inspection; export stored history')
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--rollup-output', type=Path, help='Unified dated Cloud + Local roll-up; --publish writes unified_daily.json beside local.json')
    parser.add_argument('--hermes-history', type=Path, default=state_home/'omarchy/hermes-token-accounting/history.json',
                        help='Hermes observed-history projection supplying dated Cloud days (read only)')
    args = parser.parse_args()
    if not 2 <= args.interval <= 3600:
        parser.error('interval must be between 2 and 3600 seconds')
    if args.publish:
        if args.output:
            parser.error('--publish and --output are mutually exclusive')
        args.output = state_home/'omarchy/local-router/agents/local.json'
        args.rollup_output = args.rollup_output or args.output.with_name('unified_daily.json')
    elif args.output and args.output.resolve().parent == (state_home/'omarchy/agents/usage').resolve():
        # The stock Agents widget treats every record there as syncable.
        parser.error('refusing to publish into the native Agents usage directory')
    if args.rollup_output:
        if args.rollup_output.resolve().parent == (state_home/'omarchy/agents/usage').resolve():
            parser.error('refusing to publish into the native Agents usage directory')
    try:
        validate_publication_paths(args.state_dir, args.output, args.rollup_output, args.hermes_history)
    except PublicationAliasError:
        parser.error('publication path aliases protected storage')
    except (OSError, RuntimeError, ValueError):
        parser.error('cannot validate publication paths')
    if args.publish:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.rollup_output:
        args.rollup_output.parent.mkdir(parents=True, exist_ok=True)
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
    publication_failure = None
    sampler = LiveSampler()
    rollup = None
    if args.rollup_output:
        rollup = RollupPublisher(ledger, args.hermes_history, args.rollup_output, collecting=not args.render_only)
    try:
        while True:
            now = time.time()
            status = 'Activation pending · not collecting'
            if not args.render_only:
                try:
                    status = sample(ledger, now, 3 * args.interval + 10, sampler=sampler, clock=time.time)
                except Exception as exc:
                    sampler.retain([])
                    # Errors can contain credentials, URLs or source bodies. Persist
                    # only their class, not their text or model launch arguments.
                    status = f'Collection unavailable ({type(exc).__name__})' + RETAINED
            try:
                record = ledger.record(now, status)
                if not args.output:
                    print(json.dumps(record, allow_nan=False), flush=True)
                elif record != published:
                    publish(args.output, record)
                    published = record
                publication_failure = None
            except (OSError, sqlite3.Error, PublicationLimitError) as exc:
                if not args.run:
                    raise
                # Keep sampling and retry next tick, without marking a failed
                # output as published or leaking arbitrary exception payloads.
                failure = type(exc).__name__
                if failure != publication_failure:
                    print(f'Local publication unavailable ({failure})', file=sys.stderr, flush=True)
                publication_failure = failure
            if rollup:
                rollup.tick(now)
            if not args.run:
                break
            time.sleep(args.interval)
    finally:
        ledger.db.close()
        lock.close()


if __name__ == '__main__':
    # Restrict this CLI's future files without chmod-ing retained state or
    # relaxing any stricter creation policy inherited from the caller.
    os.umask(os.umask(0o077) | 0o077)
    main()
