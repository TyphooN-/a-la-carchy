import QtQuick


Item {
  id: root
  property bool autoPoll: true
  property string helperPath: Qt.resolvedUrl("ipc.py").toString().replace(/^file:\/\//, "")
  property string state: "loading"
  property var track: null
  property var year: null
  property string yearTrackId: ""
  property string yearAttemptId: ""
  property string query: ""
  property string kind: "all"
  property int generation: 0
  property int searchGeneration: -1
  property var results: []
  property bool truncated: false
  property bool partial: false
  property string searchState: "prompt"
  property string actionState: ""
  property string timingTrackId: ""
  property string displayedTimingId: ""
  property var elapsed: null
  property var duration: null
  property var playing: null
  readonly property string timingLabel: formatTime(elapsed) + " / " + formatTime(duration)
  readonly property bool actionBusy: actionRequest.busy
  readonly property bool canControl: (state === "ready" || state === "idle") && !actionBusy
  readonly property string yearLabel: year === null ? "Year unknown" : String(year)
  readonly property string metadataLabel: track ? ((track.artist || "Artist unknown") + " · " + (track.title || "Track unknown") + " · " + (track.album || "Album unknown") + " · " + yearLabel) : stateMessage(state)


  function formatTime(value) {
    if (typeof value !== "number" || !isFinite(value) || value < 0) return "--:--"
    var seconds = Math.floor(value)
    var hours = Math.floor(seconds / 3600)
    var minutes = Math.floor(seconds / 60) % 60
    var tail = String(seconds % 60).padStart(2, "0")
    return hours ? hours + ":" + String(minutes).padStart(2, "0") + ":" + tail : minutes + ":" + tail
  }
  function clearTiming() {
    elapsed = null; duration = null; playing = null; displayedTimingId = ""
  }
  function refreshTiming() {
    if (!autoPoll || !track || timingRequest.busy) return
    timingTrackId = track.id
    timingRequest.start({op: "timing", id: timingTrackId})
  }
  function acceptTiming(value, requestId) {
    // The helper validates the exact player and encoded MPRIS song ID. Also
    // reject replies overtaken by an IPC track change while the call ran.
    if (!track || requestId !== track.id) return
    if (value.state !== "ready" || value.id !== track.id) { clearTiming(); return }
    duration = typeof value.duration === "number" && isFinite(value.duration) && value.duration > 0 ? value.duration : null
    elapsed = typeof value.elapsed === "number" && isFinite(value.elapsed) && value.elapsed >= 0 ? value.elapsed : null
    if (elapsed !== null && duration !== null) elapsed = Math.min(elapsed, duration)
    playing = typeof value.playing === "boolean" ? value.playing : null
    displayedTimingId = track.id
  }
  onTrackChanged: if (!track || track.id !== displayedTimingId) clearTiming()
  Timer { interval: 1000; repeat: true; running: root.autoPoll && root.track !== null; onTriggered: root.refreshTiming() }

  function stateMessage(value) {
    var messages = {loading: "Connecting to Supersonic…", absent: "Supersonic unavailable — open the app", idle: "Supersonic — nothing queued", timeout: "Supersonic IPC timed out", "unsafe-socket": "Supersonic socket refused", "too-large": "Too many library matches — narrow the search", "invalid-query": "Use 2–256 characters", "invalid-response": "Supersonic response unsupported", "ipc-error": "Supersonic request failed — check the app", "helper-error": "Supersonic helper failed"}
    return messages[value] || "Supersonic request failed"
  }

  function refresh() {
    if (!statusRequest.busy && !actionRequest.busy) statusRequest.start({op: "status"})
  }

  function acceptStatus(value) {
    state = value.state
    track = value.state === "ready" && value.track ? value.track : null
    if (!track || track.id !== yearTrackId) {
      year = null
      yearTrackId = track ? track.id : ""
    }
    if (!track) yearAttemptId = ""
    lookupYear()
    if (track && displayedTimingId !== track.id) refreshTiming()
  }

  function lookupYear() {
    if (!autoPoll || !track || track.type !== 0 || yearRequest.busy || yearAttemptId === track.id) return
    yearAttemptId = track.id
    if (!track.title || track.title.trim().length < 2 || track.title.length > 256) return
    yearRequest.start({op: "year", id: track.id, query: track.title})
  }

  function invalidate() {
    generation += 1
    results = []
    truncated = false
    partial = false
    searchState = query.trim().length < 2 ? "prompt" : "loading"
    debounce.restart()
  }

  function setQuery(value) { query = value; invalidate() }
  function setKind(value) {
    if ((value !== "all" && value !== "song" && value !== "album") || kind === value) return
    kind = value
    invalidate()
  }
  function cancelSearch() {
    generation += 1
    debounce.stop()
    results = []
    truncated = false
    partial = false
    searchState = "prompt"
  }
  function searchNow() {
    debounce.stop()
    if (query.trim().length < 2) { searchState = "prompt"; return }
    if (query.trim().length > 256) { searchState = "invalid-query"; return }
    if (searchRequest.busy) return
    searchGeneration = generation
    searchState = "loading"
    searchRequest.start({op: "search", kind: kind, query: query})
  }
  function acceptSearch(value, serial) {
    if (serial !== generation) return
    searchState = value.state
    results = value.state === "ready" && Array.isArray(value.results) ? value.results : []
    truncated = !!value.truncated
    partial = !!value.partial
  }
  function action(op, row) {
    if (!canControl) return false
    var payload = row ? {op: "play", kind: row.kind, id: row.id} : {op: op}
    actionState = "requesting"
    return actionRequest.start(payload)
  }
  function select(row) {
    // Delegate modelData may be a QVariantMap copy. Use current canonical IDs,
    // not object identity or the delegate's arbitrary transport payload.
    if (!row) return false
    for (var i = 0; i < results.length; i++) {
      var current = results[i]
      if (current.id === row.id && current.kind === row.kind) return action("play", current)
    }
    return false
  }

  IPCRequest {
    id: statusRequest
    helperPath: root.helperPath
    onCompleted: function(value) { root.acceptStatus(value) }
  }
  IPCRequest {
    id: timingRequest
    helperPath: root.helperPath
    onCompleted: function(value) { root.acceptTiming(value, root.timingTrackId) }
  }
  IPCRequest {
    id: yearRequest
    helperPath: root.helperPath
    onCompleted: function(value) {
      if (value.state === "ready" && root.track && value.id === root.track.id)
        root.year = value.year === null || value.year === undefined ? null : value.year
      root.lookupYear()
    }
  }
  IPCRequest {
    id: searchRequest
    helperPath: root.helperPath
    onCompleted: function(value) {
      var serial = root.searchGeneration
      root.acceptSearch(value, serial)
      if (serial !== root.generation && root.searchState === "loading") debounce.restart()
    }
  }
  IPCRequest {
    id: actionRequest
    helperPath: root.helperPath
    onCompleted: function(value) {
      root.actionState = value.state
      root.refresh()
    }
  }
  Timer { id: debounce; interval: 300; onTriggered: if (root.autoPoll) root.searchNow() }
  Timer { interval: 3000; repeat: true; running: root.autoPoll; onTriggered: root.refresh() }
  Component.onCompleted: if (autoPoll) refresh()
}
