"""Pinned modern native navigation adapter. No runtime or desktop access."""
import hashlib
SOURCE_IMPORT = 'import "../panels/elsewhen/Model.js" as Reorder\n'
LOCAL_IMPORT = 'import "AgentsReorder.js" as Reorder\n'
PREFIX_END = "  // Keeps whatever the cursor lands on inside the scrolled view."
PREFIX_SHA256 = '17749c4bf8fcbdb474d989cad7d6719b16d83f2d14c97c05373e9a24795cf7b9'
DEPENDENCY_SHA256 = '669cbfe9b28e65bea3673a003f771c1130a9ff0a8df1767dd385cf381f864c84'
REORDER_JS = 'var DRAG_HYSTERESIS = 0.6\n\n// How many rows a drag of `offset` has passed, fractionally. Rows are not all\n// one height - a row with people is a line taller - so each is measured by its\n// own pitch in `pitches`; without them every row is `rowPitch`.\nfunction rowsPassed(dragIndex, offset, rowPitch, pitches) {\n  if (!pitches) return offset / rowPitch\n  var step = offset < 0 ? -1 : 1\n  var left = Math.abs(offset)\n  var passed = 0\n  for (var i = dragIndex + step; i >= 0 && i < pitches.length; i += step) {\n    var pitch = pitches[i] > 0 ? pitches[i] : rowPitch\n    if (left < pitch) return step * (passed + left / pitch)\n    left -= pitch\n    passed++\n  }\n  return step * (passed + left / rowPitch)\n}\n\n// Where the dragged row lands when it drops into `target`: the pitches of the rows it passed.\nfunction dropOffset(dragIndex, target, rowPitch, pitches) {\n  var step = target < dragIndex ? -1 : 1\n  var out = 0\n  for (var i = dragIndex + step; step > 0 ? i <= target : i >= target; i += step)\n    out += step * (pitches && pitches[i] > 0 ? pitches[i] : rowPitch)\n  return out\n}\n\nfunction dragTargetFor(dragIndex, dragOffset, rowPitch, heldTarget, count, pitches) {\n  if (dragIndex < 0 || rowPitch <= 0) return -1\n  var raw = rowsPassed(dragIndex, dragOffset, rowPitch, pitches)\n  var held = heldTarget < 0 ? 0 : heldTarget - dragIndex\n  var next = Math.abs(raw - held) >= DRAG_HYSTERESIS ? Math.round(raw) : held\n  return Math.max(0, Math.min(count - 1, dragIndex + next))\n}\n\n// The dragged row follows the pointer; rows it has passed step aside by one pitch.\nfunction rowShift(index, dragIndex, dragTarget, dragOffset, rowPitch) {\n  if (dragIndex < 0) return 0\n  if (index === dragIndex) return dragOffset\n  if (dragIndex < dragTarget && index > dragIndex && index <= dragTarget) return -rowPitch\n  if (dragIndex > dragTarget && index >= dragTarget && index < dragIndex) return rowPitch\n  return 0\n}\n\n'


LAZY_CONTENT_HEADER = '    content: PanelKeyCatcher {\n      id: keyCatcher\n      property alias panelFlick: panelFlick\n      property alias hero: hero\n      property alias column: column\n      property alias providerSections: providerSections\n      ConfirmDialog {\n        id: deleteConfirm\n        anchors.fill: parent\n        z: 20\n        opened: root.deleteTarget !== null\n        onOpenedChanged: {\n          // Delete then Return removes, as in every other confirmation;\n          // starting on Cancel made that Return quietly do nothing.\n          selectedIndex = 1\n          if (opened) forceActiveFocus()\n          else root.focusKeys()\n        }\n        Keys.onPressed: function(event) {\n          handleKey(event)\n          event.accepted = true\n        }\n        message: !root.deleteTarget ? "" : root.deleteTarget.localModel\n          ? Commons.I18n.t("agents.localRemoveConfirm").arg(root.deleteTarget.localModel.name || root.deleteTarget.localModel.recipe)\n          : Commons.I18n.t("agents.hideProviderConfirm").arg(root.deleteTarget.providerName)\n        foreground: root.foreground\n        background: root.surface\n        fontFamily: root.fontFamily\n        onCanceled: root.deleteTarget = null\n        onConfirmed: root.confirmDelete()\n      }\n      implicitHeight: column.implicitHeight'

