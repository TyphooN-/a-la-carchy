#!/usr/bin/env python3
"""Guarded re-stage of an owned Agents clone, enabled or unused.

No activation, collection, service calls, shell-layout writes or credential
reads. A caller-authorized exact preimage can preserve known task edits, never
an unrestricted force flag. Backups survive success and rollback.
"""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

spec = importlib.util.spec_from_file_location('agents_upgrade_manage', Path(__file__).with_name('manage.py'))
assert spec is not None and spec.loader is not None
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.absolute().parents)):
        raise manage.Refused('symlinked upgrade paths are refused; preserved')


def read_file(path):
    safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 4*manage.MAX_CONFIG:
            raise manage.Refused('nonregular, linked, oversized or foreign upgrade file; preserved')
        data = stream.read(4*manage.MAX_CONFIG+1)
        if len(data) > 4*manage.MAX_CONFIG: raise manage.Refused('oversized upgrade file; preserved')
        return data, stat.S_IMODE(info.st_mode)


def snapshot(root):
    safe_path(root)
    return manage.plugin_snapshot(root)


def hashes(files):
    return {n: manage.digest(b) for n, b in files.items()}


def verify_clone_baseline(files, baseline, mutable=()):
    """Bind immutable clone bytes to a registry or external caller witness."""
    actual = hashes(files)
    if (not isinstance(baseline, dict) or set(actual) != set(baseline)
            or not all(isinstance(n, str) and not Path(n).is_absolute()
                       and '..' not in Path(n).parts and Path(n).as_posix() == n
                       and isinstance(h, str) and re.fullmatch(r'[a-f0-9]{64}', h)
                       for n, h in baseline.items())
            or any(actual[n] != baseline[n] for n in actual if n not in mutable)):
        raise manage.Refused('clone differs from the trusted ownership baseline; preserved')


def authorized_clone(files, expected, baseline):
    """Manual approval is bounded to the two task-patched QML sources."""
    if hashes(files) != expected: raise manage.Refused('clone differs from the authorized preimage; preserved')
    verify_clone_baseline(files, baseline, ('Main.qml', 'Panel.qml'))
    try:
        manifest = json.loads(files['manifest.json'])
        meta = manifest['omarchy']
        declared = meta['managedFiles']
        actual = hashes({n: b for n, b in files.items() if n != 'manifest.json'})
        if (manifest['id'] != manage.CLONE_ID or meta['clonedFrom'] != manage.SOURCE_ID
                or meta['alaCarchy'] not in manage.MARKS or set(actual) != set(declared)
                or manifest['kinds'] != ['bar-widget'] or manifest['entryPoints']['barWidget'] != 'Panel.qml'
                or any(actual[n] != declared[n] for n in actual if n not in ('Main.qml', 'Panel.qml'))):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise manage.Refused('preimage cannot authorize foreign clone files; preserved') from None


def optional_file(path):
    safe_path(path)
    try:
        return read_file(path)
    except FileNotFoundError:
        return None


