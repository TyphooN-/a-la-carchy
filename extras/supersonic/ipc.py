#!/usr/bin/env python3
"""Bounded Supersonic 0.22.1 local IPC. One JSON request on stdin; no logs."""
import base64
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import select
import socket
import stat
import struct
import subprocess
import sys
import time
from urllib.parse import urlencode

TIMEOUT = 2.0
MAX_BYTES = 512 * 1024
MAX_INPUT = 8192
MAX_RESULTS = 40

class Failure(Exception):
    def __init__(self, state):
        self.state = state

class DeadlineSocket(socket.socket):
    deadline: float
    def recv_into(self, buffer, nbytes=0, flags=0):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        self.settimeout(remaining)
        return super().recv_into(buffer, nbytes, flags)


def request(path):
    runtime = os.environ.get('XDG_RUNTIME_DIR', '')
    if not runtime or not Path(runtime).is_absolute():
        raise Failure('absent')
    directory = Path(runtime)
    ds = directory.lstat()
    if not stat.S_ISDIR(ds.st_mode) or ds.st_uid != os.getuid() or ds.st_mode & 0o022:
        raise Failure('unsafe-socket')
    address = directory / 'supersonic.sock'
    try:
        info = address.lstat()
    except FileNotFoundError:
        raise Failure('absent') from None
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise Failure('unsafe-socket')
    with DeadlineSocket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.deadline = time.monotonic() + TIMEOUT
        sock.settimeout(TIMEOUT)
        sock.connect(str(address))
        # Verify the connected peer too, rather than trusting a pre-connect stat.
        _, uid, _ = struct.unpack('3i', sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise Failure('unsafe-socket')
        sock.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: supersonic\r\nConnection: close\r\n\r\n').encode('ascii'))
        response = http.client.HTTPResponse(sock)
        try:
            response.begin()
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise Failure('too-large')
            body = json.loads(raw)
            if not isinstance(body, dict) or 'data' not in body or not isinstance(body.get('error'), str):
                raise Failure('invalid-response')
            if body['error'] or response.status != 200:
                if path == '/current_track' and body['error'] == 'nothing is playing right now':
                    raise Failure('idle')
                raise Failure('ipc-error')
            return body['data']
        finally:
            response.close()


def text(value):
    # Keep markup as literal text; remove display controls, never emit raw paths.
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value[:1024] if ord(c) >= 32 and ord(c) != 127)


def names(value):
    return ', '.join(text(v) for v in value[:12] if isinstance(v, str)) if isinstance(value, list) else ''


def year(value):
    return value if type(value) is int and 1 <= value <= 9999 else None


def metadata(data):
    if not isinstance(data, dict):
        raise Failure('invalid-response')
    item_id = identifier(data.get('ID'))
    # Supersonic 0.22.1 backend/mpris.go: UTF-8 base32 with '0' padding.
    # Match exact IDs across IPC/MPRIS, never a potentially duplicate title.
    mpris_id = mpris_track_id(item_id)
    return {'id': item_id, 'mprisTrackId': mpris_id, 'title': text(data.get('Name')), 'artist': names(data.get('Artists')),
            'album': text(data.get('Album')), 'year': None, 'type': data.get('Type')}


def mpris_track_id(item_id):
    return '/Supersonic/Track/' + base64.b32encode(item_id.encode('utf-8')).decode('ascii').replace('=', '0')