CURRENT_PREFIX_SHA256 = '94c0ce7060681785ec92cc318bbd7d54764e0e2a0f42955f89b3e5711030605a'
CURRENT_PANEL_SHA256 = '4fe2114698f1fe5b960c9bdbe72c2be3e8f9e78fc88996c86d7cd095d6684c89'
TAILSCALE_IMPORT = 'import "../panels/tailscale" as Tailscale\n'
TAILSCALE_ICON_SHA256 = '05142bf1863a671c3b6b39f4a2455d556e2a75b4a373aa1a559c36c918aa8329'
CURRENT_LAZY_CONTENT_HEADER = '    content: PanelKeyCatcher {\n      id: keyCatcher\n      property alias panelFlick: panelFlick\n      property alias hero: hero\n      property alias column: column\n      property alias providerSections: providerSections\n      ConfirmDialog {\n        id: deleteConfirm\n        anchors.fill: parent\n        z: 20\n        opened: root.deleteTarget !== null\n        onOpenedChanged: {\n          // Delete then Return removes, as in every other confirmation;\n          // starting on Cancel made that Return quietly do nothing.\n          selectedIndex = 1\n          if (opened) forceActiveFocus()\n          else root.focusKeys()\n        }\n        Keys.onPressed: function(event) {\n          handleKey(event)\n          event.accepted = true\n        }\n        message: !root.deleteTarget ? ""\n          : root.deleteTarget.localModel && root.deleteTarget.localModel.host ? Commons.I18n.t("agents.networkHideConfirm").arg(root.deleteTarget.localModel.name || root.deleteTarget.localModel.recipe).arg(root.deleteTarget.localModel.host)\n          : root.deleteTarget.localModel ? Commons.I18n.t("agents.localRemoveConfirm").arg(root.deleteTarget.localModel.name || root.deleteTarget.localModel.recipe)\n          : root.deleteTarget.localOffer && root.deleteTarget.localOffer.link ? Commons.I18n.t("agents.linkHideConfirm").arg(root.deleteTarget.providerName)\n          : root.deleteTarget.localOffer ? Commons.I18n.t("agents.localHideOfferConfirm").arg(root.deleteTarget.providerName)\n          : Commons.I18n.t("agents.hideProviderConfirm").arg(root.deleteTarget.providerName)\n        foreground: root.foreground\n        background: root.surface\n        fontFamily: root.fontFamily\n        onCanceled: root.deleteTarget = null\n        onConfirmed: root.confirmDelete()\n      }\n      implicitHeight: column.implicitHeight'

def is_modern(text, unsupported):
    markers = SOURCE_IMPORT in text or 'Reorder.' in text or '  function localActions(model) {' in text
    if not markers:
        return False
    if text.count(PREFIX_END) != 1 or text.count(SOURCE_IMPORT) != 1:
        raise ValueError(unsupported)
    prefix = text[:text.index(PREFIX_END)]
    digest = hashlib.sha256(prefix.encode()).hexdigest()
    if digest == CURRENT_PREFIX_SHA256:
        # This larger native UI is a single explicitly reviewed source family.
        if hashlib.sha256(text.encode()).hexdigest() != CURRENT_PANEL_SHA256:
            raise ValueError(unsupported)
    elif digest != PREFIX_SHA256:
        raise ValueError(unsupported)
    return True


