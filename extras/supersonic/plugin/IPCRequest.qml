import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root
  property string helperPath: Qt.resolvedUrl("ipc.py").toString().replace(/^file:\/\//, "")
  property var payload: ({})
  property bool busy: false
  property bool timedOut: false
  signal completed(var result)

  function start(value) {
    if (busy) return false
    payload = value
    busy = true
    timedOut = false
    proc.stdinEnabled = true
    proc.command = ["python3", decodeURIComponent(helperPath)]
    watchdog.restart()
    proc.running = true
    return true
  }

  function finish(value) {
    if (!busy) return
    busy = false
    watchdog.stop()
    completed(value)
  }

  Process {
    id: proc
    onStarted: {
      proc.write(JSON.stringify(root.payload))
      proc.stdinEnabled = false
    }
    stdout: StdioCollector { id: output }
    // Deliberately never forward stderr or private query/metadata to shell logs.
    stderr: StdioCollector {}
    onExited: function(exitCode) {
      var result = {state: root.timedOut ? "timeout" : "helper-error"}
      if (!root.timedOut) {
        try {
          var parsed = JSON.parse(output.text)
          if (parsed && typeof parsed.state === "string") result = parsed
        } catch (_) {}
      }
      root.finish(result)
    }
  }

  Timer {
    id: watchdog
    interval: 3500
    onTriggered: {
      root.timedOut = true
      if (proc.running) proc.signal(9)
      else root.finish({state: "helper-error"})
    }
  }
}
