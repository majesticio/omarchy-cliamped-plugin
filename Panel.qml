import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "io.github.majesticio.cliamped"
  ipcTarget: "io.github.majesticio.cliamped"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property string libraryTab: "radio"
  property string filePickerStatus: ""
  property bool filePickerFailed: false
  property string pickerMode: "files"
  readonly property var barIdentity: hostWidget || root
  readonly property color sand: bar ? bar.foreground : Color.popups.text
  // Omarchy's muted token is deliberately subtle and can be nearly invisible in
  // some themes. Blend it toward the active foreground so secondary information
  // remains recognizably muted without sacrificing readability.
  readonly property color mutedSand: Qt.tint(Color.muted, root.tint(sand, 0.72))
  readonly property color turquoise: Color.accent
  readonly property color adobe: bar ? bar.urgent : Color.urgent
  readonly property color night: Color.popups.background
  readonly property string panelFont: bar ? bar.fontFamily : Style.font.family
  readonly property string barLabel: hostWidget ? hostWidget.barLabel : "󰝚  CLIAMP"
  readonly property string tooltipLabel: hostWidget && hostWidget.sessionReady
    ? hostWidget.trackTitle : "Starting CLIAMPed…"

  function tint(colorValue, alpha) {
    return Qt.rgba(colorValue.r, colorValue.g, colorValue.b, alpha)
  }
  function launchAudioPicker(mode) {
    if (audioPicker.running) return
    root.pickerMode = mode
    audioPicker.command = [
      "python3",
      String(Qt.resolvedUrl("cliamp_file_picker.py")).replace(/^file:\/\//, ""),
      mode === "folder" ? "--folder" : "--files"
    ]
    root.filePickerStatus = mode === "folder"
      ? "Choose a folder; its audio tracks will be queued in filename order…"
      : "Waiting for your selection…"
    root.filePickerFailed = false
    audioPicker.running = true
  }
  function clock(seconds) {
    var value = Math.max(0, Math.floor(Number(seconds || 0)))
    var mins = Math.floor(value / 60)
    var secs = value % 60
    return mins + ":" + (secs < 10 ? "0" : "") + secs
  }
  function selectedProviderSearchable() {
    if (!root.hostWidget) return false
    for (var i = 0; i < root.hostWidget.providers.length; ++i) {
      var provider = root.hostWidget.providers[i]
      if (provider.key === root.hostWidget.selectedProviderKey) return provider.searchable === true
    }
    return false
  }
  function open() { controller.show() }
  function openFromHotkey() { open() }
  function close() { controller.hide() }
  function toggle() { opened ? close() : open() }
  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }


  Process {
    id: audioPicker
    command: []
    stdout: StdioCollector { id: audioPickerOut; waitForEnd: true }
    stderr: StdioCollector { id: audioPickerErr; waitForEnd: true }
    onExited: function(exitCode) {
      var response = null
      try { response = JSON.parse(String(audioPickerOut.text || "")) } catch (e) {}
      if (response && response.cancelled) {
        root.filePickerStatus = root.pickerMode === "folder" ? "No folder selected." : "No files selected."
        root.filePickerFailed = false
        return
      }
      if (exitCode !== 0 || !response || !response.ok) {
        root.filePickerStatus = response && response.error ? String(response.error)
          : String(audioPickerErr.text || "File picker did not return a result.").trim()
        root.filePickerFailed = true
        return
      }
      root.filePickerStatus = response.selected_count + (response.selected_count === 1
        ? " track is playing and visible in Queue."
        : " tracks loaded: the first is playing and the rest are queued.")
      root.filePickerFailed = false
      if (root.hostWidget) {
        root.hostWidget.queueTracks = response.tracks || []
        if (response.index !== undefined) root.hostWidget.currentIndex = Number(response.index)
        root.hostWidget.probe()
      }
      root.libraryTab = "queue"
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(510))
    contentHeight: panel.fittedContentHeight(contentColumn.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: queueInput.activeFocus || providerSearch.activeFocus
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onActivateRequested: if (root.hostWidget) root.hostWidget.togglePlayback()
      onMoveRequested: function(dx, dy) {
        if (!root.hostWidget) return
        if (dy < 0) root.hostWidget.adjustVolume(2)
        else if (dy > 0) root.hostWidget.adjustVolume(-2)
        else if (dx < 0) {
          if (root.hostWidget.durationSeconds > 0)
            root.hostWidget.seekTo(root.hostWidget.positionSeconds - 10)
          else root.hostWidget.previous()
        } else if (dx > 0) {
          if (root.hostWidget.durationSeconds > 0)
            root.hostWidget.seekTo(root.hostWidget.positionSeconds + 10)
          else root.hostWidget.next()
        }
      }
      onTextKey: function(key) {
        if (!root.hostWidget) return
        var value = String(key || "").toLowerCase()
        if (value === "n") root.hostWidget.next()
        else if (value === "p") root.hostWidget.previous()
        else if (value === "s") root.hostWidget.toggleShuffle()
        else if (value === "r") root.hostWidget.cycleRepeat()
        else if (value === "m") root.hostWidget.toggleMono()
        else if (value === "v") root.hostWidget.nextVisualizer()
      }

      Flickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
          id: contentColumn
          width: parent.width
          spacing: Style.space(14)

          Rectangle {
            width: parent.width
            height: Style.space(3)
            radius: height / 2
            gradient: Gradient {
              orientation: Gradient.Horizontal
              GradientStop { position: 0; color: root.turquoise }
              GradientStop { position: 0.52; color: root.sand }
              GradientStop { position: 1; color: root.adobe }
            }
          }

          Row {
            width: parent.width
            Column {
              width: parent.width - sessionBadge.width
              spacing: Style.space(3)
              Text {
                text: "CLIAMPED"
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 2.2
              }
              Text {
                text: "YOUR MUSIC, FULLY CLIAMPED"
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.bodySmall
                font.letterSpacing: 0.9
              }
            }
            Rectangle {
              id: sessionBadge
              width: badgeText.implicitWidth + Style.space(18)
              height: badgeText.implicitHeight + Style.space(8)
              radius: height / 2
              color: root.tint(root.adobe, 0.16)
              border.width: 1
              border.color: root.tint(root.adobe, 0.55)
              Text {
                id: badgeText
                anchors.centerIn: parent
                text: root.hostWidget ? root.hostWidget.sessionLabel : "CONNECTING"
                color: root.sand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
              }
            }
          }

          Rectangle {
            width: parent.width
            height: Style.space(170)
            radius: Style.cornerRadius
            color: root.tint(root.night, 0.78)
            border.width: 1
            border.color: root.tint(root.turquoise, 0.22)

            Column {
              anchors.fill: parent
              anchors.margins: Style.space(15)
              spacing: Style.space(8)
              Row {
                width: parent.width
                Column {
                  width: parent.width - stateText.width
                  spacing: Style.space(2)
                  Text {
                    width: parent.width
                    text: root.hostWidget
                      ? (root.hostWidget.selectedStation || root.hostWidget.trackTitle || "CLIAMPed")
                      : "CLIAMPed"
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.title
                    font.bold: true
                    elide: Text.ElideRight
                  }
                  Text {
                    text: root.hostWidget && root.hostWidget.trackArtist
                      ? (root.hostWidget.trackArtist + (root.hostWidget.trackAlbum
                        ? "  ·  " + root.hostWidget.trackAlbum : "")).toUpperCase()
                      : (root.hostWidget && root.hostWidget.selectedStation
                        ? root.hostWidget.trackTitle.toUpperCase() : "CLIAMP MEDIA")
                    color: root.adobe
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    font.letterSpacing: 1.2
                  }
                }
                Text {
                  id: stateText
                  text: root.hostWidget && root.hostWidget.playing ? "ON AIR" : "STANDBY"
                  color: root.hostWidget && root.hostWidget.playing ? root.turquoise : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  font.letterSpacing: 1.2
                }
              }
              DesertVisualizer {
                width: parent.width
                height: Style.space(86)
                bands: root.hostWidget ? root.hostWidget.bands : []
                playing: root.hostWidget && root.hostWidget.playing
                turquoise: root.turquoise
                sand: root.sand
                adobe: root.adobe
                sky: root.night
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(4)
            visible: root.hostWidget && root.hostWidget.durationSeconds > 0
            Rectangle {
              width: parent.width
              height: Style.space(6)
              radius: height / 2
              color: root.tint(root.mutedSand, 0.18)
              Rectangle {
                width: parent.width * Math.max(0, Math.min(1,
                  root.hostWidget ? root.hostWidget.positionSeconds / root.hostWidget.durationSeconds : 0))
                height: parent.height
                radius: height / 2
                color: root.turquoise
              }
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: function(mouse) {
                  if (root.hostWidget)
                    root.hostWidget.seekTo(root.hostWidget.durationSeconds * mouse.x / width)
                }
              }
            }
            Row {
              width: parent.width
              Text {
                width: parent.width / 2
                text: root.clock(root.hostWidget ? root.hostWidget.positionSeconds : 0)
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
              }
              Text {
                width: parent.width / 2
                text: root.clock(root.hostWidget ? root.hostWidget.durationSeconds : 0)
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                horizontalAlignment: Text.AlignRight
              }
            }
          }

          Row {
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: Style.space(10)
            Repeater {
              model: [
                { icon: "󰒮", label: "PREVIOUS", action: "previous" },
                { icon: root.hostWidget && root.hostWidget.playing ? "󰏤" : "󰐊", label: "PLAY / PAUSE", action: "togglePlayback" },
                { icon: "󰒭", label: "NEXT", action: "next" },
                { icon: "󰓛", label: "STOP", action: "stop" }
              ]
              Rectangle {
                required property var modelData
                width: Style.space(modelData.action === "togglePlayback" ? 128 : 94)
                height: Style.space(40)
                radius: Style.cornerRadius
                color: controlMouse.containsMouse ? root.tint(root.adobe, 0.2) : root.tint(root.night, 0.62)
                border.width: 1
                border.color: controlMouse.containsMouse ? root.adobe : root.tint(root.mutedSand, 0.26)
                Row {
                  anchors.centerIn: parent
                  spacing: Style.space(6)
                  Text { text: modelData.icon; color: root.sand; font.family: root.panelFont; font.pixelSize: Style.font.body }
                  Text { text: modelData.label; color: root.mutedSand; font.family: root.panelFont; font.pixelSize: Style.font.caption }
                }
                MouseArea {
                  id: controlMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget && root.hostWidget[modelData.action]) root.hostWidget[modelData.action]()
                }
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)
            Repeater {
              model: [
                { key: "radio", label: "STATIONS" },
                { key: "providers", label: "PROVIDERS" },
                { key: "queue", label: "QUEUE" },
                { key: "files", label: "FILES" },
                { key: "more", label: "MORE" }
              ]
              Rectangle {
                required property var modelData
                readonly property bool selected: root.libraryTab === modelData.key
                width: (contentColumn.width - Style.space(32)) / 5
                height: Style.space(34)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.turquoise, 0.16) : "transparent"
                border.width: 1
                border.color: selected ? root.turquoise : root.tint(root.mutedSand, 0.22)
                Text {
                  anchors.centerIn: parent
                  text: modelData.label
                  color: selected ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: selected
                  font.letterSpacing: 1.2
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    root.libraryTab = modelData.key
                    if (modelData.key === "providers" && root.hostWidget
                        && !root.hostWidget.providers.length)
                      root.hostWidget.refreshProviders()
                    else if (modelData.key === "queue" && root.hostWidget)
                      root.hostWidget.refreshQueue()
                    else if (modelData.key === "more" && root.hostWidget) {
                      root.hostWidget.refreshHistory()
                      root.hostWidget.refreshLyrics()
                      root.hostWidget.refreshDevices()
                    }
                  }
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.libraryTab === "providers"

            Text {
              text: "CLIAMP PROVIDER  ›  PLAYLISTS"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }

            Text {
              visible: root.hostWidget && root.hostWidget.providerBusy
              text: "LOADING " + (root.hostWidget && root.hostWidget.selectedProviderKey
                ? root.hostWidget.selectedProviderKey.toUpperCase() : "PROVIDERS") + "…"
              color: root.turquoise
              opacity: 0.9
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.1
            }

            Row {
              width: parent.width
              spacing: Style.space(7)
              Repeater {
                model: root.hostWidget ? root.hostWidget.providers : []
                Rectangle {
                  id: providerChip
                  required property var modelData
                  readonly property bool selected: root.hostWidget
                    && root.hostWidget.selectedProviderKey === modelData.key
                  width: Math.max(Style.space(76), providerName.implicitWidth + Style.space(20))
                  height: Style.space(32)
                  radius: height / 2
                  color: selected ? root.tint(root.adobe, 0.22)
                    : providerMouse.containsMouse ? root.tint(root.turquoise, 0.12)
                    : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: selected ? root.adobe
                    : providerMouse.containsMouse ? root.turquoise : root.tint(root.sand, 0.34)
                  Text {
                    id: providerName
                    anchors.centerIn: parent
                    text: String(modelData.name || modelData.key).toUpperCase()
                    color: root.sand
                    opacity: providerChip.selected || providerMouse.containsMouse ? 1 : 0.88
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                  }
                  MouseArea {
                    id: providerMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.selectProvider(modelData.key)
                  }
                }
              }
            }

            Row {
              width: parent.width
              spacing: Style.space(8)
              Rectangle {
                width: parent.width - providerSearchButton.width - Style.space(8)
                height: Style.space(36)
                radius: Style.cornerRadius
                color: root.tint(root.night, 0.5)
                border.width: 1
                border.color: providerSearch.activeFocus ? root.turquoise : root.tint(root.sand, 0.34)
                TextInput {
                  id: providerSearch
                  enabled: root.selectedProviderSearchable()
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(10)
                  verticalAlignment: TextInput.AlignVCenter
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.bodySmall
                  clip: true
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: providerSearch.text === "" && !providerSearch.activeFocus
                    text: !root.selectedProviderSearchable()
                      ? "THIS PROVIDER DOES NOT SUPPORT SEARCH"
                      : root.hostWidget && root.hostWidget.selectedProviderKey === "radio"
                        ? "SEARCH STATIONS BY NAME, GENRE, OR COUNTRY…"
                        : "SEARCH TRACKS IN THE LOCAL CLIAMP LIBRARY…"
                    color: root.sand
                    opacity: 0.82
                    font: providerSearch.font
                  }
                  Keys.onReturnPressed: if (root.hostWidget) root.hostWidget.searchProvider(text)
                }
              }
              Rectangle {
                id: providerSearchButton
                readonly property bool available: root.selectedProviderSearchable()
                width: Style.space(84)
                height: Style.space(36)
                radius: Style.cornerRadius
                color: available ? root.tint(root.turquoise, 0.18) : root.tint(root.night, 0.45)
                border.width: 1
                border.color: available ? root.turquoise : root.tint(root.sand, 0.28)
                Text {
                  anchors.centerIn: parent
                  text: root.hostWidget && root.hostWidget.providerBusy ? "WAIT…" : "SEARCH"
                  color: root.sand
                  opacity: providerSearchButton.available ? 1 : 0.55
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  anchors.fill: parent
                  enabled: providerSearchButton.available
                  cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: if (root.hostWidget) root.hostWidget.searchProvider(providerSearch.text)
                }
              }
            }

            Text {
              width: parent.width
              text: !root.selectedProviderSearchable()
                ? "This CLIAMP provider exposes playlists but no search endpoint."
                : root.hostWidget && root.hostWidget.selectedProviderKey === "radio"
                  ? "Search finds stations inside CLIAMP's Radio provider."
                  : "Search matches CLIAMP's Local library—not a folder path. Use Files to browse your disk."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              visible: root.hostWidget && root.hostWidget.providerPlaylists.length > 0
              text: (root.hostWidget ? root.hostWidget.selectedProviderKey.toUpperCase() : "SELECTED")
                + " PLAYLISTS"
              color: root.adobe
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Flow {
              width: parent.width
              spacing: Style.space(7)
              visible: root.hostWidget && root.hostWidget.providerPlaylists.length > 0
              Repeater {
                model: root.hostWidget ? root.hostWidget.providerPlaylists : []
                Rectangle {
                  id: playlistChip
                  required property var modelData
                  readonly property bool selected: root.hostWidget
                    && root.hostWidget.loadedProviderPlaylistId === String(modelData.id)
                  width: Math.min(contentColumn.width, playlistName.implicitWidth + Style.space(22))
                  height: Style.space(31)
                  radius: height / 2
                  color: selected ? root.tint(root.turquoise, 0.2)
                    : playlistMouse.containsMouse ? root.tint(root.adobe, 0.22)
                    : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: selected ? root.turquoise
                    : playlistMouse.containsMouse ? root.adobe : root.tint(root.sand, 0.3)
                  Text {
                    id: playlistName
                    anchors.centerIn: parent
                    width: parent.width - Style.space(18)
                    text: String(modelData.name || modelData.id)
                    color: root.sand
                    opacity: playlistChip.selected || playlistMouse.containsMouse ? 1 : 0.9
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    font.bold: playlistChip.selected
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    maximumLineCount: 1
                  }
                  MouseArea {
                    id: playlistMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.loadProviderPlaylist(modelData.id)
                  }
                }
              }
            }

            Text {
              visible: root.hostWidget && !root.hostWidget.providerBusy
                && root.hostWidget.selectedProviderKey !== ""
                && root.hostWidget.providerPlaylists.length === 0
              text: "No playlists are available from this provider."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
            }

            Column {
              width: parent.width
              spacing: Style.space(5)
              visible: root.hostWidget && root.hostWidget.providerResults.length > 0
              Text {
                text: "SEARCH RESULTS  ·  CLICK A TRACK TO PLAY"
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.1
              }
              Repeater {
                model: root.hostWidget ? root.hostWidget.providerResults : []
                Rectangle {
                  required property var modelData
                  width: contentColumn.width
                  height: Style.space(38)
                  radius: Style.cornerRadius
                  color: resultMouse.containsMouse ? root.tint(root.turquoise, 0.1) : "transparent"
                  border.width: 1
                  border.color: resultMouse.containsMouse
                    ? root.tint(root.turquoise, 0.55) : root.tint(root.sand, 0.18)
                  Row {
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(10)
                    anchors.rightMargin: Style.space(10)
                    Text {
                      width: parent.width * 0.62
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.title || modelData.path
                      color: root.sand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideRight
                    }
                    Text {
                      width: parent.width * 0.38
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData.artist || "PLAY"
                      color: root.sand
                      opacity: 0.86
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      horizontalAlignment: Text.AlignRight
                      elide: Text.ElideRight
                    }
                  }
                  MouseArea {
                    id: resultMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.playProviderTrack(modelData)
                  }
                }
              }
            }

            Text {
              visible: root.hostWidget && root.hostWidget.providerSearchAttempted
                && !root.hostWidget.providerBusy && root.hostWidget.providerResults.length === 0
              text: "No matching tracks or stations."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              visible: root.hostWidget && root.hostWidget.providerError !== ""
              width: parent.width
              text: root.hostWidget ? root.hostWidget.providerError : ""
              color: root.adobe
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(7)
            visible: root.libraryTab === "queue"

            Row {
              width: parent.width
              Text {
                width: parent.width - clearQueueButton.width
                anchors.verticalCenter: parent.verticalCenter
                text: "LIVE QUEUE  ·  " + (root.hostWidget ? root.hostWidget.queueTracks.length : 0) + " TRACKS"
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
              }
              Rectangle {
                id: clearQueueButton
                readonly property bool available: root.hostWidget && root.hostWidget.queueTracks.length > 0
                width: Style.space(88)
                height: Style.space(30)
                radius: Style.cornerRadius
                color: clearQueueMouse.containsMouse ? root.tint(root.adobe, 0.2) : "transparent"
                border.width: 1
                border.color: root.tint(root.adobe, available ? 0.6 : 0.2)
                Text {
                  anchors.centerIn: parent
                  text: "CLEAR"
                  color: clearQueueButton.available ? root.adobe : root.mutedSand
                  opacity: clearQueueButton.available ? 1 : 0.45
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: clearQueueMouse
                  anchors.fill: parent
                  enabled: clearQueueButton.available
                  hoverEnabled: true
                  cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: if (root.hostWidget) root.hostWidget.clearQueue()
                }
              }
            }

            Repeater {
              model: root.hostWidget ? root.hostWidget.queueTracks : []
              Rectangle {
                id: queueRow
                required property var modelData
                required property int index
                readonly property int trackIndex: index
                readonly property bool current: root.hostWidget && root.hostWidget.currentIndex === index
                width: contentColumn.width
                height: Style.space(42)
                radius: Style.cornerRadius
                color: current ? root.tint(root.turquoise, 0.13)
                  : queueRowMouse.containsMouse ? root.tint(root.mutedSand, 0.07) : "transparent"
                border.width: 1
                border.color: current ? root.turquoise : root.tint(root.mutedSand, 0.18)
                Text {
                  x: Style.space(10)
                  width: parent.width - queueActions.width - Style.space(22)
                  anchors.verticalCenter: parent.verticalCenter
                  text: (current ? "▶  " : (index + 1) + ".  ")
                    + (modelData.title || modelData.path)
                    + (modelData.artist ? "  ·  " + modelData.artist : "")
                  color: current ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
                Row {
                  id: queueActions
                  anchors.right: parent.right
                  anchors.rightMargin: Style.space(7)
                  anchors.verticalCenter: parent.verticalCenter
                  spacing: Style.space(5)
                  Repeater {
                    model: [
                      { icon: "▶", op: "play" },
                      { icon: "NEXT", op: "enqueue" },
                      { icon: "×", op: "remove" }
                    ]
                    Rectangle {
                      required property var modelData
                      width: Style.space(modelData.op === "enqueue" ? 44 : 30)
                      height: Style.space(27)
                      radius: Style.cornerRadius
                      color: queueActionMouse.containsMouse ? root.tint(root.adobe, 0.18) : "transparent"
                      Text {
                        anchors.centerIn: parent
                        text: modelData.icon
                        color: root.sand
                        font.family: root.panelFont
                        font.pixelSize: Style.font.caption
                      }
                      MouseArea {
                        id: queueActionMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                          if (!root.hostWidget) return
                          if (modelData.op === "play") root.hostWidget.playQueueIndex(queueRow.trackIndex)
                          else if (modelData.op === "enqueue") root.hostWidget.enqueueQueueIndex(queueRow.trackIndex)
                          else root.hostWidget.removeQueueIndex(queueRow.trackIndex)
                        }
                      }
                    }
                  }
                }
                MouseArea {
                  id: queueRowMouse
                  anchors.fill: parent
                  z: -1
                  hoverEnabled: true
                }
              }
            }

            Text {
              visible: root.hostWidget && root.hostWidget.queueTracks.length === 0
              width: parent.width
              text: "Queue is empty. Choose a station, provider playlist, files, or a folder."
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.libraryTab === "more"

            Text {
              text: "OUTPUT DEVICE"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Flow {
              width: parent.width
              spacing: Style.space(7)
              Repeater {
                model: root.hostWidget ? root.hostWidget.audioDevices : []
                Rectangle {
                  required property var modelData
                  width: Math.min(contentColumn.width, deviceName.implicitWidth + Style.space(24))
                  height: Style.space(30)
                  radius: height / 2
                  color: modelData.active ? root.tint(root.turquoise, 0.18) : "transparent"
                  border.width: 1
                  border.color: modelData.active ? root.turquoise : root.tint(root.mutedSand, 0.22)
                  Text {
                    id: deviceName
                    anchors.centerIn: parent
                    text: (modelData.active ? "●  " : "") + modelData.name
                    color: modelData.active ? root.sand : root.mutedSand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                  }
                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.selectDevice(modelData.name)
                  }
                }
              }
            }

            Text {
              visible: root.hostWidget && root.hostWidget.audioDevices.length === 0
              text: "No switchable devices reported; CLIAMP is using the system default."
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
            }

            Text {
              text: "RECENTLY PLAYED"
              color: root.adobe
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Repeater {
              model: root.hostWidget ? root.hostWidget.historyItems : []
              Rectangle {
                required property var modelData
                width: contentColumn.width
                height: Style.space(34)
                radius: Style.cornerRadius
                color: historyMouse.containsMouse ? root.tint(root.turquoise, 0.09) : "transparent"
                Text {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(9)
                  anchors.rightMargin: Style.space(9)
                  verticalAlignment: Text.AlignVCenter
                  text: (modelData.track.title || modelData.track.path)
                    + (modelData.track.artist ? "  ·  " + modelData.track.artist : "")
                  color: root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                }
                MouseArea {
                  id: historyMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.playHistoryItem(modelData)
                }
              }
            }
            Text {
              visible: root.hostWidget && root.hostWidget.historyItems.length === 0
              width: parent.width
              text: "No recently played tracks yet."
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
            }

            Text {
              text: "LYRICS"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Text {
              width: parent.width
              text: {
                if (!root.hostWidget || !root.hostWidget.lyricLines.length)
                  return "No synchronized lyrics available for this track."
                var lines = []
                for (var i = 0; i < root.hostWidget.lyricLines.length; ++i)
                  lines.push(root.hostWidget.lyricLines[i].text)
                return lines.join("\n")
              }
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              lineHeight: 1.2
              wrapMode: Text.WordWrap
            }
          }

          Rectangle {
            width: parent.width
            height: Style.space(root.filePickerStatus === "" ? 104 : 128)
            radius: Style.cornerRadius
            visible: root.libraryTab === "files"
            color: root.tint(root.night, 0.48)
            border.width: 1
            border.color: root.tint(root.turquoise, 0.28)
            Column {
              anchors.centerIn: parent
              spacing: Style.space(8)
              Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: "PLAY LOCAL AUDIO"
                color: root.sand
                font.family: root.panelFont
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }
              Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.space(8)
                Repeater {
                  model: [
                    { mode: "files", label: "CHOOSE FILES…" },
                    { mode: "folder", label: "ADD FOLDER…" }
                  ]
                  Rectangle {
                    required property var modelData
                    readonly property bool available: root.hostWidget && root.hostWidget.sessionReady
                    width: Style.space(142)
                    height: Style.space(34)
                    radius: Style.cornerRadius
                    color: pickerMouse.containsMouse
                      ? root.tint(root.adobe, 0.22) : root.tint(root.adobe, 0.12)
                    border.width: 1
                    border.color: root.tint(root.adobe, available ? 1 : 0.3)
                    Text {
                      anchors.centerIn: parent
                      text: modelData.label
                      color: available ? root.sand : root.mutedSand
                      opacity: available ? 1 : 0.5
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    MouseArea {
                      id: pickerMouse
                      anchors.fill: parent
                      enabled: parent.available
                      hoverEnabled: true
                      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                      onClicked: root.launchAudioPicker(modelData.mode)
                    }
                  }
                }
              }
              Text {
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(contentColumn.width - Style.space(30), implicitWidth)
                visible: root.filePickerStatus !== ""
                text: root.filePickerStatus
                color: root.filePickerFailed ? root.adobe : root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)
            visible: root.libraryTab === "more"
            Text {
              width: Style.space(60)
              anchors.verticalCenter: parent.verticalCenter
              text: "SPEED"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Repeater {
              model: [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
              Rectangle {
                required property real modelData
                readonly property bool selected: root.hostWidget
                  && Math.abs(root.hostWidget.playbackSpeed - modelData) < 0.01
                width: Style.space(48)
                height: Style.space(30)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.turquoise, 0.18) : "transparent"
                border.width: 1
                border.color: selected ? root.turquoise : root.tint(root.mutedSand, 0.22)
                Text {
                  anchors.centerIn: parent
                  text: modelData + "×"
                  color: selected ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.setSpeed(modelData)
                }
              }
            }
          }

          Text {
            width: parent.width
            visible: root.libraryTab === "more"
            text: "EQUALIZER  ·  " + (root.hostWidget ? root.hostWidget.eqPreset.toUpperCase() : "FLAT")
            color: root.adobe
            font.family: root.panelFont
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.2
          }

          Grid {
            width: parent.width
            columns: 4
            spacing: Style.space(7)
            visible: root.libraryTab === "more"
            Repeater {
              model: [
                "Flat", "Rock", "Pop", "Jazz",
                "Classical", "Bass Boost", "Treble Boost", "Vocal",
                "Electronic", "Acoustic", "Hip-Hop", "R&B",
                "Loudness", "Late Night", "Podcast", "Small Speakers"
              ]
              Rectangle {
                required property string modelData
                readonly property bool selected: root.hostWidget
                  && root.hostWidget.eqPreset.toLowerCase() === modelData.toLowerCase()
                width: (contentColumn.width - Style.space(21)) / 4
                height: Style.space(30)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.adobe, 0.2) : "transparent"
                border.width: 1
                border.color: selected ? root.adobe : root.tint(root.mutedSand, 0.2)
                Text {
                  anchors.centerIn: parent
                  width: parent.width - 4
                  text: modelData.toUpperCase()
                  color: selected ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: 10
                  horizontalAlignment: Text.AlignHCenter
                  elide: Text.ElideRight
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.setEqPreset(modelData)
                }
              }
            }
          }

          Row {
            id: utilityRow
            width: parent.width
            spacing: Style.space(8)
            visible: root.libraryTab === "more"
            Repeater {
              id: utilityRepeater
              model: {
                var controls = [
                  { title: "− VOL", active: false, action: "volumeDown" },
                  { title: (root.hostWidget ? Math.round(root.hostWidget.volumeDb) : 0) + " dB", active: false, action: "none" },
                  { title: "VOL +", active: false, action: "volumeUp" },
                  { title: "SHUFFLE", active: root.hostWidget && root.hostWidget.shuffle, action: "shuffle" },
                  { title: "REPEAT " + (root.hostWidget ? root.hostWidget.repeatMode.toUpperCase() : "OFF"), active: root.hostWidget && root.hostWidget.repeatMode.toLowerCase() !== "off", action: "repeat" }
                ]
                if (root.hostWidget && root.hostWidget.sessionMode === "tui")
                  controls.push({ title: "VIS " + root.hostWidget.visualizerMode.toUpperCase(), active: false, action: "visualizer" })
                controls.push({ title: "MONO", active: root.hostWidget && root.hostWidget.mono, action: "mono" })
                return controls
              }
              Rectangle {
                required property var modelData
                width: (contentColumn.width - utilityRow.spacing * (utilityRepeater.count - 1)) / utilityRepeater.count
                height: Style.space(34)
                radius: Style.cornerRadius
                color: modelData.active ? root.tint(root.turquoise, 0.17)
                  : utilityMouse.containsMouse ? root.tint(root.adobe, 0.14) : "transparent"
                border.width: 1
                border.color: modelData.active ? root.turquoise : root.tint(root.mutedSand, 0.2)
                Text {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(5)
                  text: modelData.title
                  color: modelData.active ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: 10
                  horizontalAlignment: Text.AlignHCenter
                  elide: Text.ElideRight
                }
                MouseArea {
                  id: utilityMouse
                  anchors.fill: parent
                  enabled: modelData.action !== "none"
                  hoverEnabled: enabled
                  cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: {
                    if (!root.hostWidget) return
                    if (modelData.action === "volumeDown") root.hostWidget.adjustVolume(-2)
                    else if (modelData.action === "volumeUp") root.hostWidget.adjustVolume(2)
                    else if (modelData.action === "shuffle") root.hostWidget.toggleShuffle()
                    else if (modelData.action === "repeat") root.hostWidget.cycleRepeat()
                    else if (modelData.action === "visualizer") root.hostWidget.nextVisualizer()
                    else if (modelData.action === "mono") root.hostWidget.toggleMono()
                  }
                }
              }
            }
          }

          Row {
            width: parent.width
            spacing: Style.space(8)
            visible: root.libraryTab === "queue"
            Rectangle {
              width: parent.width - queueButton.width - Style.space(8)
              height: Style.space(38)
              radius: Style.cornerRadius
              color: root.tint(root.night, 0.58)
              border.width: 1
              border.color: queueInput.activeFocus ? root.turquoise : root.tint(root.mutedSand, 0.25)
              TextInput {
                id: queueInput
                anchors.fill: parent
                anchors.leftMargin: Style.space(11)
                anchors.rightMargin: Style.space(11)
                verticalAlignment: TextInput.AlignVCenter
                color: root.sand
                selectionColor: root.adobe
                selectedTextColor: root.sand
                font.family: root.panelFont
                font.pixelSize: Style.font.bodySmall
                clip: true
                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: queueInput.text === "" && !queueInput.activeFocus
                  text: "PASTE A TRACK PATH OR STREAM URL…"
                  color: root.mutedSand
                  font: queueInput.font
                }
                Keys.onReturnPressed: {
                  if (root.hostWidget) root.hostWidget.queueMedia(text)
                  text = ""
                }
              }
            }
            Rectangle {
              id: queueButton
              width: Style.space(82)
              height: Style.space(38)
              radius: Style.cornerRadius
              color: queueMouse.containsMouse ? root.tint(root.adobe, 0.28) : root.tint(root.adobe, 0.15)
              border.width: 1
              border.color: root.adobe
              Text {
                anchors.centerIn: parent
                text: "QUEUE"
                color: root.sand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1
              }
              MouseArea {
                id: queueMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  if (root.hostWidget) root.hostWidget.queueMedia(queueInput.text)
                  queueInput.text = ""
                }
              }
            }
          }

          Text {
            text: "CURATED STATIONS"
            visible: root.libraryTab === "radio"
            color: root.turquoise
            font.family: root.panelFont
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.8
          }

          Grid {
            width: parent.width
            columns: 3
            spacing: Style.space(8)
            visible: root.libraryTab === "radio"
            Repeater {
              model: root.hostWidget ? root.hostWidget.stations : []
              Rectangle {
                required property var modelData
                readonly property bool selected: root.hostWidget
                  && root.hostWidget.selectedStation === modelData.name
                width: (contentColumn.width - Style.space(16)) / 3
                height: Style.space(56)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.adobe, 0.18)
                  : stationMouse.containsMouse ? root.tint(root.turquoise, 0.1) : "transparent"
                border.width: 1
                border.color: selected ? root.adobe : root.tint(root.mutedSand, 0.22)
                Column {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(12)
                  spacing: Style.space(2)
                  Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: modelData.name.toUpperCase()
                    color: selected ? root.sand : root.mutedSand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    font.bold: selected
                  }
                  Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: modelData.detail
                    color: selected ? root.adobe : root.mutedSand
                    opacity: selected ? 1 : 0.9
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                  }
                }
                MouseArea {
                  id: stationMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.selectStation(modelData)
                }
              }
            }
          }

          Text {
            visible: root.hostWidget && root.hostWidget.errorText !== ""
            width: parent.width
            text: root.hostWidget ? root.hostWidget.errorText : ""
            color: root.adobe
            font.family: root.panelFont
            font.pixelSize: Style.font.bodySmall
            horizontalAlignment: Text.AlignHCenter
          }

          Text {
            width: parent.width
            text: "SPACE PLAY   ·   ←/→ SEEK OR SKIP   ·   ↑/↓ VOLUME   ·   N/P TRACK   ·   S/R/M/V MODES"
            color: root.mutedSand
            opacity: 0.9
            font.family: root.panelFont
            font.pixelSize: Style.font.caption
            font.letterSpacing: 0.8
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }
}
