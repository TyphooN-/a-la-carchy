#!/usr/bin/env python3
"""Native headless Quickshell load of the staged Agents + Local clone.

Fixture only, never live acceptance: an isolated HOME/XDG tree, an offscreen Qt
platform, no session bus, and a PATH that holds nothing but logging stubs (the
panel's own refresh command included), so no collector, login, network or
desktop command can run. The installed Agents plugin is only read.

One native primitive is replaced: KeyboardPanel is a layer-shell window, which
cannot exist offscreen, so a plain Item with the same API stands in for it.
Everything else (the patched Panel/Main, Agent, and the shell's Ui/Commons
components) is the real code. Popup placement, focus and theming are therefore
NOT covered here.
"""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STOCK = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
SHELL = Path(os.environ.get('ALACARCHY_SHELL_SOURCE', '/usr/share/omarchy/shell'))
QS = Path('/usr/bin/qs')
# Everything the panel could spawn. Only the first two run without a click.
EXPECTED_STUBS = ('omarchy-agent-usage-update', 'omarchy-agent-account-add')
FORBIDDEN_STUBS = ('omarchy-agent', 'omarchy-agent-prompt', 'omarchy-agent-account-rename', 'omarchy-agent-account-use',
                   'omarchy-agent-account-mode', 'omarchy-launch-browser', 'bash', 'sh', 'curl', 'systemctl', 'grok', 'claude', 'codex')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skip(reason):
    print('SKIP: ' + reason)
    print('Headless fixture only; rendered/live acceptance not observed.')
    sys.exit(0)


if not QS.is_file() or not (STOCK/'Panel.qml').is_file() or not (SHELL/'Ui').is_dir():
    skip('Quickshell and the installed Omarchy Agents plugin are required')

stager = load('local_stage', ROOT/'extras/local-router-stats/stage_plugin.py')
collector = load('local_collector', ROOT/'extras/local-router-stats/collector.py')
bridge = load('qml_bridge', ROOT/'extras/hermes-codex-usage/collector.py')
bridge_fixture = load('qml_bridge_fixture', ROOT/'tests/hermes-codex-usage.py')
LOCAL_CASE = os.environ.get('ALACARCHY_LOCAL_CASE', 'sampled')
assert LOCAL_CASE in ('sampled', 'unrecorded', 'metrics-disabled', 'no-model')

