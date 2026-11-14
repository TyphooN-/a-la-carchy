#!/usr/bin/env python3
"""Private offscreen Qt/FileView characterization of Local source separation.

Run only through the maintained no-network/native cgroup fixture wrapper.
This exercises actual Main.qml, Agent.qml, discovery and atomic file refresh;
it is not installed-panel geometry or live desktop acceptance.
"""
import importlib.util
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
assert os.environ.get('HOME') == '/tmp/home', 'private native wrapper required'
assert not Path('/home/typhoon/.hermes/auth.json').exists()
assert not Path('/run/user/1000/bus').exists()
assert Path('/proc/1/comm').read_text().strip() in ('bwrap', 'python3')
MODE = os.environ['TRANSCRIPT_NATIVE_MODE']
assert MODE in ('stock', 'stock-lifecycle', 'generated', 'canonical', 'suppressed-control')
STOCK_MODE = MODE in ('stock', 'stock-lifecycle')
SHELL = Path('/tmp/native-root/shell')
CANONICAL = Path(os.environ['OMARCHY_PATH'])
spec = importlib.util.spec_from_file_location('native_transcript_stager', ROOT/'extras/local-router-stats/stage_plugin.py')
stager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stager)

NATIVE = {
    'schemaVersion': 1, 'id': 'local', 'name': 'Local', 'ready': True,
    'hasLocalStats': True, 'hasPromptStats': True,
    'todayPrompts': 3, 'todaySessions': 1, 'todayTotalTokens': 53,
    'todayTokensByModel': {'native-history-only-model': 53},
    'totalPrompts': 9, 'totalSessions': 2, 'activeDays': 2,
    'recentDays': [{'date': '2026-11-07', 'messageCount': 53}],
    'modelUsage': {'native-history-only-model': {'inputTokens': 40, 'outputTokens': 13}},
    'accounts': [], 'limits': [], 'tierLabel': ''}
ROUTER = dict(NATIVE, name='Local router', hasPromptStats=False, readOnly=True,
              todayTotalTokens=52,
              recentDays=[{'date': '2026-11-07', 'messageCount': 52}],
              todayTokensByModel={'native-history-only-model': 21, 'router-only-model': 31},
              modelUsage={'native-history-only-model': {'inputTokens': 11, 'outputTokens': 10},
                          'router-only-model': {'inputTokens': 22, 'outputTokens': 9}},
              localLive={'contract': 'local-task-rates/1', 'models': []})
REPLACEMENT = dict(NATIVE, todayTotalTokens=59,
                   recentDays=[{'date': '2026-11-07', 'messageCount': 59}],
                   todayTokensByModel={'native-replacement-only': 59},
                   modelUsage={'native-replacement-only': {'inputTokens': 42, 'outputTokens': 17}})
PHASES = [
    ('native-only', NATIVE, None, True, False, 53, ['native-history-only-model'], {}),
    ('overlap-and-distinct', NATIVE, ROUTER, True, True, 53, ['native-history-only-model'], {}),
    ('native-disappeared', None, ROUTER, False, True, None, [], {}),
    ('native-reappeared', NATIVE, ROUTER, True, True, 53, ['native-history-only-model'], {}),
    ('atomic-native-replacement', REPLACEMENT, ROUTER, True, True, 59, ['native-replacement-only'], {}),
    ('private-in-native-path-refused', ROUTER, ROUTER, False, True, None, [], {}),
    ('native-restored', NATIVE, ROUTER, True, True, 53, ['native-history-only-model'], {}),
    ('native-toggle-independent', NATIVE, ROUTER, False, True, None, [], {'local-transcripts': {'enabled': False}}),
    ('router-toggle-independent', NATIVE, ROUTER, True, False, 53, ['native-history-only-model'], {'local': {'enabled': False}}),
    ('router-disappeared', NATIVE, None, True, False, 53, ['native-history-only-model'], {}),
    ('all-disappeared', None, None, False, False, None, [], {}),
    ('both-reappeared', NATIVE, ROUTER, True, True, 53, ['native-history-only-model'], {}),
]
if MODE == 'stock':
    PHASES = PHASES[:1]
