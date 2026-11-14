#!/usr/bin/env python3
"""Stage a patched Agents clone in a NEW directory. Never installs or enables it."""
import argparse
import ast
import hashlib
import importlib.util
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

def local_assets(source):
    """Marks the clone adds. Native Local marks (current Omarchy ships them) stay
    byte-for-byte and are never mixed with these, so none are added beside them."""
    assets = Path(source)/'assets'
    if any((assets/name).exists() or (assets/name).is_symlink() for name in LOCAL_ASSETS):
        return {}
    return dict(LOCAL_ASSETS)

def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(UNSUPPORTED)
    return text.replace(old, new, 1)

def navigation_module():
    path = Path(__file__).with_name('native_navigation.py')
    spec = importlib.util.spec_from_file_location('alacarchy_native_navigation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

RECORDED_TOKEN_FUNCTIONS = '''  // Preserve absent counters: native numberValue() otherwise turns them into zero.
  function recordedTokenCounters(raw, providerId) {
    raw = raw || {}
    return { providerId: String(providerId || raw.providerId || raw.id || ""),
             hasLocalStats: raw.hasLocalStats, todayTotalTokens: raw.todayTotalTokens,
             recentDays: raw.recentDays, recentDaysUnit: raw.recentDaysUnit, modelUsage: raw.modelUsage }
  }

  function validRecordedTokens(n) {
    return typeof n === "number" && isFinite(n) && n >= 0
      && Math.floor(n) === n && n <= 9007199254740991
  }

  function recordedTokenWindows(raw, local) {
    var out = {today: null, week: null, history: null, recordedDays: 0}
    if (!raw || raw.hasLocalStats === false) return out
    if (validRecordedTokens(raw.todayTotalTokens)) out.today = raw.todayTotalTokens
    var days = raw.recentDays
    // The native Claude merger drops dailyActivity's message-count markers.
    // Without explicit units, that provider's days cannot establish tokens.
    var dailyTokens = local || String(raw.providerId || raw.id || "") !== "claude"
      || raw.recentDaysUnit === "tokens"
    if (dailyTokens && days && typeof days.length === "number" && days.length > 0 && days.length <= 366) {
      var recorded = []
      var seen = Object.create(null)
      var valid = true
      for (var i = 0; i < days.length; i++) {
        var day = days[i] || {}
        var date = String(day.date || "")
        // Claude's legacy stats-cache dailyActivity holds messages, not tokens.
        if (!/^\\d{4}-\\d{2}-\\d{2}$/.test(date) || seen[date]
            || day.sessionCount !== undefined || day.toolCallCount !== undefined) { valid = false; break }
        seen[date] = true
        if (day.recorded === false) continue
        if (!validRecordedTokens(day.messageCount)) { valid = false; break }
        recorded.push({date: date, tokens: day.messageCount})
      }
      if (local && days[days.length - 1] && days[days.length - 1].recorded !== true) out.today = null
      if (valid && recorded.length > 0) {
        recorded.sort(function(a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0 })
        var sum = 0
        var first = Math.max(0, recorded.length - 7)
        for (var d = first; d < recorded.length; d++) sum += recorded[d].tokens
        if (validRecordedTokens(sum)) { out.week = sum; out.recordedDays = recorded.length - first }
      }
    } else if (local) out.today = null
    var models = raw.modelUsage
    if (models && typeof models === "object" && !Array.isArray(models)) {
      var ids = Object.keys(models)
      var fields = ["inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"]
      var total = 0
      var ok = ids.length <= 4096 && (ids.length > 0 || raw.hasLocalStats === true)
      for (var m = 0; ok && m < ids.length; m++) {
        var bucket = models[ids[m]]
        ok = !!bucket && typeof bucket === "object" && !Array.isArray(bucket)
        var present = false
        for (var f = 0; ok && f < fields.length; f++) {
          var n = bucket[fields[f]]
          if (n === undefined) continue // Older/Local records omit cache counters.
          present = true
          ok = validRecordedTokens(n)
          if (ok) total += n
        }
        ok = ok && present && validRecordedTokens(total)
      }
      if (ok) out.history = total
    }
    return out
  }

  function recordedTokenLabel(value) {
    return String(value || "Account").slice(0, 512)
  }

  function recordedTokenProviderText(p, aggregate, accounts, usesAggregate) {
    function counts(stats) {
      var values = []
      if (stats.today !== null) values.push(formatTokenCount(stats.today) + " today")
      if (stats.week !== null) values.push(formatTokenCount(stats.week) + " last 7 days")
      if (stats.history !== null) values.push(formatTokenCount(stats.history) + " recorded")
      return values.join(" · ")
    }
    if (usesAggregate) return counts(aggregate)
    if (accounts.length > 4096) return ""
    var lines = []
    for (var a = 0; a < accounts.length; a++) {
      var row = accounts[a] || {}
      var text = counts(recordedTokenWindows(recordedTokenCounters(row.tokenCounters || row, p.providerId), false))
      if (text !== "") lines.push(recordedTokenLabel(row.label || row.id) + ": " + text)
    }
    return lines.join("\\n")
  }

  function recordedTokenTotalsFor(list, nativeCodex, hermes, rollup) {
    var cloud = {today: {value: 0, known: 0, sources: 0, overflow: false},
                 week: {value: 0, known: 0, sources: 0, overflow: false},
                 history: {value: 0, known: 0, sources: 0, overflow: false}}
    var out = {cloud: cloud, local: recordedTokenWindows(null, true),
               codex: recordedTokenWindows(nativeCodex, false), byProvider: Object.create(null)}
    var seen = Object.create(null)
    var windows = ["today", "week", "history"]
    function add(stats) {
      for (var w = 0; w < windows.length; w++) {
        var key = windows[w]
        var acc = cloud[key]
        acc.sources++
        if (stats[key] === null) continue
        acc.known++
        acc.value += stats[key]
        if (!validRecordedTokens(acc.value)) acc.overflow = true
      }
    }
    if (!list || list.length > 4096) list = [] // Explicit unknown, never truncated totals.
    for (var i = 0; i < list.length; i++) {
      var p = list[i] || {}
      var id = String(p.providerId || "")
      if (seen[id]) continue
      seen[id] = true
      var stats = recordedTokenWindows(p.tokenCounters || p, id === "local")
      if (id === "local") { out.local = stats; continue } // Overlaps Hermes requests.
      if (id === "local-transcripts") {
        // This native ledger can overlap engine observations. Keep its own
        // counters accessible without counting it in the additive header.
        out.byProvider[id] = recordedTokenProviderText(p, stats, [], true)
        continue
      }
      if (id === "codex") continue // Never attribute native CLI history to a Hermes credential.
      var accounts = p.accounts || []
      var aggregate = stats.today !== null || stats.week !== null || stats.history !== null
      // Choose a single ledger level for the entire provider, not per-window
      // fallbacks that could count overlapping aggregate/account histories.
      if (aggregate || accounts.length === 0 || accounts.length > 4096) add(stats)
      else for (var a = 0; a < accounts.length; a++) add(recordedTokenWindows(recordedTokenCounters(accounts[a].tokenCounters || accounts[a], id), false))
      out.byProvider[id] = recordedTokenProviderText(p, stats, accounts, aggregate)
    }
    // Owned Hermes accounting is a distinct, local-only ledger. Quota slots
    // never receive its unattributed counters and native CLI history stays separate.
    if (hermes && hermes.id === "hermes-token-accounting" && hermes.schemaVersion === 1
        && hermes.contract === "hermes-token-accounting/1" && hermes.readOnly === true
        && Array.isArray(hermes.sources) && hermes.sources.length <= 4096) {
      var hseen = Object.create(null)
      var hsources = []
      var hvalid = true
      var hproviders = ["openai", "openai-codex", "anthropic", "openrouter", "google", "gemini",
                        "groq", "deepseek", "nous", "xai", "mistral", "cerebras", "together", "fireworks"]
      // Validate the entire owned ledger before admitting any counters.
      // Duplicates/invalid provenance invalidate it; never silently keep a partial sum.
      for (var h = 0; h < hermes.sources.length; h++) {
        var source = hermes.sources[h]
        if (!source || typeof source !== "object" || Array.isArray(source)
            || typeof source.id !== "string" || !/^[a-f0-9]{64}$/.test(source.id)
            || hseen[source.id] || hproviders.indexOf(source.provider) < 0
            || typeof source.profile !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(source.profile)
            || source.attribution !== "unattributed") { hvalid = false; break }
        hseen[source.id] = true
        var hw = source.windows
        if (!hw || typeof hw !== "object" || Array.isArray(hw)) { hvalid = false; break }
        for (var v = 0; v < windows.length; v++) {
          var value = hw[windows[v]]
          if (value !== null && !validRecordedTokens(value)) { hvalid = false; break }
        }
        if (!hvalid) break
        // This ledger is undated, so the shared roll-up supplies its calendar windows.
        // The native Hermes record already reports Nous Portal by date: adding it
        // here would count it twice, so that provider stays undated.
        var dated = rollup && source.provider !== "nous" ? History.rollupCloudWindows(rollup, source.provider) : null
        hsources.push(dated ? {today: dated.today, week: dated.week, history: hw.history} : hw)
      }
      if (hvalid) for (var h = 0; h < hsources.length; h++) add(hsources[h])
    }
    out.total = {}
    for (var w = 0; w < windows.length; w++) {
      var key = windows[w]
      var acc = cloud[key]
      if (acc.known === 0 || acc.overflow) acc.value = null
      var local = out.local[key]
      var known = acc.value !== null || local !== null
      var sum = (acc.value === null ? 0 : acc.value) + (local === null ? 0 : local)
      out.total[key] = !known || acc.overflow || sum > 9007199254740991 ? null : sum
    }
    return out
  }

  // Adapt only the four clock-keyed windows. Reuse the data-bound accounting
  // object, provider strings and history references unchanged on every tick.
  function recordedDatedTokenTotals(undated, dated) {
    var out = {cloud: {history: undated.cloud.history},
      local: {history: undated.local.history, recordedDays: undated.local.recordedDays},
      codex: undated.codex, byProvider: undated.byProvider,
      total: {history: undated.total.history}, undated: undated}
    var grains = ["day", "week", "month", "year"]
    var windows = ["today", "week", "month", "year"]
    for (var i = 0; i < grains.length; i++) {
      var period = dated && dated.selectionError === false ? dated.periods[grains[i]] : null
      var key = windows[i]
      out.cloud[key] = period ? {value: period.cloud.value, known: period.cloud.knownSources,
        sources: period.cloud.sources, overflow: false} : {value: null, known: 0, sources: null, overflow: false}
      out.local[key] = period ? period.local.value : null
      out.total[key] = period ? period.value : null
    }
    return out
  }

  // One reactive pass per data/provider/sync revision, including Local's own
  // FileView. No animation, frame or reset-clock rescans of model history.
  readonly property var undatedTokenTotals: recordedTokenTotalsFor(enabledProviders, null, hermesAccountingAgent.record, null)
  readonly property var recordedTokenTotals: recordedDatedTokenTotals(undatedTokenTotals, datedTokenView)

'''

HISTORY_MAIN = """  // This helper resolves only system IANA calendar keys, never usage or credentials.
  property double calendarNowMs: Date.now()
  property var calendarClock: null
  property alias calendarTickTimer: calendarTick
  property alias calendarRefreshTimer: calendarRefresh
  readonly property string calendarTimezone: {
    var data = root.cloudObservedHistory ? root.cloudObservedHistory.data : null
    return data && Date.parse(data.observedAt) <= root.calendarNowMs ? data.timezone : ""
  }
  readonly property var calendarClockCommand: ["/usr/bin/python3", "-I", "-B",
    decodeURIComponent(Qt.resolvedUrl("CalendarClock.py").toString().substring(7))]
      .concat(calendarTimezone ? ["--timezone", calendarTimezone] : [])
  property int calendarGeneration: 0
  property int calendarRequestGeneration: -1
  property string calendarRequestTimezone: ""
  property var calendarRequestCommand: []
  property bool calendarRequestActive: false
  property bool calendarClockStreamDone: false
  property bool calendarClockExitDone: false
  property int calendarClockExitCode: -1
  property string calendarClockText: ""
  onCalendarTimezoneChanged: {
    calendarGeneration++
    calendarClock = null
    if (!calendarRequestActive) refreshCalendarClock()
  }
  function refreshCalendarClock() {
    if (calendarRequestActive) return
    calendarRequestActive = true
    calendarRequestGeneration = calendarGeneration
    calendarRequestTimezone = calendarTimezone
    // A timezone-change signal can precede reevaluation of the derived argv.
    // Keep the fixed helper prefix, but assemble options from the pinned zone.
    calendarRequestCommand = calendarClockCommand.slice(0, 4)
      .concat(calendarRequestTimezone ? ["--timezone", calendarRequestTimezone] : [])
    calendarClockStreamDone = false
    calendarClockExitDone = false
    calendarClockExitCode = -1
    calendarClockText = ""
    calendarProcess.running = true
  }
  function finishCalendarClock() {
    if (!calendarClockStreamDone || !calendarClockExitDone) return
    // Compare a completed lease with receipt-time wall clock, not the previous
    // display tick. Keep request custody while this can change the timezone;
    // the generation check below must observe any synchronous invalidation.
    calendarNowMs = Date.now()
    calendarRequestActive = false
    if (calendarRequestGeneration !== calendarGeneration || calendarRequestTimezone !== calendarTimezone) {
      if (!calendarRequestActive) refreshCalendarClock()
      return
    }
    var payload = null
    try {
      if (calendarClockExitCode === 0 && calendarClockText.length > 0 && calendarClockText.length <= 4096) {
        var candidate = JSON.parse(calendarClockText)
        // Admit structure independently of liveness: stale/future payloads stay
        // unusable at the actual dated view's independent display-clock boundary.
        var check = History.selectDatedHistory(null, null, candidate, {}, Date.parse(candidate.observedAt))
        if (!check.selectionError && (calendarRequestTimezone === "" || candidate.timezone === calendarRequestTimezone))
          payload = candidate
      }
    } catch (invalidClock) {}
    calendarClock = payload
  }
  Process {
    id: calendarProcess
    command: ["/usr/bin/timeout", "--signal=TERM", "--kill-after=1s", "5s"].concat(root.calendarRequestCommand)
    stdout: StdioCollector {
      onStreamFinished: {
        root.calendarClockText = text
        root.calendarClockStreamDone = true
        root.finishCalendarClock()
      }
    }
    onExited: (exitCode, exitStatus) => {
      root.calendarClockExitCode = exitCode
      root.calendarClockExitDone = true
      root.finishCalendarClock()
    }
  }
  Timer {
    id: calendarTick
    interval: 1000; repeat: true; running: true
    onTriggered: root.calendarNowMs = Date.now()
  }
  Timer {
    id: calendarRefresh
    interval: 15000; repeat: true; running: true; triggeredOnStart: true
    onTriggered: root.refreshCalendarClock()
  }
  readonly property var datedTokenView: History.selectDatedHistory(root.cloudObservedHistory,
    root.providerEnabled("local") ? localRouterAgent.record : null,
    root.calendarClock, {}, root.calendarNowMs)
  function datedHistoryFor(options) {
    if (options && options.metric === "total_tokens" && options.grain === "day" && options.mode === "combined")
      return root.datedTokenView
    return History.selectDatedHistory(root.cloudObservedHistory,
      root.providerEnabled("local") ? localRouterAgent.record : null,
      root.calendarClock, options, root.calendarNowMs)
  }

  // Explicitly private; never admitted to provider discovery or localSnapshot().
  // The one dated Cloud + Local roll-up: the calendar and the bar's Hermes today.
  readonly property string tokenRollupPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/local-router/agents/unified_daily.json"
  property var tokenRollup: ({state: "loading", summaries: [], bars: []})
  property string tokenRollupText: ""
  property bool tokenRollupFresh: false
  function refreshTokenRollupFreshness() {
    var fresh = History.rollupFresh(root.tokenRollup, Date.now())
    if (fresh !== root.tokenRollupFresh) root.tokenRollupFresh = fresh
  }
  FileView {
    id: tokenRollupFile
    path: root.tokenRollupPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      // An unchanged file must not rebuild the chart.
      var content = text()
      if (content !== root.tokenRollupText) {
        root.tokenRollupText = content
        root.tokenRollup = History.parseRollup(content)
      }
      root.refreshTokenRollupFreshness()
    }
    onLoadFailed: {
      root.tokenRollupText = ""
      root.tokenRollup = ({state: "unavailable", summaries: [], bars: []})
      root.refreshTokenRollupFreshness()
    }
  }
  // A published today expires with no further publication. While expired, also
  // re-read: a replaced file can cost this FileView its watch.
  Timer {
    interval: 5000
    repeat: true
    running: true
    onTriggered: {
      root.refreshTokenRollupFreshness()
      if (!root.tokenRollupFresh) tokenRollupFile.reload()
    }
  }

  // Cloud-only history, used only until a valid roll-up exists.
  readonly property string observedHistoryPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/hermes-token-accounting/history.json"
  property var cloudObservedHistory: ({state: "loading", summaries: [], bars: []})
  FileView {
    id: observedHistoryFile
    path: root.observedHistoryPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.cloudObservedHistory = History.parseHistory(text())
    onLoadFailed: root.cloudObservedHistory = ({state: "unavailable", summaries: [], bars: []})
  }
  readonly property var observedHistory: root.tokenRollup.data ? root.tokenRollup : root.cloudObservedHistory

"""

HISTORY_PANEL_FUNCTIONS = '''  readonly property var observedHistory: usage.observedHistory
  property string historyMetric: "total_tokens"
  property string historyGrain: "day"
  property string historyMode: "combined"
  // No source picker: omitting `sources` charts every admitted source.
  readonly property var historyView: historyMode === "combined"
    ? usage.datedHistoryFor({metric: historyMetric, grain: historyGrain, mode: "combined"})
    : History.selectHistory(observedHistory, {metric: historyMetric, grain: historyGrain, mode: historyMode})
  function historySourceColor(id) {
    var palette = [root.foreground, Commons.Color.accent, root.urgent,
                   Qt.tint(root.foreground, Qt.rgba(0, 0, 0, 0.25)),
                   Qt.tint(Commons.Color.accent, Qt.rgba(1, 1, 1, 0.25)),
                   Qt.tint(root.urgent, Qt.rgba(1, 1, 1, 0.25))]
    return palette[parseInt(id.slice(0, 2), 16) % palette.length]
  }
  function historyMetricUnit() {
    return root.historyMetric === "api_call_count" ? "calls" : "tokens"
  }
  function historyCoverageText(bucket) {
    var known = bucket.knownSources === undefined ? bucket.known : bucket.knownSources
    var sources = bucket.sources === undefined ? historyView.selectedSources.length : bucket.sources
    return known + "/" + (sources === null ? "?" : sources) + " sources observed"
  }
  function historySourceLabel(id) {
    for (var i = 0; i < historyView.sources.length; i++)
      if (historyView.sources[i].id === id) return historyView.sources[i].label
    return id.slice(0, 8)
  }

'''

HISTORY_PANEL = """          Column {
            id: observedHistorySection
            objectName: "observedHistory"
            width: column.width
            spacing: Style.space(4)
            PanelSectionHeader {
              objectName: "observedHistoryHelp"
              activeFocusOnTab: true
              property string helpText: root.historyView.help || root.observedHistory.help || "Private history not available. Missing counters are not zero."
              Accessible.role: Accessible.StaticText
              Accessible.name: helpText
              HoverHandler { id: observedHelpHover }
              ToolTip {
                id: observedHelpTip
                width: Style.space(340)
                visible: observedHelpHover.hovered || parent.activeFocus
                text: parent.helpText
                delay: 300
                contentItem: Text {
                  text: observedHelpTip.text
                  textFormat: Text.PlainText
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
              }
              text: "OBSERVED HISTORY"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: ({observed:root.historyMode === "combined" ? "Dated sampled totals" : "Sampled deltas", zero:"Measured zero",
                      baseline:"Undated baseline", empty:"No admitted sources · history unknown",
                      unavailable:"History unavailable", malformed:"History malformed", loading:"History loading"})[root.historyView.state]
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
            Flow {
              width: parent.width
              spacing: Style.space(4)
              Repeater {
                model: [{id:"total_tokens", label:"Total"}, {id:"input_tokens", label:"Input"},
                        {id:"output_tokens", label:"Output"}, {id:"prompt_tokens", label:"Prompt"},
                        {id:"cache_read_tokens", label:"Cache read"}, {id:"cache_write_tokens", label:"Cache write"},
                        {id:"reasoning_tokens", label:"Reasoning"}, {id:"api_call_count", label:"API calls"}]
                Ui.Button {
                  required property var modelData
                  objectName: "historyMetric_" + modelData.id
                  text: modelData.label
                  selected: root.historyMetric === modelData.id
                  focusable: true
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  foreground: root.foreground
                  onClicked: root.historyMetric = modelData.id
                }
              }
            }
            Flow {
              width: parent.width
              spacing: Style.space(4)
              Repeater {
                model: [{id:"day", label:"Day"}, {id:"week", label:"Week"},
                        {id:"month", label:"Month"}, {id:"year", label:"Year"}]
                Ui.Button {
                  required property var modelData
                  objectName: "historyGrain_" + modelData.id
                  text: modelData.label
                  selected: root.historyGrain === modelData.id
                  focusable: true
                  fontFamily: root.fontFamily
                  fontSize: Style.font.caption
                  foreground: root.foreground
                  onClicked: root.historyGrain = modelData.id
                }
              }
            }
            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.historyMode === "combined"
                ? (root.historyView.selectionError ? "Calendar unavailable" : "Current calendar")
                    + (usage.calendarClock ? " · " + usage.calendarClock.timezone : "")
                : (root.observedHistory.data ? "Latest retained · " + root.observedHistory.data.timezone : "")
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WrapAnywhere
            }
            Repeater {
              model: root.historyView.summaries
              Text {
                required property var modelData
                width: observedHistorySection.width
                textFormat: Text.PlainText
                text: ({day:"Day", week:"Week", month:"Month", year:"Year"})[modelData.grain]
                  + " · " + (modelData.key || "—") + " · " + root.recordedCount(modelData.value) + (modelData.partial ? "+" : "") + " " + root.historyMetricUnit()
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
            // A calendar row holds up to seven dated buckets, oldest first; an
            // eighth starts the next row. Per-source cells stack inside their
            // date, so the source count never removes date columns. Labels
            // break only where they must (after a date "-", around digit
            // runs, units and "+") to fit their bucket; no character is
            // clipped or dropped. Large per-source inventories use a bounded
            // vertical viewport; every date/bar/label stays reachable without
            // sideways scrolling.
            Flickable {
              id: historyChart
              objectName: "historyChart"
              readonly property bool multiple: root.historyView.mode === "multiple"
              readonly property int daysPerRow: 7
              readonly property real maximumViewportHeight: Style.space(320)
              readonly property real bucketWidth: Math.max(1, (historyChartGrid.width - historyChartGrid.columnSpacing * (historyChartGrid.columns - 1)) / historyChartGrid.columns)
              function dateLine(key) { return root.historyGrain === "day" || root.historyGrain === "week" ? key.slice(5) : key }
              function countLine(value, partial) { return value === null ? "?" : root.recordedCount(value) + (partial ? "+" : "") }
              function seriesLine(id) { return id.slice(0, 4) }
              // Greedy line packing at those break points; a single run wider
              // than the room (never a realistic label) breaks per character.
              function fitLines(text, room) {
                if (!(room > 0) || !(historyLabelMetrics.height > 0)) return String(text)
                var lines = String(text).split("\\n"), out = []
                for (var l = 0; l < lines.length; l++) {
                  var parts = lines[l].match(/[0-9]+[.,]?-?|[^0-9]/g) || [""], line = ""
                  for (var p = 0; p < parts.length; p++) {
                    var part = parts[p]
                    if (line && historyLabelMetrics.advanceWidth(line + part) > room) { out.push(line); line = "" }
                    while (!line && part.length > 1 && historyLabelMetrics.advanceWidth(part) > room) {
                      var n = 1
                      while (n + 1 < part.length && historyLabelMetrics.advanceWidth(part.slice(0, n + 1)) <= room) n++
                      out.push(part.slice(0, n))
                      part = part.slice(n)
                    }
                    line += part
                  }
                  out.push(line)
                }
                return out.join("\\n")
              }
              width: parent.width
              height: visible ? Math.min(maximumViewportHeight, historyChartGrid.implicitHeight) : 0
              visible: root.historyView.bars.length > 0
              clip: true
              contentWidth: width
              contentHeight: historyChartGrid.implicitHeight
              flickableDirection: Flickable.VerticalFlick
              boundsBehavior: Flickable.StopAtBounds
              interactive: contentHeight > height
              ScrollBar.vertical: ScrollBar { width: Style.space(8); policy: ScrollBar.AsNeeded }
              FontMetrics {
                id: historyLabelMetrics
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
              }
              Grid {
                id: historyChartGrid
                // Reserve the vertical thumb and a gap; it never covers labels.
                width: Math.max(1, historyChart.width - Style.space(12))
                columns: Math.max(1, Math.min(historyChart.daysPerRow, root.historyView.bars.length))
                columnSpacing: Style.space(4)
                rowSpacing: Style.space(8)
                Repeater {
                  model: root.historyView.bars
                  Item {
                    id: historyBucket
                    objectName: "historyBucket"
                    required property var modelData
                    readonly property bool multiple: historyChart.multiple
                    readonly property var cells: multiple ? modelData.series : [{id:"", value:modelData.value}]
                    readonly property string bucketKey: modelData.key
                    width: historyChart.bucketWidth
                    height: historyCells.height + historyDate.implicitHeight + Style.space(4)
                    Column {
                      id: historyCells
                      width: parent.width
                      Repeater {
                        model: historyBucket.cells
                        Item {
                          id: historyCell
                          required property var modelData
                          width: historyCells.width
                          height: Style.space(40) + (historyBucket.multiple ? historySeriesValue.implicitHeight + Style.space(4) : 0)
                          activeFocusOnTab: true
                          Accessible.role: Accessible.StaticText
                          Accessible.name: historyBucket.modelData.key + " · "
                            + (modelData.id ? root.historySourceLabel(modelData.id) + " · " : "")
                            + root.historyMetric + " · " + (modelData.value === null ? "unknown" : modelData.value)
                            + (!modelData.id && historyBucket.modelData.partial
                               ? " · lower bound · " + root.historyCoverageText(historyBucket.modelData) : "")
                          HoverHandler { id: historyBarHover }
                          ToolTip {
                            id: historyBarTip
                            visible: historyBarHover.hovered || parent.activeFocus
                            text: parent.Accessible.name
                            delay: 300
                            contentItem: Text {
                              text: historyBarTip.text
                              textFormat: Text.PlainText
                              color: root.foreground
                              font.family: root.fontFamily
                              font.pixelSize: Style.font.caption
                            }
                          }
                          Rectangle {
                            objectName: "observedHistoryBar"
                            readonly property var observedValue: historyCell.modelData.value
                            readonly property string sourceId: historyCell.modelData.id
                            readonly property string bucketKey: historyBucket.modelData.key
                            x: Style.space(2)
                            width: Math.max(1, parent.width - Style.space(4))
                            anchors.bottom: historyBucket.multiple ? historySeriesValue.top : parent.bottom
                            anchors.bottomMargin: historyBucket.multiple ? Style.space(4) : 0
                            height: observedValue === null ? 0 : Math.max(1, Style.space(40) * observedValue / Math.max(1, root.historyView.maximum))
                            color: sourceId ? root.historySourceColor(sourceId) : root.foreground
                            opacity: 0.75
                          }
                          Text {
                            id: historySeriesValue
                            objectName: "historySeriesValue"
                            // Unbroken label; `text` only adds fitted line breaks.
                            readonly property string fullText: historyChart.seriesLine(historyCell.modelData.id) + "\\n"
                              + historyChart.countLine(historyCell.modelData.value, false)
                            visible: historyBucket.multiple
                            anchors.bottom: parent.bottom
                            width: parent.width
                            textFormat: Text.PlainText
                            text: historyChart.fitLines(fullText, width)
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            horizontalAlignment: Text.AlignHCenter
                          }
                        }
                      }
                    }
                    Text {
                      id: historyDate
                      objectName: "historyDate"
                      // Unbroken label; `text` only adds fitted line breaks.
                      readonly property string fullText: historyChart.dateLine(modelData.key)
                        + (historyBucket.multiple ? "" : "\\n" + historyChart.countLine(modelData.value, modelData.partial))
                      anchors.bottom: parent.bottom
                      width: parent.width
                      textFormat: Text.PlainText
                      text: historyChart.fitLines(fullText, width)
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      horizontalAlignment: Text.AlignHCenter
                    }
                  }
                }
              }
            }
          }

"""

def credential_contract():
    """Share presentation allowlists without importing any auth/collector code."""
    tree = ast.parse(source_bytes(Path(__file__).parents[1]/'hermes-codex-usage/collector.py'))
    values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in ('PROVIDER_LABELS', 'REASONS')}
    return values