def install_files(paths, replacements):
    """Guarded tool/unit bootstrap; no clone, layout, auth or service changes."""
    safe_path(paths.registry)
    before_registry = optional_file(paths.registry)
    registry = manage.Registry(paths.registry)
    old = {}
    for path, data in replacements:
        current = optional_file(path)
        if current is not None and current[0] != data and not registry.owns(path, current[0]):
            raise manage.Refused('modified or unregistered installed tool/unit; preserved')
        old[path] = current
    if all(old[p] is not None and old[p][0] == b and registry.owns(p, b) for p, b in replacements):
        return {'backup': None, 'changed': False}
    paths.registry.parent.mkdir(parents=True, exist_ok=True)
    lock_path = paths.registry.with_name('.agents-upgrade.lock')
    safe_path(lock_path)
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'wb') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise manage.Refused('unsafe installer lock')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise manage.Refused('another Agents installer is running') from None
        backup_root = paths.registry.parent/'agents-upgrade-backups'
        safe_path(backup_root)
        backup_root.mkdir(mode=0o700, exist_ok=True)
        if backup_root.stat().st_uid != os.getuid() or stat.S_IMODE(backup_root.stat().st_mode) & 0o077:
            raise manage.Refused('installer backup directory must be private')
        backup = Path(tempfile.mkdtemp(prefix='tools-', dir=backup_root))
        if before_registry is not None: manage.atomic_write(backup/'registry', before_registry[0], 0o600)
        receipt = {'files': {str(p): None if v is None else manage.digest(v[0]) for p, v in old.items()}}
        for i, current in enumerate(old.values()):
            if current is not None: manage.atomic_write(backup/'files'/str(i), current[0], 0o600)
        manage.atomic_write(backup/'receipt.json', (json.dumps(receipt, indent=2)+'\n').encode(), 0o600)
        applied, registry_after = [], None
        def guard():
            if optional_file(paths.registry) != before_registry:
                raise manage.Refused('registry changed during tool installation')
        try:
            guard()
            for p, data in replacements:
                guard()
                if optional_file(p) != old[p]: raise manage.Refused('tool/unit changed during installation')
                manage.atomic_write(p, data, 0o644 if old[p] is None else old[p][1])
                applied.append((p, data))
                registry.files[str(p)] = manage.digest(data)
            guard()
            for p, data in applied:
                installed = optional_file(p)
                if installed is None or installed[0] != data: raise manage.Refused('installed tool changed concurrently')
            registry_after = (json.dumps({'version': 1, 'files': registry.files}, indent=2, sort_keys=True)+'\n').encode()
            manage.atomic_write(paths.registry, registry_after, 0o600 if before_registry is None else before_registry[1])
            if read_file(paths.registry)[0] != registry_after: raise manage.Refused('installed registry changed concurrently')
            return {'backup': str(backup), 'changed': True}
        except (OSError, ValueError, manage.Refused):
            recovery = False
            if registry_after is not None:
                current = optional_file(paths.registry)
                if current is not None and current[0] == registry_after:
                    try:
                        if before_registry is None: paths.registry.unlink()
                        else: manage.atomic_write(paths.registry, *before_registry)
                    except OSError: recovery = True
            for p, data in reversed(applied):
                current = optional_file(p)
                if current is not None and current[0] == data:
                    try:
                        if old[p] is None: p.unlink()
                        else: manage.atomic_write(p, *old[p])
                    except OSError: recovery = True
                else: recovery = True
            raise manage.Refused(('tool rollback incomplete; ' if recovery else 'tools refused/rolled back; ')
                                 + 'private backup retained at ' + str(backup)) from None


