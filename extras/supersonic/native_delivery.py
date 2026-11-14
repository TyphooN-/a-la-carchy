#!/usr/bin/env python3
"""Version-bound packaging of canonical native Supersonic, not a second UI.

Inputs are a reviewed exact canonical snapshot. Only the widget's module name
and manifest identity/provenance change. The caller supplies the source; no
verification-cache pathname is embedded or consulted by installed code.
"""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

ID = 'alacarchy.supersonic'
SOURCE_ID = 'omarchy.supersonic'
MARK = 'supersonic-native/1'
MAX_FILE = 262144
MAX_TOTAL = 1048576
SOURCE_SHA256 = {'BarWidget.qml': 'd78c4106d40edc0e76d824c77708bcc8d58a41eda06cf46b9dfa79423b69c1f6', 'Service.qml': '5086848c01f05d0df87df6a98321848de772081153e928298575d1593d65662d', 'IPCRequest.qml': '9769fb46d5b17abe0029a075ef4533d7a6bcdce03f79e5c230ad3bbbebc73eb5', 'ipc.py': '24db29158489bf0cecd7f32bd721d77d4afe2fd871eef09a3029b9ad993c1279', 'manifest.json': '8b8b58a22ebc9173f1f6d7eecc7dbc927dad3ca2df4d263065d1ecb7d871e4ce'}
TARGET_SHA256 = {'BarWidget.qml': '969df94bfa867ef7cce027743ec45b249b0ea74b753eaed67452dbe61f34b5d4', 'Service.qml': '5086848c01f05d0df87df6a98321848de772081153e928298575d1593d65662d', 'IPCRequest.qml': '9769fb46d5b17abe0029a075ef4533d7a6bcdce03f79e5c230ad3bbbebc73eb5', 'ipc.py': '24db29158489bf0cecd7f32bd721d77d4afe2fd871eef09a3029b9ad993c1279'}
SOURCE_FINGERPRINT = '831b715b23994745d6901370c18a26e99a86b6c1fbf325facf3fd9a302b8847f'
CANONICAL_MANIFEST = json.loads('{\n  "schemaVersion": 1,\n  "id": "omarchy.supersonic",\n  "name": "Supersonic",\n  "version": "1.0.0",\n  "author": "Omarchy",\n  "description": "Supersonic app-local now-playing and playback controls",\n  "kinds": [\n    "bar-widget"\n  ],\n  "entryPoints": {\n    "barWidget": "BarWidget.qml"\n  },\n  "barWidget": {\n    "displayName": "Supersonic",\n    "description": "Supersonic metadata, exact-track timing and playback controls",\n    "category": "Media",\n    "allowMultiple": false\n  }\n}\n')


def _parents(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('linked Supersonic path refused')
    return path


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read(path, owners=None):
    owners = (os.getuid(),) if owners is None else owners
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in owners or before.st_size > MAX_FILE):
            raise ValueError('unowned, linked or oversized Supersonic file refused')
        chunks = []
        total = 0
        while True:
            block = os.read(fd, min(65536, MAX_FILE + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > MAX_FILE:
                raise ValueError('oversized Supersonic file refused')
        if _identity(os.fstat(fd)) != _identity(before):
            raise ValueError('Supersonic source changed while reading')
        return b''.join(chunks)
    finally:
        os.close(fd)


def _tree(path, names, source=False):
    owners = (0, os.getuid()) if source else (os.getuid(),)
    path = _parents(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners:
        raise ValueError('unowned Supersonic directory refused')
    if {p.name for p in path.iterdir()} != set(names):
        raise ValueError('unexpected Supersonic source entries refused')
    values = {name: _read(path/name, owners) for name in names}
    if sum(len(value) for value in values.values()) > MAX_TOTAL:
        raise ValueError('Supersonic tree too large')
    return values


def is_source(path):
    # Classification remains bounded/no-follow; validation pins every byte.
    path = _parents(path)
    manifest = json.loads(_read(path/'manifest.json', (0, os.getuid())))
    return isinstance(manifest, dict) and manifest.get('id') == SOURCE_ID


def manifest():
    value = json.loads(json.dumps(CANONICAL_MANIFEST))
    value['id'] = ID
    value['omarchy'] = {
        'derivedFrom': SOURCE_ID,
        'alaCarchy': MARK,
        'canonicalSourceSha256': SOURCE_FINGERPRINT,
        'managedFiles': dict(TARGET_SHA256),
    }
    return value


def render(source):
    values = _tree(source, SOURCE_SHA256, source=True)
    if any(hashlib.sha256(values[name]).hexdigest() != digest
           for name, digest in SOURCE_SHA256.items()):
        raise ValueError('unrecognized canonical Supersonic source')
    widget = values['BarWidget.qml'].decode('utf-8')
    anchor = 'moduleName: "omarchy.supersonic"'
    if widget.count(anchor) != 1:
        raise ValueError('ambiguous canonical Supersonic widget identity')
    values['BarWidget.qml'] = widget.replace(anchor, 'moduleName: "alacarchy.supersonic"').encode()
    values['manifest.json'] = (json.dumps(manifest(), indent=2) + '\n').encode()
    return values


def managed(target):
    try:
        values = _tree(target, SOURCE_SHA256)
        if json.loads(values['manifest.json']) != manifest():
            return False
        return all(hashlib.sha256(values[name]).hexdigest() == digest
                   for name, digest in TARGET_SHA256.items())
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def _publish_new(source, target):
    # Omarchy is Linux-only. Fail closed when atomic no-replace is unavailable;
    # an ordinary rename can replace an intervening empty user directory.
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, 'atomic no-replace unavailable') from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, 'new Supersonic destination unavailable')


def stage(source, target):
    target = _parents(target)
    if target.exists() or target.is_symlink():
        raise ValueError('existing Supersonic target preserved')
    values = render(source)  # Full admission before creating destination state.
    target.parent.mkdir(parents=True, exist_ok=True)
    _parents(target.parent)
    scratch = Path(tempfile.mkdtemp(prefix='.' + target.name + '.', dir=target.parent))
    try:
        plugin = scratch/'plugin'
        plugin.mkdir()
        for name, data in values.items():
            with (plugin/name).open('xb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
        if not managed(plugin):
            raise ValueError('canonical Supersonic output mismatch')
        # Recheck path admission; renameat2 separately protects the final leaf.
        _parents(target.parent)
        _publish_new(plugin, target)
        fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        shutil.rmtree(scratch)