HERMES_INVENTORY_MAIN = '''  // Controlled scanner provenance, never a prefix-based account allowlist.
  property var hermesInventory: null
  readonly property var hermesInventoryIndex: validateHermesInventory(hermesInventory)
  FileView {
    id: hermesInventoryFile
    path: root.usageDir + "/.hermes-credential-inventory"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.hermesInventory = JSON.parse(text()) } catch (e) { root.hermesInventory = null }
      root.rescanAgents()
      root.dataRevision++
    }
    onLoadFailed: { root.hermesInventory = null; root.dataRevision++ }
  }

  function hermesId(id) {
    return String(id).indexOf("codex-hermes-") === 0 || String(id).indexOf("hermes-credential-") === 0
  }

  function hermesDigest(ascii) {
    // SHA-256 of the bounded ASCII public identity schema (not secret material).
    var k = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
      0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
      0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
      0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
      0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
      0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
      0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
      0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]
    var h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]
    var words = [], length = ascii.length
    for (var i=0;i<length;i++) {
      var c=ascii.charCodeAt(i)
      if (c>127) return ""
      words[i>>2]=(words[i>>2] || 0) | (c << (24-(i%4)*8))
    }
    words[length>>2]=(words[length>>2] || 0) | (0x80 << (24-(length%4)*8))
    var end=(((length+8)>>6)+1)*16
    words[end-1]=length*8
    function rotate(x,n) { return (x>>>n) | (x<<(32-n)) }
    for (var block=0;block<end;block+=16) {
      var w=[], state=h.slice()
      for (var t=0;t<64;t++) {
        if (t<16) w[t]=words[block+t] | 0
        else {
          var x=w[t-15], y=w[t-2]
          w[t]=((rotate(x,7)^rotate(x,18)^(x>>>3))+w[t-16]+w[t-7]+(rotate(y,17)^rotate(y,19)^(y>>>10))) | 0
        }
        var a=state[0], b=state[1], c=state[2], d=state[3], e=state[4], f=state[5], g=state[6], z=state[7]
        var one=(z+(rotate(e,6)^rotate(e,11)^rotate(e,25))+((e&f)^(~e&g))+k[t]+w[t]) | 0
        var two=((rotate(a,2)^rotate(a,13)^rotate(a,22))+((a&b)^(a&c)^(b&c))) | 0
        state=[(one+two)|0,a,b,c,(d+one)|0,e,f,g]
      }
      for (var j=0;j<8;j++) h[j]=(h[j]+state[j]) | 0
    }
    return h.map(function(n) { return (n>>>0).toString(16).padStart(8,"0") }).join("")
  }

  function validateHermesInventory(inventory) {
    var empty = Object.create(null)
    if (!inventory || inventory.schemaVersion !== 1 || inventory.contract !== "hermes-credential-inventory/1"
        || typeof inventory.generation !== "string" || !/^[a-f0-9]{64}$/.test(inventory.generation)
        || !Array.isArray(inventory.entries) || inventory.entries.length > 256) return empty
    var seen = Object.create(null)
    var canonical = []
    var previous = ""
    var labels = __HERMES_PROVIDER_LABELS__
    for (var i = 0; i < inventory.entries.length; i++) {
      var e = inventory.entries[i]
      if (!e || typeof e !== "object" || Array.isArray(e) || seen[e.id]
          || typeof e.principalId !== "string" || !/^[a-f0-9]{64}$/.test(e.principalId)
          || !Object.prototype.hasOwnProperty.call(labels, e.provider)
          || e.quota !== (e.provider === "openai-codex" ? "codex" : "unavailable")
          || e.id !== (e.provider === "openai-codex" ? "codex-hermes-" : "hermes-credential-") + e.principalId
          || e.name !== labels[e.provider] + " (Hermes " + e.principalId.slice(0, 8) + ")"
          || e.id <= previous || Object.keys(e).sort().join(",") !== "id,name,principalId,provider,quota") return empty
      previous = e.id
      canonical.push({id:e.id, name:e.name, principalId:e.principalId, provider:e.provider, quota:e.quota})
      seen[e.id] = e
    }
    return hermesDigest(JSON.stringify(canonical)) === inventory.generation ? seen : empty
  }

  function hermesEntry(id) {
    var index = typeof hermesInventoryIndex === "undefined"
      ? validateHermesInventory(typeof hermesInventory === "undefined" ? null : hermesInventory) : hermesInventoryIndex
    return Object.prototype.hasOwnProperty.call(index, id) ? index[id] : null
  }

  function hermesRecordAdmitted(record) {
    if (!record) return false
    if (!hermesId(record.id)) return true
    var e = hermesEntry(String(record.id))
    return !!e && record.inventoryGeneration === hermesInventory.generation
      && record.credentialProvider === e.provider && record.principalId === e.principalId
  }

  function hermesSyncedRecord(id, stats) {
    var record = {id:id, inventoryGeneration:stats.inventoryGeneration,
                  credentialProvider:stats.credentialProvider, principalId:stats.principalId}
    if (!hermesRecordAdmitted(record)) return null
    // Remote metadata admits an identity, never its per-account quotas/history.
    record.limits = []
    record.limitsFetchedAt = 0
    record.limitsStale = true
    return hermesCleanRecord(record)
  }

  function hermesSnapshot(record) {
    var e = hermesEntry(String(record.id))
    return {providerId:e.id, providerName:e.name, inventoryGeneration:hermesInventory.generation,
            credentialProvider:e.provider, principalId:e.principalId,
            ready:true, hasLocalStats:false, hasPromptStats:false, readOnly:true}
  }

  function hermesCleanRecord(record) {
    if (!hermesId(record.id)) return record
    if (!hermesRecordAdmitted(record)) return null
    var e = hermesEntry(String(record.id))
    var reasons = __HERMES_REASONS__
    var out = {id:e.id, name:e.name, readOnly:true, ready:true, hasLocalStats:false, hasPromptStats:false,
      inventoryGeneration:hermesInventory.generation, credentialProvider:e.provider, principalId:e.principalId,
      limits:[], limitsStale:true, limitsFetchedAt:0, tierLabel:"Hermes",
      usageStatusText:reasons.unsupported, authHelpText:reasons.unsupported}
    if (e.quota !== "codex") return out
    var status = typeof record.usageStatusText === "string" ? record.usageStatusText : ""
    if (status !== "" && Object.keys(reasons).map(function(k) { return reasons[k] }).indexOf(status) < 0) status = reasons.quota
    var limits = record.limits
    var stamp = record.limitsFetchedAt
    var valid = Array.isArray(limits) && limits.length <= 2
      && typeof stamp === "number" && isFinite(stamp) && stamp >= 0 && stamp <= 4102444800000
    var windows = []
    for (var i = 0; valid && i < limits.length; i++) {
      var w = limits[i]
      var match = w && typeof w.label === "string" ? w.label.match(/^([0-9]{1,8})-(day|hour|minute|second) window$/) : null
      var seconds = match ? Number(match[1]) * ({day:86400,hour:3600,minute:60,second:1})[match[2]] : 0
      valid = !!w && typeof w.percent === "number" && isFinite(w.percent) && w.percent >= 0 && w.percent <= 1
        && seconds > 0 && seconds <= 31622400 && typeof w.resetsAt === "string"
        && (w.resetsAt === "" || (w.resetsAt.length <= 35 && isFinite(Date.parse(w.resetsAt))))
      if (valid) windows.push({label:w.label, title:seconds === 604800 ? "Weekly" : match[1] + "-" + match[2],
                              percent:w.percent, resetsAt:w.resetsAt})
    }
    out.limits = valid ? windows : []
    out.limitsFetchedAt = valid ? stamp : 0
    out.limitsStale = !valid || record.limitsStale !== false || windows.length === 0
    out.tierLabel = ["free","plus","pro","team","business","enterprise","edu"].indexOf(record.tierLabel) >= 0 ? record.tierLabel : "Hermes"
    out.usageStatusText = out.authHelpText = !valid || (windows.length === 0 && status === "") ? reasons.quota : status
    return out
  }

'''