def timing(item_id):
    # Qt's metadata map preserves QDBusObjectPath as an opaque QVariant. Decode
    # one typed, read-only GetAll snapshot instead of matching by title/album.
    command = ['busctl', '--user', '--timeout=1s', '--json=short', 'call',
               'org.mpris.MediaPlayer2.Supersonic', '/org/mpris/MediaPlayer2',
               'org.freedesktop.DBus.Properties', 'GetAll', 's', 'org.mpris.MediaPlayer2.Player']
    child = None
    pipe = None
    try:
        deadline = time.monotonic() + TIMEOUT
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        pipe = child.stdout
        assert pipe is not None  # Popen stdout=PIPE above.
        raw = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([pipe], [], [], remaining)[0]:
                raise TimeoutError
            chunk = os.read(pipe.fileno(), min(65536, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                raise ValueError
        if child.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
            raise ValueError
        snapshot = json.loads(raw)
        if snapshot['type'] != 'a{sv}' or len(snapshot['data']) != 1:
            raise ValueError
        props = snapshot['data'][0]
        def field(mapping, key, signature):
            value = mapping.get(key, {})
            return value.get('data') if value.get('type') == signature else None
        meta = field(props, 'Metadata', 'a{sv}')
        if not isinstance(meta, dict) or field(meta, 'mpris:trackid', 'o') != mpris_track_id(item_id):
            raise ValueError
        def seconds(value):
            return value / 1000000 if type(value) is int and 0 <= value <= 2**53 - 1 else None
        position = seconds(field(props, 'Position', 'x'))
        duration = seconds(field(meta, 'mpris:length', 'x'))
        if duration == 0:
            duration = None
        if position is not None and duration is not None:
            position = min(position, duration)
        status = field(props, 'PlaybackStatus', 's')
        playing = status == 'Playing' if status in ('Playing', 'Paused', 'Stopped') else None
        return {'state': 'ready', 'id': item_id, 'elapsed': position, 'duration': duration, 'playing': playing}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, TimeoutError, subprocess.TimeoutExpired):
        return {'state': 'timing-unknown', 'id': item_id}
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
        if pipe is not None:
            pipe.close()


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise Failure('invalid-response')
    return value


def query(value):
    if not isinstance(value, str) or not 2 <= len(value.strip()) <= 256 or any(ord(c) < 32 for c in value):
        raise Failure('invalid-query')
    return value.strip()


def search_one(kind, q):
    try:
        data = request('/transport/search-' + ('track' if kind == 'song' else 'album') + '?' + urlencode({'s': q}))
        if not isinstance(data, list):
            raise Failure('invalid-response')
        rows, seen = [], set()
        for row in data[:MAX_RESULTS]:
            if not isinstance(row, dict):
                raise Failure('invalid-response')
            item_id = identifier(row.get('ID'))
            if item_id in seen:
                continue
            seen.add(item_id)
            date = row.get('Date')
            rows.append({'id': item_id, 'kind': kind,
                         'title': text(row.get('Title' if kind == 'song' else 'Name')),
                         'artist': names(row.get('ArtistNames')), 'album': text(row.get('Album')) if kind == 'song' else '',
                         'year': year(row.get('Year') if kind == 'song' else (date.get('Year') if isinstance(date, dict) else None))})
        return {'state': 'ready', 'rows': rows, 'truncated': len(data) > MAX_RESULTS}
    except Failure as exc:
        state = exc.state
    except (socket.timeout, TimeoutError):
        state = 'timeout'
    except (ConnectionError, FileNotFoundError):
        state = 'absent'
    except (OSError, ValueError, http.client.HTTPException, RecursionError):
        state = 'invalid-response'
    return {'state': state, 'rows': [], 'truncated': False}


def search(kind, q):
    kinds = ('song', 'album') if kind == 'all' else (kind,)
    # Both bounded reads run together: the helper remains inside the native
    # IPCRequest watchdog rather than serializing two two-second deadlines.
    if len(kinds) == 2:
        with ThreadPoolExecutor(max_workers=2) as pool:
            parts = list(pool.map(lambda k: search_one(k, q), kinds))
    else:
        parts = [search_one(kind, q)]
    failures = [k for k, part in zip(kinds, parts) if part['state'] != 'ready']
    if len(failures) == len(parts):
        states = {part['state'] for part in parts}
        raise Failure(next(iter(states)) if len(states) == 1 else 'ipc-error')
    # Interleave kinds so album matches are not starved by a large track list.
    # IDs are unique within each kind, not across song/album namespaces.
    merged = []
    for i in range(max(len(part['rows']) for part in parts)):
        for part in parts:
            if i < len(part['rows']):
                merged.append(part['rows'][i])
    return {'state': 'ready', 'results': merged[:MAX_RESULTS],
            'truncated': len(merged) > MAX_RESULTS or any(part['truncated'] for part in parts),
            'partial': bool(failures), 'failedKinds': failures}


def perform(payload):
    if not isinstance(payload, dict):
        raise Failure('invalid-request')
    op = payload.get('op')
    if op == 'timing':
        return timing(identifier(payload.get('id')))
    if op == 'status':
        return {'state': 'ready', 'track': metadata(request('/current_track')), 'playing': None}
    if op == 'year':
        item_id = identifier(payload.get('id'))
        q = query(payload.get('query'))
        data = request('/transport/search-track?' + urlencode({'s': q}))
        if not isinstance(data, list):
            raise Failure('invalid-response')
        matches = [row for row in data[:MAX_RESULTS] if isinstance(row, dict) and row.get('ID') == item_id]
        # Ambiguous or out-of-cap results are unknown, not the first title match.
        value = year(matches[0].get('Year')) if len(matches) == 1 else None
        return {'state': 'ready', 'id': item_id, 'year': value}
    if op in ('playpause', 'next', 'previous'):
        request('/transport/' + op)
        return {'state': 'requested'}
    kind = payload.get('kind', 'all' if op == 'search' else None)
    if op == 'play' and kind not in ('song', 'album'):
        raise Failure('invalid-request')
    if op == 'search' and kind not in ('all', 'song', 'album'):
        raise Failure('invalid-request')
    if op == 'play':
        item_id = identifier(payload.get('id'))
        params = {'id': item_id}
        if kind == 'album':
            params.update(t='0', s='false')
        request('/transport/play-' + ('track' if kind == 'song' else 'album') + '?' + urlencode(params))
        return {'state': 'requested'}
    if op == 'search':
        return search(kind, query(payload.get('query')))
    raise Failure('invalid-request')


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise Failure('invalid-request')
        output = perform(json.loads(raw))
        rc = 0
    except Failure as exc:
        output = {'state': exc.state}
        rc = 0 if exc.state in ('idle', 'absent') else 1
    except (socket.timeout, TimeoutError):
        output, rc = {'state': 'timeout'}, 1
    except (ConnectionError, FileNotFoundError):
        output, rc = {'state': 'absent'}, 0
    except (OSError, ValueError, http.client.HTTPException, RecursionError):
        output, rc = {'state': 'invalid-response'}, 1
    print(json.dumps(output, ensure_ascii=True, separators=(',', ':')))
    return rc

if __name__ == '__main__':
    sys.exit(main())