def transform(text, replace_once):
    if hashlib.sha256(text.encode()).hexdigest() == CURRENT_PANEL_SHA256:
        # Only the native drawing primitive is copied, never its service/panel.
        text = replace_once(text, TAILSCALE_IMPORT, '')
        text = replace_once(text, 'Tailscale.TailscaleIcon {', 'AgentsTailscaleIcon {')
    text = replace_once(text, SOURCE_IMPORT, LOCAL_IMPORT)
    text = replace_once(text, '      for (var p = 0; p < providers.length; p++) {',
                        '      for (var p = 0; p < providers.length; p++) {\n'
                        '        if (!root.providerCardVisible(providers[p])) continue')
    text = replace_once(text, '  function reorderProvider(dy) {',
                        '  function reorderProvider(dy) {\n    if (dy !== -1 && dy !== 1) return')
    text = replace_once(text, '    if (from < 0) return\n    var to = clamp(from + dy, 0, providers.length - 1)\n    if (to === from) return',
                        '    if (from < 0 || !root.providerCardVisible(providers[from])) return\n'
                        '    var to = from + dy\n'
                        '    while (to >= 0 && to < providers.length && !root.providerCardVisible(providers[to])) to += dy\n'
                        '    if (to < 0 || to >= providers.length || to === from) return')
    text = replace_once(text, '  property bool renaming: false\n', '  property bool renaming: false\n' + DOMAIN)
    begin = text.index('  function beginProviderDrag(index, y) {')
    end = text.index('  function dragProviderOver(y) {', begin)
    text = text[:begin] + BEGIN + '\n' + text[end:]
    text = replace_once(text, '    if (dragIndex < 0 || settling) return\n    dragOffset = y - dragPressY\n'
                        '    dragTarget = Reorder.dragTargetFor(dragIndex, dragOffset, rowPitches[dragIndex],\n'
                        '                                      dragTarget, providers.length, rowPitches)',
                        '    if (dragIndex < 0 || settling) return\n'
                        '    if (!dragDomainCurrent()) { resetProviderDrag(); return }\n'
                        '    dragOffset = y - dragPressY\n'
                        '    var slot = Reorder.dragTargetFor(dragPositions[dragIndex], dragOffset, rowPitches[dragIndex],\n'
                        '                                     dragPositions[dragTarget], dragVisibleIndices.length, dragVisiblePitches)\n'
                        '    dragTarget = slot >= 0 ? dragVisibleIndices[slot] : dragIndex')
    text = replace_once(text, '    dragProviders = null\n',
                        '    dragProviders = null\n    dragVisibleIndices = []\n    dragVisiblePitches = []\n'
                        '    dragPositions = []\n    dragProviderIds = []\n')
    text = replace_once(text, '  function finishProviderDrag() {',
                        '  function finishProviderDrag() {\n    if (!dragDomainCurrent()) { resetProviderDrag(); return }')
    text = replace_once(text, '  function settleProviderDrag(cancel) {\n    if (dragIndex < 0) return',
                        '  function settleProviderDrag(cancel) {\n    if (dragIndex < 0) return\n'
                        '    if (!dragDomainCurrent()) { resetProviderDrag(); return }')
    text = replace_once(text, '    var offset = Reorder.dropOffset(dragIndex, dragTarget, rowPitches[dragIndex], rowPitches)',
                        '    var offset = Reorder.dropOffset(dragPositions[dragIndex], dragPositions[dragTarget],\n'
                        '                                    rowPitches[dragIndex], dragVisiblePitches)')
    return text


DOMAIN = '''
  // Drag slots are geometry only; provider indices/identities stay native.
  property var dragVisibleIndices: []
  property var dragVisiblePitches: []
  property var dragPositions: []
  property var dragProviderIds: []
  readonly property bool dragDomainValid: dragIndex < 0 || dragDomainCurrent()
  onDragDomainValidChanged: if (!dragDomainValid) resetProviderDrag()
  function dragDomainCurrent() {
    if (dragIndex < 0 || dragProviders !== usage.panelProviders ||
        providers.length !== dragPositions.length || providers.length !== dragProviderIds.length) return false
    for (var i = 0; i < providers.length; i++) {
      if (!providers[i] || providers[i].providerId !== dragProviderIds[i] ||
          root.providerCardVisible(providers[i]) !== (dragPositions[i] >= 0)) return false
    }
    return providers[dragIndex].providerId === dragProviderId
  }
'''

BEGIN = '''  function beginProviderDrag(index, y) {
    if (dragIndex >= 0 || settling || !providerSections || !column ||
        typeof index !== "number" || Math.floor(index) !== index || index < 0 || index >= providers.length ||
        providerSections.count !== providers.length || !root.providerCardVisible(providers[index])) return
    var pitches = [], indices = [], visiblePitches = [], positions = [], ids = [], seen = Object.create(null)
    for (var i = 0; i < providers.length; i++) {
      var provider = providers[i], item = providerSections.itemAt(i)
      if (!provider || typeof provider.providerId !== "string" || !item ||
          Object.prototype.hasOwnProperty.call(seen, provider.providerId)) return
      seen[provider.providerId] = true
      ids.push(provider.providerId)
      var pitch = item.height + column.spacing
      pitches.push(pitch)
      if (root.providerCardVisible(provider)) {
        if (!item.visible || !isFinite(pitch) || pitch <= 0) return
        positions.push(indices.length)
        indices.push(i)
        visiblePitches.push(pitch)
      } else positions.push(-1)
    }
    rowPitches = pitches
    dragVisibleIndices = indices
    dragVisiblePitches = visiblePitches
    dragPositions = positions
    dragProviderIds = ids
    dragProviders = providers
    dragProviderId = providers[index].providerId
    dragPressY = y
    dragOffset = 0
    dragTarget = index
    dragIndex = index
  }
'''
