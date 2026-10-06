#!/usr/bin/env python3
"""Stage a patched Agents clone in a NEW directory. Never installs or enables it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

SOURCE_ID = 'omarchy.agents'
NAME = 'Agents + Local'
MARK = 'local-router-stats/1'
UNSUPPORTED = 'unsupported Agents source; refusing an ambiguous patch'

# Follow the native dev panel's dark/light provider-mark convention.
LOCAL_ICON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <g fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <rect x="6" y="6" width="12" height="12" rx="2"/>
    <path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4M18 9h4m-4 6h4"/>
    <path d="M9 10v4h6v-4"/>
  </g>
</svg>'''
LOCAL_ASSETS = {'local.svg': LOCAL_ICON.format(color='#f5f5f5'),
                'local-light.svg': LOCAL_ICON.format(color='#202020')}

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(UNSUPPORTED)
    return text.replace(old, new, 1)

def transform_main(text):
    # The Local record lives outside the usage directory the stock widget
    # scans, and it is kept out of `agents`: localSnapshot() cannot include it
    # and its updates do not go through recordsChanged(), so they never start
    # a sync run. A stray usage/local.json is ignored rather than adopted.
    text = replace_once(text, '      if (name.slice(-5) === ".json") ids.push(name.slice(0, -5))',
                        '      if (name.slice(-5) === ".json" && name !== "local.json" && name !== "codex.json") ids.push(name.slice(0, -5))')
    text = replace_once(text, '  Instantiator {\n    id: agentInstantiator',
                        '  // a-la-carchy: the llama.cpp router record of this machine only.\n'
                        '  readonly property string localRouterRecordPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/local-router/agents/local.json"\n\n'
                        '  Agent {\n    id: localRouterAgent\n    agentId: "local"\n    path: root.localRouterRecordPath\n  }\n\n'
                        '  Instantiator {\n    id: agentInstantiator')
    text = replace_once(text, '    var syncedProviders = syncConfigured() && aggregateData && aggregateData.providers ? aggregateData.providers : {}',
                        '    var localRouterRecord = localRouterAgent.record\n'
                        '    if (localRouterRecord && String(localRouterRecord.id) === "local" && providerEnabled("local"))\n'
                        '      result.push(displayProvider(localRouterRecord)) // Status stays visible before recording starts.\n'
                        '    var syncedProviders = syncConfigured() && aggregateData && aggregateData.providers ? aggregateData.providers : {}')
    text = replace_once(text, '    for (var syncedId in syncedProviders) {',
                        '    for (var syncedId in syncedProviders) {\n      if (syncedId === "local") continue')
    text = replace_once(text, '      providerMap[String(record.id)] = providerSnapshot(record)',
                        '      if (String(record.id) === "local") continue // Router statistics stay on this machine.\n      providerMap[String(record.id)] = providerSnapshot(record)')
    # Re-check identity at admission as well as filtering the native filename.
    # This also suppresses a Codex provider arriving only through sync.
    text = replace_once(text, '  function providerEnabled(id) {',
                        '  function providerEnabled(id) {\n'
                        '    if ((id === "codex" || id.indexOf("codex-") === 0) && id !== "codex-hermes-1" && id !== "codex-hermes-2") return false // Only two Hermes slots.')
    text = replace_once(text, '  function displayProvider(record) {',
                        '  function displayProvider(record) {\n'
                        '    var external = record.readOnly === true || String(record.id) === "local" || String(record.id) === "codex-hermes-1" || String(record.id) === "codex-hermes-2"')
    text = replace_once(text, '      providerId: String(record.id),\n      providerName: String(record.name || record.id),\n      ready: record.ready === true || synced,',
                        '      providerId: String(record.id),\n      readOnly: external,\n'
                        '      providerName: String(record.id) === "codex-hermes-1" ? "Codex (Hermes 1)" : String(record.id) === "codex-hermes-2" ? "Codex (Hermes 2)" : String(record.name || record.id),\n'
                        '      ready: record.ready === true || synced,')
    text = replace_once(text, '      accounts: Array.isArray(record.accounts) ? record.accounts : [],',
                        '      accounts: external ? [] : (Array.isArray(record.accounts) ? record.accounts : []),')
    text = replace_once(text, '      accountSwitch: record.accountSwitch || ({ mode: "manual", threshold: 95 }),',
                        '      accountSwitch: external ? null : (record.accountSwitch || ({ mode: "manual", threshold: 95 })),')
    return replace_once(text, '  function syncedStatsFor(providerId) {',
                        '  function syncedStatsFor(providerId) {\n    if (providerId === "local") return null')

def transform_panel_tabs(text):
    """Omarchy 4.0.4 panel: one tab per agent with model rows and a 7-day chart."""
    text = replace_once(text, '    return rows.slice(0, 4)',
                        '    return p && p.providerId === "local" ? rows : rows.slice(0, 4)')
    text = replace_once(text, 'name: usage.friendlyModelName(id),',
                        'name: p && p.providerId === "local" ? id : usage.friendlyModelName(id),')
    text = replace_once(text, '    if (!row) return ""\n    return "In "',
                        '    if (!row) return ""\n    if (provider && provider.providerId === "local")\n      return "Evaluated input " + usage.formatTokenCount(row.input) + " · generated " + usage.formatTokenCount(row.output) + " · cached input not counted"\n    return "In "')
    text = replace_once(text, '  function dayTooltip(day, today) {',
                        '  function dayTooltip(day, today) {\n    if (day && day.recorded === false) return "Not recorded — not zero usage"')
    text = replace_once(text, 'text: usage.formatTokenCount(dayRow.day ? Number(dayRow.day.messageCount || 0) : 0)',
                        'text: dayRow.day && dayRow.day.recorded === false ? "—" : usage.formatTokenCount(dayRow.day ? Number(dayRow.day.messageCount || 0) : 0)')
    return text

LIMITS_TROUBLE_TEXT = '''    Text {
      visible: !section.multi && root.otherTrouble(section.provider) !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: section.provider ? String(section.provider.authHelpText || "") : ""
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
'''

LIMITS_TROUBLE_TEXT_CURRENT = LIMITS_TROUBLE_TEXT.replace(
    '      text: section.provider ? String(section.provider.authHelpText || "") : ""',
    '      // Shown for the status, so a record with no help to offer says the status rather than nothing.\n'
    '      text: section.provider ? String(section.provider.authHelpText || section.provider.usageStatusText || "") : ""')

LIMITS_LOCAL_TEXT = '''
    // a-la-carchy: sampled llama.cpp router tokens of this machine.
    Text {
      visible: !!section.provider && section.provider.providerId === "local"
      width: parent.width
      textFormat: Text.PlainText
      text: visible ? root.localRouterText(section.provider) : ""
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }
'''

LIMITS_LOCAL_FUNCTION = '''  // a-la-carchy: what the Local section says. These are sampled llama.cpp
  // router counters of this machine: a day without samples is a gap, never
  // zero usage, and cached input is not part of the counters.
  function localRouterText(p) {
    if (!p) return ""
    var lines = []
    var status = String(p.usageStatusText || "")
    if (status !== "") lines.push(status)
    var days = p.recentDays || []
    var recorded = 0
    var week = 0
    for (var i = 0; i < days.length; i++) {
      if (!days[i] || days[i].recorded !== true) continue
      recorded++
      week += Number(days[i].messageCount || 0)
    }
    var lastDay = days.length > 0 ? days[days.length - 1] : null
    var today = lastDay && lastDay.recorded === true
      ? usage.formatTokenCount(Number(p.todayTotalTokens || 0)) : "—"
    if (recorded > 0)
      lines.push("Today " + today + " · last 7 days "
        + usage.formatTokenCount(week) + " tokens (" + recorded + " of " + days.length + " days recorded)")
    else
      lines.push("No recorded days yet (not zero usage)")
    var models = p.todayTokensByModel || {}
    var names = Object.keys(models).filter(function(id) { return Number(models[id]) > 0 })
    names.sort(function(a, b) { return Number(models[b]) - Number(models[a]) || (a < b ? -1 : 1) })
    if (names.length > 0) {
      var shown = names.map(function(id) { return id + " " + usage.formatTokenCount(Number(models[id])) })
      lines.push("Today by model: " + shown.join(" · "))
    }
    lines.push("Evaluated input + generated output · cached input not counted")
    return lines.join("\\n")
  }

'''

def transform_panel_limits(text):
    """Limits-first panel (omarchy-dev 4.0.0.r6694): one section per agent.

    That panel has no per-agent token rows and reads any status text as
    trouble, so Local gets its own informational lines and stays out of the
    hero's cross-subscription token summary.
    """
    # Preserve stock subscription trouble semantics; Local is information.
    text = replace_once(text, '  function otherTrouble(item) {',
                        '  function otherTrouble(item) {\n'
                        '    if (item && item.providerId === "local") return "" // a-la-carchy: Local status is information, not trouble.')
    text = replace_once(text, '      var p = providers[i]\n      var days = p.recentDays || []',
                        '      var p = providers[i]\n'
                        '      if (p.providerId === "local") continue // a-la-carchy: sampled local tokens are not subscription usage.\n'
                        '      var days = p.recentDays || []')
    blocks = [block for block in (LIMITS_TROUBLE_TEXT, LIMITS_TROUBLE_TEXT_CURRENT) if block in text]
    if len(blocks) != 1:
        raise ValueError(UNSUPPORTED)
    text = replace_once(text, blocks[0], blocks[0] + LIMITS_LOCAL_TEXT)
    text = replace_once(text, '  function providerAccounts(p) {',
                        '  function providerAccounts(p) {\n    if (p && p.readOnly === true) return []')
    text = replace_once(text, '  function needsSignIn(item) {',
                        '  function needsSignIn(item) {\n    if (item && item.readOnly === true) return false')
    for signature in ('renameAccount(p, account, label)', 'useAccount(p, account)',
                      'setSwitchMode(p, mode)', 'signInAgain(p, account)'):
        anchor = '  function ' + signature + ' {'
        text = replace_once(text, anchor, anchor + '\n    if (p && p.readOnly === true) return')
    text = replace_once(text, '  function iconCandidatesForProvider(p, surfaceColor) {',
                        '  function iconCandidatesForProvider(p, surfaceColor) {\n'
                        '    var iconId = p && (p.providerId === "codex-hermes-1" || p.providerId === "codex-hermes-2") ? "codex" : (p ? p.providerId : "")')
    text = replace_once(text, 'Qt.resolvedUrl("assets/" + p.providerId + "-light.svg")',
                        'Qt.resolvedUrl("assets/" + iconId + "-light.svg")')
    text = replace_once(text, 'Qt.resolvedUrl("assets/" + p.providerId + ".svg")',
                        'Qt.resolvedUrl("assets/" + iconId + ".svg")')
    return replace_once(text, '  function planLabel(p) {', LIMITS_LOCAL_FUNCTION + '  function planLabel(p) {')

PANEL_LAYOUTS = (('limits', transform_panel_limits), ('tabs', transform_panel_tabs))

def patch_panel(text):
    """Return one complete supported transform; never claim a partial patch."""
    matches = []
    for name, transform in PANEL_LAYOUTS:
        try:
            matches.append((name, transform(text)))
        except ValueError:
            continue
    if len(matches) != 1:
        raise ValueError(UNSUPPORTED)
    return matches[0]

def transform_panel(text):
    return patch_panel(text)[1]

def native_providers(manifest):
    """Agent ids the packaged plugin itself ships support for."""
    widget = manifest.get('barWidget') if isinstance(manifest, dict) else None
    defaults = widget.get('defaults') if isinstance(widget, dict) else None
    providers = defaults.get('providers') if isinstance(defaults, dict) else None
    return set(providers) if isinstance(providers, dict) else set()

MAX_SOURCE_ENTRIES = 1024
MAX_SOURCE_FILE = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024

def source_files(source, require_nonempty_dirs=False, owned=False):
    """Validate the whole bounded tree before reading any plugin contents."""
    source = Path(source).absolute()
    if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_dir():
        raise ValueError('native source must be a no-follow directory')
    uid = os.getuid() if owned else None
    root_info = source.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or (owned and root_info.st_uid != uid):
        raise ValueError('foreign-owned or non-directory plugin root is refused')
    pending, files, count, size = [source], [], 0, 0
    directories = {source}
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                path = Path(entry.path)
                if count > MAX_SOURCE_ENTRIES or len(path.relative_to(source).parts) > 32:
                    raise ValueError('native source tree is too large')
                info = entry.stat(follow_symlinks=False)
                if owned and info.st_uid != uid:
                    raise ValueError('foreign-owned plugin node is refused')
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    directories.add(path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_SOURCE_FILE:
                    size += info.st_size
                    if size > MAX_SOURCE_BYTES:
                        raise ValueError('native source tree is too large')
                    files.append(path)
                else:
                    raise ValueError('linked or nonregular native source is refused')
    if require_nonempty_dirs:
        represented = {source}
        for path in files:
            represented.update(path.parents)
        if not directories.issubset(represented):
            raise ValueError('unrepresented empty plugin directory is refused')
    return sorted(files)

def source_bytes(path, owned=False):
    """Bounded regular-file read; never dereference a source leaf link."""
    path = Path(path)
    if any(p.is_symlink() for p in path.absolute().parents):
        raise ValueError('linked native source parent is refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_SOURCE_FILE
                or (owned and info.st_uid != os.getuid())):
            raise ValueError('linked, nonregular or oversized native source is refused')
        data = stream.read(MAX_SOURCE_FILE + 1)
        if len(data) > MAX_SOURCE_FILE:
            raise ValueError('oversized native source is refused')
        return data

def copy_source_file(source, target):
    Path(target).write_bytes(source_bytes(source))
    os.chmod(target, stat.S_IMODE(Path(source).lstat().st_mode))
    return target

def fingerprint(source):
    """Identity of the packaged source a clone was built from."""
    source = Path(source)
    digest = hashlib.sha256()
    for path in source_files(source):
        digest.update(path.relative_to(source.absolute()).as_posix().encode() + b'\0' + hashlib.sha256(source_bytes(path)).digest())
    return digest.hexdigest()

def render(source, plugin_id):
    """Validate the source and return what a clone replaces. Writes nothing."""
    source = Path(source)
    source_files(source)
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', plugin_id) or plugin_id.startswith('omarchy.'):
        raise ValueError('invalid plugin ID')
    manifest = json.loads(source_bytes(source/'manifest.json'))
    if not isinstance(manifest, dict) or manifest.get('id') != SOURCE_ID:
        raise ValueError('only the packaged omarchy.agents implementation is supported')
    main = transform_main(source_bytes(source/'Main.qml').decode())
    layout, panel = patch_panel(source_bytes(source/'Panel.qml').decode())
    # As `omarchy plugin clone` does: built-in ids inside the QML stay, so the
    # shell keeps routing omarchy.agents IPC and settings through clonedFrom.
    manifest['id'] = plugin_id
    manifest['name'] = NAME
    if isinstance(manifest.get('barWidget'), dict):
        manifest['barWidget']['displayName'] = NAME
    meta = manifest.get('omarchy') if isinstance(manifest.get('omarchy'), dict) else {}
    manifest['omarchy'] = {k: v for k, v in meta.items() if k != 'clonePaths'} | {
        'clonedFrom': SOURCE_ID, 'alaCarchy': MARK, 'alaCarchyLayout': layout, 'alaCarchySource': fingerprint(source)}
    return {'layout': layout, 'manifest': manifest, 'Main.qml': main, 'Panel.qml': panel, 'assets': LOCAL_ASSETS}

def write(source, target, rendered):
    source, target = Path(source), Path(target)
    source_files(source)
    if target.exists() or target.is_symlink():
        raise ValueError('target must be a new directory; existing plugins are never overwritten')
    target.parent.mkdir(parents=True, exist_ok=True)
    # Hidden while incomplete: the shell does not treat dot directories as plugins.
    scratch = Path(tempfile.mkdtemp(prefix='.' + target.name + '.', dir=target.parent))
    try:
        staged = scratch/'plugin'
        shutil.copytree(source, staged, symlinks=True, copy_function=copy_source_file)
        source_files(staged)
        (staged/'Main.qml').write_text(rendered['Main.qml'])
        (staged/'Panel.qml').write_text(rendered['Panel.qml'])
        (staged/'manifest.json').write_text(json.dumps(rendered['manifest'], indent=2) + '\n')
        (staged/'assets').mkdir(exist_ok=True)
        for name, data in rendered['assets'].items():
            (staged/'assets'/name).write_text(data)
        os.rename(staged, target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

def stage(source, target, plugin_id):
    if Path(target).exists() or Path(target).is_symlink():
        raise ValueError('target must be a new directory; existing plugins are never overwritten')
    write(source, target, render(source, plugin_id))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/usr/share/omarchy/shell/plugins/agents'))
    parser.add_argument('--id', default='alacarchy.agents')
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    try:
        stage(args.source, args.target, args.id)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