elif MODE == 'stock-lifecycle':
    PHASES = [tuple([p[0], p[1], None, p[3], False, p[5], p[6], {}])
              for p in PHASES if p[0] in ('native-only','native-disappeared','native-reappeared',
                                         'atomic-native-replacement','all-disappeared','both-reappeared')]

QML = r'''import QtQuick
import Quickshell
import Quickshell.Io
import "plugin" as Agents
ShellRoot {
  id: root
  property int phase: -1
  property int ticks: 0
  property int stable: 0
  property string previous: ""
  property bool done: false
  property var control: null
  property var observations: []
  Agents.Main { id: actual; settings: ({refreshIntervalSec:3600}) }
  FileView {
    path: Quickshell.env("TRANSCRIPT_CONTROL")
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      var c = JSON.parse(text())
      if (c.phase <= root.phase) return
      root.control = c
      root.phase = c.phase
      root.ticks = 0
      root.stable = 0
      root.previous = ""
      actual.settings = {refreshIntervalSec:3600, providers:c.settings}
      // The real native refresh fallback rescans filenames and reloads records.
      // No record, provider model or normalized counter is assigned by this test.
      actual.rescanAgents()
      actual.reloadRecords()
    }
  }
  function privateObservers(item) {
    var count = item && item.agentId === "local" && item.path === Quickshell.env("TRANSCRIPT_ROUTER_PATH") ? 1 : 0
    var kids = item && item.children ? item.children : []
    for (var i=0; i<kids.length; i++) count += privateObservers(kids[i])
    return count
  }
  function capture() {
    var providers = actual.enabledProviders
    var alias = Quickshell.env("TRANSCRIPT_MODE").indexOf("stock") === 0 ? "local" : "local-transcripts"
    var nativeRows = providers.filter(function(p) {return p.providerId === alias})
    var routerRows = providers.filter(function(p) {return p.providerId === "local" && alias !== "local"})
    var n = nativeRows.length ? nativeRows[0] : null
    var r = routerRows.length ? routerRows[0] : null
    var wire = actual.localSnapshot().providers
    var raw = actual.agents.filter(function(a) {return a && a.agentId === "local"})
    return {phase:root.control.name, nativeCount:nativeRows.length, routerCount:routerRows.length,
      discovered:actual.agentIds, rawNativeRecord:raw.length ? raw[0].record : null,
      native:n, router:r, wire:wire, privateObservers:privateObservers(actual),
      aggregate:typeof actual.recordedTokenTotalsFor === "function"
        ? actual.recordedTokenTotalsFor(providers, null, null, null) : null}
  }
  function check(s) {
    var c = root.control
    if (s.nativeCount !== (c.native ? 1 : 0) || s.routerCount !== (c.router ? 1 : 0)) return false
    if (s.discovered.indexOf("local-transcripts") >= 0) return false
    if (c.native) {
      if (!s.native || s.native.todayTotalTokens !== c.value || s.native.totalSessions !== 2) return false
      if (JSON.stringify(Object.keys(s.native.modelUsage).sort()) !== JSON.stringify(c.models)) return false
      if (s.native.hasPromptStats !== true || s.native.localLive) return false
      if (!s.wire.local || s.wire.local.modelUsage[c.models[0]].inputTokens !== (c.value === 59 ? 42 : 40)) return false
      if (Quickshell.env("TRANSCRIPT_MODE").indexOf("stock") !== 0) {
        if (s.native.sourceKind !== "native-local-transcripts" || s.native.readOnly !== true) return false
        if (s.wire.local.sourceKind !== "native-local-transcripts") return false
      }
    } else if (s.wire.local) return false
    if (s.wire["local-transcripts"] || (s.wire.local && s.wire.local.localLive)) return false
    if (c.router && (!s.router || s.router.todayTotalTokens !== 52 || s.router.hasPromptStats !== false)) return false
    if (Quickshell.env("TRANSCRIPT_MODE").indexOf("stock") !== 0 && s.privateObservers !== 1) return false
    if (s.aggregate && c.router && (s.aggregate.total.history !== 52 || s.aggregate.total.week !== 52
        || s.aggregate.local.history !== 52 || s.aggregate.cloud.history.value !== null)) return false
    return true
  }
  Timer {
    interval: 50
    running: !root.done
    repeat: true
    onTriggered: {
      if (!root.control) return
      root.ticks++
      var s = root.capture()
      var text = JSON.stringify(s)
      if (root.check(s)) {
        root.stable = text === root.previous ? root.stable+1 : 0
        root.previous = text
        if (root.stable < 2 || root.ticks < 5) return
        root.observations = root.observations.concat([s])
        if (root.control.last) {
          root.done = true
          console.log("TRANSCRIPT_NATIVE_RESULTS " + JSON.stringify({mode:Quickshell.env("TRANSCRIPT_MODE"), observations:root.observations}))
          Qt.quit()
        } else {
          // Sticky completion: wait for a new actual file/publication phase.
          root.done = true
          console.log("TRANSCRIPT_NATIVE_READY " + root.phase)
        }
      } else if (root.ticks >= 80) {
        root.done = true
        console.log("TRANSCRIPT_NATIVE_FAILURE " + JSON.stringify({expected:root.control,actual:s}))
        Qt.quit()
      }
    }
  }
  onPhaseChanged: root.done = false
}
'''