CALIBRATION_MAIN = '''  // Local-only estimate publication. Never an Agent, ledger or sync source.
  readonly property string quotaCalibrationPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/hermes-token-accounting/quota-calibration.json"
  readonly property int quotaCalibrationMaxBytes: 1048576
  property var quotaCalibration: null
  property string quotaCalibrationBuffer: ""
  property bool quotaCalibrationStreamDone: false
  property bool quotaCalibrationExitDone: false
  property bool quotaCalibrationExitOk: false
  function applyQuotaCalibrationRead() {
    if (!quotaCalibrationStreamDone || !quotaCalibrationExitDone) return
    try {
      quotaCalibration = quotaCalibrationExitOk && quotaCalibrationBuffer.length <= quotaCalibrationMaxBytes
        ? JSON.parse(quotaCalibrationBuffer) : null
    } catch (e) { quotaCalibration = null }
    quotaCalibrationBuffer = ""
    dataRevision++
  }
  Process {
    id: quotaCalibrationReader
    command: ["/usr/bin/head", "-c", "1048577", "--", root.quotaCalibrationPath]
    running: true
    onRunningChanged: if (running) {
      root.quotaCalibrationStreamDone = false
      root.quotaCalibrationExitDone = false
      root.quotaCalibrationExitOk = false
      root.quotaCalibrationBuffer = ""
    }
    stdout: StdioCollector {
      onStreamFinished: {
        root.quotaCalibrationBuffer = text
        root.quotaCalibrationStreamDone = true
        root.applyQuotaCalibrationRead()
      }
    }
    onExited: (code, status) => {
      root.quotaCalibrationExitDone = true
      root.quotaCalibrationExitOk = code === 0 && status === 0
      root.applyQuotaCalibrationRead()
    }
  }
  Timer {
    interval: 30000
    repeat: true
    running: true
    onTriggered: if (!quotaCalibrationReader.running) quotaCalibrationReader.running = true
  }
  function quotaCalibrationFor(record) {
    var data = typeof quotaCalibration === "undefined" ? null : quotaCalibration
    if (!record || record.credentialProvider !== "openai-codex" || record.limitsStale !== false
        || record.id !== "codex-hermes-" + record.principalId || !data
        || data.contract !== "hermes-quota-calibration/1" || data.schemaVersion !== 1 || data.readOnly !== true
        || data.inventoryGeneration !== record.inventoryGeneration
        || data.inventoryGeneration !== hermesInventory.generation
        || !Array.isArray(data.accounts) || data.accounts.length > 256
        || typeof data.observedAt !== "number" || !isFinite(data.observedAt)
        || typeof data.validUntil !== "number" || !isFinite(data.validUntil)
        || data.validUntil <= data.observedAt || data.validUntil - data.observedAt > 120) return null
    var seen = Object.create(null), found = null
    for (var i = 0; i < data.accounts.length; i++) {
      var row = data.accounts[i]
      if (!row || typeof row.principalId !== "string" || !/^[a-f0-9]{64}$/.test(row.principalId) || seen[row.principalId]) return null
      seen[row.principalId] = true
      if (row.principalId === record.principalId) found = row
    }
    var windows = record.limits || [], weekly = null
    for (var w = 0; w < windows.length; w++) {
      if (windows[w].label === "7-day window") {
        if (weekly !== null) return null
        weekly = windows[w]
      }
    }
    // Date.parse truncates sub-millisecond ISO fractions. Rebuild seconds from
    // the integral date and the producer's microsecond fraction instead.
    var resetText = weekly && typeof weekly.resetsAt === "string" ? weekly.resetsAt : ""
    var resetParts = /^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:[.]([0-9]{1,6}))?(Z|[+-][0-9]{2}:[0-9]{2})$/.exec(resetText)
    var resetSeconds = resetParts ? Date.parse(resetParts[1] + resetParts[3]) / 1000
      + (resetParts[2] ? Number("0." + resetParts[2]) : 0) : NaN
    if (!found || !weekly || typeof found.quotaUsed !== "number" || !isFinite(found.quotaUsed)
        || typeof found.quotaObservedAt !== "number" || !isFinite(found.quotaObservedAt)
        || found.quotaObservedAt <= 0 || data.observedAt <= 0
        || typeof record.limitsFetchedAt !== "number" || !isFinite(record.limitsFetchedAt)
        || typeof found.resetsAt !== "number" || !isFinite(found.resetsAt)
        || found.quotaUsed !== weekly.percent
        || found.quotaObservedAt * 1000 !== record.limitsFetchedAt
        || Math.abs(found.quotaObservedAt - data.observedAt) > 120
        || data.validUntil > Math.min(found.quotaObservedAt + 120,found.resetsAt)
        || found.resetsAt !== resetSeconds) return null
    // Copy the explicit estimate envelope only, not file-supplied counter fields.
    return {status:found.status,sampleCount:found.sampleCount,estimate:found.estimate,
            observedAt:data.observedAt,quotaObservedAt:found.quotaObservedAt,
            validUntil:data.validUntil,resetsAt:found.resetsAt}
  }

'''

