import QtQuick
import Quickshell
import qs.Ui
import qs.Commons

BarWidget {
  id: root
  moduleName: "alacarchy.supersonic"
  property bool popupOpen: false
  readonly property bool opened: popupOpen
  property alias music: service
  property alias librarySearch: search
  readonly property color fg: bar ? bar.barForeground : Color.foreground
  readonly property string family: bar ? bar.fontFamily : Style.font.family
  readonly property real maxLabelWidth: {
    var requested = Number(setting("maxLabelWidth", 1000))
    if (!isFinite(requested)) requested = 1000
    var limit = Style.space(Math.max(120, Math.min(1600, requested)))
    var window = root.QsWindow.window
    return window ? Math.min(limit, window.width * 0.6) : limit
  }
  function open() { if (visible) popupOpen = true }
  function close() { popupOpen = false }
  visible: service.state === "ready" || service.state === "idle"
  onVisibleChanged: if (!visible) close()
  onPopupOpenChanged: {
    if (popupOpen) Qt.callLater(function() { search.forceActiveFocus() })
    else service.cancelSearch()
  }
  implicitWidth: visible ? (vertical ? barSize : row.implicitWidth + Style.space(14)) : 0
  implicitHeight: visible ? barSize : 0
  activeFocusOnTab: true
  Keys.onReturnPressed: popupOpen = !popupOpen
  Keys.onSpacePressed: service.action("playpause")

  Service { id: service }
  TextMetrics { id: labelMetrics; font: musicLabel.font; text: musicLabel.text }

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(6)
    Text {
      textFormat: Text.PlainText
      text: service.playing === true ? "󰏤" : (service.playing === false ? "󰐊" : "󰐎")
      color: root.fg
      font.family: root.family
      font.pixelSize: Style.font.body
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {
      id: musicLabel
      visible: !root.vertical
      width: visible ? Math.min(root.maxLabelWidth, Math.ceil(labelMetrics.advanceWidth)) : 0
      textFormat: Text.PlainText
      text: service.metadataLabel + (service.track ? " · " + service.timingLabel : "")
      color: root.fg
      font.family: root.family
      font.pixelSize: Style.font.body
      elide: Text.ElideRight
      anchors.verticalCenter: parent.verticalCenter
    }
  }
  MouseArea {
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    acceptedButtons: Qt.LeftButton | Qt.MiddleButton | Qt.RightButton
    onClicked: function(mouse) {
      if (mouse.button === Qt.RightButton) root.popupOpen = !root.popupOpen
      else if (mouse.button === Qt.MiddleButton) service.action("next")
      else service.action("playpause")
    }
    onWheel: function(wheel) {
      if (wheel.angleDelta.y > 0) service.action("previous")
      else if (wheel.angleDelta.y < 0) service.action("next")
    }
    onEntered: if (root.bar) root.bar.showTooltip(root, service.metadataLabel + (service.track ? " · " + service.timingLabel : "") + "\nLeft: play/pause · Middle: next · Right: library · Scroll: previous/next")
    onExited: if (root.bar) root.bar.hideTooltip(root)
  }

  PopupCard {
    id: popup
    anchorItem: root
    bar: root.bar
    owner: root
    open: root.popupOpen
    contentWidth: fittedContentWidth(Style.space(400))
    contentHeight: fittedContentHeight(header.implicitHeight + Style.space(240))

    Item {
      anchors.fill: parent
      Keys.onEscapePressed: root.close()
      Column {
        id: header
        width: parent.width
        spacing: Style.space(8)
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: service.track ? (service.track.title || "Track unknown") : service.stateMessage(service.state)
          color: Color.popups.text
          font.family: root.family
          font.pixelSize: Style.font.subtitle
          font.bold: true
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: service.track ? (service.track.artist || "Artist unknown") : ""
          visible: text !== ""
          color: Color.popups.text
          font.family: root.family
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: service.track ? ((service.track.album || "Album unknown") + " · " + service.yearLabel) : ""
          visible: text !== ""
          color: Color.popups.text
          font.family: root.family
          font.pixelSize: Style.font.bodySmall
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: service.timingLabel
          visible: service.track !== null
          color: Color.popups.text
          font.family: root.family
          font.pixelSize: Style.font.bodySmall
        }
        Row {
          spacing: Style.space(6)
          Button { text: "Previous"; focusable: true; foreground: Color.popups.text; enabled: service.canControl; opacity: enabled ? 1 : 0.4; onClicked: service.action("previous") }
          Button { text: "Play/pause"; focusable: true; foreground: Color.popups.text; enabled: service.canControl; opacity: enabled ? 1 : 0.4; onClicked: service.action("playpause") }
          Button { text: "Next"; focusable: true; foreground: Color.popups.text; enabled: service.canControl; opacity: enabled ? 1 : 0.4; onClicked: service.action("next") }
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: service.actionState === "requested" ? "Playback requested" : (service.actionState === "requesting" ? "Sending playback request…" : (service.actionState ? service.stateMessage(service.actionState) : (service.playing === true ? "Playing" : (service.playing === false ? "Paused" : "Playback state unavailable"))))
          color: Color.popups.text
          font.family: root.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.Wrap
        }
        PanelSeparator { foreground: Color.popups.text }
        Row {
          spacing: Style.space(6)
          Button { text: "All"; focusable: true; foreground: Color.popups.text; selected: service.kind === "all"; onClicked: service.setKind("all") }
          Button { text: "Songs"; focusable: true; foreground: Color.popups.text; selected: service.kind === "song"; onClicked: service.setKind("song") }
          Button { text: "Albums"; focusable: true; foreground: Color.popups.text; selected: service.kind === "album"; onClicked: service.setKind("album") }
          Button { text: "Retry"; focusable: true; foreground: Color.popups.text; onClicked: { service.refresh(); service.searchNow() } }
        }
        TextField {
          id: search
          width: parent.width
          foreground: Color.popups.text
          placeholderText: "Search music…"
          maximumLength: 256
          onTextChanged: service.setQuery(text)
          onAccepted: service.searchNow()
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: service.searchState === "prompt" ? "Search songs, albums or band names (2+ characters)" : (service.searchState === "loading" ? "Searching library…" : (service.searchState === "ready" ? (service.partial ? "Some results unavailable — retry or narrow your search" : (service.results.length ? (service.truncated ? "First 40 matches — narrow your search" : "Select a song or album to request playback") : "No matches")) : service.stateMessage(service.searchState)))
          color: Color.popups.text
          font.family: root.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.Wrap
        }
      }
      ListView {
        id: resultList
        anchors.top: header.bottom
        anchors.topMargin: Style.space(8)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        clip: true
        spacing: Style.space(4)
        boundsBehavior: Flickable.StopAtBounds
        model: service.results
        delegate: BorderSurface {
          id: resultRow
          required property var modelData
          width: resultList.width
          height: resultColumn.implicitHeight + Style.space(14)
          radius: Style.cornerRadius
          activeFocusOnTab: true
          color: Style.controlFill(activeFocus, resultHover.hovered, Color.popups.text, Color.accent)
          borderSpec: Border.controlSpec(activeFocus ? "focus" : (resultHover.hovered ? "hover-cursor" : "normal"), Color.popups.text, Color.accent)
          Keys.onReturnPressed: service.select(modelData)
          Keys.onEnterPressed: service.select(modelData)
          Keys.onSpacePressed: service.select(modelData)
          Column {
            id: resultColumn
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: Style.space(8)
            spacing: Style.space(4)
            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: (resultRow.modelData.kind === "album" ? "Album · " : "Song · ") + (resultRow.modelData.title || "Title unknown")
              color: Color.popups.text
              font.family: root.family
              font.pixelSize: Style.font.body
              elide: Text.ElideRight
            }
            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: (resultRow.modelData.artist || "Artist unknown") + (resultRow.modelData.album ? " · " + resultRow.modelData.album : "") + " · " + (resultRow.modelData.year || "Year unknown")
              color: Color.popups.text
              font.family: root.family
              font.pixelSize: Style.font.caption
              elide: Text.ElideRight
            }
          }
          HoverHandler { id: resultHover }
          MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { resultRow.forceActiveFocus(); service.select(resultRow.modelData) } }
        }
      }
    }
  }
}
