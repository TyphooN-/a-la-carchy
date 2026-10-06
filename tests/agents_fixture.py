"""Synthetic Agents plugin sources for deterministic stager/manager tests.

These are NOT Omarchy's files: each is a small stand-in holding the literal
snippets the stagers anchor on, written out independently of the stager code so
an accidental edit to an anchor there fails here. `tabs` mirrors the Omarchy
4.0.4 panel (per-agent tabs, three packaged agents); `limits` mirrors the
limits-first panel of omarchy-dev 4.0.0.r6694, which also collects Grok itself.
Compatibility with the really installed plugin is checked separately by the
tests that read /usr/share/omarchy.
"""
import json
from pathlib import Path

MAIN = '''import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root
  property var settings: ({})
  readonly property string home: Quickshell.env("HOME") || ""

  function applyAgentListing(output) {
    var ids = []
    var lines = String(output || "").split("\\n")
    for (var i = 0; i < lines.length; i++) {
      var name = lines[i].trim()
      if (name.slice(-5) === ".json") ids.push(name.slice(0, -5))
    }
  }

  Instantiator {
    id: agentInstantiator
    model: root.agentIds
  }

  property var enabledProviders: {
    var result = []
    var syncedProviders = syncConfigured() && aggregateData && aggregateData.providers ? aggregateData.providers : {}
    for (var syncedId in syncedProviders) {
      if (!providerEnabled(syncedId)) continue
    }
    return result
  }

  function localSnapshot() {
    var providerMap = {}
    for (var i = 0; i < agents.length; i++) {
      var record = agents[i] ? agents[i].record : null
      if (!providerEnabled(String(record.id))) continue
      providerMap[String(record.id)] = providerSnapshot(record)
    }
    return providerMap
  }

  function syncedStatsFor(providerId) {
    return aggregateData.providers[providerId] || null
  }

  function displayProvider(record) {
    var synced = false
    return {
      providerId: String(record.id),
      providerName: String(record.name || record.id),
      ready: record.ready === true || synced,
      accounts: Array.isArray(record.accounts) ? record.accounts : [],
      accountSwitch: record.accountSwitch || ({ mode: "manual", threshold: 95 }),
    }
  }

  function providerEnabled(id) {
    if (!settings || !settings.providers || !settings.providers[id]) return true
    return settings.providers[id].enabled !== false
  }
}
'''

PANEL_TABS = '''import QtQuick
import qs.Ui

Panel {
  id: root

  function modelRows(p) {
    var rows = []
    for (var id in p.modelUsage) {
      rows.push({
        name: usage.friendlyModelName(id),
        input: 0
      })
    }
    return rows.slice(0, 4)
  }

  function modelTooltip(provider, row) {
    if (!row) return ""
    return "In " + usage.formatTokenCount(row.input)
  }

  function dayTooltip(day, today) {
    return String(day.date)
  }

  Text {
    text: usage.formatTokenCount(dayRow.day ? Number(dayRow.day.messageCount || 0) : 0)
  }
}
'''

PANEL_LIMITS = '''import QtQuick
import qs.Ui

Panel {
  id: root

  function providerAccounts(p) {
    return p && p.accounts && p.accounts.length > 0 ? p.accounts : []
  }

  function renameAccount(p, account, label) {
    if (!p || !account) return
  }
  function useAccount(p, account) {
    if (!p || !account || account.active) return
  }
  function setSwitchMode(p, mode) {
    if (!p || providerAccounts(p).length < 2) return
  }
  function signInAgain(p, account) {
    if (!p) return
  }
  function iconCandidatesForProvider(p, surfaceColor) {
    if (!p) return []
    var candidates = []
    candidates.push(Qt.resolvedUrl("assets/" + p.providerId + "-light.svg"))
    candidates.push(Qt.resolvedUrl("assets/" + p.providerId + ".svg"))
    return candidates
  }
  function needsSignIn(item) {
    return String(item && item.usageStatusText || "") === "Waiting for auth"
  }

  function otherTrouble(item) {
    var status = String(item && item.usageStatusText || "")
    return status !== "" && !needsSignIn(item) ? status : ""
  }

  function planLabel(p) {
    return String(p && p.tierLabel || "")
  }

  readonly property var summaryPhrases: {
    var week = 0
    for (var i = 0; i < providers.length; i++) {
      var p = providers[i]
      var days = p.recentDays || []
      for (var d = 0; d < days.length; d++) week += Number(days[d].messageCount || 0)
    }
    return [String(week)]
  }

  component ProviderSection: Column {
    id: section
    property var provider: null

    Text {
      visible: !section.multi && root.otherTrouble(section.provider) !== ""
      width: parent.width
      textFormat: Text.PlainText
      text: section.provider ? String(section.provider.authHelpText || "") : ""
      color: root.urgent
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    Column {
      visible: !section.multi
    }
  }
}
'''

AGENT = 'import QtQuick\n\nItem {\n  property string agentId: ""\n  property string path: ""\n  property var record: null\n}\n'


def make_source(path, layout, native_grok=None):
    """Write a synthetic packaged Agents plugin; returns its directory."""
    path = Path(path)
    (path/'assets').mkdir(parents=True)
    native_grok = layout == 'limits' if native_grok is None else native_grok
    providers = {'claude': {'enabled': True}, 'codex': {'enabled': True}, 'fireworks': {'enabled': True}}
    if native_grok:
        providers['grok'] = {'enabled': True}
    manifest = {'schemaVersion': 1, 'id': 'omarchy.agents', 'name': 'Agents', 'version': '1.0.0', 'kinds': ['bar-widget'],
                'entryPoints': {'barWidget': 'Panel.qml'},
                'barWidget': {'displayName': 'Agents', 'allowMultiple': False,
                              'defaults': {'providers': providers, 'refreshIntervalSec': 900}}}
    (path/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (path/'Main.qml').write_text(MAIN)
    (path/'Panel.qml').write_text({'tabs': PANEL_TABS, 'limits': PANEL_LIMITS}[layout])
    (path/'Agent.qml').write_text(AGENT)
    (path/'README.md').write_text('# Agents (synthetic ' + layout + ' fixture)\n')
    (path/'assets/claude.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>\n')
    return path