CALIBRATION_PANEL_FUNCTIONS = '''  function quotaCalibrationText(p) {
    if (!p || String(p.providerId || "").indexOf("codex-hermes-") !== 0) return ""
    var fallback = "Weekly tokens · calibrating"
    var sample = p.quotaCalibration, now = root.nowMs / 1000
    if (!sample || typeof sample.observedAt !== "number" || typeof sample.validUntil !== "number"
        || typeof sample.quotaObservedAt !== "number" || !isFinite(sample.quotaObservedAt)
        || typeof sample.resetsAt !== "number" || !isFinite(sample.resetsAt)
        || !isFinite(sample.observedAt) || !isFinite(sample.validUntil) || !isFinite(now)
        || sample.observedAt <= 0 || sample.quotaObservedAt <= 0
        || now < sample.observedAt || now < sample.quotaObservedAt
        || now >= sample.validUntil || now >= sample.resetsAt || now >= sample.quotaObservedAt + 120
        || Math.abs(sample.quotaObservedAt - sample.observedAt) > 120
        || sample.validUntil <= sample.observedAt
        || sample.validUntil > Math.min(sample.observedAt + 120,sample.quotaObservedAt + 120,sample.resetsAt)
        || typeof sample.sampleCount !== "number" || sample.sampleCount % 1 !== 0
        || sample.sampleCount < 0 || sample.sampleCount > 24) return fallback
    var suffix = " · " + sample.sampleCount + " samples"
    if (sample.status !== "estimated") return fallback + (sample.status === "ambiguous" ? " · ambiguous" : "") + suffix
    var e = sample.estimate
    if (!e) return fallback
    var fields = [e.tokensPerQuotaPoint,e.usedWeeklyTokens,e.observedSeconds,e.observedFrom,e.observedTo]
    for (var i = 0; i < fields.length; i++)
      if (typeof fields[i] !== "number" || !isFinite(fields[i]) || fields[i] < 0 || fields[i] > 9007199254740991) return fallback
    var range = e.observedRange
    if (sample.sampleCount < 3 || e.tokensPerQuotaPoint <= 0 || e.tokensPerQuotaPoint > 1000000000
        || e.usedWeeklyTokens > 100000000000 || e.observedSeconds < 1800 || e.observedSeconds > 172800
        || e.observedTo > sample.observedAt || e.observedFrom >= e.observedTo
        || !Array.isArray(range) || range.length !== 2
        || typeof range[0] !== "number" || typeof range[1] !== "number"
        || !isFinite(range[0]) || !isFinite(range[1]) || range[0] <= 0 || range[1] < range[0]
        || range[1] > 2*range[0] || e.tokensPerQuotaPoint < range[0] || e.tokensPerQuotaPoint > range[1]) return fallback
    return "Weekly tokens ≈ " + usage.formatTokenCount(e.usedWeeklyTokens) + " used · estimated, workload-dependent"
      + suffix + " · " + Math.round(e.observedSeconds / 60) + " min observed · correlation only"
      + "\\n" + usage.formatTokenCount(e.tokensPerQuotaPoint) + " tokens/quota point · observed range "
      + usage.formatTokenCount(range[0]) + "–" + usage.formatTokenCount(range[1]) + " (not confidence bounds)"
  }

'''

CALIBRATION_PANEL_TEXT = '''    Text {
      objectName: "quotaCalibrationStatus"
      visible: text !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: root.quotaCalibrationText(section.provider)
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

'''


NATIVE_LOCAL_FUNCTIONS = '''  // A native session ledger is not the private router's engine sample stream.
  // Reserve a separate display identity without changing its stored model IDs.
  function nativeLocalRecordAdmitted(record) {
    return !!record && record.id === "local" && record.schemaVersion === 1
      && record.hasPromptStats === true && String(record.scope || "device") === "device"
      && record.localLive === undefined && record.readOnly !== true
  }

  function nativeLocalSyncStatsAdmitted(stats) {
    return !!stats && stats.hasPromptStats === true
      && String(stats.scope || "device") === "device"
      && stats.localLive === undefined && stats.readOnly !== true
      && (stats.sourceKind === undefined || stats.sourceKind === "native-local-transcripts")
  }

  function nativeLocalSyncRecord(stats) {
    if (!nativeLocalSyncStatsAdmitted(stats)) return null
    return {id:"local", name:"Local", schemaVersion:1, hasPromptStats:true}
  }

  function nativeLocalProvider(record) {
    if (!nativeLocalRecordAdmitted(record)) return null
    var p = displayProvider(record, true)
    if (!p) return null
    p.providerId = "local-transcripts"
    p.providerName = "Local transcripts"
    p.sourceKind = "native-local-transcripts"
    p.readOnly = true
    p.localLive = null
    p.tierLabel = p.tierLabel || "Native transcript history"
    p.usageStatusText = "Separate transcript ledger; may overlap router telemetry. Not added to router totals."
      + (p.usageStatusText ? " " + p.usageStatusText : "")
    return p
  }

'''


def transform_main(text):
    # Keep the refresh fallback safe while Qt tears down a removed reader.
    # Both admitted native versions have this exact body; drift must refuse.
    text = replace_once(text, '  function reloadRecords() {\n    for (var i = 0; i < agents.length; i++)\n      if (agents[i]) agents[i].reload()\n  }',
                        '  function reloadRecords() {\n    for (var i = 0; i < agents.length; i++) {\n      var agent = agents[i]\n      // A just-removed Instantiator delegate can leave a dead Qt wrapper.\n      if (agent && typeof agent.reload === "function") agent.reload()\n    }\n  }')
    text = replace_once(text, "import Quickshell.Io\n", 'import Quickshell.Io\nimport "History.js" as History\n')
    text = replace_once(text, '  Instantiator {\n    id: agentInstantiator', HISTORY_MAIN + CALIBRATION_MAIN + '  Instantiator {\n    id: agentInstantiator')
    # The private router record remains outside `agents` and never starts sync.
    # Genuine native Local transcripts retain their normal file reader, with
    # a separate display identity and admission that refuses misplaced router data.
    text = replace_once(text, '      if (name.slice(-5) === ".json") ids.push(name.slice(0, -5))',
                        '      if (/^[A-Za-z0-9_-]+[.]json$/.test(name) && name !== "local-transcripts.json" && name !== "codex.json"\n'
                        '          && (!hermesId(name.slice(0, -5)) || hermesEntry(name.slice(0, -5)))) ids.push(name.slice(0, -5))')
    text = replace_once(text, '  Instantiator {\n    id: agentInstantiator',
                        '  // a-la-carchy: the llama.cpp router record of this machine only.\n'
                        '  readonly property string localRouterRecordPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/local-router/agents/local.json"\n\n'
                        '  Agent {\n    id: localRouterAgent\n    agentId: "local"\n    path: root.localRouterRecordPath\n  }\n\n'
                        '  readonly property string hermesAccountingRecordPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/hermes-token-accounting/usage.json"\n\n'
                        '  Agent {\n    id: hermesAccountingAgent\n    agentId: "hermes-token-accounting"\n    path: root.hermesAccountingRecordPath\n  }\n\n'
                        '  Instantiator {\n    id: agentInstantiator')
    text = replace_once(text, '    var syncedProviders = syncConfigured() && aggregateData && aggregateData.providers ? aggregateData.providers : {}',
                        '    var localRouterRecord = localRouterAgent.record\n'
                        '    if (localRouterRecord && String(localRouterRecord.id) === "local" && providerEnabled("local"))\n'
                        '      result.push(displayProvider(localRouterRecord)) // Status stays visible before recording starts.\n'
                        '    var syncedProviders = syncConfigured() && aggregateData && aggregateData.providers ? aggregateData.providers : {}')
    text = replace_once(text, '    for (var syncedId in syncedProviders) {',
                        '    for (var syncedId in syncedProviders) {\n      if (syncedId === "local-transcripts") continue')
    if '      var syncedDisplay = displayProvider({ id: syncedId, name: stats.providerName || syncedId })' in text:
        text = replace_once(text, '      var syncedDisplay = displayProvider({ id: syncedId, name: stats.providerName || syncedId })',
                            '      var syncedRecord = syncedId === "local" ? nativeLocalSyncRecord(stats) : (hermesId(syncedId) ? hermesSyncedRecord(syncedId, stats) : {id:syncedId, name:stats.providerName || syncedId})\n'
                            '      if (!syncedRecord) continue\n'
                            '      var syncedDisplay = syncedId === "local" ? nativeLocalProvider(syncedRecord) : displayProvider(syncedRecord)')
    # Preserve the Local-only snapshot guard used by the optional Grok stager;
    # enforce the independent Hermes inventory guard on its own following line.
    text = replace_once(text, '      providerMap[String(record.id)] = providerSnapshot(record)',
                        '      if (String(record.id) === "local" && !nativeLocalRecordAdmitted(record)) continue // Only native transcripts enter sync.\n'
                        '      if (!hermesRecordAdmitted(record)) continue\n'
                        '      if (hermesId(record.id) && String(agents[i].agentId) !== String(record.id)) continue\n'
                        '      providerMap[String(record.id)] = providerSnapshot(record)')
    # Re-check identity at admission as well as filtering the native filename.
    # This also suppresses a Codex provider arriving only through sync.
    text = replace_once(text, '  function providerEnabled(id) {',
                        '  function providerEnabled(id) {\n'
                        '    if (id === "codex" || (id.indexOf("codex-") === 0 && !hermesEntry(id))) return false\n'
                        '    if (hermesId(id) && !hermesEntry(id)) return false')
    contract = credential_contract()
    inventory_main = HERMES_INVENTORY_MAIN.replace('__HERMES_PROVIDER_LABELS__', json.dumps(contract['PROVIDER_LABELS']))
    inventory_main = inventory_main.replace('__HERMES_REASONS__', json.dumps(contract['REASONS']))
    text = replace_once(text, '  function displayProvider(record) {', inventory_main + NATIVE_LOCAL_FUNCTIONS + '  function displayProvider(record) {')
    if '      if (!record || !record.id) continue\n      var id = String(record.id)' in text:
        text = replace_once(text, '      if (!record || !record.id) continue\n      var id = String(record.id)',
                            '      if (!record || !record.id || !hermesRecordAdmitted(record)) continue\n'
                            '      var id = String(record.id)\n'
                            '      if (id === "local" && !nativeLocalRecordAdmitted(record)) continue\n'
                            '      if (hermesId(id) && String(agents[i].agentId) !== id) continue')
    if '  function providerSnapshot(record) {' in text:
        text = replace_once(text, '  function providerSnapshot(record) {',
                            '  function providerSnapshot(record) {\n    if (hermesId(record.id)) return hermesRecordAdmitted(record) ? hermesSnapshot(record) : null')
    if '  function aggregateSnapshots(snapshots) {' in text:
        text = replace_once(text, '  function aggregateSnapshots(snapshots) {',
                            '  function aggregateSnapshots(snapshots) {\n    var hermesProviders = Object.create(null)')
        text = replace_once(text, '        var stats = snapshotProviders[providerId] || {}',
                            '        var stats = snapshotProviders[providerId] || {}\n'
                            '        if (providerId === "local-transcripts" || !providerEnabled(providerId === "local" ? "local-transcripts" : String(providerId))) continue\n'
                            '        if (providerId === "local" && !nativeLocalSyncStatsAdmitted(stats)) continue\n'
                            '        if (hermesId(providerId)) {\n'
                            '          var admitted = hermesSyncedRecord(providerId, stats)\n'
                            '          if (admitted) hermesProviders[providerId] = hermesSnapshot(admitted)\n'
                            '          continue\n        }')
        text = replace_once(text, '    var outProviders = {}', '    var outProviders = {}\n    for (var h in hermesProviders) outProviders[h] = hermesProviders[h]')
    text = replace_once(text, '  function displayProvider(record) {',
                        '  function displayProvider(record) {\n'
                        '    record = hermesCleanRecord(record)\n'
                        '    if (!record) return null\n'
                        '    var external = record.readOnly === true || String(record.id) === "local" || hermesId(record.id)')
    text = replace_once(text, '      providerId: String(record.id),\n      providerName: String(record.name || record.id),\n      ready: record.ready === true || synced,',
                        '      providerId: String(record.id),\n      readOnly: external,\n'
                        '      credentialProvider: hermesId(record.id) ? record.credentialProvider : "",\n'
                        '      quotaCalibration: quotaCalibrationFor(record),\n'
                        '      providerName: hermesEntry(String(record.id)) ? hermesEntry(String(record.id)).name : String(record.name || record.id),\n'
                        '      ready: record.ready === true || synced,')
    text = replace_once(text, '      ready: record.ready === true || synced,',
                        '      ready: record.ready === true || synced,\n'
                        '      localLive: String(record.id) === "local" && !synced ? record.localLive : null,\n'
                        '      tokenCounters: recordedTokenCounters(synced ? stats : record, String(record.id)),')
    text = replace_once(text, '      accounts: Array.isArray(record.accounts) ? record.accounts : [],',
                        '      accounts: external ? [] : (Array.isArray(record.accounts) ? record.accounts : []),')
    text = replace_once(text, '      accountSwitch: record.accountSwitch || ({ mode: "manual", threshold: 95 }),',
                        '      accountSwitch: external ? null : (record.accountSwitch || ({ mode: "manual", threshold: 95 })),')
    text = replace_once(text, '  function syncedStatsFor(providerId) {',
                        RECORDED_TOKEN_FUNCTIONS + '  function syncedStatsFor(providerId, nativeLocal) {\n    if (providerId === "local" && nativeLocal !== true) return null')
    if '    return aggregateData.providers[providerId] || null' in text:
        text = replace_once(text, '    return aggregateData.providers[providerId] || null',
                            '    var stats = aggregateData.providers[providerId]\n'
                            '    if (providerId === "local" && !nativeLocalSyncStatsAdmitted(stats)) return null\n'
                            '    if (hermesId(providerId)) {\n'
                            '      var admitted = stats ? hermesSyncedRecord(providerId, stats) : null\n'
                            '      return admitted ? hermesSnapshot(admitted) : null\n    }\n'
                            '    return stats || null')
    if '      var display = displayProvider(record)' in text:
        text = replace_once(text, '      var display = displayProvider(record)',
                            '      var display = id === "local" ? nativeLocalProvider(record) : displayProvider(record)')
        text = replace_once(text, '      if (!providerEnabled(id)) continue',
                            '      if (!providerEnabled(id === "local" ? "local-transcripts" : id)) continue')
    if '      if (localIds[syncedId] || !providerEnabled(syncedId)) continue' in text:
        text = replace_once(text, '      if (localIds[syncedId] || !providerEnabled(syncedId)) continue',
                            '      if (localIds[syncedId] || !providerEnabled(syncedId === "local" ? "local-transcripts" : syncedId)) continue')
    if '      if (!providerEnabled(String(record.id))) continue' in text:
        text = replace_once(text, '      if (!providerEnabled(String(record.id))) continue',
                            '      if (!providerEnabled(record.id === "local" ? "local-transcripts" : String(record.id))) continue')
    if '    var stats = syncedStatsFor(String(record.id))' in text:
        text = replace_once(text, '    var stats = syncedStatsFor(String(record.id))',
                            '    var stats = syncedStatsFor(String(record.id), nativeLocal === true)')
    text = replace_once(text, '  function displayProvider(record) {', '  function displayProvider(record, nativeLocal) {')
    snapshot_fields = '      providerId: String(record.id),\n      providerName: String(record.name || record.id),\n      ready: record.ready === true,'
    if snapshot_fields in text:
        text = replace_once(text, snapshot_fields,
                            '      sourceKind: record.id === "local" ? "native-local-transcripts" : "native-agent",\n' + snapshot_fields)
    return text

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