# Keep native Quickshell IPC socket paths below Linux's sun_path limit.
with tempfile.TemporaryDirectory(prefix='ag-') as td:
    fixture = Path(td)
    runtime = fixture/'runtime'
    runtime.mkdir(mode=0o700)
    candidate = os.environ.get('ALACARCHY_AGENTS_CANDIDATE')
    if candidate:
        manager = load('qml_manager', ROOT/'extras/agents-clone/manage.py')
        assert manager.clone_info(Path(candidate)) is not None, 'candidate is not a complete managed clone'
        assert json.loads((Path(candidate)/'manifest.json').read_text())['omarchy']['alaCarchySource'] == stager.fingerprint(STOCK)
        shutil.copytree(candidate, fixture/'plugin')
    else:
        stager.stage(STOCK, fixture/'plugin', 'fixture.agents')
    layout = json.loads((fixture/'plugin/manifest.json').read_text())['omarchy']['alaCarchyLayout']
    if layout != 'limits':
        skip('installed Agents panel uses the %s layout; this harness drives the limits-first layout' % layout)
    (fixture/'Commons').symlink_to(SHELL/'Commons', target_is_directory=True)
    (fixture/'Ui').mkdir()
    for path in (SHELL/'Ui').iterdir():
        if path.name != 'KeyboardPanel.qml':
            (fixture/'Ui'/path.name).symlink_to(path)
    (fixture/'Ui/KeyboardPanel.qml').write_text('''import QtQuick
// Test stand-in for the layer-shell popup: same API, plain Item.
Item {
  id: root
  property Item anchorItem: null
  property QtObject bar: null
  property var owner: null
  property int margin: 0
  property int padding: 0
  property int contentWidth: 380
  property int contentHeight: 600
  property bool centerOnBar: false
  property bool open: false
  property Item focusTarget: null
  default property alias contentItem: contentHolder.children
  function close() { if (owner && "close" in owner) owner.close(); else root.open = false }
  function fittedContentWidth(width, cap) { var d = Math.max(1, Number(width) || 1); return Math.round(Number(cap) > 0 ? Math.min(d, Number(cap)) : d) }
  function fittedContentHeight(height, cap) { var d = Math.max(1, Number(height) || 1); return Math.round(Number(cap) > 0 ? Math.min(d, Number(cap)) : d) }
  function cappedContentHeight(height) { return Math.max(1, Number(height) || 1) }
  width: contentWidth
  height: contentHeight
  Item { id: contentHolder; anchors.fill: parent }
}
''')

    # --- records: one subscription in the native usage directory, the Local
    # record in its own directory, and a stray usage/local.json that must
    # never be adopted.
    state = fixture/'state'
    usage = state/'omarchy/agents/usage'
    usage.mkdir(parents=True)
    # Native reorder support is preserved; pin fixture order before async discovery.
    (usage.parent/'order.json').write_text(json.dumps(['claude', 'codex-hermes-1', 'codex-hermes-2', 'grok', 'local']))
    today = dt.date.today()
    days = [str(today - dt.timedelta(days=offset)) for offset in range(6, -1, -1)]
    subscription = {
        'id': 'claude', 'name': 'Claude Code', 'ready': True, 'tierLabel': 'max', 'usageStatusText': '', 'authHelpText': '',
        'limits': [{'label': 'Session (5-hour)', 'percent': 0.25, 'resetsAt': ''}],
        'todayPrompts': 3, 'todaySessions': 1, 'todayTotalTokens': 1000, 'todayTokensByModel': {'claude-opus-4-8': 1000},
        'recentDays': [{'date': day, 'messageCount': 1000 if day == days[-1] else 0} for day in days],
        'totalPrompts': 3, 'totalSessions': 1, 'activeDays': 1,
        'modelUsage': {'claude-opus-4-8': {'inputTokens': 600, 'outputTokens': 400, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}}}
    (usage/'claude.json').write_text(json.dumps(subscription))
    # Dev-channel Grok is first-party: retain its real record contract beside Local.
    native_grok = dict(subscription, id='grok', name='Grok', tierLabel='X Premium',
                       limits=[{'label': 'Weekly', 'percent': 0.75, 'resetsAt': ''}],
                       todayPrompts=0, todaySessions=0, todayTotalTokens=0, todayTokensByModel={},
                       recentDays=[], modelUsage={})
    (usage/'grok.json').write_text(json.dumps(native_grok))
    native_codex = dict(subscription, id='codex', name='NATIVE CODEX SENTINEL', tierLabel='team')
    (usage/'codex.json').write_text(json.dumps(native_codex))
    (usage/'codex-hermes-3.json').write_text(json.dumps(dict(subscription, id='codex-hermes-3', name='GHOST CODEX SENTINEL')))
    rows = [bridge_fixture.entry(subject='auth0|qml-member-1'), bridge_fixture.entry(subject='auth0|qml-member-2')]
    # Fresh 1 and matching last-known stale 2, using the real publication adapter.
    def measured(token, ident): return bridge.normalize_quota(bridge_fixture.quota(), bridge_fixture.NOW)
    old = bridge.build_record(rows, {}, measured, bridge_fixture.NOW)
    def partly_expired(token, ident):
        if ident['key'] == old['accounts'][1]['id']: raise bridge.Unavailable('expired')
        return measured(token, ident)
    hermes = bridge.build_record(rows, old, partly_expired, bridge_fixture.NOW)
    bridge.publish_accounts(usage, hermes)
    stray_grok = state/'omarchy/grok-usage/agents/grok.json'
    stray_grok.parent.mkdir(parents=True)
    stray_grok.write_text(json.dumps({'id':'grok', 'name':'STRAY CUSTOM GROK', 'ready':False,
                                    'usageStatusText':'Waiting for Grok token metadata · quota unavailable'}))
    (usage/'local.json').write_text(json.dumps({'id': 'local', 'name': 'STRAY', 'ready': True, 'tierLabel': 'stray', 'todayTotalTokens': 999999999}))

    ledger_dir = state/'omarchy/local-router'
    (ledger_dir/'agents').mkdir(parents=True)
    ledger = collector.Ledger(ledger_dir/'tokens.sqlite3')
    noon = dt.datetime.combine(today, dt.time(12)).timestamp()
    ledger.observe('local/model <b>A</b>', 'worker-a', (100, 20), noon)             # baseline, counts nothing
    ledger.observe('local/model <b>A</b>', 'worker-a', (1600, 520), noon + 5)       # +1500 in, +500 out
    ledger.observe('model-b', 'worker-b', (0, 0), noon + 5)
    ledger.observe('model-b', 'worker-b', (30, 12), noon + 10)                      # +42
    record = ledger.record(noon + 10, 'Sampled tokens · 2 model(s) · cache excluded')
    ledger.db.close()
    collector.publish(ledger_dir/'agents/local.json', record)
    assert record['todayTotalTokens'] == 2042 and sum(day['recorded'] for day in record['recentDays']) == 1
    if LOCAL_CASE != 'sampled':
        record = dict(record, hasLocalStats=False, todayTotalTokens=0, todayTokensByModel={}, modelUsage={},
                      recentDays=[{'date':d, 'messageCount':None, 'recorded':False} for d in days],
                      usageStatusText={'unrecorded':'Activation pending · not collecting',
                                       'metrics-disabled':'Metrics disabled · history retained',
                                       'no-model':'No loaded model · history retained'}[LOCAL_CASE])
        collector.publish(ledger_dir/'agents/local.json', record)

    # --- PATH: stubs only. The real collectors live in /usr/bin, which is absent.
    bin_dir = fixture/'bin'
    bin_dir.mkdir()
    calls = fixture/'calls.log'
    unexpected = fixture/'unexpected.log'
    for name in EXPECTED_STUBS:
        (bin_dir/name).write_text('#!/bin/sh\nprintf "%%s %%s\\n" "%s" "$*" >> "%s"\nexit 0\n' % (name, calls))
    for name in FORBIDDEN_STUBS:
        (bin_dir/name).write_text('#!/bin/sh\nprintf "%%s %%s\\n" "%s" "$*" >> "%s"\nexit 97\n' % (name, unexpected))
    (bin_dir/'hyprctl').write_text('#!/bin/sh\nprintf \'{"int": 0}\\n\'\n')
    (bin_dir/'fc-match').write_text('#!/bin/sh\nprintf monospace\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    (bin_dir/'find').symlink_to('/usr/bin/find')
    env = {'HOME': str(fixture), 'XDG_CONFIG_HOME': str(fixture/'config'), 'XDG_RUNTIME_DIR': str(runtime),
           'XDG_CACHE_HOME': str(fixture/'cache'), 'XDG_STATE_HOME': str(state), 'XDG_DATA_HOME': str(fixture/'data'),
           'PATH': str(bin_dir), 'LANG': os.environ.get('LANG', 'C.UTF-8'), 'QT_QPA_PLATFORM': 'offscreen',
           'QT_QPA_PLATFORMTHEME': '', 'QT_STYLE_OVERRIDE': 'Fusion', 'QT_QUICK_CONTROLS_STYLE': 'Basic',
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(fixture/'no-session-bus'),
           'http_proxy': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9'}
    for name in EXPECTED_STUBS + FORBIDDEN_STUBS:
        assert shutil.which(name, path=env['PATH']) == str(bin_dir/name), 'a real ' + name + ' is reachable'

    (fixture/'shell.qml').write_text('''import QtQuick
import Quickshell
import "plugin" as Agents
ShellRoot {
  id: root
  property bool reported: false
  Agents.Main { id: admission; settings: ({ refreshIntervalSec: 3600 }) }
  QtObject {
    id: fixtureBar
    property bool vertical: false
    property bool foregroundAnimationEnabled: false
    property int barSize: 32
    property string position: "top"
    property color barForeground: "white"
    property color foreground: "white"
    property color urgent: "red"
    property string fontFamily: "sans-serif"
    property var activePopout: null
    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { activePopout = null }
    function showTooltip(owner, text) {}
    function hideTooltip(owner) {}
    function run(command) { console.log("AGENTS_QML_UNEXPECTED_RUN " + command) }
  }
  FloatingWindow {
    visible: true
    implicitWidth: 800
    implicitHeight: 80
    Agents.Panel { id: panel; bar: fixtureBar; settings: ({ refreshIntervalSec: 3600 }) }
  }
  // Every visible text the panel shows, in item order.
  function shownTexts(item, out) {
    if (!item || item.visible === false) return out
    if (typeof item.text === "string" && item.text !== "" && item.wrapMode !== undefined) out.push(item.text)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) shownTexts(kids[i], out)
    return out
  }
  Timer {
    interval: 50; repeat: true; running: !root.reported
    onTriggered: {
      var list = panel.providers
      if (!list || list.length < 5 || admission.enabledProviders.length < 5) return
      var local = null
      for (var i = 0; i < list.length; i++) if (list[i].providerId === "local") local = list[i]
      var texts = shownTexts(panel, [])
      if (!local || texts.indexOf("Local") < 0) return // sections are still being built
      root.reported = true
      var h1 = list.filter(function(p) { return p.providerId === "codex-hermes-1" })[0]
      var h2 = list.filter(function(p) { return p.providerId === "codex-hermes-2" })[0]
      var syntheticAccount = {id:"synthetic", active:false, usageStatusText:"Waiting for auth"}
      var guarded = {providerId:"codex-hermes-2", readOnly:true, accounts:[syntheticAccount, {id:"second"}], accountSwitch:{mode:"manual"}}
      panel.signInAgain(guarded, syntheticAccount)
      panel.useAccount(guarded, syntheticAccount)
      panel.renameAccount(guarded, syntheticAccount, "SYNTHETIC")
      panel.setSwitchMode(guarded, "auto")
      admission.syncEnabled = true
      admission.syncDir = admission.home + "/synthetic-sync"
      admission.aggregateData = {providers:{codex:{providerName:"SYNC NATIVE CODEX", todayPrompts:99},
                                            local:{providerName:"SYNC LOCAL", todayPrompts:99},
                                            "codex-hermes-3":{providerName:"SYNC GHOST CODEX", todayPrompts:99}}}
      admission.syncRevision++
      var snapshot = admission.localSnapshot()
      admission.agents = [] // Exercise remote-only slots, not just local-id suppression.
      admission.aggregateData = {providers:{
        "codex-hermes-1":{providerName:"WRONG SYNC LABEL", todayPrompts:1},
        "codex-hermes-2":{providerName:"WRONG SYNC LABEL", todayPrompts:1},
        "codex-hermes-3":{providerName:"SYNC GHOST", todayPrompts:1},
        codex:{providerName:"SYNC NATIVE", todayPrompts:1},
        local:{providerName:"REMOTE LOCAL", todayPrompts:1},
        grok:{providerName:"Grok", todayPrompts:1},
        claude:{providerName:"Claude Code", todayPrompts:1}}}
      admission.syncRevision++
      console.log("AGENTS_QML_RESULT " + JSON.stringify({
        hermes: [h1, h2],
        signInHermes: panel.needsSignIn({providerId:"codex-hermes-2", readOnly:true, usageStatusText:"Waiting for auth"}),
        accountsHermes: panel.providerAccounts(guarded),
        switchHermes: h2.accountSwitch,
        normalSignIn: panel.needsSignIn({providerId:"claude", usageStatusText:"Waiting for auth"}),
        normalAccounts: panel.providerAccounts({providerId:"claude", accounts:[syntheticAccount, {id:"second"}]}),
        iconDark: panel.iconCandidatesForProvider(h2, Qt.rgba(0,0,0,1)),
        iconLight: panel.iconCandidatesForProvider(h2, Qt.rgba(1,1,1,1)),
        keyRows: panel.keyRows,
        providerIds: list.map(function(p) { return p.providerId }),
        syncedIds: admission.enabledProviders.map(function(p) { return p.providerId }),
        localSnapshot: snapshot,
        syncOnlyHermes: admission.enabledProviders.filter(function(p) { return p.providerId.indexOf("codex") === 0 }),
        texts: texts,
        ids: list.map(function(p) { return p.providerId }),
        local: local,
        grok: list.filter(function(p) { return p.providerId === "grok" })[0],
        allModelsText: panel.localRouterText({ todayTokensByModel: {"model-1": 1, "model-2": 2, "model-3": 3, "model-4": 4, "model-5": 5, "model-6": 6} }),
        localText: local ? panel.localRouterText(local) : null,
        localTrouble: local ? panel.otherTrouble(local) : null,
        subscriptionTrouble: panel.otherTrouble({ providerId: "claude", usageStatusText: "Endpoint down" }),
        accountTrouble: panel.otherTrouble({ usageStatusText: "Endpoint down" }),
        signIn: panel.needsSignIn(local),
        alarming: panel.alarming,
        blankSlate: panel.blankSlate,
        phrases: panel.summaryPhrases,
        unknownTodayText: panel.localRouterText({ todayTotalTokens: 0, recentDays: [{ messageCount: 7, recorded: true }, { messageCount: null, recorded: false }] }),
        emptyText: panel.localRouterText({ providerId: "local", usageStatusText: "Activation pending · not collecting",
                                           recentDays: [{ date: "2026-01-01", messageCount: null, recorded: false }], todayTotalTokens: 0 })
      }))
      Qt.quit()
    }
  }
}
''')
    try:
        run = subprocess.run([str(QS), '-p', str(fixture), '--no-color'], env=env, text=True, capture_output=True, timeout=8)
        output = run.stdout + run.stderr
        assert run.returncode == 0, (run.returncode, output)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b'').decode() + (exc.stderr or b'').decode()
        raise AssertionError('native fixture timeout; child killed/reaped\n' + output) from None
    print(output)
    marker = [line for line in output.splitlines() if 'AGENTS_QML_RESULT ' in line]
    assert len(marker) == 1, 'the patched panel never produced its provider list'
    result = json.loads(marker[0].split('AGENTS_QML_RESULT ', 1)[1])
    for bad in ('Failed to load', 'TypeError', 'ReferenceError', 'is not defined', 'is not a type', 'AGENTS_QML_UNEXPECTED_RUN'):
        assert bad not in output, bad
    # Local supplies both native surface variants; missing-mark warnings are regressions.
    plugin_noise = [line for line in output.splitlines() if '@plugin/' in line]
    assert 'Failed to start IPC server' not in output, 'fixture socket path is too long'
    assert not plugin_noise, plugin_noise
    allowed_platform_warnings = ('$HYPRLAND_INSTANCE_SIGNATURE is unset', 'This plugin does not support setting window masks')
    assert not [line for line in output.splitlines() if 'WARN' in line and not any(note in line for note in allowed_platform_warnings)], output

    # Discovery: the subscription from the native directory, Local from its own
    # record. The stray usage/local.json is ignored, not adopted.
    assert result['ids'] == ['claude', 'codex-hermes-1', 'codex-hermes-2', 'grok', 'local'], result['ids']
    assert result['syncedIds'] == result['ids'], result['syncedIds']
    assert not any(ident in result['localSnapshot']['providers'] for ident in ('codex', 'local', 'codex-hermes-3'))
    assert [p['providerId'] for p in result['syncOnlyHermes']] == ['codex-hermes-1', 'codex-hermes-2']
    assert all(p['readOnly'] is True and p['accountSwitch'] is None and p['accounts'] == [] for p in result['syncOnlyHermes'])
    assert [p['providerName'] for p in result['syncOnlyHermes']] == ['Codex (Hermes 1)', 'Codex (Hermes 2)']
    h1, h2 = result['hermes']
    assert h1['readOnly'] is True and h2['readOnly'] is True
    assert h1['providerName'] == 'Codex (Hermes 1)' and h2['providerName'] == 'Codex (Hermes 2)'
    assert h1['limitsStale'] is False and h2['limitsStale'] is True
    assert h1['limitsFetchedAt'] == h2['limitsFetchedAt'] == bridge_fixture.NOW*1000
    assert h2['usageStatusText'] == bridge.REASONS['expired']
    assert result['signInHermes'] is False and result['accountsHermes'] == []
    assert result['switchHermes'] is None
    assert result['normalSignIn'] is True and len(result['normalAccounts']) == 2
    assert result['iconDark'][0].endswith('/assets/codex.svg')
    assert result['iconLight'][0].endswith('/assets/codex-light.svg')
    for row in result['keyRows']:
        for target in row:
            if target['kind'] == 'providerSignin':
                assert not result['providerIds'][target['index']].startswith('codex-hermes-')
    assert not any('--reauth' in line for line in (calls.read_text().splitlines() if calls.exists() else [])), 'Hermes auth command executed'
    assert result['grok']['providerName'] == 'Grok' and result['grok']['tierLabel'] == 'X Premium'
    assert result['grok']['limits'] == native_grok['limits'], result['grok']['limits']
    assert result['grok']['todayTotalTokens'] == 0
    assert 'Today by model: model-6 6 · model-5 5 · model-4 4 · model-3 3 · model-2 2 · model-1 1' in result['allModelsText'], result['allModelsText']
    assert '+2 more' not in result['allModelsText']
    assert 'Today — · last 7 days 7 tokens (1 of 2 days recorded)' in result['unknownTodayText'], result['unknownTodayText']
    local = result['local']
    assert local['providerName'] == 'Local' and local['tierLabel'] == 'llama.cpp router'
    assert local['todayTotalTokens'] == record['todayTotalTokens'], local['todayTotalTokens']
    assert local['syncEnabled'] is False
    assert len(local['recentDays']) == 7 and local['recentDays'][0]['recorded'] is False

    # Display: status is information for Local, still trouble for everyone else.
    assert result['localTrouble'] == '' and result['signIn'] is False
    assert result['subscriptionTrouble'] == 'Endpoint down' and result['accountTrouble'] == 'Endpoint down'
    assert result['alarming'] is False and result['blankSlate'] is False
    expected_sampled = [
        'Sampled tokens · 2 model(s) · cache excluded',
        'Today 2.0K · last 7 days 2.0K tokens (1 of 7 days recorded)',
        'Today by model: local/model <b>A</b> 2.0K · model-b 42',
        'Evaluated input + generated output · cached input not counted']
    assert result['localText'].split('\n') == (expected_sampled if LOCAL_CASE == 'sampled' else [record['usageStatusText'],
        'No recorded days yet (not zero usage)', 'Evaluated input + generated output · cached input not counted']), result['localText']
    # Rendering: the Local section shows its own lines, and its caveat text is
    # never drawn as (urgent) trouble the way a subscription's would be.
    texts = result['texts']
    assert texts.count(result['localText']) == 1, texts
    assert 'Local' in texts and 'Llama.cpp router' in texts and 'Claude Code' in texts and 'Max' in texts, texts
    assert 'Grok' in texts and 'X Premium' in texts, texts
    assert 'Codex (Hermes 1)' in texts and 'Codex (Hermes 2)' in texts and bridge.REASONS['expired'] in texts, texts
    assert not any('CODEX SENTINEL' in t or 'CUSTOM GROK' in t for t in texts), texts
    assert local['authHelpText'] and local['authHelpText'] not in texts, texts
    assert not any('STRAY' in text or 'Stray' in text for text in texts), texts
    assert result['emptyText'].split('\n') == [
        'Activation pending · not collecting', 'No recorded days yet (not zero usage)',
        'Evaluated input + generated output · cached input not counted'], result['emptyText']

    # The hero's cross-subscription summary counts the subscription only.
    assert '1.0K tokens today' in result['phrases'] and '1.0K tokens this week' in result['phrases'], result['phrases']
    assert not any('3.0K' in phrase or '2.0K' in phrase or 'model' in phrase.lower() for phrase in result['phrases']), result['phrases']

    # Commands: the panel's refresh reached the stub; nothing else ran.
    assert not unexpected.exists(), unexpected.read_text()
    ran = calls.read_text().splitlines() if calls.exists() else []
    assert any(line.startswith('omarchy-agent-usage-update') for line in ran), ran
    assert all(line.split()[0] in EXPECTED_STUBS for line in ran), ran
    assert sorted(p.name for p in usage.iterdir() if p.suffix == '.json') == ['claude.json', 'codex-hermes-1.json', 'codex-hermes-2.json', 'codex-hermes-3.json', 'codex.json', 'grok.json', 'local.json']
    assert json.loads((usage/'grok.json').read_text()) == native_grok, 'native Grok record was touched'
    assert json.loads((usage/'codex.json').read_text()) == native_codex, 'native Codex was touched'
    assert json.loads(stray_grok.read_text())['name'] == 'STRAY CUSTOM GROK', 'stray custom Grok metadata was touched'
    print('Native primitives loaded; patched %s-layout Agents clone listed Local beside a subscription.' % layout)
print('Headless fixture only; rendered/live acceptance not observed.')
