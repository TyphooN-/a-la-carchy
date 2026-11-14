#!/usr/bin/env python3
"""Native provider settings only: no collector, credentials, install or service calls.

Invoked by manage.py. Reuse its layout, clone ownership and guarded write helpers.
Settings control the native updater's --except path, not independent bridge timers.
"""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')


def read_file(path, manager, *, native=False):
    path = Path(path)
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise manager.Refused('linked or relative provider settings path; preserved')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_uid not in ({0, os.getuid()} if native else {os.getuid()}) or
                before.st_mode & 0o022 or before.st_size > manager.MAX_CONFIG):
            raise manager.Refused('unsafe provider settings input; preserved')
        raw = stream.read(manager.MAX_CONFIG + 1)
        after = os.fstat(stream.fileno())
    fields = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns)
    if len(raw) > manager.MAX_CONFIG or fields(before) != fields(after) or fields(after) != fields(path.lstat()):
        raise manager.Refused('provider settings input changed; preserved')
    return raw, fields(after)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate settings key')
        result[key] = value
    return result


def finite_number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non-finite JSON number')
    return number


def reject_constant(_value):
    raise ValueError('non-finite JSON constant')


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique, parse_float=finite_number,
                      parse_constant=reject_constant)


def view(paths, manager):
    raw, identity = read_file(paths.shell, manager)
    config = decode(raw)
    # Reuse maintained layout validation and bind that separate read to these bytes.
    checked, _ = manager.read_config(paths.shell)
    if checked != raw:
        raise manager.Refused('shell.json changed during provider read; preserved')
    candidates = [r for r in manager.rows(config) if r.get('id') in (manager.SOURCE_ID, manager.CLONE_ID)]
    if len(candidates) != 1 or manager.custom_clones(config, paths.plugins):
        raise manager.Refused('one supported Agents widget is required; preserved')
    entry = candidates[0]
    targets = [entry]
    if entry['id'] == manager.CLONE_ID:
        if manager.marker(entry) is None:
            raise manager.Refused('unmanaged Agents layout; preserved')
        registry_raw, registry_identity = read_file(paths.registry, manager)
        trusted = decode(registry_raw)
        if not isinstance(trusted, dict) or not isinstance(trusted.get('files'), dict):
            raise manager.Refused('invalid provider ownership registry; preserved')
        registry = manager.Registry(paths.registry)
        if (registry.files != trusted['files'] or
                read_file(paths.registry, manager) != (registry_raw, registry_identity)):
            raise manager.Refused('provider ownership registry changed; preserved')
        if manager.clone_info(paths.clone, registry) is None:
            raise manager.Refused('Agents clone ownership differs; preserved')
        original = entry[manager.ENTRY_KEY]['original']
        if original is not None:
            targets.append(original)
    source_raw, _ = read_file(paths.source / 'manifest.json', manager, native=True)
    main_raw, _ = read_file(paths.source / 'Main.qml', manager, native=True)
    manifest = decode(source_raw)
    widget = manifest.get('barWidget') if isinstance(manifest, dict) else None
    settings = widget.get('defaults') if isinstance(widget, dict) else None
    defaults = settings.get('providers') if isinstance(settings, dict) else None
    if (not isinstance(manifest, dict) or manifest.get('id') != manager.SOURCE_ID or not isinstance(defaults, dict) or not defaults or
            not all(ID.fullmatch(k) and isinstance(v, dict) and type(v.get('enabled', True)) is bool
                    for k, v in defaults.items()) or
            main_raw.count(b'function updateCommand(') != 1 or
            main_raw.count(b'providers[id] && providers[id].enabled === false') != 1 or
            main_raw.count(b'command.push("--except", id)') != 1):
        raise manager.Refused('unsupported native Agents provider contract; preserved')
    for target in targets:
        providers = target.get('providers', {})
        if not isinstance(providers, dict):
            raise manager.Refused('invalid provider settings; preserved')
        for key in defaults:
            value = providers.get(key, {})
            if not isinstance(value, dict) or ('enabled' in value and type(value['enabled']) is not bool):
                raise manager.Refused('invalid provider setting; preserved')
    providers = entry.get('providers', {})
    result = [{'id': key, 'enabled': providers.get(key, {}).get('enabled', defaults[key].get('enabled', True))}
              for key in sorted(defaults)]
    binding = hashlib.sha256(json.dumps([str(paths.shell), identity, hashlib.sha256(raw).hexdigest(),
                  hashlib.sha256(source_raw).hexdigest(), hashlib.sha256(main_raw).hexdigest()]).encode()).hexdigest()
    return {'binding': binding, 'providers': result}, raw, config, targets, defaults


def control(paths, manager, setting=None, expected=None):
    if setting is None:
        return view(paths, manager)[0]
    if not isinstance(expected, str) or re.fullmatch(r'[a-f0-9]{64}', expected) is None:
        raise manager.Refused('provider change requires the displayed shell binding')
    provider, choice = setting
    if not ID.fullmatch(provider) or choice not in ('enabled', 'disabled'):
        raise manager.Refused('invalid provider choice')
    # Cooperative per-config lock; write_config retains the concurrent-byte guard.
    # Neither promises atomic CAS against a hostile same-UID writer.
    read_file(paths.shell, manager)
    lock = os.open(paths.shell.with_name('.agents-providers.lock'),
                   os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_size):
            raise manager.Refused('unsafe provider settings lock; preserved')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        status, before, config, targets, defaults = view(paths, manager)
        if status['binding'] != expected:
            raise manager.Refused('displayed provider settings changed; preserved')
        if provider not in {p['id'] for p in status['providers']}:
            raise manager.Refused('unknown native provider; preserved')
        enabled = choice == 'enabled'
        changed = False
        for target in targets:
            current = target.get('providers', {}).get(provider, {}).get('enabled', defaults[provider].get('enabled', True))
            if current != enabled:
                target.setdefault('providers', {}).setdefault(provider, {})['enabled'] = enabled
                changed = True
        if not changed:
            return {'changed': False, 'provider': provider, 'enabled': enabled}
        intended = (json.dumps(config, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
        # Readback and later reads refuse oversized files; never install bytes they reject.
        if len(intended) > manager.MAX_CONFIG:
            raise manager.Refused('updated shell.json would be too large; preserved')
        fd, backup = tempfile.mkstemp(prefix=paths.shell.name + '.backup.agents-', dir=paths.shell.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(before)
            stream.flush()
            os.fsync(stream.fileno())
        if view(paths, manager)[0]['binding'] != expected:
            raise manager.Refused('provider settings changed before write; backup retained')
        manager.write_config(paths.shell, config, before)
        after, _ = read_file(paths.shell, manager)
        if after != intended:
            raise manager.Refused('provider settings readback differs; backup retained')
        directory = os.open(paths.shell.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {'changed': True, 'provider': provider, 'enabled': enabled, 'backup': Path(backup).name}
    finally:
        os.close(lock)
