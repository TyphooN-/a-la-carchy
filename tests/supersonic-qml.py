#!/usr/bin/env python3
"""Native headless Quickshell + real helper/UNIX fixture, never live acceptance."""
import base64
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='supersonic-qml-') as td:
    fixture = Path(td)
    runtime = fixture/'runtime'
    runtime.mkdir(mode=0o700)
    shutil.copytree(ROOT/'extras/supersonic/plugin', fixture/'plugin')
    shutil.copyfile(ROOT/'extras/supersonic/ipc.py', fixture/'plugin/ipc.py')
    for name in ('Ui', 'Commons'):
        (fixture/name).symlink_to(Path('/usr/share/omarchy/shell')/name, target_is_directory=True)
    (fixture/'bin').mkdir()
    (fixture/'bin/hyprctl').write_text('#!/bin/sh\nprintf \'int: 0\\n\'\n')
    (fixture/'bin/hyprctl').chmod(0o755)
    timing_snapshot = {'type':'a{sv}', 'data':[{
        'Metadata': {'type':'a{sv}', 'data':{
            'mpris:trackid': {'type':'o','data':'/Supersonic/Track/' + base64.b32encode(b'fixture').decode().replace('=', '0')},
            'mpris:length': {'type':'x','data':217000000}}},
        'Position': {'type':'x','data':61800000},
        'PlaybackStatus': {'type':'s','data':'Playing'}}]}
    timing_calls = fixture/'timing-calls'
    (fixture/'bin/busctl').write_text('#!/usr/bin/env python3\nwith open(' + repr(str(timing_calls)) + ', "a") as f: f.write("timing\\n")\nprint(' + repr(json.dumps(timing_snapshot)) + ')\n')
    (fixture/'bin/busctl').chmod(0o755)
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args): pass
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            calls.append((parsed.path, params))
            data = None
            if parsed.path == '/current_track':
                data = {'Type':0, 'ID':'fixture', 'Name':'Fixture song', 'Artists':['日本 Artist'], 'Album':'Album'}
            elif parsed.path == '/transport/search-track':
                q = params.get('s', [''])[0]
                if q == 'old': time.sleep(0.45)
                data = [{'ID': 'fixture' if q == 'Fixture song' else q, 'Title': 'Fixture song', 'ArtistNames':['日本 Artist'], 'Album':'Album', 'Year':2001}]
            elif parsed.path == '/transport/search-album':
                data = [{'ID':'album&? 日本', 'Name':'Album', 'ArtistNames':['日本 Artist'], 'Date':{'Year':1990}}]
            self.send_response(200); self.end_headers()
            try: self.wfile.write(json.dumps({'data': data, 'error':''}).encode())
            except BrokenPipeError: pass
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    server = Server(str(runtime/'supersonic.sock'), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (fixture/'shell.qml').write_text('''import QtQuick
import Quickshell
import "plugin" as Music
ShellRoot {
  id: root
  property int step: 0
  QtObject {
    id: fixtureBar
    property bool vertical: false
    property int barSize: 32
    property string position: "top"
    property color barForeground: "white"
    property color foreground: "white"
    property string fontFamily: "sans-serif"
    property var activePopout: null
    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { activePopout = null }
    function showTooltip(owner, text) {}
    function hideTooltip(owner) {}
  }
  FloatingWindow {
    visible: true
    implicitWidth: 1600
    implicitHeight: 80
    Music.BarWidget { id: widget; bar: fixtureBar; music.autoPoll: false }
  }
  Music.Service { id: service; autoPoll: false }
  Component.onCompleted: {
    if (widget.visible || widget.implicitWidth !== 0 || widget.implicitHeight !== 0)
      throw new Error("closed/loading app still occupies the bar")
    widget.music.acceptStatus({state:"ready",track:{id:"presence",title:"Present",type:1}})
    if (!widget.visible || widget.implicitWidth <= 0) throw new Error("open app did not reappear: " + JSON.stringify({visible:widget.visible,width:widget.implicitWidth,height:widget.implicitHeight,state:widget.music.state}))
    widget.open()
    if (!widget.opened) throw new Error("present app panel did not open")
    widget.music.acceptStatus({state:"absent"})
    if (widget.visible || widget.implicitWidth !== 0 || widget.implicitHeight !== 0 || widget.opened)
      throw new Error("closed app retained icon/space/popup")
    widget.open()
    if (widget.opened) throw new Error("absent app panel can still open")
    fixtureBar.vertical = true
    if (widget.visible || widget.implicitWidth !== 0 || widget.implicitHeight !== 0)
      throw new Error("closed app still occupies vertical bar")
    fixtureBar.vertical = false
    widget.music.acceptStatus({state:"idle"})
    if (!widget.visible || widget.implicitWidth <= 0) throw new Error("running idle app cannot search")
    for (var unavailable of ["timeout", "unsafe-socket", "invalid-response", "helper-error", "loading"]) {
      widget.music.acceptStatus({state:unavailable})
      if (widget.visible || widget.implicitWidth !== 0) throw new Error("unavailable indicator still on bar")
    }
    widget.music.acceptStatus({state:"ready",track:{id:"presence",title:"Present",type:1}})
    if (!widget.visible) throw new Error("app restart did not restore widget")
    if (service.kind !== "all" || widget.music.kind !== "all") throw new Error("unified search is not the default")
    if (!widget.librarySearch || widget.librarySearch.placeholderText !== "Search music…") throw new Error("unified search field missing")
    if (service.timingLabel === undefined || typeof service.acceptTiming !== "function")
      throw new Error("decoded exact-track timing feature missing")
    if (widget.maxLabelWidth < 700) throw new Error("music bar still unnecessarily narrow")
    service.acceptStatus({state:"ready", track:{id:"timing", title:"Timing", type:1}})
    service.acceptTiming({state:"ready",id:"timing",elapsed:61.8,duration:217,playing:false}, "timing")
    if (service.timingLabel !== "1:01 / 3:37" || service.playing !== false) throw new Error("MPRIS seconds/paused state")
    if (service.formatTime(null) !== "--:--" || service.formatTime(NaN) !== "--:--" || service.formatTime(-1) !== "--:--")
      throw new Error("unknown time fabricated")
    if (service.formatTime(3601) !== "1:00:01" || service.formatTime(0) !== "0:00") throw new Error("time formatting")
    service.acceptTiming({state:"ready",id:"timing",elapsed:500,duration:217,playing:true}, "timing")
    if (service.elapsed !== 217) throw new Error("position not clamped to duration")
    service.acceptTiming({state:"timing-unknown",id:"timing"}, "timing")
    if (service.timingLabel !== "--:-- / --:--" || service.playing !== null) throw new Error("unknown timing retained")
    service.acceptStatus({state:"ready",track:{id:"other-track",title:"Other",type:1}})
    service.acceptTiming({state:"ready",id:"timing",elapsed:61,duration:217,playing:true}, "timing")
    if (service.elapsed !== null) throw new Error("stale timing accepted")
    if (typeof widget.open !== "function" || typeof widget.close !== "function" || widget.opened === undefined)
      throw new Error("native bar panel open/close contract missing")
    widget.open()
    if (!widget.opened) throw new Error("native panel open did not update state")
    widget.close()
    if (widget.opened) throw new Error("native panel close did not update state")
    var metadata = {state:"ready", track:{id:"one", title:"日本 <b>literal</b>", artist:"Artist", album:"Album", type:1, year:null}}
    service.acceptStatus(metadata)
    widget.music.acceptStatus(metadata)
    if (service.track.title !== "日本 <b>literal</b>" || service.year !== null) throw new Error("metadata")
    service.setQuery("old")
    var old = service.generation
    service.setQuery("new")
    service.acceptSearch({state:"ready", results:[{id:"old"}]}, old)
    if (service.results.length !== 0) throw new Error("stale results accepted")
    service.acceptSearch({state:"ready", results:[{id:"new"}]}, service.generation)
    if (service.results[0].id !== "new") throw new Error("new results rejected")
    service.setKind("album")
    if (service.results.length !== 0) throw new Error("mode did not invalidate")
    service.acceptStatus({state:"absent"})
    if (service.track !== null || service.canControl) throw new Error("absent metadata retained")
    service.setKind("all")
    service.setQuery("")
    service.autoPoll = true
    service.refresh()
    widget.music.acceptStatus({state:"absent"})
    widget.music.autoPoll = true
    console.log("SUPERSONIC_QML_DATA_PASS")
  }
  Timer {
    interval: 50; repeat: true; running: root.step < 10
    onTriggered: {
      if (root.step === 0 && service.state === "ready" && service.year === 2001 && service.timingLabel === "1:01 / 3:37") {
        service.setQuery("old"); service.searchNow(); root.step = 1
      } else if (root.step === 1) {
        service.setQuery("new"); service.searchNow(); root.step = 2
      } else if (root.step === 2 && service.results.length) {
        if (service.results[0].id !== "new") throw new Error("async stale result")
        if (!service.results.some(function(r) { return r.kind === "album" })) throw new Error("default search did not merge albums")
        if (!service.select({id:service.results[0].id, kind:service.results[0].kind})) throw new Error("delegate copy cannot select")
        root.step = 3
      } else if (root.step === 3 && service.actionState === "requested") {
        service.setKind("album"); service.searchNow(); root.step = 4
      } else if (root.step === 4 && service.results.length) {
        if (service.results[0].kind !== "album") throw new Error("album mode")
        if (!service.select(service.results[0])) throw new Error("album selection")
        root.step = 5
      } else if (root.step === 5 && service.actionState === "requested") {
        service.action("playpause"); root.step = 6
      } else if (root.step === 6 && service.actionState === "requested") {
        service.action("next"); root.step = 7
      } else if (root.step === 7 && service.actionState === "requested") {
        service.action("previous"); root.step = 8
      } else if (root.step === 8 && service.actionState === "requested") {
        widget.music.acceptSearch({state:"ready", results:service.results}, widget.music.generation)
        if (service.select({id:"stale",kind:"song"})) throw new Error("stale selection")
        console.log("SUPERSONIC_QML_FLOW_PASS")
        root.step = 10
      }
    }
  }
  Timer {
    interval: 50; repeat: true; running: true
    onTriggered: {
      if (root.step === 10 && widget.visible && widget.music.track && widget.music.track.id === "fixture"
          && widget.music.timingLabel === "1:01 / 3:37") {
        console.log("SUPERSONIC_QML_REAPPEAR_PASS")
        running = false
      }
    }
  }
}
''')
    env = os.environ.copy()
    for k in ('DISPLAY', 'WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE', 'DBUS_SESSION_BUS_ADDRESS', 'QS_CONFIG_PATH', 'QS_CONFIG_NAME', 'QS_MANIFEST'):
        env.pop(k, None)
    env.update(HOME=str(fixture), XDG_CONFIG_HOME=str(fixture/'config'), XDG_RUNTIME_DIR=str(runtime), XDG_CACHE_HOME=str(fixture/'cache'), XDG_STATE_HOME=str(fixture/'state'), QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='', QT_STYLE_OVERRIDE='Fusion', QT_QUICK_CONTROLS_STYLE='Basic')
    env['PATH'] = str(fixture/'bin') + ':' + env['PATH']
    env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=' + str(fixture/'no-session-bus')
    try:
        try:
            run = subprocess.run(['/usr/bin/qs', '-p', str(fixture), '--no-color'], env=env, text=True, capture_output=True, timeout=5)
            output = run.stdout + run.stderr
            assert run.returncode == 0, (run.returncode, output)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or b'').decode() + (exc.stderr or b'').decode()
        print(output)
        assert 'SUPERSONIC_QML_DATA_PASS' in output
        assert 'SUPERSONIC_QML_FLOW_PASS' in output
        assert 'SUPERSONIC_QML_REAPPEAR_PASS' in output, 'hidden widget did not poll and reappear'
        assert 'Failed to load' not in output and 'Error:' not in output
        assert 'Binding loop detected' not in output, 'widget geometry contains a binding loop'
        assert ('/transport/play-track', {'id':['new']}) in calls
        assert ('/transport/play-album', {'id':['album&? 日本'], 't':['0'], 's':['false']}) in calls
        for op in ('playpause','next','previous'):
            assert ('/transport/'+op, {}) in calls
        assert len(timing_calls.read_text().splitlines()) >= 3, 'one-second timing refresh did not recur'
        print('Native primitives loaded; real QML → stdin JSON → UNIX IPC status/year/search/play flows passed.')
    finally:
        server.shutdown(); server.server_close(); thread.join()
print('Headless fixture only; rendered/live acceptance not observed.')