def upgrade(paths, preimage=None, *, unused=False, plan=None, registry=None):
    """Return backup path and new clone hashes after a verified local upgrade.

    Exact-byte checks and an advisory lock protect cooperating installers.
    They are not OS-level compare-and-swap against a hostile same-user writer.
    A crash can retain an incomplete transaction; inspect the private backup
    before retrying. Never infer ownership from the destination name alone.
    """
    for path in (paths.clone, paths.registry, paths.shell, paths.plugins): safe_path(path)
    shell_before, _ = read_file(paths.shell)
    _, config = manage.read_config(paths.shell)
    entry = manage.managed_entry(config)
    stock = [r for r in manage.rows(config) if r.get('id') == manage.SOURCE_ID]
    if manage.custom_clones(config, paths.plugins):
        raise manage.Refused('another Agents clone is enabled; preserved')
    if unused:
        if entry is not None or len(stock) > 1 or preimage is not None:
            raise manage.Refused('unused rebuild needs an unmodified disabled clone; preserved')
    elif entry is None or stock:
        raise manage.Refused('one enabled managed Agents entry required; preserved')
    enabled_features = () if entry is None else entry[manage.ENTRY_KEY]['features']
    registry_before, registry_mode = read_file(paths.registry)
    loaded_registry = manage.Registry(paths.registry)
    if registry is None:
        registry = loaded_registry
    elif registry.path != paths.registry or registry.files != loaded_registry.files:
        raise manage.Refused('registry changed before clone replacement; preserved')
    old_clone = snapshot(paths.clone)
    prefix = str(paths.clone) + '/'
    baseline = {n[len(prefix):]: h for n, h in registry.files.items() if n.startswith(prefix)}
    if preimage is not None:
        fields = {'version', 'clone', 'registry', 'shell', 'files'}
        schema = (isinstance(preimage, dict) and
                  ((preimage.get('version') == 1 and set(preimage) == fields) or
                   (preimage.get('version') == 2 and set(preimage) == fields | {'clone_baseline'})))
        if (not schema or preimage['registry'] != manage.digest(registry_before)
                or preimage['shell'] != manage.digest(shell_before) or not isinstance(preimage['files'], dict)):
            raise manage.Refused('shell/registry differs from the authorized preimage; preserved')
        if preimage['version'] == 2:
            if baseline and baseline != preimage['clone_baseline']:
                raise manage.Refused('external baseline cannot override registered ownership; preserved')
            baseline = preimage['clone_baseline']
        if not baseline:
            raise manage.Refused('legacy clone needs a separate trusted baseline; preserved')
        authorized_clone(old_clone, preimage['clone'], baseline)
    else:
        verify_clone_baseline(old_clone, baseline)
        if manage.clone_info(paths.clone, registry) is None:
            raise manage.Refused('clone is edited or unmanaged; an exact authorized task preimage is required')
    plan = plan if plan is not None else manage.clone_plan(paths)
    local, rendered, _ = plan
    supported = manage.MARKS[rendered['manifest']['omarchy']['alaCarchy']]
    if any(f not in supported for f in enabled_features):
        raise manage.Refused('upgrade would drop an enabled feature; disable that feature explicitly first')
    replacements = []
    if 'local' in enabled_features:
        replacements += [(paths.local_collector, manage.checkout_bytes('local-router-stats/collector.py')),
                         (paths.unit, manage.checkout_bytes('local-router-stats/local-router-stats.service'))]
    if 'grok' in enabled_features:
        replacements += [(paths.grok_collector, manage.checkout_bytes('grok-usage/collector.py'))]
    old_files = {}
    allowed = {str(p) for p, _ in replacements}
    if preimage is not None and not set(preimage['files']).issubset(allowed):
        raise manage.Refused('preimage names files outside this enabled feature; preserved')
    for p, _ in replacements:
        current, mode = read_file(p)
        approved = preimage is not None and preimage['files'].get(str(p)) == manage.digest(current)
        # Manual approval cannot adopt an unregistered program/unit.
        if str(p) not in registry.files or not (registry.owns(p, current) or approved):
            raise manage.Refused('unregistered or modified collector/unit; preserved')
        old_files[p] = (current, mode)
    lock_path = paths.registry.with_name('.agents-upgrade.lock')
    safe_path(lock_path)
    lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    with os.fdopen(lock_fd, 'wb') as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise manage.Refused('unsafe upgrade lock')
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise manage.Refused('another Agents upgrade is running') from None
        backup_root = paths.registry.parent/'agents-upgrade-backups'
        safe_path(backup_root)
        backup_root.mkdir(mode=0o700, exist_ok=True)
        if backup_root.stat().st_uid != os.getuid() or stat.S_IMODE(backup_root.stat().st_mode) & 0o077:
            raise manage.Refused('upgrade backup directory must be private')
        backup = Path(tempfile.mkdtemp(prefix='upgrade-', dir=backup_root))
        shutil.copytree(paths.clone, backup/'clone')
        manage.atomic_write(backup/'registry', registry_before, 0o600)
        manage.atomic_write(backup/'shell', shell_before, 0o600)
        for i, (p, (data, _)) in enumerate(old_files.items()): manage.atomic_write(backup/'files'/str(i), data, 0o600)
        (backup/'receipt.json').write_text(json.dumps({'clone': hashes(old_clone), 'files': [str(p) for p in old_files]}, indent=2)+'\n')
        (backup/'receipt.json').chmod(0o600)
        scratch = Path(tempfile.mkdtemp(prefix='.'+manage.CLONE_ID+'.upgrade-', dir=paths.plugins))
        staged, previous = scratch/'plugin', scratch/'previous'
        applied, swapped, registry_after = [], False, None
        original_moved = False
        new_clone = {}
        try:
            local.write(paths.source, staged, rendered)
            manifest = json.loads((staged/'manifest.json').read_text())
            manifest['omarchy']['managedFiles'] = manage.plugin_files(staged)
            (staged/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
            new_clone = snapshot(staged)
            if manage.current_source(paths) != rendered['manifest']['omarchy']['alaCarchySource']:
                raise manage.Refused('native source changed while staging; preserved')
            def guard():
                if read_file(paths.shell)[0] != shell_before or read_file(paths.registry)[0] != registry_before:
                    raise manage.Refused('shell/registry changed during upgrade; preserved')
                if manage.current_source(paths) != rendered['manifest']['omarchy']['alaCarchySource']:
                    raise manage.Refused('native source changed during upgrade; preserved')
            guard()
            if snapshot(paths.clone) != old_clone: raise manage.Refused('clone changed during upgrade; preserved')
            for p, data in replacements:
                guard()
                if read_file(p)[0] != old_files[p][0]: raise manage.Refused('collector/unit changed during upgrade; preserved')
                manage.atomic_write(p, data, old_files[p][1]); applied.append((p, data))
            guard()
            if snapshot(paths.clone) != old_clone: raise manage.Refused('clone changed during upgrade; preserved')
            os.rename(paths.clone, previous)
            original_moved = True
            os.rename(staged, paths.clone)
            swapped = True
            guard()
            if snapshot(paths.clone) != new_clone: raise manage.Refused('installed clone changed during upgrade')
            for p, data in replacements:
                if read_file(p)[0] != data: raise manage.Refused('installed collector changed during upgrade')
                registry.files[str(p)] = manage.digest(data)
            prefix = str(paths.clone) + '/'
            registry.files = {n: h for n, h in registry.files.items() if not n.startswith(prefix)}
            registry.files.update({str(paths.clone/n): manage.digest(b) for n, b in new_clone.items()})
            registry_after = (json.dumps({'version': 1, 'files': registry.files}, indent=2, sort_keys=True)+'\n').encode()
            if unused:
                registry.save()
            else:
                manage.atomic_write(paths.registry, registry_after, registry_mode)
            if (read_file(paths.registry)[0] != registry_after or read_file(paths.shell)[0] != shell_before
                    or snapshot(paths.clone) != new_clone
                    or any(read_file(p)[0] != data for p, data in replacements)):
                raise manage.Refused('upgrade changed concurrently; inspect the private backup')
            return {'backup': str(backup), 'clone': hashes(new_clone)}
        except (OSError, ValueError, manage.Refused):
            recovery = False
            if registry_after is not None:
                try:
                    current_registry = read_file(paths.registry)[0]
                except (OSError, ValueError, manage.Refused):
                    recovery = True
                else:
                    if current_registry == registry_after:
                        try: manage.atomic_write(paths.registry, registry_before, registry_mode)
                        except OSError: recovery = True
                    elif current_registry != registry_before:
                        recovery = True
            if original_moved:
                try:
                    if snapshot(previous) != old_clone:
                        raise manage.Refused('original clone changed during recovery')
                    if swapped:
                        if snapshot(paths.clone) != new_clone:
                            raise manage.Refused('installed clone changed during recovery')
                        os.rename(paths.clone, scratch/'failed')
                    # A failed install may leave a concurrently created node.
                    # Restore only into a still-absent destination.
                    try:
                        paths.clone.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        raise manage.Refused('clone destination changed during recovery')
                    os.rename(previous, paths.clone)
                    if snapshot(paths.clone) != old_clone:
                        recovery = True
                except (OSError, ValueError, manage.Refused):
                    recovery = True
            for p, written in reversed(applied):
                try:
                    unmodified = read_file(p)[0] == written
                except (OSError, ValueError, manage.Refused):
                    unmodified = False
                if unmodified:
                    try: manage.atomic_write(p, old_files[p][0], old_files[p][1])
                    except OSError: recovery = True
                else: recovery = True
            raise manage.Refused(('upgrade rollback incomplete; ' if recovery else 'upgrade refused/rolled back; ')
                                 + 'private backup retained at ' + str(backup)) from None
        finally:
            # Never discard the original if a concurrent edit prevented rollback.
            if previous.exists():
                os.rename(previous, backup/'original-at-swap')
            shutil.rmtree(scratch)
