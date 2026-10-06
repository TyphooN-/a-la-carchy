#!/usr/bin/env python3
"""Opt-in Local router / Grok token tabs for the native Agents panel.

Both features share ONE managed Agents clone, reversibly swapped into the bar.
Each feature is tracked separately in the managed layout entry, so enabling or
disabling one never restages, rewrites or drops the other. What the clone can
show depends on the installed Agents plugin: where Omarchy collects Grok usage
itself, the clone adds Local only and the Grok tab is refused rather than
shadowing the first-party record. Nothing here starts collection, contacts the
router, reloads/restarts services or launches Grok.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib

CLONE_ID = 'alacarchy.agents'
SOURCE_ID = 'omarchy.agents'
# Clone provenance mark -> the tabs that clone can show.
MARKS = {'local-router-stats/1': ('local',), 'local-router-stats/1;grok-observed-stats/1': ('local', 'grok')}
NATIVE_GROK = ('this Omarchy release already shows Grok usage (limits and tokens) natively in the Agents panel; '
               'the A La Carchy Grok tab would shadow it and was not added')
NO_GROK_LAYOUT = 'the A La Carchy Grok tab does not support this Agents panel layout; nothing changed'
CLONE_WITHOUT_GROK = ('the staged ' + CLONE_ID + ' clone in use has no Grok tab; disable the Local tab and enable '
                      'again to rebuild it from the installed Agents plugin')
ENTRY_KEY = '_alaCarchyAgents'
FEATURES = ('local', 'grok')
UNIT = 'local-router-stats.service'
TOOL_FILES = ('agents-clone/manage.py', 'agents-clone/upgrade.py',
              'local-router-stats/stage_plugin.py', 'local-router-stats/collector.py',
              'local-router-stats/local-router-stats.service', 'grok-usage/stage_plugin.py',
              'grok-usage/collector.py', 'hermes-codex-usage/collector.py')
EXTRAS = Path(__file__).resolve().parents[1]
MAX_CONFIG = 1024 * 1024
BLOCK_START = '# >>> a-la-carchy grok-usage (managed by A La Carchy)'
BLOCK_END = '# <<< a-la-carchy grok-usage'
PLUGIN_ID = re.compile(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+')


class Refused(Exception):
    """A safe, printable refusal. Never carries config text or raw exceptions."""


def say(message):
    print('  ' + message)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Refused('checkout helper missing: ' + path.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Paths:
    def __init__(self, args):
        home = Path(os.environ.get('HOME') or Path.home())
        state = Path(os.environ.get('XDG_STATE_HOME') or home/'.local/state')
        config = Path(os.environ.get('XDG_CONFIG_HOME') or home/'.config')
        share = home/'.local/share/a-la-carchy'
        self.source = args.source
        self.shell = args.shell
        self.plugins = args.plugins or home/'.config/omarchy/plugins'
        self.clone = self.plugins/CLONE_ID
        self.registry = share/'managed-files.json'
        self.tools = share
        self.bridge_collector = share/'hermes-codex-usage/collector.py'
        self.bridge_unit = config/'systemd/user/hermes-codex-usage.service'
        self.bridge_timer = self.bridge_unit.with_suffix('.timer')
        self.bridge_home = getattr(args, 'hermes_home', None) or Path(os.environ.get('HERMES_HOME') or home/'.hermes')
        self.bridge_state = state/'omarchy/hermes-codex'
        self.bridge_usage = state/'omarchy/agents/usage'
        # The unit template runs %h/.local/share/a-la-carchy/..., so keep HOME-based.
        self.local_collector = share/'local-router-stats/collector.py'
        self.unit = config/'systemd/user'/UNIT
        self.local_record = state/'omarchy/local-router/agents/local.json'
        self.grok_collector = share/'grok-usage/collector.py'
        self.grok_state = state/'omarchy/grok-usage'
        self.grok_record = self.grok_state/'agents/grok.json'
        self.grok_dir = home/'.grok'
        self.grok_config = self.grok_dir/'config.toml'


# ----------------------------------------------------------------- files

def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular_or_absent(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Refused(f'{path.name} is not a regular file; preserved')
    return path.exists()


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Registry:
    """Hashes of files this tool installed, so upgrades replace only our bytes."""

    def __init__(self, path):
        self.path = path
        self.files = {}
        if regular_or_absent(path):
            try:
                data = json.loads(path.read_text())
                files = data.get('files') if isinstance(data, dict) else None
                if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
                    raise ValueError
                self.files = files
            except (ValueError, UnicodeError):
                raise Refused('managed-file registry unreadable; preserved') from None

    def save(self):
        atomic_write(self.path, (json.dumps({'version': 1, 'files': self.files}, indent=2, sort_keys=True) + '\n').encode(), 0o600)

    def owns(self, path, data):
        return self.files.get(str(path)) == digest(data)

    def install(self, path, data, mode):
        """Write data unless a foreign or user-modified file is in the way."""
        if regular_or_absent(path):
            current = path.read_bytes()
            if current != data and not self.owns(path, current):
                raise Refused(f'existing {path.name} differs from the managed copy; preserved')
            if current == data:
                if not self.owns(path, current):
                    self.files[str(path)] = digest(data)
                    self.save()
                return
        atomic_write(path, data, mode)
        self.files[str(path)] = digest(data)
        self.save()

    def remove(self, path, data):
        if not regular_or_absent(path):
            self.files.pop(str(path), None)
            return
        current = path.read_bytes()
        if current != data and not self.owns(path, current):
            raise Refused(f'{path.name} was modified; preserved for manual review')
        path.unlink()
        self.files.pop(str(path), None)
        self.save()


# ----------------------------------------------------------------- clone

def plugin_snapshot(root):
    """Complete bounded no-follow clone contents; no ignored special nodes."""
    root = Path(root).absolute()
    try:
        local = load('alc_clone_inventory', EXTRAS/'local-router-stats/stage_plugin.py')
        paths = local.source_files(root, require_nonempty_dirs=True, owned=True)
        return {p.relative_to(root).as_posix(): local.source_bytes(p, owned=True) for p in paths}
    except (OSError, ValueError):
        raise Refused('unsafe Agents clone filesystem; preserved') from None


def plugin_files(root):
    return {n: digest(data) for n, data in plugin_snapshot(root).items() if n != 'manifest.json'}


def clone_info(target, registry=None):
    """Validate clone shape, and ownership when a trusted registry is supplied.

    The editable manifest alone is not an ownership witness. Mutation callers
    must supply the registry or use the guarded upgrade's external preimage.
    """
    if not target.is_dir() or target.is_symlink():
        return None
    try:
        contents = plugin_snapshot(target)
        if 'manifest.json' not in contents:
            return None
        manifest = json.loads(contents['manifest.json'])
        meta = manifest.get('omarchy', {})
        managed = meta.get('managedFiles')
        if (manifest.get('id') != CLONE_ID or meta.get('clonedFrom') != SOURCE_ID or meta.get('alaCarchy') not in MARKS
                or manifest.get('kinds') != ['bar-widget'] or manifest.get('entryPoints', {}).get('barWidget') != 'Panel.qml'
                or not isinstance(managed, dict) or not managed or any('..' in Path(name).parts for name in managed)):
            return None
        actual = {n: digest(data) for n, data in contents.items()}
        if ({n: h for n, h in actual.items() if n != 'manifest.json'} != managed
                or not all(name in managed for name in ('Main.qml', 'Panel.qml', 'Agent.qml'))):
            return None
        if registry is not None:
            prefix = str(target) + '/'
            recorded = {n[len(prefix):]: h for n, h in registry.files.items() if n.startswith(prefix)}
            if recorded != actual:
                return None
        source = meta.get('alaCarchySource')
        return {'features': MARKS[meta['alaCarchy']], 'source': source if isinstance(source, str) else ''}
    except (OSError, ValueError, AttributeError, TypeError, Refused):
        return None


def clone_plan(paths):
    """What staging the installed Agents plugin would produce. Writes nothing.

    Returns (rendered files, features, why the Grok tab is unavailable or None).
    """
    try:
        local = load('alc_local_stage', EXTRAS/'local-router-stats/stage_plugin.py')
        grok = load('alc_grok_stage', EXTRAS/'grok-usage/stage_plugin.py')
    except Exception:
        raise Refused('full checkout required: Local/Grok staging helpers missing; nothing staged') from None
    try:
        try:
            return local, grok.render(paths.source, CLONE_ID), None
        except grok.NativeGrok:
            return local, local.render(paths.source, CLONE_ID), NATIVE_GROK
        except ValueError:
            return local, local.render(paths.source, CLONE_ID), NO_GROK_LAYOUT
    except Exception:
        raise Refused('installed Agents source is unsupported or unreadable; nothing staged') from None


def stage_clone(paths, plan, replace, registry=None):
    """Create a new clone, or transactionally rebuild an unused owned clone."""
    if replace:
        helper = load('alc_agents_rebuild', EXTRAS/'agents-clone/upgrade.py')
        try:
            return helper.upgrade(paths, unused=True, plan=plan, registry=registry)['clone']
        except helper.manage.Refused as exc:
            raise Refused(str(exc)) from None
    local, rendered, _ = plan
    if any(parent.is_symlink() for parent in paths.clone.absolute().parents):
        raise Refused('symlinked plugin directory parents are refused')
    paths.plugins.mkdir(parents=True, exist_ok=True)
    # Hidden while incomplete: the shell ignores dot entries in its plugin scan.
    scratch = Path(tempfile.mkdtemp(prefix='.' + CLONE_ID + '.', dir=paths.plugins))
    try:
        staged = scratch/'plugin'
        try:
            local.write(paths.source, staged, rendered)
        except Exception:
            raise Refused('installed Agents source is unsupported or unreadable; nothing staged') from None
        manifest = json.loads((staged/'manifest.json').read_text())
        if manifest.get('omarchy', {}).get('alaCarchy') not in MARKS:
            raise Refused('unexpected staged clone provenance; nothing staged')
        manifest['omarchy']['managedFiles'] = plugin_files(staged)
        (staged/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        trusted_hashes = plugin_files(staged) | {'manifest.json': digest((staged/'manifest.json').read_bytes())}
        os.rename(staged, paths.clone)
        return trusted_hashes
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------- layout

def read_config(path):
    if path.is_symlink() or not path.is_file():
        raise Refused('shell.json must be a regular file')
    data = path.read_bytes()
    if len(data) > MAX_CONFIG:
        raise Refused('shell.json is too large')
    try:
        config = json.loads(data)
    except ValueError:
        raise Refused('shell.json is not valid JSON; preserved') from None
    layout = config.get('bar', {}).get('layout') if isinstance(config, dict) and isinstance(config.get('bar'), dict) else None
    if not isinstance(layout, dict) or not all(isinstance(rows, list) and all(isinstance(r, dict) for r in rows) for rows in layout.values()):
        raise Refused('invalid bar layout; preserved')
    return data, config


def write_config(path, config, before):
    if path.is_symlink() or not path.is_file() or path.read_bytes() != before:
        raise Refused('shell.json changed during the update; nothing overwritten')
    atomic_write(path, (json.dumps(config, indent=2, ensure_ascii=False) + '\n').encode(), stat.S_IMODE(path.stat().st_mode))


def rows(config):
    return [row for section in config['bar']['layout'].values() for row in section]


def current_source(paths):
    """Fingerprint of the installed Agents plugin, or '' when it cannot be read."""
    try:
        return load('alc_local_stage', EXTRAS/'local-router-stats/stage_plugin.py').fingerprint(paths.source)
    except Exception:
        return ''


def marker(row):
    """Validated feature marker of a managed entry, or None if malformed."""
    value = row.get(ENTRY_KEY)
    if not isinstance(value, dict) or set(value) != {'original', 'features'}:
        return None
    original, features = value['original'], value['features']
    if original is not None and not (isinstance(original, dict) and original.get('id') == SOURCE_ID and ENTRY_KEY not in original):
        return None
    if not isinstance(features, list) or not features or len(set(features)) != len(features) or not all(f in FEATURES for f in features):
        return None
    return value


def custom_clones(config, plugins):
    found = []
    for row in rows(config):
        entry_id = row.get('id')
        if not isinstance(entry_id, str) or entry_id in (SOURCE_ID, CLONE_ID) or not PLUGIN_ID.fullmatch(entry_id):
            continue
        manifest = plugins/entry_id/'manifest.json'
        try:
            if manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size <= 65536:
                meta = json.loads(manifest.read_text()).get('omarchy', {})
                if isinstance(meta, dict) and meta.get('clonedFrom') == SOURCE_ID:
                    found.append(entry_id)
        except (OSError, ValueError, AttributeError):
            continue  # the shell cannot load an unparseable manifest either
    return found


def managed_entry(config):
    managed = [row for row in rows(config) if row.get('id') == CLONE_ID]
    if len(managed) > 1 or any(marker(row) is None for row in managed):
        raise Refused('unmanaged or duplicate Agents clone entries in the bar; preserved')
    return managed[0] if managed else None


# ------------------------------------------------------------ grok hook

def hook_command(paths):
    return shlex.join(['/usr/bin/python3', str(paths.grok_collector), '--hook', '--state-dir', str(paths.grok_state)])


def hook_block(command):
    return '\n'.join([BLOCK_START, '[ui.status_line]', 'type = "command"', 'command = ' + json.dumps(command),
                      'refresh_interval = 5', BLOCK_END]) + '\n'


OUR_BLOCK = re.compile(r'(?m)^' + re.escape(BLOCK_START) + r'\n\[ui\.status_line\]\ntype = "command"\ncommand = ("(?:[^"\\\n]|\\.)*")\nrefresh_interval = 5\n' + re.escape(BLOCK_END) + r'\n')


def parse_toml(text):
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        raise Refused('~/.grok/config.toml is not valid TOML; preserved') from None


def without_status_line(parsed):
    result = copy.deepcopy(parsed)
    ui = result.get('ui')
    if isinstance(ui, dict):
        ui.pop('status_line', None)
        if not ui:
            del result['ui']
    return result


def grok_config(paths):
    """(text or None, parsed, our block match or None)."""
    if not paths.grok_dir.is_dir() or paths.grok_dir.is_symlink():
        raise Refused('Grok is not set up for this user (~/.grok missing); nothing changed')
    if not regular_or_absent(paths.grok_config):
        return None, {}, None
    data = paths.grok_config.read_bytes()
    if len(data) > MAX_CONFIG:
        raise Refused('~/.grok/config.toml is too large; preserved')
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise Refused('~/.grok/config.toml is not UTF-8; preserved') from None
    parsed = parse_toml(text)
    starts = [m for m in re.finditer(r'(?m)^' + re.escape(BLOCK_START) + '$', text)]
    ends = [m for m in re.finditer(r'(?m)^' + re.escape(BLOCK_END) + '$', text)]
    if not starts and not ends:
        return text, parsed, None
    blocks = list(OUR_BLOCK.finditer(text))
    if len(starts) != 1 or len(ends) != 1 or len(blocks) != 1:
        raise Refused('the A La Carchy Grok status-line block was edited; preserved for manual review')
    match = blocks[0]
    try:
        command = json.loads(match.group(1))
    except ValueError:
        raise Refused('the A La Carchy Grok status-line block was edited; preserved for manual review') from None
    status = parsed.get('ui', {}).get('status_line') if isinstance(parsed.get('ui'), dict) else None
    if not isinstance(command, str) or status != {'type': 'command', 'command': command, 'refresh_interval': 5} or \
            not command.startswith('/usr/bin/python3 ') or '/a-la-carchy/grok-usage/collector.py' not in command or ' --hook' not in command:
        raise Refused('the A La Carchy Grok status-line block was edited; preserved for manual review')
    return text, parsed, match


def grok_hook_present(paths):
    try:
        return grok_config(paths)[2]
    except (OSError, Refused):
        return None


def verify_grok_text(new_text, expected):
    try:
        result = tomllib.loads(new_text)
    except (tomllib.TOMLDecodeError, ValueError):
        result = None
    if result != expected:
        raise Refused('the Grok status line cannot be changed without altering other settings (for example an inline "ui" table); preserved')


def replace_grok_config(paths, old_text, new_text, expected):
    verify_grok_text(new_text, expected)
    if old_text is not None:
        if paths.grok_config.read_bytes() != old_text.encode():
            raise Refused('~/.grok/config.toml changed during the update; nothing overwritten')
        backup = paths.grok_config.with_name(paths.grok_config.name + time.strftime('.backup.%Y%m%d_%H%M%S'))
        fd, name = tempfile.mkstemp(prefix=backup.name + '.', dir=paths.grok_config.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(old_text.encode())
        say('Backup: ' + name)
        mode = stat.S_IMODE(paths.grok_config.stat().st_mode)
    else:
        mode = 0o600  # Grok's config can hold private settings
    atomic_write(paths.grok_config, new_text.encode(), mode)


def grok_hook_plan(paths):
    """Validate (dry run) the Grok change: None if our block is present, else
    (old text, new text, expected parse) proven to add only our status line."""
    text, parsed, match = grok_config(paths)
    command = hook_command(paths)
    if match:
        if json.loads(match.group(1)) != command:
            raise Refused('an A La Carchy Grok hook with different paths exists; disable Grok tokens first')
        return None
    if 'ui' in parsed and not isinstance(parsed['ui'], dict):
        raise Refused('Grok config "ui" is not a table; preserved')
    if 'status_line' in parsed.get('ui', {}):
        raise Refused('Grok already has a status line ([ui.status_line]); it was preserved, not replaced')
    base = text or ''
    prefix = base + ('\n' if base and not base.endswith('\n') else '') + ('\n' if base.strip() else '')
    new_text = prefix + hook_block(command)
    expected = copy.deepcopy(parsed)
    expected.setdefault('ui', {})['status_line'] = {'type': 'command', 'command': command, 'refresh_interval': 5}
    verify_grok_text(new_text, expected)
    return text, new_text, expected


def add_grok_hook(paths):
    plan = grok_hook_plan(paths)
    if plan is None:
        return False
    replace_grok_config(paths, *plan)
    say('Added a [ui.status_line] command row to ~/.grok/config.toml')
    return True


def remove_grok_hook(paths):
    try:
        text, parsed, match = grok_config(paths)
    except Refused:
        if not paths.grok_dir.exists() and not paths.grok_dir.is_symlink():
            return  # Grok removed: nothing of ours can remain there
        raise
    if match is None:
        return
    start, end = match.span()
    # Drop the separating blank line add_grok_hook wrote, if it is still there.
    if text[:start].endswith('\n\n'):
        start -= 1
    new_text = text[:start] + text[end:]
    if not new_text.strip():
        if paths.grok_config.read_bytes() != text.encode():
            raise Refused('~/.grok/config.toml changed during the update; nothing removed')
        paths.grok_config.unlink()  # it held nothing but the A La Carchy block
    else:
        replace_grok_config(paths, text, new_text, without_status_line(parsed))
    say('Removed the A La Carchy status line from ~/.grok/config.toml; restart running Grok sessions to drop it')


# -------------------------------------------------------------- systemd

RUNNING = {'active', 'activating', 'deactivating', 'reloading', 'refreshing'}
STOPPED = {'inactive', 'failed'}
ENABLED = {'enabled', 'enabled-runtime', 'linked', 'linked-runtime', 'alias', 'indirect', 'generated', 'transient'}
NOT_ENABLED = {'disabled', 'static', 'masked', 'masked-runtime', 'not-found', 'bad'}


def systemctl(*args):
    exe = shutil.which('systemctl')
    if not exe:
        raise Refused('systemctl unavailable; collector service state unknown, nothing removed')
    try:
        return subprocess.run([exe, '--user', *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        raise Refused('systemctl --user failed; collector service state unknown, nothing removed') from None


def unit_state():
    active = systemctl('is-active', UNIT).stdout.strip()
    enabled = systemctl('is-enabled', UNIT).stdout.strip()
    if active not in RUNNING | STOPPED or enabled not in ENABLED | NOT_ENABLED:
        raise Refused('could not read the collector service state; nothing removed')
    return active in RUNNING, enabled in ENABLED


def stop_collector_service():
    running, enabled = unit_state()
    if not running and not enabled:
        return
    if systemctl('disable', '--now', UNIT).returncode != 0:
        raise Refused('could not stop/disable the local collector service; files preserved')
    running, enabled = unit_state()
    if running or enabled:
        raise Refused('the local collector service is still running or enabled; files preserved')
    say('Stopped and disabled ' + UNIT + ' (the router was not touched)')


# ------------------------------------------------------------- features

def checkout_bytes(relative):
    path = EXTRAS/relative
    if path.is_symlink() or not path.is_file():
        raise Refused('full checkout required: extras/' + relative + ' missing')
    return path.read_bytes()


def remove_record(path):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise Refused(f'{path.name} is not a regular file; preserved')
    if path.exists():
        path.unlink()


def publish_initial(paths, feature):
    record = paths.local_record if feature == 'local' else paths.grok_record
    if record.exists() or record.is_symlink():
        regular_or_absent(record)
        return
    command = ([sys.executable, str(paths.local_collector), '--render-only', '--publish'] if feature == 'local'
               else [sys.executable, str(paths.grok_collector), '--initialize', '--state-dir', str(paths.grok_state)])
    try:
        run = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise Refused('could not publish the initial panel record') from None
    if run.returncode != 0 or not record.is_file():
        raise Refused('could not publish the initial panel record')


def feature_installed(paths, feature):
    try:
        if feature == 'local':
            files = [(paths.local_collector, checkout_bytes('local-router-stats/collector.py')),
                     (paths.unit, checkout_bytes('local-router-stats/local-router-stats.service'))]
            record = paths.local_record
        else:
            files = [(paths.grok_collector, checkout_bytes('grok-usage/collector.py'))]
            record = paths.grok_record
            match = grok_hook_present(paths)
            if match is None or json.loads(match.group(1)) != hook_command(paths):
                return False
        for path, data in files:
            if not regular_or_absent(path) or path.read_bytes() != data:
                return False
        return record.is_file() and not record.is_symlink()
    except (OSError, Refused):
        return False


def feature_absent(paths, feature):
    if feature == 'local':
        return not any(p.exists() or p.is_symlink() for p in (paths.unit, paths.local_record))
    try:
        hook = grok_hook_present(paths) if paths.grok_dir.exists() else None
    except OSError:
        return False
    return hook is None and not (paths.grok_record.exists() or paths.grok_record.is_symlink()) and \
        (not paths.grok_dir.exists() or grok_config_readable(paths))


def grok_config_readable(paths):
    try:
        grok_config(paths)
        return True
    except (OSError, Refused):
        return False


def check(paths, feature, enabled):
    try:
        _, config = read_config(paths.shell)
        entry = managed_entry(config)
    except (OSError, Refused):
        return False
    listed = entry is not None and feature in entry[ENTRY_KEY]['features']
    if enabled:
        info = clone_info(paths.clone, Registry(paths.registry)) if listed else None
        return info is not None and feature in info['features'] and feature_installed(paths, feature)
    return not listed and feature_absent(paths, feature)


def enable(paths, feature):
    before, config = read_config(paths.shell)
    entry = managed_entry(config)
    others = custom_clones(config, paths.plugins)
    if others:
        raise Refused('another Agents clone is in the bar (' + ', '.join(sorted(others)) + '); preserved')
    stock = [row for row in rows(config) if row.get('id') == SOURCE_ID]
    if len(stock) > 1 or (stock and entry is not None):
        raise Refused('ambiguous Agents entries in the bar; preserved')
    info = None
    if paths.clone.exists() or paths.clone.is_symlink():
        info = clone_info(paths.clone, Registry(paths.registry))
        if info is None:
            raise Refused(CLONE_ID + ' exists but is not an unmodified managed clone; preserved')
    # A clone is a frozen copy of the packaged plugin. While no tab is enabled
    # nothing in the bar uses it, so that is the one safe moment to rebuild it
    # from a newer Omarchy; a clone in use is never touched. The Grok tab is
    # always judged against the INSTALLED plugin, whatever an older clone can do.
    probe = clone_plan(paths) if info is None or entry is None or feature == 'grok' else None
    plan = probe if info is None or (entry is None and info['source'] != probe[1]['manifest']['omarchy']['alaCarchySource']) else None
    features = MARKS[plan[1]['manifest']['omarchy']['alaCarchy']] if plan else info['features']
    if feature == 'grok' and probe[2] == NATIVE_GROK:
        raise Refused(NATIVE_GROK)
    if feature not in features:
        raise Refused(probe[2] or CLONE_WITHOUT_GROK)
    if feature == 'grok':
        grok_hook_plan(paths)  # validate before changing anything
    registry = Registry(paths.registry)
    if feature == 'local':
        collector = checkout_bytes('local-router-stats/collector.py')
        unit = checkout_bytes('local-router-stats/local-router-stats.service')
        for path, data in ((paths.local_collector, collector), (paths.unit, unit)):
            if regular_or_absent(path) and path.read_bytes() != data and not registry.owns(path, path.read_bytes()):
                raise Refused(f'existing {path.name} differs from the managed copy; preserved')
    else:
        collector = checkout_bytes('grok-usage/collector.py')
        if regular_or_absent(paths.grok_collector) and paths.grok_collector.read_bytes() != collector and \
                not registry.owns(paths.grok_collector, paths.grok_collector.read_bytes()):
            raise Refused('existing grok-usage collector differs from the managed copy; preserved')
    # Every refusal above happens before anything is written.
    if plan:
        clone_hashes = stage_clone(paths, plan, replace=info is not None, registry=registry)
        if info is None:
            prefix = str(paths.clone) + '/'
            registry.files = {n: h for n, h in registry.files.items() if not n.startswith(prefix)}
            registry.files.update({str(paths.clone/n): h for n, h in clone_hashes.items()})
            registry.save()
        say(('Rebuilt ' if info else 'Staged ') + CLONE_ID + ' (' + plan[1]['manifest']['name'] + ') from the installed Agents plugin')
    elif entry is not None and current_source(paths) not in ('', info['source']):
        say('Note: ' + CLONE_ID + ' was built from a different Omarchy Agents version and is in use, so it was kept as is.')
        say('      Disable both tabs and enable again to rebuild it from the installed version.')
    if feature == 'local':
        registry.install(paths.local_collector, collector, 0o644)
        registry.install(paths.unit, unit, 0o644)
        publish_initial(paths, 'local')
    else:
        registry.install(paths.grok_collector, collector, 0o644)
        publish_initial(paths, 'grok')
    hook_added = add_grok_hook(paths) if feature == 'grok' else False
    changed = False
    if entry is None:
        if stock:
            # Replace in place, keeping the widget's settings, like a supported clone.
            original = {k: v for k, v in stock[0].items() if k != ENTRY_KEY}
            stock[0].clear()
            stock[0].update({'id': CLONE_ID} | {k: v for k, v in original.items() if k != 'id'}
                            | {ENTRY_KEY: {'original': copy.deepcopy(original), 'features': [feature]}})
        else:
            config['bar']['layout'].setdefault('right', []).append({'id': CLONE_ID, ENTRY_KEY: {'original': None, 'features': [feature]}})
        changed = True
    elif feature not in entry[ENTRY_KEY]['features']:
        entry[ENTRY_KEY]['features'] = sorted(entry[ENTRY_KEY]['features'] + [feature])
        changed = True
    if changed:
        try:
            write_config(paths.shell, config, before)
        except (OSError, Refused):
            if hook_added:
                try:
                    remove_grok_hook(paths)  # do not leave a hook for a panel that was not enabled
                except (OSError, Refused):
                    say('The Grok status-line block remains; remove it manually if unwanted')
            raise
    if feature == 'local':
        say('Collection is NOT running. To activate after enabling llama.cpp --metrics at a safe router restart:')
        say('  systemctl --user daemon-reload && systemctl --user enable --now ' + UNIT)
    else:
        say('Grok reads its status line at startup: new Grok sessions will report token counters.')
        say('Subscription quota is not exported by Grok and stays unknown.')


def disable(paths, feature):
    before, config = read_config(paths.shell)
    entry = managed_entry(config)
    registry = Registry(paths.registry)
    if feature == 'local':
        unit = checkout_bytes('local-router-stats/local-router-stats.service')
        if regular_or_absent(paths.unit):
            current = paths.unit.read_bytes()
            if current != unit and not registry.owns(paths.unit, current):
                raise Refused(UNIT + ' was modified or is not ours; preserved')
            stop_collector_service()
            registry.remove(paths.unit, unit)
        remove_record(paths.local_record)
    else:
        remove_grok_hook(paths)
        remove_record(paths.grok_record)
    if entry is not None and feature in entry[ENTRY_KEY]['features']:
        features = [f for f in entry[ENTRY_KEY]['features'] if f != feature]
        if features:
            entry[ENTRY_KEY]['features'] = features
        else:
            original = entry[ENTRY_KEY]['original']
            for key, section in config['bar']['layout'].items():
                restored = []
                for row in section:
                    if row is not entry:
                        restored.append(row)
                    elif original is not None:
                        # Restore the stock ID; keep later per-entry setting edits.
                        restored.append({'id': original['id']} | {k: v for k, v in row.items() if k not in ('id', ENTRY_KEY)})
                config['bar']['layout'][key] = restored
        write_config(paths.shell, config, before)
    say('History, collector programs and the ' + CLONE_ID + ' plugin files were kept.')


def bridge_units(paths):
    # ExecStart is systemd syntax, not a shell command: escape expansion too.
    def argument(value):
        text = str(value)
        if not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise Refused('invalid bridge unit pathname')
        return json.dumps(text.replace('%', '%%').replace('$', '$$'), ensure_ascii=False)
    command = ' '.join(argument(v) for v in ('/usr/bin/python3', '-B', paths.bridge_collector,
                       '--hermes-home', paths.bridge_home, '--state-dir', paths.bridge_state,
                       '--usage-dir', paths.bridge_usage))
    service = ('[Unit]\nDescription=Read-only Hermes Codex quota publication\n\n[Service]\n'
               'Type=oneshot\nUMask=0077\nExecStart=' + command + '\n')
    timer = ('[Unit]\nDescription=Read-only Hermes Codex quota polling\n\n[Timer]\n'
             'OnStartupSec=2min\nOnUnitActiveSec=5min\nUnit=hermes-codex-usage.service\n\n'
             '[Install]\nWantedBy=timers.target\n')
    return [(paths.bridge_unit, service.encode()), (paths.bridge_timer, timer.encode())]


def install_tools(paths, bridge=False):
    helper = load('alc_agents_install', EXTRAS/'agents-clone/upgrade.py')
    replacements = [(paths.tools/name, checkout_bytes(name)) for name in TOOL_FILES]
    if bridge: replacements += bridge_units(paths)
    try:
        result = helper.install_files(paths, replacements)
    except helper.manage.Refused as exc:
        raise Refused(str(exc)) from None
    say('Managed Agents tools installed; no collection, layout or activation changes.')
    if result['backup']: say('Private backup: ' + result['backup'])


def upgrade(paths, preimage_path=None):
    helper = load('alc_agents_upgrade', EXTRAS/'agents-clone/upgrade.py')
    try:
        expected = None
        if preimage_path is not None:
            expected = json.loads(helper.read_file(preimage_path)[0])
        result = helper.upgrade(paths, expected)
    except helper.manage.Refused as exc:
        raise Refused(str(exc)) from None
    say('Upgraded ' + CLONE_ID + '; private backup: ' + result['backup'])
    say('No service or shell activation performed. Reload only at an authorized boundary.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/usr/share/omarchy/shell/plugins/agents'))
    parser.add_argument('--shell', type=Path, required=True)
    parser.add_argument('--plugins', type=Path)
    parser.add_argument('--hermes-home', type=Path, help='Install bridge only: explicit Hermes profile directory (no auth read)')
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('enable', 'disable', 'upgrade', 'install-tools', 'install-bridge', 'check-enabled', 'check-disabled'):
        modes.add_argument('--' + mode, action='store_const', dest='mode', const=mode)
    parser.add_argument('--preimage', type=Path, help='Upgrade only: exact caller-authorized task preimage JSON; no force override')
    parser.add_argument('feature', choices=FEATURES)
    args = parser.parse_args()
    if args.preimage is not None and args.mode != 'upgrade': parser.error('--preimage requires --upgrade')
    paths = Paths(args)
    if args.mode.startswith('check-'):
        try:
            return 0 if check(paths, args.feature, args.mode == 'check-enabled') else 1
        except Exception:
            return 1  # a state that cannot be read is neither enabled nor disabled
    try:
        if args.mode == 'upgrade': upgrade(paths, args.preimage)
        elif args.mode in ('install-tools', 'install-bridge'): install_tools(paths, args.mode == 'install-bridge')
        else: (enable if args.mode == 'enable' else disable)(paths, args.feature)
        return 0
    except Refused as exc:
        say('Refused: ' + str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Never echo config contents, private paths or exception payloads.
        say('Failed (' + type(exc).__name__ + '); see backups and rerun after review')
    return 1


if __name__ == '__main__':
    sys.exit(main())