LIMITS_TROUBLE_TEXT_LOCALIZED = LIMITS_TROUBLE_TEXT.replace(
    '      text: section.provider ? String(section.provider.authHelpText || "") : ""',
    '      // Shown for the status, so a record with no help to offer says the status rather than nothing.\n'
    '      text: section.provider ? (root.helpDisplayText(section.provider.authHelpText) || root.statusDisplayText(section.provider.usageStatusText) || "") : ""')

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
    Text {
      objectName: "localTaskRates"
      visible: !!section.provider && section.provider.providerId === "local"
      width: parent.width
      textFormat: Text.PlainText
      text: visible ? root.localLiveText(section.provider) : ""
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      fontSizeMode: Text.HorizontalFit
      minimumPixelSize: 8
      wrapMode: Text.NoWrap
    }
'''

PROVIDER_ROWS_FUNCTIONS = '''  // Stable semantic rows: raw records and native indices are not filtered.
  property var nativeRenameOwner: null
  function setNativeRename(token, editing) {
    if (editing) root.nativeRenameOwner = token
    else if (root.nativeRenameOwner === token) root.nativeRenameOwner = null
    root.renaming = root.nativeRenameOwner !== null
  }
  property var providerRowsByKey: ({})
  property alias providerRowsModel: stableProviderRows
  readonly property var providerRowInput: root.providers
  onProviderRowInputChanged: syncProviderRows()
  ListModel {
    id: stableProviderRows
    Component.onCompleted: root.syncProviderRows()
  }
  function syncProviderRows() {
    root.syncPanelRows(root.providerRowsModel, root.providerRowInput || root.providers || [],
      "providerId", root, "providerRowsByKey")
  }
  function syncPanelRows(model, input, kind, owner, field) {
    if (!model) return
    var records = Object.create(null), occurrences = Object.create(null), keys = []
    for (var i = 0; i < input.length; i++) {
      var record = input[i], raw = record ? record[kind] : null
      var id = kind === "window" ? JSON.stringify([record.title, record.resetAt])
        : raw === null || raw === undefined ? "" : String(raw)
      var ordinal = occurrences[id] || 0
      occurrences[id] = ordinal + 1
      var key = JSON.stringify([id, ordinal])
      keys.push(key)
      records[key] = record
    }
    // References only, one per current row; no lifetime union. Value refresh
    // is O(n) with no model mutations. Publish records before adding new keys.
    owner[field] = records
    for (var i = model.count - 1; i >= 0; i--)
      if (!Object.prototype.hasOwnProperty.call(records, model.get(i).providerKey)) model.remove(i)
    // Cold membership/order changes use bounded O(n^2) reconciliation.
    for (var i = 0; i < keys.length; i++) {
      if (i < model.count && model.get(i).providerKey === keys[i]) continue
      var found = -1
      for (var j = i + 1; j < model.count; j++)
        if (model.get(j).providerKey === keys[i]) { found = j; break }
      if (found >= 0) model.move(found, i, 1)
      else model.insert(i, {providerKey: keys[i]})
    }
  }