with tempfile.TemporaryDirectory(prefix='tl-') as td:
    fixture = Path(td)
    runtime = fixture/'runtime'; runtime.mkdir(mode=0o700)
    state = fixture/'state'
    usage = state/'omarchy/agents/usage'; usage.mkdir(parents=True)
    private_path = state/'omarchy/local-router/agents/local.json'
    private_path.parent.mkdir(parents=True)
    plugin = fixture/'plugin'
    if MODE in ('generated', 'suppressed-control'):
        stager.stage(SHELL/'plugins/agents', plugin, 'fixture.agents')
    elif MODE == 'canonical':
        shutil.copytree(CANONICAL/'shell/plugins/agents', plugin)
    else:
        shutil.copytree(SHELL/'plugins/agents', plugin)
    if MODE == 'suppressed-control':
        path = plugin/'Main.qml'; text = path.read_text()
        anchor = 'name !== "local-transcripts.json" && name !== "codex.json"'
        assert text.count(anchor) == 1
        path.write_text(text.replace(anchor, 'name !== "local.json" && '+anchor, 1))
    shell_inputs = CANONICAL/'shell' if MODE == 'canonical' else SHELL
    # Preserve the real relative navigation import closure, even for the scanner.
    for name in ('Commons','Ui','panels'):
        target = shell_inputs/'plugins/panels' if name == 'panels' else shell_inputs/name
        assert target.is_dir(), 'missing native import closure: '+str(target)
        (fixture/name).symlink_to(target, target_is_directory=True)
    (fixture/'shell.qml').write_text(QML)
    bins = fixture/'bin'; bins.mkdir()
    command_log = fixture/'calls.log'
    for name in ('omarchy-agent-usage-update','omarchy-agent-local','hyprctl','fc-match'):
        answer = "printf '{\"int\":0}\\n'\n" if name == 'hyprctl' else "printf monospace\\n\n" if name == 'fc-match' else ''
        (bins/name).write_text('#!/bin/sh\nprintf "%s %s\\n" "'+name+'" "$*" >> "'+str(command_log)+'"\n'+answer+'exit 0\n')
        (bins/name).chmod(0o700)
    (bins/'find').symlink_to('/usr/bin/find')
    control_path = fixture/'control.json'
    def publish(path, value):
        if value is None:
            path.unlink(missing_ok=True)
        else:
            next_path = path.with_name(path.name+'.next')
            with next_path.open('w') as stream:
                json.dump(value, stream);stream.flush();os.fsync(stream.fileno())
            next_path.replace(path)
    def advance(index):
        name, native, router, native_shown, router_shown, value, models, settings = PHASES[index]
        publish(usage/'local.json', native)
        publish(private_path, router)
        # This reservation belongs to the new mapped consumers, not stock.
        if not STOCK_MODE:
            publish(usage/'local-transcripts.json', dict(NATIVE, id='local-transcripts'))
        publish(control_path, {'phase':index,'name':name,'native':native_shown,'router':router_shown,
                'value':value,'models':models,'settings':settings,'last':index == len(PHASES)-1})
    advance(0)
    env = {'HOME':str(fixture),'HERMES_HOME':str(fixture/'hermes'),'XDG_STATE_HOME':str(state),
           'XDG_RUNTIME_DIR':str(runtime),'XDG_CONFIG_HOME':str(fixture/'config'),
           'XDG_CACHE_HOME':str(fixture/'cache'),'XDG_DATA_HOME':str(fixture/'data'),
           'PATH':str(bins),'LANG':'C.UTF-8','TZ':'UTC','QT_QPA_PLATFORM':'offscreen',
           'QT_QUICK_BACKEND':'software','OMARCHY_PATH':str(CANONICAL),
           'DBUS_SESSION_BUS_ADDRESS':'unix:path='+str(fixture/'no-bus'),
           'TRANSCRIPT_CONTROL':str(control_path),'TRANSCRIPT_ROUTER_PATH':str(private_path),
           'TRANSCRIPT_MODE':MODE}
    argv = ['/usr/bin/qs','-p',str(fixture),'--no-color']
    child = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks = [];pending = b'';actions = [];deadline = time.monotonic()+65
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout,selectors.EVENT_READ)
            while selector.get_map():
                left = deadline-time.monotonic()
                if left <= 0:raise subprocess.TimeoutExpired(argv,65)
                for key,_ in selector.select(min(left,0.2)):
                    chunk = os.read(key.fd,65536)
                    if not chunk:
                        selector.unregister(key.fileobj);continue
                    chunks.append(chunk);pending += chunk
                    while b'\n' in pending:
                        line,pending = pending.split(b'\n',1)
                        if b'TRANSCRIPT_NATIVE_READY ' in line:
                            index = int(line.split(b'TRANSCRIPT_NATIVE_READY ',1)[1])
                            assert index == len(actions), (index,actions)
                            actions.append(index);advance(index+1)
        code = child.wait(timeout=2)
    except BaseException:
        print(b''.join(chunks).decode(errors='replace'),flush=True)
        child.kill()
        try:child.wait(timeout=3)
        except subprocess.TimeoutExpired:raise AssertionError('native transcript child unreaped; do not repeat') from None
        raise
    finally:
        child.stdout.close()
    output = b''.join(chunks).decode(errors='replace');print(output,flush=True)
    assert code == 0, (code,output)
    assert 'TRANSCRIPT_NATIVE_FAILURE ' not in output, 'actual Qt reader/model did not meet the pinned phase contract'
    reports = [json.loads(l.split('TRANSCRIPT_NATIVE_RESULTS ',1)[1]) for l in output.splitlines() if 'TRANSCRIPT_NATIVE_RESULTS ' in l]
    assert len(reports) == 1 and len(reports[0]['observations']) == len(PHASES)
    assert [r['phase'] for r in reports[0]['observations']] == [p[0] for p in PHASES]
    assert actions == list(range(len(PHASES)-1))
    for bad in ('TypeError','ReferenceError','Failed to load','is not defined','is not a type'):
        assert bad not in output, bad
    calls = command_log.read_text().splitlines() if command_log.exists() else []
    assert all(l.startswith(('omarchy-agent-usage-update ','omarchy-agent-local refresh --quiet','hyprctl ','fc-match ')) for l in calls), calls
    print('PASS native Local source characterization: '+MODE+'; '+str(len(PHASES))+' observed publication phases',flush=True)
    print('TRANSCRIPT_FIXTURE_SCOPE native Qt reader/model only; not installed/live/popup geometry',flush=True)
