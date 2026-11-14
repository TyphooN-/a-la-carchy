#!/usr/bin/env python3
"""Stage a native Supersonic widget; optional explicit reversible layout change."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

ID = 'alacarchy.supersonic'
MARK = 'supersonic-ipc/1'
ORIGINAL = '_alaCarchySupersonicOriginal'
BASE = Path(__file__).resolve().parent
FILES = ('BarWidget.qml', 'Service.qml', 'IPCRequest.qml', 'ipc.py')


def native_delivery():
    path = BASE/'native_delivery.py'
    spec = importlib.util.spec_from_file_location('alacarchy_native_supersonic', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def regular(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('expected a regular file; refusing symlinks')


def validate_source(source):
    regular(source/'manifest.json')
    manifest = json.loads((source/'manifest.json').read_text())
    if manifest.get('id') != 'omarchy.media' or manifest.get('entryPoints', {}).get('barWidget') != 'BarWidget.qml':
        raise ValueError('unsupported native media source')
    regular(source/'BarWidget.qml')
    text = (source/'BarWidget.qml').read_text()
    if not all(token in text for token in ('import qs.Ui', 'BarWidget {', 'PopupCard {', 'Qt.MiddleButton', 'wheel.angleDelta.y')):
        raise ValueError('unsupported native media primitives')


def managed(target):
    if not target.is_dir() or target.is_symlink():
        return False
    try:
        regular(target/'manifest.json')
        manifest = json.loads((target/'manifest.json').read_text())
        meta = manifest.get('omarchy', {})
        native = native_delivery()
        if meta.get('alaCarchy') == native.MARK:
            return native.managed(target)
        if manifest.get('id') != ID or meta.get('alaCarchy') != MARK or manifest.get('kinds') != ['bar-widget']:
            return False
        for name in FILES:
            regular(target/name)
            if hashlib.sha256((target/name).read_bytes()).hexdigest() != meta.get('managedFiles', {}).get(name):
                return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def stage(source, target):
    if any(parent.is_symlink() for parent in target.absolute().parents):
        raise ValueError('symlink plugin parents refused')
    if target.exists() or target.is_symlink():
        raise ValueError('target must be new; existing plugins are never overwritten')
    native = native_delivery()
    if native.is_source(source):
        native.stage(source, target)
        return
    validate_source(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Hide incomplete plugin data from the shell registry.
    temporary = Path(tempfile.mkdtemp(prefix='.' + target.name + '.', dir=target.parent))
    try:
        plugin = temporary/'plugin'
        plugin.mkdir()
        for name in FILES:
            src = BASE/'ipc.py' if name == 'ipc.py' else BASE/'plugin'/name
            regular(src)
            shutil.copyfile(src, plugin/name)
        hashes = {name: hashlib.sha256((plugin/name).read_bytes()).hexdigest() for name in FILES}
        manifest = {'schemaVersion': 1, 'id': ID, 'name': 'Supersonic', 'version': '1.0.0', 'author': 'A La Carchy',
                    'description': 'Supersonic app-local now-playing and library search', 'kinds': ['bar-widget'],
                    'entryPoints': {'barWidget': 'BarWidget.qml'},
                    'barWidget': {'displayName': 'Supersonic', 'category': 'Media', 'allowMultiple': False},
                    # Not clonedFrom: do not redirect Omarchy's MPRIS service/shortcuts.
                    'omarchy': {'derivedFrom': 'omarchy.media', 'alaCarchy': MARK, 'managedFiles': hashes}}
        (plugin/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        os.rename(plugin, target)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def read_config(path):
    regular(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('shell configuration too large')
    config = json.loads(path.read_text())
    bar = config.get('bar') if isinstance(config, dict) else None
    layout = bar.get('layout') if isinstance(bar, dict) else None
    if not isinstance(layout, dict) or not all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows) for rows in layout.values()):
        raise ValueError('invalid bar layout')
    return config


def entries(config):
    return [row for rows in config['bar']['layout'].values() for row in rows if row.get('id') == ID]


def valid_entry(row):
    original = row.get(ORIGINAL, 'missing')
    return ORIGINAL in row and (original is None or (isinstance(original, dict) and original.get('id') == 'omarchy.media' and ORIGINAL not in original))


def write_config(path, config, before):
    regular(path)
    if path.read_bytes() != before:
        raise ValueError('shell config changed concurrently; refusing overwrite')
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(config, output, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.chmod(name, stat.S_IMODE(path.stat().st_mode))
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def configure(source, target, path, mode):
    before = path.read_bytes()
    config = read_config(path)
    rows = entries(config)
    if mode == 'check-disabled':
        return not rows
    if mode == 'check-enabled':
        if len(rows) != 1 or not valid_entry(rows[0]):
            return False
        native = native_delivery()
        if native.managed(target):
            return True
        if not source.exists() or native.is_source(source):
            return False
        return managed(target)
    if len(rows) > 1 or any(not valid_entry(row) for row in rows):
        raise ValueError('unmanaged or duplicate Supersonic layout entries; preserved')
    if mode == 'enable':
        native = native_delivery()
        if source.exists() or source.is_symlink():
            if native.is_source(source):
                native.render(source)
                if (target.exists() or target.is_symlink()) and not native.managed(target):
                    raise ValueError('legacy or changed Supersonic plugin preserved; guarded migration required')
        elif not native.managed(target):
            raise ValueError('reviewed canonical Supersonic source required')
        if rows:
            if not managed(target): raise ValueError('Supersonic plugin changed or missing; preserved')
            return True
        if target.exists() or target.is_symlink():
            if not managed(target): raise ValueError('existing custom plugin preserved')
        else:
            # Validate layout before creating any plugin.
            stock = [row for section in config['bar']['layout'].values() for row in section if row.get('id') == 'omarchy.media']
            if len(stock) > 1: raise ValueError('multiple native media entries; preserved')
            stage(source, target)
        replaced = False
        for section in config['bar']['layout'].values():
            for row in section:
                if row.get('id') == 'omarchy.media':
                    if replaced: raise ValueError('multiple native media entries; preserved')
                    original = copy.deepcopy(row)
                    row['id'] = ID
                    row[ORIGINAL] = original
                    replaced = True
        if not replaced:
            config['bar']['layout'].setdefault('center', []).append({'id': ID, ORIGINAL: None})
    elif mode == 'disable':
        if not rows: return True
        for key, section in config['bar']['layout'].items():
            restored = []
            for row in section:
                if row.get('id') != ID:
                    restored.append(row)
                elif row[ORIGINAL] is not None:
                    # Restore original widget ID, but preserve later per-entry edits.
                    restored.append({k: v for k, v in row.items() if k not in ('id', ORIGINAL)} | {'id': row[ORIGINAL]['id']})
            config['bar']['layout'][key] = restored
    else:
        raise ValueError('invalid operation')
    write_config(path, config, before)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/usr/share/omarchy/shell/plugins/supersonic'))
    parser.add_argument('--shell', type=Path)
    modes = parser.add_mutually_exclusive_group()
    for mode in ('enable', 'disable', 'check-enabled', 'check-disabled'):
        modes.add_argument('--' + mode, action='store_const', dest='mode', const=mode)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    try:
        if args.mode:
            if not args.shell: raise ValueError('layout operation requires --shell')
            return 0 if configure(args.source, args.target, args.shell, args.mode) else 1
        stage(args.source, args.target)
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        # Never echo arbitrary config, private paths or exception payloads.
        print('Supersonic staging/configuration refused; verify regular files, native source and managed plugin/layout.', file=__import__('sys').stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