'''


def transform_provider_row_continuity(text):
    original = '''          Repeater {
            id: providerSections
            model: root.addStage === "" ? root.providers : []

            ProviderSection {
              required property var modelData
              required property int index
              width: column.width
              provider: modelData
              providerIndex: index
            }
          }'''
    retained = '''          Repeater {
            id: providerSections
            model: root.addStage === "" ? root.providerRowsModel : null

            ProviderSection {
              required property string providerKey
              required property int index
              readonly property var modelData: root.providerRowsByKey[providerKey] || null
              width: column.width
              provider: modelData
              providerIndex: index
            }
          }'''
    text = replace_once(text, original, retained)
    return replace_once(text, '  function planLabel(p) {',
                        PROVIDER_ROWS_FUNCTIONS + '  function planLabel(p) {')



def transform_nested_row_continuity(text):
    # Only reached for the complete pinned modern source, after optional Grok
    # composition. Do not consume another stager's exact anchor prematurely.
    header = limits_component_body(text, '  component AccountHeader: Item {')
    guarded_header = replace_once(header, '    property bool editing: false',
                                 '    property bool editing: false\n    readonly property var nativeRenameToken: ({})')
    guarded_header = replace_once(guarded_header, '    property string renamedTo: ""',
                                 '    property string renamedTo: ""\n'
                                 '    property double renameGeneration: 0\n'
                                 '    property string renameProviderId: ""\n'
                                 '    property string renameAccountId: ""')
    guarded_header = replace_once(guarded_header, '    onAccountChanged: renamedTo = ""',
                                 '    onAccountChanged: { renamedTo = ""; cancelRenameIfChanged() }\n'
                                 '    onOwnerChanged: cancelRenameIfChanged()')
    guarded_header = replace_once(guarded_header, '    function startRename() {\n      editing = true',
                                 '''    function renameIdentityMatches() {
      return renameProviderId !== "" && renameAccountId !== "" && owner && account
        && owner.readOnly !== true && String(owner.providerId || "") === renameProviderId
        && String(account.id || "") === renameAccountId
    }
    function cancelRenameIfChanged() {
      if (!renameProviderId && !renameAccountId) return
      if (!renameIdentityMatches()) {
        renameGeneration++
        renameProviderId = ""
        renameAccountId = ""
        editing = false
      }
    }
    function startRename() {
      if (!owner || owner.readOnly === true || !owner.providerId || !account || !account.id) return
      renameGeneration++
      renameProviderId = String(owner.providerId)
      renameAccountId = String(account.id)
      editing = true''')
    guarded_header = replace_once(guarded_header, '      var value = nameField.text.trim()',
                                 '      var generation = renameGeneration\n      var value = nameField.text.trim()')
    guarded_header = replace_once(guarded_header, '      if (save && value !== "" && value !== label) {',
                                 '      if (save && generation === renameGeneration && renameIdentityMatches()\n'
                                 '          && value !== "" && value !== label) {')
    guarded_header = replace_once(guarded_header, '        root.renameAccount(owner, account, value)\n      }',
                                 '        root.renameAccount(owner, account, value)\n      }\n'
                                 '      if (generation === renameGeneration) { renameProviderId = ""; renameAccountId = "" }')
    guarded_header = replace_once(guarded_header, '    onEditingChanged: root.renaming = editing',
                                 '    onEditingChanged: root.setNativeRename(head.nativeRenameToken, editing)\n'
                                 '    Component.onDestruction: { renameGeneration++; root.setNativeRename(head.nativeRenameToken, false) }')
    text = replace_once(text, header, guarded_header)
    anchor = '    readonly property var windows: root.displayWindows(provider)'
    models = '''
    property var accountRowsByKey: ({})
    property var limitRowsByKey: ({})
    onAccountsChanged: root.syncPanelRows(stableAccountRows, section.accounts, "id", section, "accountRowsByKey")
    onWindowsChanged: root.syncPanelRows(stableLimitRows, section.windows, "window", section, "limitRowsByKey")
    ListModel {
      id: stableAccountRows
      Component.onCompleted: root.syncPanelRows(stableAccountRows, section.accounts, "id", section, "accountRowsByKey")
    }
    ListModel {
      id: stableLimitRows
      Component.onCompleted: root.syncPanelRows(stableLimitRows, section.windows, "window", section, "limitRowsByKey")
    }'''
    text = replace_once(text, anchor, anchor + models)
    text = replace_once(text, '''        model: section.multi ? [] : section.windows

        CompactLimit {
          required property var modelData
          width: section.width
          window: modelData''', '''        model: section.multi ? null : stableLimitRows

        CompactLimit {
          required property string providerKey
          width: section.width
          window: section.limitRowsByKey[providerKey] || null''')
    text = replace_once(text, '''      model: section.multi ? section.accounts : []

      Column {
        id: accountBlock
        required property var modelData
        required property int index''', '''      model: section.multi ? stableAccountRows : null

      Column {
        id: accountBlock
        required property string providerKey
        required property int index
        readonly property var modelData: section.accountRowsByKey[providerKey] || ({})''')
    expressions = ['root.grokAccountWindows(section.provider, accountBlock.modelData)',
                   'root.displayWindows({ limits: accountBlock.modelData.limits || [] })']
    supported = [expr for expr in expressions if ('            model: ' + expr) in text]
    if len(supported) != 1:
        raise ValueError(UNSUPPORTED)
    expression = supported[0]
    extra = '''        property var limitRowsByKey: ({})
        readonly property var stableLimitInput: EXPRESSION
        onStableLimitInputChanged: root.syncPanelRows(stableAccountLimits, stableLimitInput, "window", accountBlock, "limitRowsByKey")
        ListModel {
          id: stableAccountLimits
          Component.onCompleted: root.syncPanelRows(stableAccountLimits, accountBlock.stableLimitInput, "window", accountBlock, "limitRowsByKey")
        }
'''.replace('EXPRESSION', expression)
    text = replace_once(text, '        readonly property var modelData: section.accountRowsByKey[providerKey] || ({})\n',
                        '        readonly property var modelData: section.accountRowsByKey[providerKey] || ({})\n' + extra)
    text = replace_once(text, '            model: ' + expression, '            model: stableAccountLimits')
    return replace_once(text, '''              required property var modelData
              width: accountBlock.width
              window: modelData''', '''              required property string providerKey
              width: accountBlock.width
              window: accountBlock.limitRowsByKey[providerKey] || null''')

LIMITS_LIVE_FUNCTIONS = '''  property double localRateNowMs: Date.now()
  property var localRecentEngineRates: ({})
  readonly property var localRatePublication: {
    for (var i = 0; i < providers.length; i++)
      if (providers[i] && providers[i].providerId === "local") return providers[i]
    return null
  }
  onLocalRatePublicationChanged: {
    // A publication can arrive after the last periodic tick. Observe actual
    // receipt time before admission; do not turn an ordinary arrival into a
    // future sample and erase still-leased phases. Real future data still fails.
    root.localRateNowMs = Date.now()
    root.localRecentEngineRates = localRememberEngineRates(root.localRatePublication)
  }
  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: {
      root.localRateNowMs = Date.now()
      root.localRecentEngineRates = localRememberEngineRates(root.localRatePublication)
    }
  }

  // a-la-carchy: current-task live consumer
  function localLiveFresh(at, ttl) {
    var now = root.localRateNowMs / 1000
    return typeof at === "number" && isFinite(at) && at > 0 && at <= 253402300799
      && typeof ttl === "number" && isFinite(ttl) && ttl > 0 && ttl <= 30
      && isFinite(now) && now >= at && now - at < ttl
  }

  function localLiveIdentity(row) {
    var worker = row.workerIdentity
    var tasks = row.taskIdentity
    if (typeof worker !== "string" || worker.length === 0 || worker.length > 256 || /[\\x00-\\x1f]/.test(worker)
        || !tasks || typeof tasks.length !== "number" || tasks.length < 1 || tasks.length > 256
        || row.activeRequests !== tasks.length) return null
    var pairs = [], last = -1
    for (var i = 0; i < tasks.length; i++) {
      var task = tasks[i]
      if (!task || task.length !== 2 || typeof task[0] !== "number" || Math.floor(task[0]) !== task[0]
          || task[0] <= last || task[0] > 255 || typeof task[1] !== "number" || !isFinite(task[1])
          || Math.floor(task[1]) !== task[1] || task[1] < 0 || task[1] > 9007199254740991) return null
      last = task[0]
      pairs.push([task[0], task[1]])
    }
    return JSON.stringify([worker, pairs])
  }

  function localLiveRate(row, phase, live) {
    var value = row[phase + "TokPerSec"], at = row[phase + "ObservedAt"], span = row[phase + "WindowSec"]
    var identity = localLiveIdentity(row)
    if (row.state !== "processing" || row.slotsState !== "available" || identity === null
        || JSON.stringify(row[phase + "Identity"]) !== identity
        || !localLiveFresh(row.observedAt, live.staleAfterSec) || row.observedAt > live.observedAt
        || !localLiveFresh(at, live.staleAfterSec) || at > row.observedAt
        || typeof span !== "number" || !isFinite(span) || span < 0.1 || span > 30
        || typeof value !== "number" || !isFinite(value) || value <= 0 || value > 9007199254740991) return "—"
    return String(Number(value.toFixed(2))) + " tok/s"
  }

  function localLiveModels(live) {
    var models = live ? live.models : null
    if (!models || typeof models.length !== "number" || Math.floor(models.length) !== models.length
        || models.length < 0 || models.length > 16) return null
    var seen = Object.create(null)
    for (var i = 0; i < models.length; i++) {
      var row = models[i]
      if (!row || typeof row.model !== "string" || row.model.length === 0 || row.model.length > 256
          || /[\\x00-\\x1f]/.test(row.model) || seen[row.model]) return null
      seen[row.model] = true
      if (row.state === "shared worker (counted once)") continue
      for (var phase of ["prompt", "generation"]) {
        var rate = row[phase + "TokPerSec"]
        if (rate !== null && rate !== undefined && (typeof rate !== "number" || !isFinite(rate)
            || rate <= 0 || rate > 9007199254740991)) return null
      }
    }
    return models
  }

  function localLiveMetrics(row, live) {
    var metrics = row.engineMetrics, worker = row.workerIdentity
    if (!metrics || metrics.contract !== "llamacpp-metrics-summary/1"
        || typeof worker !== "string" || worker.length === 0 || worker.length > 256
        || /[\\x00-\\x1f]/.test(worker) || metrics.workerIdentity !== worker
        || !localLiveFresh(row.observedAt, live.staleAfterSec) || row.observedAt > live.observedAt
        || !localLiveFresh(metrics.observedAt, live.staleAfterSec) || metrics.observedAt > row.observedAt) return null
    var active = metrics.activeRequests
    if (active !== null && (typeof active !== "number" || !isFinite(active)
        || Math.floor(active) !== active || active < 0 || active > 256)) return null
    for (var phase of ["prompt", "generation"]) {
      var rate = metrics[phase + "TokPerSec"]
      if (rate !== null && (typeof rate !== "number" || !isFinite(rate)
          || rate <= 0 || rate > 9007199254740991)) return null
    }
    return metrics
  }

  function localEngineKey(row) { return JSON.stringify([row.model, row.workerIdentity]) }

  function localEnginePhaseFresh(value) {
    var now = root.localRateNowMs / 1000
    return value && typeof value.value === "number" && isFinite(value.value) && value.value > 0
      && value.value <= 9007199254740991 && typeof value.observedAt === "number"
      && isFinite(value.observedAt) && value.observedAt > 0 && now >= value.observedAt
      && now - value.observedAt < 60
  }

  function localRememberEngineRates(p) {
    // Rebuild only the current bounded inventory: no lifetime union or hidden source cache.
    var next = Object.create(null), live = p ? p.localLive : null
    if (!live || live.contract !== "local-task-rates/1" || !localLiveFresh(live.observedAt, live.staleAfterSec)) return next
    var models = localLiveModels(live)
    if (models === null) return next
    var old = root.localRecentEngineRates || ({})
    for (var i = 0; i < models.length; i++) {
      var row = models[i]
      if (row.state === "shared worker (counted once)") continue
      var metrics = localLiveMetrics(row, live)
      if (metrics === null) continue
      var key = localEngineKey(row), previous = old[key], entry = {observedAt: metrics.observedAt}
      if (previous && previous.observedAt > metrics.observedAt) continue
      for (var phase of ["prompt", "generation"]) {
        var rate = metrics[phase + "TokPerSec"], prior = previous ? previous[phase] : null
        if (rate !== null) entry[phase] = {value: rate, observedAt: metrics.observedAt}
        else if (localEnginePhaseFresh(prior) && prior.observedAt <= metrics.observedAt)
          entry[phase] = {value: prior.value, observedAt: prior.observedAt}
      }
      next[key] = entry
    }
    return next
  }

  function localEngineRate(row, metrics, phase) {
    var value = metrics[phase + "TokPerSec"]
    if (value !== null) return String(Number(value.toFixed(2))) + " tok/s"
    var cached = (root.localRecentEngineRates || ({}))[localEngineKey(row)]
    var prior = cached ? cached[phase] : null
    return localEnginePhaseFresh(prior) && prior.observedAt <= metrics.observedAt
      ? String(Number(prior.value.toFixed(2))) + " tok/s" : "—"
  }

  function localLiveText(p) {
    var live = p ? p.localLive : null
    if (!live || live.contract !== "local-task-rates/1") return "Live rates unavailable"
    if (!localLiveFresh(live.observedAt, live.staleAfterSec)) return "Live rates stale · activity unknown"
    var models = localLiveModels(live)
    if (models === null) return "Live rates unavailable"
    var lines = [], processing = 0, unknown = 0, workers = 0, usedMetrics = false, usedTasks = false
    for (var i = 0; i < models.length; i++) {
      var row = models[i]
      if (row.state === "shared worker (counted once)") continue
      workers++
      var fresh = localLiveFresh(row.observedAt, live.staleAfterSec) && row.observedAt <= live.observedAt
      var prompt = localLiveRate(row, "prompt", live), generation = localLiveRate(row, "generation", live)
      var metrics = localLiveMetrics(row, live)
      if (metrics !== null && prompt === "—" && generation === "—") {
        usedMetrics = true
        if (metrics.activeRequests === null) unknown++
        else if (metrics.activeRequests > 0) processing++
        lines.push(row.model + " · " + (metrics.activeRequests === null ? "activity unknown"
          : metrics.activeRequests + " processing (metrics)") + "\\n"
          + "prompt " + localEngineRate(row, metrics, "prompt")
          + " · generation " + localEngineRate(row, metrics, "generation"))
      } else {
        usedTasks = true
        if (fresh && row.state === "processing") processing++
        else if (!(fresh && row.state === "idle")) unknown++
        if (fresh && row.state === "idle") lines.push(row.model + " · idle")
        else lines.push(row.model + " · prompt " + prompt + " · generation " + generation)
      }
    }
    var headline = workers + " observed worker(s) · " + (unknown > 0
      ? (processing > 0 ? "at least " + processing + " processing" : "activity unknown") + " · " + unknown + " unknown"
      : processing + " processing")
    if (models.length === 0) headline = typeof live.status === "string" && live.status.length <= 512
      ? live.status : "Live activity unavailable"
    var scopes = []
    if (usedTasks || !usedMetrics) scopes.push("Sampled wall-clock · current tasks")
    if (usedMetrics) scopes.push("Engine averages · not current-task rates")
    return [headline].concat(lines).concat(scopes).join("\\n")
  }

'''

LIMITS_LOCAL_FUNCTION = LIMITS_LIVE_FUNCTIONS + '''  // a-la-carchy: what the Local section says. These are sampled llama.cpp
  // router counters of this machine: a day without samples is a gap, never
  // zero usage, and cached input is not part of the counters.
  function localRouterText(p) {
    if (!p) return ""
    var lines = []
    var status = String(p.usageStatusText || "")
    if (status.indexOf("Sampled tokens") !== 0 && status !== "")
      lines.push(status.replace(/ · (history retained|not collecting)$/, ""))
    var models = p.todayTokensByModel || {}
    var names = Object.keys(models).filter(function(id) { return Number(models[id]) > 0 })
    names.sort(function(a, b) { return Number(models[b]) - Number(models[a]) || (a < b ? -1 : 1) })
    if (names.length > 0)
      lines.push("Today: " + names.map(function(id) { return id + " " + usage.formatTokenCount(Number(models[id])) }).join(" · "))
    if (lines.length === 0) {
      var days = p.recentDays || []
      var last = days.length > 0 ? days[days.length - 1] : null
      lines.push(last && last.recorded === true && p.todayTotalTokens === 0 ? "No tokens today" : "No token data")
    }
    return lines.join("\\n")
  }

'''

HERMES_CARD_FUNCTIONS = '''  // Presentation only: retain every provider in the usage/accounting models.
  readonly property var hermesDisplayNames: {
    var index = usage.hermesInventoryIndex || ({})
    var ids = Object.keys(index).sort(), counts = Object.create(null), names = Object.create(null)
    for (var i = 0; i < ids.length; i++) {
      var entry = index[ids[i]]
      var ordinal = (counts[entry.provider] || 0) + 1
      counts[entry.provider] = ordinal
      var base = entry.name.slice(0, entry.name.lastIndexOf(" (Hermes "))
      names[entry.id] = base + " " + ordinal + " (Hermes)"
    }
    return names
  }
  function providerHeading(p) {
    return p ? (hermesDisplayNames[p.providerId] || p.providerName) : ""
  }
  function providerCardVisible(p) {
    if (!p) return false
    if (p.readOnly !== true || String(p.credentialProvider || "") === "") return true
    // Same clock-aware windows as the actual meter Repeater; measured zero counts.
    return root.displayWindows(p).length > 0
  }

'''

HERMES_QUOTA_FUNCTIONS = '''  // Retained quota samples never become a reset forecast or a fabricated zero.
  // Read the native ticking clock, independently of publisher/FileView activity.
  function hermesQuotaFresh(p) {
    if (!p || String(p.providerId || "").indexOf("codex-hermes-") !== 0) return true
    if (p.limitsStale !== false) return false
    var fetched = p.limitsFetchedAt
    var interval = Number(usage.setting("refreshIntervalSec", 900))
    if (!isFinite(interval) || interval < 30 || interval > 3600) interval = 900
    if (typeof fetched !== "number" || !isFinite(fetched) || fetched <= 0
        || fetched > root.nowMs + 60000 || root.nowMs - fetched > Math.max(60, interval) * 2000)
      return false
    var list = p.limits || []
    // Repeater modelData can contain a Qt sequence rather than a JS Array.
    if (typeof list.length !== "number" || list.length < 1 || list.length > 2
        || Math.floor(list.length) !== list.length) return false
    for (var i = 0; i < list.length; i++) {
      var w = list[i] || {}
      var reset = typeof w.resetsAt === "string" ? Date.parse(w.resetsAt) : NaN
      if (typeof w.percent !== "number" || !isFinite(w.percent) || w.percent < 0 || w.percent > 1
          || !isFinite(reset) || reset <= root.nowMs) return false
    }
    return true
  }

  function hermesQuotaText(p) {
    if (!p || String(p.providerId || "").indexOf("codex-hermes-") !== 0
        || root.hermesQuotaFresh(p) || String(p.usageStatusText || "") !== "") return ""
    // Existing collector errors already have their own native status surface.
    // A quota never validly fetched is unknown, not a message; only a retained sample is stale.
    var fetched = p.limitsFetchedAt
    return typeof fetched === "number" && isFinite(fetched) && fetched > 0 && fetched <= root.nowMs + 60000
      ? "Quota stale" : ""
  }

'''

HERMES_QUOTA_TEXT = '''    Text {
      objectName: "hermesQuotaStatus"
      textFormat: Text.PlainText
      visible: text !== ""
      width: parent.width
      text: root.hermesQuotaText(section.provider)
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

'''

TOKEN_PANEL_FUNCTIONS = '''  readonly property var recordedTotals: usage.recordedTokenTotals
  readonly property string recordedTokenBarText: "Tokens Today " + recordedCount(recordedTotals.total.today)

  readonly property string recordedTokenCompactText: recordedCount(recordedTotals.total.today)

  function recordedCount(n) { return n === null ? "—" : usage.formatTokenCount(n) }

  function recordedTokenHelpText() {
    var cloud = recordedTotals.cloud.today
    return "Cloud today: " + cloud.known + "/" + (cloud.sources === null ? "?" : cloud.sources) + " sources recorded. "
      + "Local today: " + (recordedTotals.local.today === null ? "not recorded" : "recorded") + ". "
      + (cloud.overflow ? "Cloud sum unavailable. " : "")
      + "Missing counters are not zero; quota is not tokens. Recording boundaries vary by source. "
      + "Local is sampled; cached input not counted. CLI history is not added separately. — unavailable."
  }

  function recordedCloudWindowText(key, label) {
    var window = recordedTotals.cloud[key]
    return label + " " + recordedCount(window.value) + " · " + window.known + "/" + window.sources + " sources"
      + (window.overflow ? " · sum unavailable" : "")
  }

  function recordedCloudTotalsText() {
    var p = recordedTotals.cloud
    return "Cloud · today " + recordedCount(p.today.value)
      + " · week " + recordedCount(p.week.value)
      + " · recorded " + recordedCount(p.history.value)
  }

  function recordedLocalTotalsText() {
    var p = recordedTotals.local
    return "Local · today " + recordedCount(p.today)
      + " · week " + recordedCount(p.week)
      + " · recorded " + recordedCount(p.history)
  }

  function recordedCombinedTotalsText() {
    var p = recordedTotals.total
    return "Total · today " + recordedCount(p.today) + " · week " + recordedCount(p.week) + " · recorded " + recordedCount(p.history)
  }

'''

TOKEN_HEADER = '''      // Pinned totals; explanations are available on hover, not repeated in cards.
      Column {
        id: recordedTokenHeader
        objectName: "recordedTokenHeader"
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Style.space(4)
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: "Recorded tokens"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
          wrapMode: Text.WordWrap
          HoverHandler { id: tokenHelpHover }
          ToolTip.visible: tokenHelpHover.hovered
          ToolTip.delay: 300
          ToolTip.text: root.recordedTokenHelpText()
        }
        Repeater {
          model: [root.recordedCombinedTotalsText(), root.recordedCloudTotalsText(), root.recordedLocalTotalsText()]
          Text {
            required property string modelData
            width: recordedTokenHeader.width
            textFormat: Text.PlainText
            text: modelData
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }
        }
      }

'''

TOKEN_PROVIDER_TEXT = '''    Text {
      visible: text !== "" && !!section.provider && section.provider.providerId !== "local"
      width: parent.width
      textFormat: Text.PlainText
      text: section.provider ? String(root.recordedTotals.byProvider[section.provider.providerId] || "") : ""
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WrapAnywhere
    }

'''

def transform_token_visibility(text, *, current_native=False):
    """Supported limits-first layout only; every anchor is unique and required."""
    text = replace_once(text, 'import qs.Ui\n', 'import qs.Ui\nimport "History.js" as History\nimport qs.Ui as Ui\n')
    text = replace_once(text, '  function planLabel(p) {', HISTORY_PANEL_FUNCTIONS + TOKEN_PANEL_FUNCTIONS + CALIBRATION_PANEL_FUNCTIONS + '  function planLabel(p) {')
    text = replace_once(text, '    id: button\n    anchors.fill: parent\n    bar: root.bar\n    text: "󱚣"',
                        '    id: button\n    objectName: "recordedTokenBar"\n    anchors.fill: parent\n    bar: root.bar\n    text: vertical ? root.recordedTokenCompactText : "󱚣"\n'
                        '    labelText: vertical ? "" : root.recordedTokenBarText\n'
                        '    tooltipText: root.recordedTokenHelpText()\n'
                        '    fixedWidth: vertical ? -1 : canvasSize + tokenLabelMetrics.width + Style.space(4) + scaledHorizontalMargin * 2\n'
                        '    TextMetrics {\n      id: tokenLabelMetrics\n      text: button.labelText\n'
                        '      font.family: button.fontFamily\n      font.pixelSize: button.fontSize\n    }')
    eager_height = '    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(860))'
    lazy_height = '    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(860))'
    shapes = [anchor for anchor in (eager_height, lazy_height) if anchor in text]
    if len(shapes) != 1 or text.count(shapes[0]) != 1:
        raise ValueError(UNSUPPORTED)
    lazy_content = '    content: PanelKeyCatcher {'
    if shapes[0] == lazy_height:
        if text.count(lazy_content) != 1:
            raise ValueError(UNSUPPORTED)
        text = replace_once(text, lazy_height,
                            '    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)')
        adjacent = '      property alias providerSections: providerSections\n      implicitHeight: column.implicitHeight'
        navigation = navigation_module()
        supported_headers = [adjacent, navigation.LAZY_CONTENT_HEADER]
        if current_native:
            supported_headers.append(navigation.CURRENT_LAZY_CONTENT_HEADER)
        headers = [anchor for anchor in supported_headers if anchor in text]
        if len(headers) != 1 or text.count(headers[0]) != 1:
            raise ValueError(UNSUPPORTED)
        owner = limits_component_body(text, lazy_content)
        fragment = headers[0].split('{', 1)[1] if headers[0].startswith(lazy_content) else headers[0]
        if fragment not in owner:
            raise ValueError(UNSUPPORTED)
        text = replace_once(text, headers[0], headers[0].replace(
            'implicitHeight: column.implicitHeight',
            'implicitHeight: recordedTokenHeader.implicitHeight + Style.space(16) + column.implicitHeight'))
    else:
        if lazy_content in text:
            raise ValueError(UNSUPPORTED)
        text = replace_once(text, eager_height,
                            '    contentHeight: panel.fittedContentHeight(recordedTokenHeader.implicitHeight + Style.space(16) + column.implicitHeight)')
    text = replace_once(text, '      Flickable {\n        id: panelFlick',
                        TOKEN_HEADER + '      Flickable {\n        id: panelFlick\n        objectName: "recordedTokenBody"')
    text = replace_once(text, '        anchors.fill: parent\n        anchors.leftMargin: -overhang',
                        '        anchors.top: recordedTokenHeader.bottom\n        anchors.topMargin: Style.space(16)\n'
                        '        anchors.bottom: parent.bottom\n        anchors.left: parent.left\n        anchors.right: parent.right\n'
                        '        anchors.leftMargin: -overhang')
    text = replace_once(text, '    var n = summaryPhrases.length\n    shownPhrase = n > 0 ? summaryPhrases[phraseIndex % n] : ""',
                        '    var phrases = summaryPhrases.filter(function(text) { return text.indexOf(" tokens ") < 0 })\n'
                        '    var n = phrases.length\n    shownPhrase = n > 0 ? phrases[phraseIndex % n] : ""')
    text = replace_once(text, '          Repeater {\n            id: providerSections', HISTORY_PANEL + '          Repeater {\n            id: providerSections')
    anchor = "  }\n\n  // A provider's mark, falling back to the bar glyph when it ships none."
    return replace_once(text, anchor, TOKEN_PROVIDER_TEXT + anchor)

# Current native limits panels use an informational mark/tooltip rather than
# the older separate trouble Text. Admit the complete function, component and
# both exact invocation forms together; a partial witness is not this API.
LIMITS_USAGE_NOTE_FUNCTION = '''  function usageNote(item) {
    if (!item || needsSignIn(item)) return ""
    var status = String(item.usageStatusText || "")
    var stale = item.limitsStale === true || item.stale === true
    if (status === "" && !(stale && String(item.authHelpText || "") !== "")) return ""
    return helpDisplayText(item.authHelpText) || statusDisplayText(status)
  }
'''
LIMITS_USAGE_NOTE_COMPONENT = '''  component UsageNote: Text {
    id: noteMark
    property var record: null
    readonly property string message: root.usageNote(record)
    visible: message !== ""
    text: "󰋽"
    textFormat: Text.PlainText
    color: root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption

    HoverHandler { id: noteHover }

    PanelToolTip {
      visible: noteHover.hovered && noteMark.visible
      // Capped through implicitWidth, which the tooltip centers itself by.
      implicitWidth: Math.min(implicitContentWidth + leftPadding + rightPadding, Style.space(320))
      text: noteMark.message
    }
  }
'''
LIMITS_USAGE_NOTE_PROVIDER = '''      UsageNote {
        anchors.left: sectionPlan.visible ? sectionPlan.right : sectionName.right
        anchors.leftMargin: Style.space(8)
        anchors.verticalCenter: sectionName.verticalCenter
        visible: !section.multi && message !== ""
        record: section.provider
      }
'''
LIMITS_USAGE_NOTE_ACCOUNT = '''        UsageNote {
          id: accountNote
          record: head.account
        }
'''


def limits_component_body(text, declaration):
    """Locate a complete known-format component, not arbitrary QML syntax.

    Ignore braces in ordinary quoted strings and comments. Arithmetic division
    after an operand is supported; regex and template literals fail closed.
    Control-condition parentheses do not make a following slash a division.
    """
    if text.count(declaration) != 1 or not declaration.endswith('{'):
        raise ValueError(UNSUPPORTED)
    start = text.index(declaration) + len(declaration)
    index, depth, quote, previous = start, 1, None, '{'
    controls = {'if', 'while', 'for', 'switch', 'catch', 'with'}
    operators = controls | {'return', 'throw', 'case', 'else', 'do', 'delete',
                            'void', 'typeof', 'new', 'in', 'instanceof', 'yield', 'await', 'function'}
    parentheses = []
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == '\\':
                index += 2
                continue
            if char == quote:
                quote, previous = None, 'operand'
            index += 1
            continue
        if char.isalnum() or char in '_$':
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in '_$'):
                end += 1
            word = text[index:end]
            previous = word if word in operators else 'operand'
            index = end
            continue
        if char in ('"', "'"):
            quote = char
        elif text.startswith('//', index):
            end = text.find('\n', index + 2)
            if end < 0:
                raise ValueError(UNSUPPORTED)
            index = end + 1
            continue
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            if end < 0:
                raise ValueError(UNSUPPORTED)
            index = end + 2
            continue
        elif char == '/':
            if previous not in ('operand', ')', ']'):
                raise ValueError(UNSUPPORTED)
            previous = '/'
        elif char == '`':
            raise ValueError(UNSUPPORTED)
        elif char == '(':
            parentheses.append(previous in controls)
            previous = '('
        elif char == ')':
            if not parentheses:
                raise ValueError(UNSUPPORTED)
            previous = 'control-close' if parentheses.pop() else ')'
        elif char == '{':
            depth += 1
            previous = '{'
        elif char == '}':
            depth -= 1
            if depth == 0:
                if parentheses:
                    raise ValueError(UNSUPPORTED)
                return text[start:index]
            previous = '}'
        elif not char.isspace():
            previous = char
        index += 1
    raise ValueError(UNSUPPORTED)


def limits_note_interface(text):
    legacy = '  function otherTrouble(item) {'
    current = '  function usageNote(item) {'
    if text.count(legacy) == 1 and current not in text and 'component UsageNote:' not in text:
        return 'otherTrouble'
    if (legacy not in text and 'otherTrouble' not in text
            and text.count(current) == 1
            and text.count('component UsageNote:') == 1
            and text.count('UsageNote {') == 2
            and all(text.count(block) == 1 for block in (
                LIMITS_USAGE_NOTE_FUNCTION, LIMITS_USAGE_NOTE_COMPONENT,
                LIMITS_USAGE_NOTE_PROVIDER, LIMITS_USAGE_NOTE_ACCOUNT))
            and LIMITS_USAGE_NOTE_PROVIDER in limits_component_body(text, '  component ProviderSection: Column {')
            and LIMITS_USAGE_NOTE_ACCOUNT in limits_component_body(text, '  component AccountHeader: Item {')):
        return 'usageNote'
    raise ValueError(UNSUPPORTED)


def validate_legacy_provider_rows(text):
    # The tiny historical fixture has no delegates. Do not let that exemption
    # silently admit arbitrary cards; real legacy rows need the complete owner.
    empty = '''          Repeater {
            id: providerSections
            model: root.providers
          }'''
    card = '''          Repeater {
            id: providerSections
            model: MODEL

            ProviderSection {
              required property var modelData
              required property int index
              width: column.width
              provider: modelData
              providerIndex: index
            }
          }'''
    instances = len(re.findall(r'(?m)^\s*(?:delegate:\s*)?ProviderSection\s*\{', text))
    if instances == 0:
        if text.count(empty) != 1:
            raise ValueError(UNSUPPORTED)
    else:
        supported = [card.replace('MODEL', expression) for expression in
                     ('root.providers', 'root.addStage === "" ? root.providers : []')]
        if instances != 1 or sum(text.count(block) for block in supported) != 1:
            raise ValueError(UNSUPPORTED)


def transform_panel_limits(text):
    """Limits-first panels with legacy trouble Text or a complete UsageNote API.

    Local information and current rates use separate lines rather than stock
    subscription notices. All remaining layout/interaction anchors must also
    match; recognizing the note interface alone never admits a partial port.
    """
    # Preserve stock subscription trouble semantics; Local is information. A
    # Hermes credential type with no quota source is not trouble either; its
    # record keeps the raw status, only the card omits the placeholder line.
    navigation = navigation_module()
    modern_navigation = navigation.is_modern(text, UNSUPPORTED)
    current_navigation = hashlib.sha256(text.encode()).hexdigest() == navigation.CURRENT_PANEL_SHA256
    if modern_navigation:
        text = navigation.transform(text, replace_once)
        text = transform_provider_row_continuity(text)
    else:
        validate_legacy_provider_rows(text)
    note_interface = limits_note_interface(text)
    note_anchor = '  function ' + note_interface + '(item) {'
    text = replace_once(text, note_anchor,
                        note_anchor + '\n'
                        '    if (item && item.providerId === "local") return "" // a-la-carchy: Local status is information, not trouble.\n'
                        '    if (item && item.readOnly === true && String(item.credentialProvider || "") !== ""\n'
                        '        && item.usageStatusText === ' + json.dumps(credential_contract()['REASONS']['unsupported']) + ') return ""')
    text = replace_once(text, '      var p = providers[i]\n      var days = p.recentDays || []',
                        '      var p = providers[i]\n'
                        '      if (p.providerId === "local") continue // a-la-carchy: sampled local tokens are not subscription usage.\n'
                        '      var days = p.recentDays || []')
    blocks = [block for block in (LIMITS_TROUBLE_TEXT, LIMITS_TROUBLE_TEXT_CURRENT,
                                  LIMITS_TROUBLE_TEXT_LOCALIZED) if block in text]
    if note_interface == 'otherTrouble':
        if len(blocks) != 1 or text.count('visible: !section.multi && root.otherTrouble(section.provider) !== ""') != 1:
            raise ValueError(UNSUPPORTED)
        text = replace_once(text, blocks[0], blocks[0] + LIMITS_LOCAL_TEXT + HERMES_QUOTA_TEXT)
    else:
        if blocks:
            raise ValueError(UNSUPPORTED)
        anchor = "  }\n\n  // A provider's mark, falling back to the bar glyph when it ships none."
        text = replace_once(text, anchor, LIMITS_LOCAL_TEXT + HERMES_QUOTA_TEXT + anchor)
    text = replace_once(text, '  function limitWindows(p) {\n    if (!p) return []',
                        '  function limitWindows(p) {\n    if (!p) return []\n'
                        '    if (String(p.providerId || "").indexOf("codex-hermes-") === 0 && !root.hermesQuotaFresh(p)) return []')
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
                        '    if (p && p.credentialProvider && p.credentialProvider !== "openai-codex") return [] // Native glyph fallback, no credential-named paths.\n'
                        '    var iconId = p && p.credentialProvider === "openai-codex" ? "codex" : (p ? p.providerId : "")')
    text = replace_once(text, 'Qt.resolvedUrl("assets/" + p.providerId + "-light.svg")',
                        'Qt.resolvedUrl("assets/" + iconId + "-light.svg")')
    text = replace_once(text, 'Qt.resolvedUrl("assets/" + p.providerId + ".svg")',
                        'Qt.resolvedUrl("assets/" + iconId + ".svg")')
    text = replace_once(text, '  function planLabel(p) {', LIMITS_LOCAL_FUNCTION + HERMES_QUOTA_FUNCTIONS + HERMES_CARD_FUNCTIONS + '  function planLabel(p) {')
    text = replace_once(text, '    id: section\n    property var provider: null',
                        '    id: section\n    visible: root.providerCardVisible(section.provider)\n    property var provider: null')
    heading = '        text: section.provider ? section.provider.providerName : ""'
    model_heading = ('        text: section.model ? (section.model.name || section.model.recipe) : '
                     'section.provider ? section.provider.providerName : ""')
    current_model_heading = ('        text: section.model ? (section.model.name || section.model.recipe)\n'
                             '          : section.offer ? (section.offer.link ? root.linkTitle(section.offer.link) : (section.offer.name || section.offer.id))\n'
                             '          : section.provider ? section.provider.providerName : ""')
    supported_headings = [heading, model_heading]
    if current_navigation:
        supported_headings.append(current_model_heading)
    headings = [anchor for anchor in supported_headings if anchor in text]
    if len(headings) != 1 or text.count(headings[0]) != 1:
        raise ValueError(UNSUPPORTED)
    if headings[0] in (model_heading, current_model_heading) and note_interface != 'usageNote':
        raise ValueError(UNSUPPORTED)
    replacement = 'root.providerHeading(section.provider)'
    if headings[0] == model_heading:
        replacement = 'section.model ? (section.model.name || section.model.recipe) : ' + replacement
    elif headings[0] == current_model_heading:
        replacement = current_model_heading[len('        text: '):].replace(
            'section.provider ? section.provider.providerName : ""', replacement)
    text = replace_once(text, headings[0], '        text: ' + replacement)
    text = replace_once(text, '        id: sectionName\n',
                        '        id: sectionName\n'
                        '        HoverHandler { id: hermesCaptionHover }\n'
                        '        PanelToolTip {\n'
                        '          visible: hermesCaptionHover.hovered && text !== ""\n'
                        '          text: root.quotaCalibrationText(section.provider)\n'
                        '        }\n')
    # Current keyboard/drag API uses raw provider indices. Skip invisible cards,
    # without filtering that shared list or changing native ordering semantics.
    if not modern_navigation and '  function reorderProvider(dy) {' in text:
        text = replace_once(text, '  function reorderProvider(dy) {',
                            '  function reorderProvider(dy) {\n    if (dy !== -1 && dy !== 1) return')
        text = replace_once(text, '        rows.push([{ kind: "provider", index: p }])',
                            '        if (!root.providerCardVisible(providers[p])) continue\n'
                            '        rows.push([{ kind: "provider", index: p }])')
        text = replace_once(text, '    var to = clamp(from + dy, 0, providers.length - 1)\n    if (to === from) return',
                            '    var to = from + dy\n'
                            '    while (to >= 0 && to < providers.length && !root.providerCardVisible(providers[to])) to += dy\n'
                            '    if (to < 0 || to >= providers.length || to === from) return')
        text = replace_once(text, '      if (item && y >= item.y && y < item.y + item.height) {',
                            '      if (item && item.visible && y >= item.y && y < item.y + item.height) {')
    return transform_token_visibility(text, current_native=current_navigation)

GROK_QUOTA_FUNCTIONS = '''  // A reset forecast is not an authenticated quota measurement.
  function grokQuotaFresh(p) {
    if (!p || p.providerId !== "grok") return true
    if (p.limitsStale === true) return false
    var fetched = Number(p.limitsFetchedAt)
    var interval = Number(usage.setting("refreshIntervalSec", 900))
    if (!isFinite(interval) || interval < 30 || interval > 3600) interval = 900
    if (!isFinite(fetched) || fetched <= 0 || fetched > root.nowMs + 60000 || root.nowMs - fetched > Math.max(60, interval) * 2000)
      return false
    var list = p.limits || []
    // QML modelData can expose a Qt sequence rather than a JS Array.
    if (typeof list.length !== "number" || list.length <= 0 || list.length > 128) return false
    for (var i = 0; i < list.length; i++) {
      var entry = list[i] || {}
      var reset = new Date(String(entry.resetsAt || "")).getTime()
      if (typeof entry.percent !== "number" || !isFinite(entry.percent) || entry.percent < 0 || entry.percent > 1 || !isFinite(reset) || reset <= root.nowMs)
        return false
    }
    return true
  }

  function grokQuotaText(p) {
    if (!p || p.providerId !== "grok" || root.grokQuotaFresh(p)) return ""
    var fetched = Number(p.limitsFetchedAt)
    if (isFinite(fetched) && fetched > 0 && fetched <= root.nowMs + 60000)
      return "Quota stale"
    return ""
  }

  function grokAccountWindows(provider, account) {
    var p = { limits: account ? account.limits || [] : [] }
    if (provider && provider.providerId === "grok") {
      p.providerId = "grok"
      p.limitsStale = account ? account.stale === true : true
      p.limitsFetchedAt = account ? account.fetchedAt : 0
    }
    return root.displayWindows(p)
  }

  function grokUsageText(p) {
    if (!p || p.providerId !== "grok") return ""
    var lines = []
    var quota = root.grokQuotaText(p)
    if (quota !== "") lines.push(quota)
    var models = p.modelUsage
    var ids = models && typeof models === "object" && !Array.isArray(models) ? Object.keys(models).sort() : []
    if (ids.length === 0) {
      return lines.join("\\n")
    }
    lines.push("Recorded tokens")
    var fields = ["inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"]
    var labels = ["In", "Out", "Cache read", "Cache creation"]
    // Bound rendering only; the complete producer ledger stays unchanged.
    for (var i = 0; i < Math.min(ids.length, 128); i++) {
      var entry = models[ids[i]]
      var parts = []
      var valid = !!entry && typeof entry === "object" && !Array.isArray(entry)
      for (var j = 0; valid && j < fields.length; j++) {
        var n = entry[fields[j]] === undefined ? 0 : entry[fields[j]]
        valid = typeof n === "number" && isFinite(n) && n >= 0 && Math.floor(n) === n && n <= 9007199254740991
        if (valid) parts.push(labels[j] + " " + usage.formatTokenCount(n))
      }
      if (ids[i].length > 512) lines.push("Model label exceeds display bound · entry retained")
      else lines.push(ids[i] + " · " + (valid ? parts.join(" · ") : "Counts unavailable"))
    }
    if (ids.length > 128) lines.push("Additional model entries retained: " + (ids.length - 128))
    return lines.join("\\n")
  }

'''

GROK_QUOTA_TEXT = '''    Text {
      textFormat: Text.PlainText
      visible: text !== ""
      width: parent.width
      text: root.grokUsageText(section.provider)
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WrapAnywhere
    }

'''

def transform_grok_panel(text):
    """Native Grok only: retain real quotas, never roll expired data to zero."""
    text = replace_once(text, '  function planLabel(p) {',
                        GROK_QUOTA_FUNCTIONS + '  function planLabel(p) {')
    text = replace_once(text, '  function limitWindows(p) {\n    if (!p) return []',
                        '  function limitWindows(p) {\n    if (!p) return []\n'
                        '    if (p.providerId === "grok" && !root.grokQuotaFresh(p)) return []')
    text = replace_once(text, 'model: root.displayWindows({ limits: accountBlock.modelData.limits || [] })',
                        'model: root.grokAccountWindows(section.provider, accountBlock.modelData)')
    anchor = "  }\n\n  // A provider's mark, falling back to the bar glyph when it ships none."
    return replace_once(text, anchor, GROK_QUOTA_TEXT + anchor)

PANEL_LAYOUTS = (('limits', transform_panel_limits), ('tabs', transform_panel_tabs))

def patch_panel(text):
    """Return one complete supported transform; never claim a partial patch."""
    if '  function modelRows(p) {' in text and '  function providerAccounts(p) {' in text:
        raise ValueError(UNSUPPORTED)
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

def source_files(source, require_nonempty_dirs=False, owned=False, reserved_entries=0, reserved_bytes=0):
    """Validate the whole bounded tree before reading any plugin contents."""
    source = Path(source).absolute()
    if any(p.is_symlink() for p in (source, *source.parents)) or not source.is_dir():
        raise ValueError('native source must be a no-follow directory')
    uid = os.getuid() if owned else None
    root_info = source.lstat()
    if not stat.S_ISDIR(root_info.st_mode) or (owned and root_info.st_uid != uid):
        raise ValueError('foreign-owned or non-directory plugin root is refused')
    if (type(reserved_entries) is not int or not 0 <= reserved_entries <= MAX_SOURCE_ENTRIES or
            type(reserved_bytes) is not int or not 0 <= reserved_bytes <= MAX_SOURCE_BYTES):
        raise ValueError('native source tree is too large')
    pending, files, count, size = [source], [], reserved_entries, reserved_bytes
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
    navigation = navigation_module()
    panel = source_bytes(source/'Panel.qml').decode()
    if navigation.SOURCE_IMPORT in panel:
        dependency = source_bytes(source.parent/'panels/elsewhen/Model.js')
        if hashlib.sha256(dependency).hexdigest() != navigation.DEPENDENCY_SHA256:
            raise ValueError(UNSUPPORTED)
        source_files(source, reserved_entries=1, reserved_bytes=len(dependency))
        digest.update(b'dependency:panels/elsewhen/Model.js\0' + hashlib.sha256(dependency).digest())
        if hashlib.sha256(panel.encode()).hexdigest() == navigation.CURRENT_PANEL_SHA256:
            icon = source_bytes(source.parent/'panels/tailscale/TailscaleIcon.qml')
            if hashlib.sha256(icon).hexdigest() != navigation.TAILSCALE_ICON_SHA256:
                raise ValueError(UNSUPPORTED)
            source_files(source, reserved_entries=2, reserved_bytes=len(dependency) + len(icon))
            digest.update(b'dependency:panels/tailscale/TailscaleIcon.qml\0' + hashlib.sha256(icon).digest())
    return digest.hexdigest()

# Display annotations only: credential discovery, quotas and history are unchanged.
_PLAN_LABEL_FORMS = (
    '''  function planLabel(p) {
    var tier = String(p && p.tierLabel || "")
    if (tier === "Prepaid") return Commons.I18n.t("agents.tierPrepaid")
    return tier === "" ? "" : tier.charAt(0).toUpperCase() + tier.slice(1)
  }''',
    '''  function planLabel(p) {
    return String(p && p.tierLabel || "")
  }''',
)
_HERMES_PLAN_LABEL_PREFIX = '''    if (p && p.readOnly === true && p.credentialProvider === "openai-codex") {
      var labels = usage.setting("hermesPlanLabels", ({}))
      var label = labels && typeof labels === "object" && !Array.isArray(labels)
        && Object.prototype.hasOwnProperty.call(labels, String(p.providerId || ""))
        ? labels[String(p.providerId)] : ""
      if (label === "Business Standard" || label === "Business Premium") return label
      if (p.tierLabel === "team" || p.tierLabel === "business") return "Business Standard"
    }
'''


def transform_hermes_plan_labels(text):
    """Recognize a complete formatter; bind optional labels to stable provider IDs."""
    header = '  function planLabel(p) {\n'
    revised = tuple(old.replace(header, header + _HERMES_PLAN_LABEL_PREFIX, 1)
                    for old in _PLAN_LABEL_FORMS)
    if sum(text.count(form) for form in (*_PLAN_LABEL_FORMS, *revised)) != 1:
        raise ValueError('unsupported or ambiguous Agents plan-label formatter; preserved')
    for old, new in zip(_PLAN_LABEL_FORMS, revised):
        if old in text:
            return text.replace(old, new, 1)
    return text


def render(source, plugin_id):
    """Validate the source and return what a clone replaces. Writes nothing."""
    source = Path(source)
    source_files(source)
    if not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+', plugin_id) or plugin_id.startswith('omarchy.'):
        raise ValueError('invalid plugin ID')
    manifest = json.loads(source_bytes(source/'manifest.json'))
    if not isinstance(manifest, dict) or manifest.get('id') != SOURCE_ID:
        raise ValueError('only the packaged omarchy.agents implementation is supported')
    for reserved in ('History.js', 'CalendarClock.py', 'AgentsReorder.js', 'AgentsTailscaleIcon.qml'):
        if (source/reserved).exists() or (source/reserved).is_symlink():
            raise ValueError(UNSUPPORTED)
    history_js = source_bytes(Path(__file__).with_name('history_consumer.js')).decode()
    calendar_clock = source_bytes(Path(__file__).with_name('calendar_clock.py')).decode('utf-8')
    main = transform_main(source_bytes(source/'Main.qml').decode())
    layout, panel = patch_panel(source_bytes(source/'Panel.qml').decode())
    navigation = navigation_module()
    dependencies = {}
    dependency_input_bytes = 0
    if navigation.LOCAL_IMPORT in panel:
        dependency = source_bytes(source.parent/'panels/elsewhen/Model.js')
        if hashlib.sha256(dependency).hexdigest() != navigation.DEPENDENCY_SHA256:
            raise ValueError(UNSUPPORTED)
        dependency_input_bytes += len(dependency)
        source_files(source, reserved_entries=1, reserved_bytes=dependency_input_bytes)
        dependencies['AgentsReorder.js'] = navigation.REORDER_JS
    if 'AgentsTailscaleIcon {' in panel:
        if panel.count('AgentsTailscaleIcon {') != 1:
            raise ValueError(UNSUPPORTED)
        icon = source_bytes(source.parent/'panels/tailscale/TailscaleIcon.qml')
        if hashlib.sha256(icon).hexdigest() != navigation.TAILSCALE_ICON_SHA256:
            raise ValueError(UNSUPPORTED)
        # Charge complete input files, not the smaller generated JS subset.
        dependency_input_bytes += len(icon)
        source_files(source, reserved_entries=len(dependencies) + 1,
                     reserved_bytes=dependency_input_bytes)
        dependencies['AgentsTailscaleIcon.qml'] = icon.decode('utf-8')
    if layout == 'limits' and 'grok' in native_providers(manifest):
        panel = transform_grok_panel(panel)
    if navigation.LOCAL_IMPORT in panel:
        panel = transform_nested_row_continuity(panel)
    # As `omarchy plugin clone` does: built-in ids inside the QML stay, so the
    # shell keeps routing omarchy.agents IPC and settings through clonedFrom.
    manifest['id'] = plugin_id
    manifest['name'] = NAME
    if isinstance(manifest.get('barWidget'), dict):
        manifest['barWidget']['displayName'] = NAME
    meta = manifest.get('omarchy') if isinstance(manifest.get('omarchy'), dict) else {}
    manifest['omarchy'] = {k: v for k, v in meta.items() if k != 'clonePaths'} | {
        'clonedFrom': SOURCE_ID, 'alaCarchy': MARK, 'alaCarchyLayout': layout, 'alaCarchySource': fingerprint(source)}
    return {'layout': layout, 'manifest': manifest, 'Main.qml': main,
            'Panel.qml': transform_hermes_plan_labels(panel) if layout == 'limits' else panel,
            'History.js': history_js, 'CalendarClock.py': calendar_clock, 'assets': local_assets(source),
            **dependencies}

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
        (staged/'History.js').write_text(rendered['History.js'])
        (staged/'CalendarClock.py').write_text(rendered['CalendarClock.py'], encoding='utf-8')
        for dependency in ('AgentsReorder.js', 'AgentsTailscaleIcon.qml'):
            if dependency in rendered:
                (staged/dependency).write_text(rendered[dependency], encoding='utf-8')
        (staged/'Panel.qml').write_text(rendered['Panel.qml'])
        (staged/'manifest.json').write_text(json.dumps(rendered['manifest'], indent=2) + '\n')
        (staged/'assets').mkdir(exist_ok=True)
        for name, data in rendered['assets'].items():
            # Exclusive: never replace a native file, even one that appeared after render().
            with (staged/'assets'/name).open('x', encoding='utf-8') as stream:
                stream.write(data)
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
