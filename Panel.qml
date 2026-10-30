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
  property string libraryTab: "favorites"
  property string filePickerStatus: ""
  property bool filePickerFailed: false
  property string pickerMode: "files"
  property bool audioPickerTimedOut: false
  property bool destroying: false
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
    ? hostWidget.plainLabel(hostWidget.trackTitle, 256) : "Starting CLIAMPed…"
  readonly property var processEnvironment: ({
    "PATH": "/usr/bin",
    "HOME": Quickshell.env("HOME"),
    "XDG_CONFIG_HOME": Quickshell.env("XDG_CONFIG_HOME"),
    "XDG_RUNTIME_DIR": Quickshell.env("XDG_RUNTIME_DIR"),
    "DBUS_SESSION_BUS_ADDRESS": Quickshell.env("DBUS_SESSION_BUS_ADDRESS"),
    "WAYLAND_DISPLAY": Quickshell.env("WAYLAND_DISPLAY"),
    "DISPLAY": Quickshell.env("DISPLAY"),
    "LANG": Quickshell.env("LANG") || "C.UTF-8"
  })

  function tint(colorValue, alpha) {
    return Qt.rgba(colorValue.r, colorValue.g, colorValue.b, alpha)
  }
  function failLibraryPicker(message) {
    root.libraryTab = "library"
    if (root.hostWidget) root.hostWidget.libraryError = message
    root.open()
  }
  function launchAudioPicker(mode) {
    if (audioPicker.running || pickerLaunch.running) return
    root.pickerMode = mode === "folder" ? "folder" : mode === "music" ? "music" : "files"
    // A music folder is added to the Library, so the Queue has no status to show.
    root.filePickerStatus = mode === "music" ? ""
      : mode === "folder"
        ? "Choose a folder; its audio tracks will be queued in filename order…"
        : "Waiting for your selection…"
    root.filePickerFailed = false
    // A layer-shell panel sits above ordinary application windows. Close it
    // before Zenity opens so the native picker cannot appear behind the panel.
    root.close()
    pickerLaunch.restart()
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
  function returnToPanelKeys() { keyCatcher.forceActiveFocus() }
  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(barIdentity, direction)
    return false
  }


  Process {
    id: audioPicker
    property bool launchPending: false
    command: []
    clearEnvironment: true
    environment: root.processEnvironment
    stdout: StdioCollector { id: audioPickerOut; waitForEnd: true }
    onStarted: launchPending = false
    onRunningChanged: {
      if (!running && launchPending) {
        launchPending = false
        audioPickerWatchdog.stop()
        audioPickerKill.stop()
        root.audioPickerTimedOut = false
        if (root.destroying) return
        if (root.pickerMode === "music") {
          root.failLibraryPicker("Could not start the bounded folder picker.")
          return
        }
        root.filePickerStatus = "Could not start the bounded file picker."
        root.filePickerFailed = true
        root.libraryTab = "queue"
        root.open()
      }
    }
    onExited: function(exitCode) {
      launchPending = false
      audioPickerWatchdog.stop()
      audioPickerKill.stop()
      if (root.destroying) return
      var response = null
      try { response = JSON.parse(String(audioPickerOut.text || "")) } catch (e) {}
      if (root.pickerMode === "music") {
        // A music folder goes to the Library, not the Queue.
        var timedOut = root.audioPickerTimedOut
        root.audioPickerTimedOut = false
        root.libraryTab = "library"
        if (!timedOut && response && response.cancelled) {
          root.open()
          return
        }
        if (timedOut || exitCode !== 0 || !response || !response.ok || typeof response.path !== "string") {
          root.failLibraryPicker(timedOut ? "Folder selection exceeded its deadline."
            : response && response.error && root.hostWidget
              ? root.hostWidget.cleanText(response.error, 1024)
              : "The folder chooser did not return a folder.")
          return
        }
        if (root.hostWidget) root.hostWidget.addLibraryRoot(response.path)
        root.open()
        return
      }
      if (root.audioPickerTimedOut) {
        root.filePickerStatus = "File selection exceeded its deadline."
        root.filePickerFailed = true
        root.audioPickerTimedOut = false
        if (!root.destroying) {
          root.libraryTab = "queue"
          root.open()
        }
        return
      }
      if (response && response.cancelled) {
        root.filePickerStatus = root.pickerMode === "folder" ? "No folder selected." : "No files selected."
        root.filePickerFailed = false
        root.libraryTab = "queue"
        root.open()
        return
      }
      if (exitCode !== 0 || !response || !response.ok) {
        root.filePickerStatus = response && response.error && root.hostWidget
          ? root.hostWidget.cleanText(response.error, 1024) : "File picker did not return a result."
        root.filePickerFailed = true
        root.libraryTab = "queue"
        root.open()
        return
      }
      var selectedCount = root.hostWidget
        ? root.hostWidget.cleanInteger(response.selected_count, 0, root.hostWidget.maxTrackItems, 0) : 0
      root.filePickerStatus = selectedCount + (selectedCount === 1
        ? " track is playing and visible in Queue."
        : " tracks loaded: the first is playing and the rest are queued.")
      root.filePickerFailed = false
      if (root.hostWidget) {
        root.hostWidget.queueTracks = root.hostWidget.normalizeTracks(
          response.tracks, root.hostWidget.maxTrackItems)
        if (response.index !== undefined)
          root.hostWidget.currentIndex = root.hostWidget.cleanInteger(response.index, -1, 1000000, -1)
        root.hostWidget.probe()
      }
      root.libraryTab = "queue"
      root.open()
    }
  }

  Timer {
    id: pickerLaunch
    interval: 180
    onTriggered: {
      audioPicker.command = [
        "/usr/bin/python3",
        "-I",
        String(Qt.resolvedUrl("cliamp_file_picker.py")).replace(/^file:\/\//, ""),
        root.pickerMode === "folder" ? "--folder" : root.pickerMode === "music" ? "--music-folder" : "--files"
      ]
      root.audioPickerTimedOut = false
      audioPicker.launchPending = true
      audioPickerWatchdog.restart()
      audioPicker.running = true
    }
  }

  Timer {
    id: audioPickerWatchdog
    // 300 s chooser + 8 s traversal + 30 s aggregate IPC, with cleanup slack.
    interval: 345000
    repeat: false
    onTriggered: {
      if (!audioPicker.running) return
      root.audioPickerTimedOut = true
      audioPicker.signal(15)
      audioPickerKill.restart()
    }
  }

  Timer {
    id: audioPickerKill
    interval: 2500
    repeat: false
    onTriggered: if (audioPicker.running) audioPicker.signal(9)
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(1120))
    contentHeight: panel.fittedContentHeight(Style.space(930))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: queueInput.activeFocus || providerSearch.activeFocus || librarySearch.activeFocus || playlistNameInput.activeFocus
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
              SafeText {
                text: "CLIAMPED"
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.title
                font.bold: true
                font.letterSpacing: 2.4
              }
              SafeText {
                text: "YOUR MUSIC, FULLY CLIAMPED"
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.letterSpacing: 1.1
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
              SafeText {
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
            height: Style.space(230)
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
                  SafeText {
                    width: parent.width
                    text: root.hostWidget
                      ? (root.hostWidget.trackTitle || "CLIAMPed")
                      : "CLIAMPed"
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.title
                    font.bold: true
                    elide: Text.ElideRight
                  }
                  SafeText {
                    width: parent.width
                    text: root.hostWidget && root.hostWidget.trackSubtitle
                      ? root.hostWidget.trackSubtitle : "CLIAMP MEDIA"
                    elide: Text.ElideRight
                    color: root.adobe
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    font.letterSpacing: 1.2
                  }
                }
                SafeText {
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
                height: Style.space(146)
                bands: root.hostWidget ? root.hostWidget.bands : []
                playing: root.hostWidget && root.hostWidget.playing
                mode: root.hostWidget ? root.hostWidget.panelVisualizerIndex : 0
                turquoise: root.turquoise
                sand: root.sand
                adobe: root.adobe
                sky: root.night
                onCycleRequested: if (root.hostWidget) root.hostWidget.nextVisualizer()
              }
            }
          }

          Row {
            id: navigationRow
            width: parent.width
            spacing: Style.space(8)
            Repeater {
              model: [
                { key: "favorites", label: "FAVORITES" },
                { key: "providers", label: "BROWSE" },
                { key: "queue", label: "QUEUE" },
                { key: "library", label: "LIBRARY" },
                { key: "more", label: "MORE" }
              ]
              Rectangle {
                required property var modelData
                readonly property bool selected: root.libraryTab === modelData.key
                width: (navigationRow.width - Style.space(32)) / 5
                height: Style.space(36)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.turquoise, 0.16) : "transparent"
                border.width: 1
                border.color: selected ? root.turquoise : root.tint(root.mutedSand, 0.22)
                SafeText {
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
                    browserFlick.contentY = 0
                    root.returnToPanelKeys()
                    if (modelData.key === "favorites" && root.hostWidget)
                      root.hostWidget.showFavorites()
                    else if (modelData.key === "providers" && root.hostWidget)
                      root.hostWidget.refreshProviders()
                    else if (modelData.key === "queue" && root.hostWidget)
                      root.hostWidget.refreshQueue()
                    else if (modelData.key === "library" && root.hostWidget)
                      root.hostWidget.showLibrary(true)
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

          Row {
            id: workspaceRow
            width: parent.width
            height: Style.space(540)
            spacing: Style.space(16)

            Rectangle {
              id: playerCard
              width: Style.space(450)
              height: workspaceRow.height
              radius: Style.cornerRadius
              color: root.tint(root.night, 0.46)
              border.width: 1
              border.color: root.tint(root.adobe, 0.18)

              Column {
                id: playerDeck
                anchors.fill: parent
                anchors.margins: Style.space(14)
                spacing: Style.space(14)

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
              SafeText {
                width: parent.width / 2
                text: root.clock(root.hostWidget ? root.hostWidget.positionSeconds : 0)
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
              }
              SafeText {
                width: parent.width / 2
                text: root.clock(root.hostWidget ? root.hostWidget.durationSeconds : 0)
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                horizontalAlignment: Text.AlignRight
              }
            }
          }

          SafeText {
            width: parent.width
            text: "PLAYBACK"
            color: root.turquoise
            font.family: root.panelFont
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.2
          }

          Grid {
            id: transportGrid
            width: parent.width
            columns: 2
            spacing: Style.space(8)
            Repeater {
              model: [
                { icon: root.hostWidget && root.hostWidget.playing ? "󰏤" : "󰐊", label: "PLAY / PAUSE", action: "togglePlayback" },
                { icon: "󰓛", label: "STOP", action: "stop" },
                { icon: "󰒮", label: "PREVIOUS", action: "previous" },
                { icon: "󰒭", label: "NEXT", action: "next" }
              ]
              Rectangle {
                required property var modelData
                width: (transportGrid.width - transportGrid.spacing) / 2
                height: Style.space(44)
                radius: Style.cornerRadius
                color: controlMouse.containsMouse ? root.tint(root.adobe, 0.2) : root.tint(root.night, 0.62)
                border.width: 1
                border.color: controlMouse.containsMouse ? root.adobe : root.tint(root.mutedSand, 0.26)
                Row {
                  anchors.centerIn: parent
                  spacing: Style.space(6)
                  SafeText { text: modelData.icon; color: root.sand; font.family: root.panelFont; font.pixelSize: Style.font.body }
                  SafeText { text: modelData.label; color: root.mutedSand; font.family: root.panelFont; font.pixelSize: Style.font.caption }
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

          SafeText {
            width: parent.width
            text: "PLAYBACK SPEED  ·  " + (root.hostWidget ? root.hostWidget.playbackSpeed : 1) + "×"
            color: root.turquoise
            font.family: root.panelFont
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.2
          }

          Grid {
            id: speedGrid
            width: parent.width
            columns: 4
            spacing: Style.space(7)
            Repeater {
              model: [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
              Rectangle {
                required property real modelData
                readonly property bool selected: root.hostWidget
                  && Math.abs(root.hostWidget.playbackSpeed - modelData) < 0.01
                width: (speedGrid.width - speedGrid.spacing * 3) / 4
                height: Style.space(31)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.turquoise, 0.18) : "transparent"
                border.width: 1
                border.color: selected ? root.turquoise : root.tint(root.mutedSand, 0.22)
                SafeText {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(3)
                  text: modelData + "×"
                  color: selected ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  horizontalAlignment: Text.AlignHCenter
                  elide: Text.ElideRight
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.setSpeed(modelData)
                }
              }
            }
          }

          SafeText {
            width: parent.width
            text: "EQUALIZER  ·  " + (root.hostWidget ? root.hostWidget.eqPreset.toUpperCase() : "FLAT")
            color: root.adobe
            font.family: root.panelFont
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.2
          }

          Grid {
            id: deckEqGrid
            width: parent.width
            columns: 4
            spacing: Style.space(7)
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
                width: (deckEqGrid.width - Style.space(21)) / 4
                height: Style.space(31)
                radius: Style.cornerRadius
                color: selected ? root.tint(root.adobe, 0.2) : "transparent"
                border.width: 1
                border.color: selected ? root.adobe : root.tint(root.mutedSand, 0.2)
                SafeText {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(4)
                  text: modelData.toUpperCase()
                  color: selected ? root.sand : root.mutedSand
                  font.family: root.panelFont
                  font.pixelSize: 9
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
            id: deckUtilityRow
            width: parent.width
            spacing: Style.space(8)

            Rectangle {
              id: volumeDeck
              width: parent.width * 0.4
              height: Style.space(48)
              radius: Style.cornerRadius
              color: root.tint(root.night, 0.42)
              border.width: 1
              border.color: root.tint(root.mutedSand, 0.24)
              Row {
                anchors.fill: parent
                Repeater {
                  model: [
                    { label: "−", action: "down" },
                    { label: (root.hostWidget ? Math.round(root.hostWidget.volumeDb) : 0) + " dB", action: "none" },
                    { label: "+", action: "up" }
                  ]
                  Rectangle {
                    required property var modelData
                    width: volumeDeck.width / 3
                    height: volumeDeck.height
                    color: volumeMouse.containsMouse && modelData.action !== "none"
                      ? root.tint(root.turquoise, 0.15) : "transparent"
                    SafeText {
                      anchors.centerIn: parent
                      text: modelData.label
                      color: modelData.action === "none" ? root.sand : root.turquoise
                      font.family: root.panelFont
                      font.pixelSize: modelData.action === "none" ? Style.font.caption : Style.font.body
                      font.bold: true
                    }
                    MouseArea {
                      id: volumeMouse
                      anchors.fill: parent
                      enabled: modelData.action !== "none"
                      hoverEnabled: enabled
                      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                      onClicked: if (root.hostWidget)
                        root.hostWidget.adjustVolume(modelData.action === "up" ? 2 : -2)
                    }
                  }
                }
              }
              SafeText {
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: Style.space(3)
                text: "VOLUME"
                color: root.mutedSand
                opacity: 0.75
                font.family: root.panelFont
                font.pixelSize: 8
                font.letterSpacing: 0.8
              }
            }

            Repeater {
              model: [
                { label: "SHUFFLE", value: root.hostWidget && root.hostWidget.shuffle ? "ON" : "OFF", action: "shuffle", active: root.hostWidget && root.hostWidget.shuffle },
                { label: "REPEAT", value: root.hostWidget ? root.hostWidget.repeatMode.toUpperCase() : "OFF", action: "repeat", active: root.hostWidget && root.hostWidget.repeatMode.toLowerCase() !== "off" },
                { label: "MONO", value: root.hostWidget && root.hostWidget.mono ? "ON" : "OFF", action: "mono", active: root.hostWidget && root.hostWidget.mono }
              ]
              Rectangle {
                required property var modelData
                width: (deckUtilityRow.width - volumeDeck.width - deckUtilityRow.spacing * 3) / 3
                height: Style.space(48)
                radius: Style.cornerRadius
                color: modelData.active ? root.tint(root.turquoise, 0.14)
                  : deckStatusMouse.containsMouse ? root.tint(root.adobe, 0.1) : root.tint(root.night, 0.42)
                border.width: 1
                border.color: modelData.active ? root.turquoise : root.tint(root.mutedSand, 0.2)
                Column {
                  anchors.centerIn: parent
                  spacing: Style.space(2)
                  SafeText {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: modelData.label
                    color: root.mutedSand
                    font.family: root.panelFont
                    font.pixelSize: 9
                    font.bold: true
                    font.letterSpacing: 1.1
                  }
                  SafeText {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: modelData.value
                    color: modelData.active ? root.turquoise : root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }
                MouseArea {
                  id: deckStatusMouse
                  anchors.fill: parent
                  enabled: modelData.action !== "none"
                  hoverEnabled: enabled
                  cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: {
                    if (!root.hostWidget) return
                    if (modelData.action === "shuffle") root.hostWidget.toggleShuffle()
                    else if (modelData.action === "repeat") root.hostWidget.cycleRepeat()
                    else if (modelData.action === "mono") root.hostWidget.toggleMono()
                  }
                }
              }
            }
          }

              }
            }

            Rectangle {
              id: browserSurface
              width: workspaceRow.width - playerCard.width - workspaceRow.spacing
              height: workspaceRow.height
              radius: Style.cornerRadius
              color: root.tint(root.night, 0.46)
              border.width: 1
              border.color: root.tint(root.turquoise, 0.2)

              Flickable {
                id: browserFlick
                anchors.fill: parent
                anchors.margins: Style.space(14)
                contentWidth: width
                contentHeight: browserColumn.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                interactive: contentHeight > height

                Column {
                  id: browserColumn
                  width: browserFlick.width
                  spacing: Style.space(12)

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.libraryTab === "favorites"

            SafeText {
              text: "RADIO FAVORITES"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }

            SafeText {
              width: parent.width
              text: "Your starred Radio Browser stations. Manage stars here or while browsing the directory."
              color: root.sand
              opacity: 0.86
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            Grid {
              id: favoritesGrid
              width: parent.width
              columns: 2
              spacing: Style.space(8)
              visible: root.hostWidget && root.hostWidget.favoriteItems.length > 0
              Repeater {
                model: root.hostWidget ? root.hostWidget.favoriteItems : []
                Rectangle {
                  id: favoriteCard
                  required property var modelData
                  readonly property bool selected: root.hostWidget
                    && root.hostWidget.loadedProviderPlaylistId === String(modelData.id)
                  width: (favoritesGrid.width - favoritesGrid.spacing) / 2
                  height: Style.space(54)
                  radius: Style.cornerRadius
                  color: selected ? root.tint(root.turquoise, 0.2)
                    : favoritePlayMouse.containsMouse ? root.tint(root.adobe, 0.16)
                    : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: selected ? root.turquoise
                    : favoritePlayMouse.containsMouse ? root.adobe : root.tint(root.sand, 0.28)
                  SafeText {
                    anchors.left: parent.left
                    anchors.right: favoriteRemove.left
                    anchors.leftMargin: Style.space(12)
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    text: String(modelData.name || modelData.id).replace(/^★\s*/, "").toUpperCase()
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    font.bold: favoriteCard.selected
                    elide: Text.ElideRight
                  }
                  SafeText {
                    id: favoriteRemove
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(12)
                    anchors.verticalCenter: parent.verticalCenter
                    text: "★"
                    color: root.adobe
                    font.family: root.panelFont
                    font.pixelSize: Style.font.body
                  }
                  MouseArea {
                    id: favoritePlayMouse
                    anchors.fill: parent
                    anchors.rightMargin: Style.space(42)
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.playFavorite(modelData)
                  }
                  MouseArea {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: Style.space(42)
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.removeFavorite(modelData)
                  }
                }
              }
            }

            Rectangle {
              id: emptyFavoritesCard
              width: parent.width
              height: Style.space(150)
              radius: Style.cornerRadius
              color: emptyFavoritesMouse.containsMouse ? root.tint(root.turquoise, 0.1)
                : root.tint(root.night, 0.32)
              border.width: 1
              border.color: emptyFavoritesMouse.containsMouse ? root.turquoise : root.tint(root.sand, 0.2)
              visible: root.hostWidget && !root.hostWidget.providerBusy
                && root.hostWidget.favoriteItems.length === 0
              Column {
                anchors.centerIn: parent
                width: parent.width - Style.space(40)
                spacing: Style.space(8)
                SafeText {
                  anchors.horizontalCenter: parent.horizontalCenter
                  text: "NO FAVORITES YET"
                  color: root.adobe
                  font.family: root.panelFont
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                SafeText {
                  width: parent.width
                  text: "Open Browse and select ☆ beside a directory station. Click here to find stations."
                  color: root.sand
                  opacity: 0.84
                  font.family: root.panelFont
                  font.pixelSize: Style.font.bodySmall
                  horizontalAlignment: Text.AlignHCenter
                  wrapMode: Text.WordWrap
                }
              }
              MouseArea {
                id: emptyFavoritesMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                  root.libraryTab = "providers"
                  browserFlick.contentY = 0
                  if (root.hostWidget) {
                    if (root.hostWidget.selectedProviderKey !== "radio")
                      root.hostWidget.selectProvider("radio")
                    else if (root.hostWidget.catalogSize(root.hostWidget.providerPlaylists) === 0)
                      root.hostWidget.loadRadioCatalog()
                  }
                }
              }
            }

            SafeText {
              visible: root.hostWidget && root.hostWidget.providerBusy
              text: "LOADING FAVORITES…"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.1
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.libraryTab === "providers"

            SafeText {
              text: "SOURCES  ›  COLLECTIONS"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }

            SafeText {
              visible: root.hostWidget && root.hostWidget.providerBusy
              text: "LOADING " + (root.hostWidget && root.hostWidget.selectedProviderKey
                ? root.hostWidget.selectedProviderKey.toUpperCase() : "PROVIDERS") + "…"
              color: root.turquoise
              opacity: 0.9
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.1
            }

            Grid {
              id: sourceGrid
              width: parent.width
              columns: Math.max(1, sourceRepeater.count)
              spacing: Style.space(7)
              Repeater {
                id: sourceRepeater
                model: root.hostWidget ? root.hostWidget.providers : []
                Rectangle {
                  id: providerChip
                  required property var modelData
                  readonly property bool selected: root.hostWidget
                    && root.hostWidget.selectedProviderKey === modelData.key
                  width: (sourceGrid.width - sourceGrid.spacing * (sourceRepeater.count - 1))
                    / Math.max(1, sourceRepeater.count)
                  height: Style.space(38)
                  radius: Style.cornerRadius
                  color: selected ? root.tint(root.adobe, 0.22)
                    : providerMouse.containsMouse ? root.tint(root.turquoise, 0.12)
                    : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: selected ? root.adobe
                    : providerMouse.containsMouse ? root.turquoise : root.tint(root.sand, 0.34)
                  SafeText {
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

            SafeText {
              visible: root.hostWidget && root.hostWidget.selectedProviderKey === "radio"
              text: "CLIAMP RADIO  ·  11 CHANNELS"
              color: root.adobe
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Grid {
              id: cliampRadioGrid
              width: parent.width
              columns: 3
              spacing: Style.space(7)
              visible: root.hostWidget && root.hostWidget.selectedProviderKey === "radio"
              Repeater {
                model: root.hostWidget ? root.hostWidget.stations : []
                Rectangle {
                  id: radioChannelCard
                  required property var modelData
                  readonly property bool selected: root.hostWidget
                    && root.hostWidget.selectedStation === String(modelData.name)
                  width: (cliampRadioGrid.width - cliampRadioGrid.spacing * 2) / 3
                  height: Style.space(44)
                  radius: Style.cornerRadius
                  color: selected ? root.tint(root.adobe, 0.22)
                    : radioChannelMouse.containsMouse ? root.tint(root.turquoise, 0.14)
                    : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: selected ? root.adobe
                    : radioChannelMouse.containsMouse ? root.turquoise : root.tint(root.sand, 0.28)
                  SafeText {
                    anchors.centerIn: parent
                    width: parent.width - Style.space(12)
                    text: String(modelData.name).toUpperCase()
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    font.bold: radioChannelCard.selected
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                  }
                  MouseArea {
                    id: radioChannelMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.selectStation(modelData)
                  }
                }
              }
            }

            Row {
              width: parent.width
              visible: root.hostWidget && root.hostWidget.selectedProviderKey === "radio"
              spacing: Style.space(8)
              SafeText {
                width: parent.width - radioCatalogButton.width - Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                text: root.hostWidget && root.hostWidget.radioCatalogOffset > 0
                  ? "RADIO BROWSER DIRECTORY  ·  " + root.hostWidget.radioCatalogOffset + " LOADED"
                  : "RADIO BROWSER DIRECTORY"
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.1
                elide: Text.ElideRight
              }
              Rectangle {
                id: radioCatalogButton
                width: Style.space(132)
                height: Style.space(34)
                radius: Style.cornerRadius
                color: radioCatalogMouse.containsMouse ? root.tint(root.turquoise, 0.2)
                  : root.tint(root.night, 0.5)
                border.width: 1
                border.color: root.turquoise
                SafeText {
                  anchors.centerIn: parent
                  text: root.hostWidget && root.hostWidget.providerBusy ? "LOADING…"
                    : root.hostWidget && root.hostWidget.radioCatalogOffset > 0 ? "LOAD MORE" : "LOAD DIRECTORY"
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: radioCatalogMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  enabled: root.hostWidget && !root.hostWidget.providerBusy
                    && root.hostWidget.radioCatalogHasMore
                  cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                  onClicked: if (root.hostWidget) root.hostWidget.loadRadioCatalog()
                }
              }
            }

            SafeText {
              visible: root.hostWidget && root.hostWidget.providerCollections.length > 0
              text: root.hostWidget && root.hostWidget.selectedProviderKey === "local"
                ? "LOCAL COLLECTIONS" : "DIRECTORY STATIONS"
              color: root.adobe
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Grid {
              id: collectionsGrid
              width: parent.width
              columns: Math.max(1, Math.min(2, collectionRepeater.count))
              spacing: Style.space(7)
              visible: root.hostWidget && root.hostWidget.providerCollections.length > 0
              Repeater {
                id: collectionRepeater
                model: root.hostWidget ? root.hostWidget.providerCollections : []
                Rectangle {
                  id: collectionCard
                  required property var modelData
                  readonly property bool selected: root.hostWidget
                    && root.hostWidget.loadedProviderPlaylistId === String(modelData.id)
                  readonly property bool favoritable: modelData.favoritable === true
                    || String(modelData.id || "").indexOf("f:") === 0
                  readonly property bool starred: modelData.favorite === true
                    || String(modelData.name || "").indexOf("★") === 0
                  width: (collectionsGrid.width - collectionsGrid.spacing * (collectionsGrid.columns - 1))
                    / collectionsGrid.columns
                  height: Style.space(48)
                  radius: Style.cornerRadius
                  color: selected ? root.tint(root.turquoise, 0.2)
                    : collectionMouse.containsMouse ? root.tint(root.adobe, 0.18)
                    : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: selected ? root.turquoise
                    : collectionMouse.containsMouse ? root.adobe : root.tint(root.sand, 0.3)
                  SafeText {
                    anchors.left: parent.left
                    anchors.right: collectionStar.visible ? collectionStar.left : parent.right
                    anchors.leftMargin: Style.space(12)
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    text: String(modelData.name || modelData.id).replace(/^★\s*/, "").toUpperCase()
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    font.bold: collectionCard.selected
                    horizontalAlignment: collectionStar.visible ? Text.AlignLeft : Text.AlignHCenter
                    elide: Text.ElideRight
                    maximumLineCount: 1
                  }
                  SafeText {
                    id: collectionStar
                    visible: collectionCard.favoritable
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(12)
                    anchors.verticalCenter: parent.verticalCenter
                    text: collectionCard.starred ? "★" : "☆"
                    color: collectionCard.starred ? root.adobe : root.turquoise
                    font.family: root.panelFont
                    font.pixelSize: Style.font.body
                  }
                  MouseArea {
                    id: collectionMouse
                    anchors.fill: parent
                    anchors.rightMargin: collectionStar.visible ? Style.space(42) : 0
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.loadProviderPlaylist(modelData.id)
                  }
                  MouseArea {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: Style.space(42)
                    visible: collectionCard.favoritable
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.toggleProviderFavorite(modelData.id)
                  }
                }
              }
            }

            SafeText {
              visible: root.hostWidget && !root.hostWidget.providerBusy
                && root.hostWidget.selectedProviderKey !== ""
                && root.hostWidget.selectedProviderKey !== "radio"
                && root.hostWidget.providerCollections.length === 0
              text: "No saved collections are available from this source."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
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
                  maximumLength: 256
                  enabled: root.selectedProviderSearchable()
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(10)
                  verticalAlignment: TextInput.AlignVCenter
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.bodySmall
                  clip: true
                  SafeText {
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
                  Keys.onReturnPressed: {
                    if (root.hostWidget) root.hostWidget.searchProvider(text)
                    root.returnToPanelKeys()
                  }
                  Keys.onUpPressed: if (root.hostWidget) root.hostWidget.adjustVolume(2)
                  Keys.onDownPressed: if (root.hostWidget) root.hostWidget.adjustVolume(-2)
                  Keys.onEscapePressed: root.returnToPanelKeys()
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
                SafeText {
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
                  onClicked: {
                    if (root.hostWidget) root.hostWidget.searchProvider(providerSearch.text)
                    root.returnToPanelKeys()
                  }
                }
              }
            }

            SafeText {
              width: parent.width
              text: !root.selectedProviderSearchable()
                ? "This CLIAMP source exposes collections but no search endpoint."
                : root.hostWidget && root.hostWidget.selectedProviderKey === "radio"
                  ? "Search finds stations inside CLIAMP's Radio provider."
                  : "Search matches CLIAMP's Local library—not a folder path. Use the Library tab for your indexed folders, or Queue to add files from your disk."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Column {
              width: parent.width
              spacing: Style.space(5)
              visible: root.hostWidget && root.hostWidget.providerResults.length > 0
              SafeText {
              text: "SEARCH RESULTS  ·  PLAY OR STAR A STATION"
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
                  readonly property bool favoritable: root.hostWidget
                    && root.hostWidget.selectedProviderKey === "radio"
                  readonly property bool starred: favoritable
                    && root.hostWidget.isSearchFavorite(modelData)
                  width: browserColumn.width
                  height: Style.space(38)
                  radius: Style.cornerRadius
                  color: resultMouse.containsMouse ? root.tint(root.turquoise, 0.1) : "transparent"
                  border.width: 1
                  border.color: resultMouse.containsMouse
                    ? root.tint(root.turquoise, 0.55) : root.tint(root.sand, 0.18)
                  SafeText {
                    anchors.left: parent.left
                    anchors.right: resultDetail.left
                    anchors.leftMargin: Style.space(10)
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.title || modelData.path
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    elide: Text.ElideRight
                  }
                  SafeText {
                    id: resultDetail
                    width: parent.width * 0.28
                    anchors.right: resultStar.left
                    anchors.rightMargin: Style.space(6)
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData.artist || "PLAY"
                    color: root.sand
                    opacity: 0.86
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                  }
                  SafeText {
                    id: resultStar
                    width: favoritable ? Style.space(34) : 0
                    anchors.right: parent.right
                    anchors.rightMargin: favoritable ? Style.space(5) : 0
                    anchors.verticalCenter: parent.verticalCenter
                    visible: favoritable
                    text: starred ? "★" : "☆"
                    color: starred ? root.adobe : root.turquoise
                    font.family: root.panelFont
                    font.pixelSize: Style.font.body
                    horizontalAlignment: Text.AlignHCenter
                  }
                  MouseArea {
                    id: resultMouse
                    anchors.fill: parent
                    anchors.rightMargin: favoritable ? Style.space(42) : 0
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.playProviderTrack(modelData)
                  }
                  MouseArea {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: Style.space(42)
                    visible: favoritable
                    enabled: visible && root.hostWidget && !root.hostWidget.searchFavoritesBusy
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.toggleSearchFavorite(modelData)
                  }
                }
              }
            }

            SafeText {
              visible: root.hostWidget && root.hostWidget.providerSearchAttempted
                && !root.hostWidget.providerBusy && root.hostWidget.providerResults.length === 0
              text: "No matching tracks or stations."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
            }

            SafeText {
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
              SafeText {
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
                SafeText {
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
                width: browserColumn.width
                height: Style.space(42)
                radius: Style.cornerRadius
                color: current ? root.tint(root.turquoise, 0.13)
                  : queueRowMouse.containsMouse ? root.tint(root.mutedSand, 0.07) : "transparent"
                border.width: 1
                border.color: current ? root.turquoise : root.tint(root.mutedSand, 0.18)
                SafeText {
                  x: Style.space(10)
                  width: parent.width - queueActions.width - Style.space(22)
                  anchors.verticalCenter: parent.verticalCenter
                  readonly property string displayTitle: current ? root.hostWidget.trackTitle : (modelData.title || modelData.path)
                  readonly property string displayArtist: current ? root.hostWidget.trackArtist : (modelData.artist || "")
                  text: (current ? "▶  " : (index + 1) + ".  ")
                    + displayTitle + (displayArtist ? "  ·  " + displayArtist : "")
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
                      SafeText {
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

            SafeText {
              visible: root.hostWidget && root.hostWidget.queueTracks.length === 0
              width: parent.width
              text: "Queue is empty. Choose a station, collection, files, or a folder."
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
            }
          }

          Column {
            id: libraryBody
            width: parent.width
            spacing: Style.space(8)
            visible: root.libraryTab === "library"
            readonly property var host: root.hostWidget
            readonly property bool available: host && host.libraryAvailable
            readonly property bool picking: host && host.libraryPickerOpen
            readonly property bool showing: available && !picking
            readonly property string view: host ? host.libraryView : "artists"
            readonly property bool topView: view === "artists" || view === "albums"
              || view === "playlists" || view === "folders"
            readonly property bool cardsView: view === "artists" || view === "albums"
              || view === "artistAlbums" || view === "playlists"
            readonly property bool tracksView: view === "tracks" || view === "search" || view === "playlistTracks"

            Row {
              width: parent.width
              spacing: Style.space(8)
              visible: libraryBody.showing && libraryBody.view !== "folders"
              Rectangle {
                width: parent.width - librarySearchButton.width - Style.space(8)
                height: Style.space(36)
                radius: Style.cornerRadius
                color: root.tint(root.night, 0.5)
                border.width: 1
                border.color: librarySearch.activeFocus ? root.turquoise : root.tint(root.sand, 0.34)
                TextInput {
                  id: librarySearch
                  maximumLength: 256
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(10)
                  verticalAlignment: TextInput.AlignVCenter
                  color: root.sand
                  selectionColor: root.adobe
                  selectedTextColor: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.bodySmall
                  clip: true
                  SafeText {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: librarySearch.text === "" && !librarySearch.activeFocus
                    text: "SEARCH TITLE, ARTIST OR ALBUM…"
                    color: root.sand
                    opacity: 0.82
                    font: librarySearch.font
                  }
                  Keys.onReturnPressed: {
                    if (root.hostWidget) root.hostWidget.searchLibrary(text)
                    root.returnToPanelKeys()
                  }
                  Keys.onEnterPressed: {
                    if (root.hostWidget) root.hostWidget.searchLibrary(text)
                    root.returnToPanelKeys()
                  }
                  Keys.onUpPressed: if (root.hostWidget) root.hostWidget.adjustVolume(2)
                  Keys.onDownPressed: if (root.hostWidget) root.hostWidget.adjustVolume(-2)
                  Keys.onEscapePressed: root.returnToPanelKeys()
                }
              }
              Rectangle {
                id: librarySearchButton
                width: Style.space(84)
                height: Style.space(36)
                radius: Style.cornerRadius
                color: root.tint(root.turquoise, 0.18)
                border.width: 1
                border.color: root.turquoise
                SafeText {
                  anchors.centerIn: parent
                  text: libraryBody.host && libraryBody.host.libraryBusy ? "WAIT…" : "SEARCH"
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    if (root.hostWidget) root.hostWidget.searchLibrary(librarySearch.text)
                    root.returnToPanelKeys()
                  }
                }
              }
            }

            Row {
              id: libraryChipRow
              width: parent.width
              spacing: Style.space(6)
              visible: libraryBody.showing && libraryBody.topView
              Repeater {
                model: [
                  { key: "artists", label: "ARTISTS" },
                  { key: "albums", label: "ALBUMS" },
                  { key: "playlists", label: "PLAYLISTS" },
                  { key: "folders", label: "FOLDERS" }
                ]
                Rectangle {
                  required property var modelData
                  readonly property bool selected: libraryBody.host
                    && libraryBody.host.libraryTop === modelData.key
                  width: Style.space(96)
                  height: Style.space(32)
                  radius: Style.cornerRadius
                  color: selected ? root.tint(root.turquoise, 0.16) : "transparent"
                  border.width: 1
                  border.color: selected ? root.turquoise : root.tint(root.mutedSand, 0.3)
                  SafeText {
                    anchors.centerIn: parent
                    text: modelData.label
                    color: selected ? root.sand : root.mutedSand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    font.bold: selected
                    font.letterSpacing: 0.8
                  }
                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.showLibraryTop(modelData.key)
                  }
                }
              }
              Rectangle {
                visible: libraryBody.view === "albums" && libraryBody.host
                  && libraryBody.host.librarySorts.length > 1
                width: Style.space(170)
                height: Style.space(32)
                radius: Style.cornerRadius
                color: sortMouse.containsMouse ? root.tint(root.adobe, 0.2) : root.tint(root.adobe, 0.1)
                border.width: 1
                border.color: root.adobe
                SafeText {
                  anchors.centerIn: parent
                  width: parent.width - Style.space(16)
                  text: libraryBody.host ? "SORT: " + libraryBody.host.librarySortLabel.toUpperCase() : ""
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  horizontalAlignment: Text.AlignHCenter
                  elide: Text.ElideRight
                }
                MouseArea {
                  id: sortMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.cycleLibrarySort()
                }
              }
            }

            Row {
              id: libraryTitleRow
              width: parent.width
              spacing: Style.space(8)
              visible: libraryBody.showing && !libraryBody.topView
              Rectangle {
                id: libraryBackButton
                width: Style.space(84)
                height: Style.space(34)
                radius: Style.cornerRadius
                color: backMouse.containsMouse ? root.tint(root.turquoise, 0.18) : "transparent"
                border.width: 1
                border.color: root.turquoise
                SafeText {
                  anchors.centerIn: parent
                  text: "‹ BACK"
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: backMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: if (root.hostWidget) root.hostWidget.libraryBack()
                }
              }
              SafeText {
                width: parent.width - libraryBackButton.width - libraryPlayAll.width
                  - librarySaveAlbum.width - parent.spacing * 3
                anchors.verticalCenter: parent.verticalCenter
                text: !libraryBody.host ? "" : libraryBody.view === "artistAlbums"
                  ? libraryBody.host.libraryArtistName.toUpperCase()
                  : libraryBody.view === "tracks"
                    ? libraryBody.host.libraryAlbumName.toUpperCase()
                    : libraryBody.view === "playlistTracks"
                      ? libraryBody.host.libraryPlaylistName.toUpperCase()
                      : "RESULTS FOR “" + libraryBody.host.librarySearchQuery.toUpperCase() + "”"
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.1
                elide: Text.ElideRight
                maximumLineCount: 1
              }
              Rectangle {
                id: libraryPlayAll
                visible: libraryBody.view === "tracks" || libraryBody.view === "playlistTracks"
                width: visible ? Style.space(112) : 0
                height: Style.space(34)
                radius: Style.cornerRadius
                color: playAllMouse.containsMouse ? root.tint(root.adobe, 0.28) : root.tint(root.adobe, 0.15)
                border.width: 1
                border.color: root.adobe
                SafeText {
                  anchors.centerIn: parent
                  text: libraryBody.view === "playlistTracks" ? "PLAY ALL" : "PLAY ALBUM"
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: playAllMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  enabled: libraryBody.host && libraryBody.host.libraryTracks.length > 0
                  onClicked: {
                    if (!root.hostWidget) return
                    if (libraryBody.view === "playlistTracks") root.hostWidget.playLibraryPlaylist(0)
                    else root.hostWidget.playLibraryAlbum(0)
                  }
                }
              }
              Rectangle {
                id: librarySaveAlbum
                visible: libraryBody.view === "tracks"
                width: visible ? Style.space(124) : 0
                height: Style.space(34)
                radius: Style.cornerRadius
                color: saveAlbumMouse.containsMouse ? root.tint(root.turquoise, 0.2) : root.tint(root.turquoise, 0.1)
                border.width: 1
                border.color: root.turquoise
                SafeText {
                  anchors.centerIn: parent
                  text: "SAVE ALBUM"
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: saveAlbumMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  enabled: libraryBody.host && libraryBody.host.libraryTracks.length > 0
                  onClicked: if (root.hostWidget)
                    root.hostWidget.openLibraryPicker(root.hostWidget.libraryTracks, root.hostWidget.libraryAlbumName)
                }
              }
            }

            Grid {
              id: libraryGrid
              width: parent.width
              columns: 2
              spacing: Style.space(7)
              visible: libraryBody.showing && libraryBody.cardsView
              Repeater {
                model: libraryBody.host ? libraryBody.host.libraryCards : []
                Rectangle {
                  id: libraryCard
                  required property var modelData
                  width: (libraryGrid.width - libraryGrid.spacing) / 2
                  height: Style.space(52)
                  radius: Style.cornerRadius
                  color: libraryCardMouse.containsMouse ? root.tint(root.adobe, 0.18) : root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: libraryCardMouse.containsMouse ? root.adobe : root.tint(root.sand, 0.3)
                  Column {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: Style.space(12)
                    anchors.rightMargin: Style.space(12)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(2)
                    SafeText {
                      width: parent.width
                      text: libraryCard.modelData.title.toUpperCase()
                      color: root.sand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideRight
                      maximumLineCount: 1
                    }
                    SafeText {
                      width: parent.width
                      visible: libraryCard.modelData.detail !== ""
                      text: libraryCard.modelData.detail
                      color: root.mutedSand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
                      maximumLineCount: 1
                    }
                  }
                  MouseArea {
                    id: libraryCardMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                      if (!root.hostWidget) return
                      var card = libraryCard.modelData
                      if (card.kind === "artist") root.hostWidget.openLibraryArtist(card.id, card.title)
                      else if (card.kind === "playlist") root.hostWidget.openLibraryPlaylist(card.id, card.title)
                      else root.hostWidget.openLibraryAlbum(card.id, card.title)
                    }
                  }
                }
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(5)
              visible: libraryBody.showing && libraryBody.tracksView
              Repeater {
                model: libraryBody.host ? libraryBody.host.libraryTracks : []
                Rectangle {
                  id: libraryRow
                  required property var modelData
                  required property int index
                  width: libraryBody.width
                  height: Style.space(38)
                  radius: Style.cornerRadius
                  color: libraryRowMouse.containsMouse ? root.tint(root.turquoise, 0.1) : "transparent"
                  border.width: 1
                  border.color: libraryRowMouse.containsMouse
                    ? root.tint(root.turquoise, 0.55) : root.tint(root.sand, 0.18)
                  SafeText {
                    id: libraryRowNumber
                    width: libraryBody.view === "search" ? 0 : Style.space(34)
                    visible: libraryBody.view !== "search"
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(10)
                    anchors.verticalCenter: parent.verticalCenter
                    text: libraryBody.view === "playlistTracks"
                      ? String(libraryRow.index + 1)
                      : ("0" + (libraryRow.modelData.track_number || libraryRow.index + 1)).slice(-2)
                    color: root.mutedSand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                  }
                  SafeText {
                    anchors.left: libraryRowNumber.right
                    anchors.right: libraryBody.view === "tracks" ? libraryRowDetail.left : libraryRowLinks.left
                    anchors.leftMargin: libraryBody.view === "search" ? Style.space(10) : Style.space(4)
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    text: libraryRow.modelData.title || libraryRow.modelData.path
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    elide: Text.ElideRight
                    maximumLineCount: 1
                  }
                  SafeText {
                    id: libraryRowStar
                    width: Style.space(34)
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(5)
                    anchors.verticalCenter: parent.verticalCenter
                    readonly property bool hearted: libraryBody.host && libraryBody.host.isLibraryFavorite(libraryRow.modelData)
                    text: hearted ? "★" : "☆"
                    color: hearted ? root.adobe : root.turquoise
                    font.family: root.panelFont
                    font.pixelSize: Style.font.body
                    horizontalAlignment: Text.AlignHCenter
                  }
                  SafeText {
                    id: libraryRowAdd
                    width: Style.space(34)
                    anchors.right: libraryRowStar.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: "＋"
                    color: root.turquoise
                    font.family: root.panelFont
                    font.pixelSize: Style.font.body
                    horizontalAlignment: Text.AlignHCenter
                  }
                  SafeText {
                    id: libraryRowDetail
                    width: parent.width * 0.2
                    anchors.right: libraryRowAdd.left
                    anchors.rightMargin: Style.space(6)
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.clock(libraryRow.modelData.duration_secs)
                    visible: libraryBody.view === "tracks" && text !== "0:00"
                    color: root.sand
                    opacity: 0.86
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    horizontalAlignment: Text.AlignRight
                    elide: Text.ElideRight
                    maximumLineCount: 1
                  }
                  // The artist and the album of a result or of a playlist track
                  // open their own pages, when CLIAMP gave the track their IDs.
                  Row {
                    id: libraryRowLinks
                    visible: libraryBody.view !== "tracks"
                    width: visible ? parent.width * 0.44 : 0
                    anchors.right: libraryRowAdd.left
                    anchors.rightMargin: Style.space(6)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(5)
                    z: 2
                    readonly property bool artistLinked: libraryBody.host
                      && libraryBody.host.trackLink(libraryRow.modelData, "library.artist") !== ""
                    readonly property bool albumLinked: libraryBody.host
                      && libraryBody.host.trackLink(libraryRow.modelData, "library.album") !== ""
                    SafeText {
                      id: libraryArtistLink
                      width: Math.min(implicitWidth, libraryRowLinks.width * 0.55)
                      visible: libraryRow.modelData.artist !== ""
                      text: libraryRow.modelData.artist
                      color: libraryRowLinks.artistLinked ? root.turquoise : root.sand
                      opacity: libraryRowLinks.artistLinked ? 1 : 0.86
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      font.underline: libraryArtistMouse.containsMouse
                      elide: Text.ElideRight
                      maximumLineCount: 1
                      MouseArea {
                        id: libraryArtistMouse
                        anchors.fill: parent
                        enabled: libraryRowLinks.artistLinked
                        hoverEnabled: true
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: if (root.hostWidget) root.hostWidget.openTrackArtist(libraryRow.modelData)
                      }
                    }
                    SafeText {
                      id: libraryLinkDot
                      visible: libraryArtistLink.visible && libraryAlbumLink.visible
                      text: "·"
                      color: root.mutedSand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                    }
                    SafeText {
                      id: libraryAlbumLink
                      width: Math.max(0, libraryRowLinks.width - (libraryArtistLink.visible ? libraryArtistLink.width : 0)
                        - (libraryLinkDot.visible ? libraryLinkDot.implicitWidth : 0) - libraryRowLinks.spacing * 2)
                      visible: libraryRow.modelData.album !== ""
                      text: libraryRow.modelData.album
                      color: libraryRowLinks.albumLinked ? root.turquoise : root.sand
                      opacity: libraryRowLinks.albumLinked ? 1 : 0.86
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      font.underline: libraryAlbumMouse.containsMouse
                      elide: Text.ElideRight
                      maximumLineCount: 1
                      MouseArea {
                        id: libraryAlbumMouse
                        anchors.fill: parent
                        enabled: libraryRowLinks.albumLinked
                        hoverEnabled: true
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: if (root.hostWidget) root.hostWidget.openTrackAlbum(libraryRow.modelData)
                      }
                    }
                  }
                  MouseArea {
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: Style.space(40)
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget) root.hostWidget.toggleTrackFavorite(libraryRow.modelData)
                  }
                  MouseArea {
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(40)
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    width: Style.space(40)
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (root.hostWidget)
                      root.hostWidget.openLibraryPicker([libraryRow.modelData], libraryRow.modelData.title)
                  }
                  MouseArea {
                    id: libraryRowMouse
                    anchors.fill: parent
                    anchors.rightMargin: Style.space(80)
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                      if (!root.hostWidget) return
                      if (libraryBody.view === "tracks") root.hostWidget.playLibraryAlbum(libraryRow.index)
                      else if (libraryBody.view === "playlistTracks") root.hostWidget.playLibraryPlaylist(libraryRow.index)
                      else root.hostWidget.playProviderTrack(libraryRow.modelData)
                    }
                  }
                }
              }
            }

            Rectangle {
              visible: libraryBody.showing && libraryBody.cardsView && libraryBody.host
                && libraryBody.host.libraryHasMore
              width: parent.width
              height: Style.space(40)
              radius: Style.cornerRadius
              color: moreMouse.containsMouse ? root.tint(root.turquoise, 0.2) : root.tint(root.turquoise, 0.1)
              border.width: 1
              border.color: root.turquoise
              SafeText {
                anchors.centerIn: parent
                text: libraryBody.host && libraryBody.host.libraryBusy ? "LOADING…" : "LOAD MORE"
                color: root.sand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              MouseArea {
                id: moreMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: if (root.hostWidget && !root.hostWidget.libraryBusy) root.hostWidget.loadLibraryPage(false)
              }
            }

            Column {
              id: libraryFolders
              width: parent.width
              spacing: Style.space(8)
              visible: libraryBody.showing && libraryBody.view === "folders"
              SafeText {
                text: "MUSIC FOLDERS"
                color: root.adobe
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
              }
              SafeText {
                visible: libraryBody.host && libraryBody.host.libraryRootsLoaded
                  && libraryBody.host.libraryRoots.length === 0
                width: parent.width
                text: "No music folder yet. Add the folder where your music lives, such as a mounted NAS share. CLIAMP reads its tags in the background, so a large collection can take several minutes the first time."
                color: root.sand
                opacity: 0.92
                font.family: root.panelFont
                font.pixelSize: Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
              Repeater {
                model: libraryBody.host ? libraryBody.host.libraryRoots : []
                Rectangle {
                  id: folderRow
                  required property var modelData
                  width: libraryFolders.width
                  height: Style.space(48)
                  radius: Style.cornerRadius
                  color: root.tint(root.night, 0.46)
                  border.width: 1
                  border.color: modelData.available ? root.tint(root.sand, 0.3) : root.adobe
                  Column {
                    anchors.left: parent.left
                    anchors.right: removeFolder.left
                    anchors.leftMargin: Style.space(12)
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(2)
                    SafeText {
                      width: parent.width
                      text: folderRow.modelData.path
                      color: root.sand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideMiddle
                      maximumLineCount: 1
                    }
                    SafeText {
                      width: parent.width
                      text: (folderRow.modelData.tracks === 1 ? "1 TRACK" : folderRow.modelData.tracks + " TRACKS")
                        + (folderRow.modelData.available ? "" : "  ·  NOT READABLE, SHOWING THE SAVED INDEX")
                      color: folderRow.modelData.available ? root.mutedSand : root.adobe
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
                      maximumLineCount: 1
                    }
                  }
                  Rectangle {
                    id: removeFolder
                    anchors.right: parent.right
                    anchors.rightMargin: Style.space(8)
                    anchors.verticalCenter: parent.verticalCenter
                    width: Style.space(84)
                    height: Style.space(30)
                    radius: Style.cornerRadius
                    color: removeMouse.containsMouse ? root.tint(root.adobe, 0.25) : "transparent"
                    border.width: 1
                    border.color: root.adobe
                    SafeText {
                      anchors.centerIn: parent
                      text: "REMOVE"
                      color: root.sand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    MouseArea {
                      id: removeMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: if (root.hostWidget) root.hostWidget.removeLibraryRoot(folderRow.modelData.path)
                    }
                  }
                }
              }
              Row {
                id: folderActions
                width: parent.width
                spacing: Style.space(8)
                Repeater {
                  model: [
                    { action: "add", label: "ADD MUSIC FOLDER…" },
                    { action: "scan", label: "RESCAN" }
                  ]
                  Rectangle {
                    required property var modelData
                    // Folders need no CLIAMP: only the library helper.
                    readonly property bool available: root.hostWidget
                      && (modelData.action === "add" || root.hostWidget.libraryRoots.length > 0)
                    width: (folderActions.width - folderActions.spacing) / 2
                    height: Style.space(44)
                    radius: Style.cornerRadius
                    color: folderActionMouse.containsMouse
                      ? root.tint(root.adobe, 0.22) : root.tint(root.adobe, 0.12)
                    border.width: 1
                    border.color: root.tint(root.adobe, available ? 1 : 0.3)
                    SafeText {
                      anchors.centerIn: parent
                      text: modelData.label
                      color: available ? root.sand : root.mutedSand
                      opacity: available ? 1 : 0.5
                      font.family: root.panelFont
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                    MouseArea {
                      id: folderActionMouse
                      anchors.fill: parent
                      enabled: parent.available
                      hoverEnabled: true
                      cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                      onClicked: {
                        if (!root.hostWidget) return
                        if (modelData.action === "add") root.launchAudioPicker("music")
                        else root.hostWidget.rescanLibrary()
                      }
                    }
                  }
                }
              }
              SafeText {
                visible: libraryBody.host && libraryBody.host.libraryScanning
                width: parent.width
                text: libraryBody.host ? libraryBody.host.libraryScanSummary : ""
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
              SafeText {
                width: parent.width
                text: "Removing a folder only takes it out of the Library. Your files are not touched."
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
              }
            }

            Column {
              id: libraryPicker
              width: parent.width
              spacing: Style.space(8)
              visible: libraryBody.available && libraryBody.picking
              SafeText {
                width: parent.width
                text: libraryBody.host
                  ? "ADD " + (libraryBody.host.libraryPickerTracks.length === 1
                      ? "“" + libraryBody.host.libraryPickerLabel.toUpperCase() + "”"
                      : libraryBody.host.libraryPickerTracks.length + " TRACKS")
                    + " TO A PLAYLIST"
                  : ""
                color: root.turquoise
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.1
                elide: Text.ElideRight
                maximumLineCount: 1
              }
              Row {
                width: parent.width
                spacing: Style.space(8)
                Rectangle {
                  width: parent.width - createPlaylistButton.width - Style.space(8)
                  height: Style.space(36)
                  radius: Style.cornerRadius
                  color: root.tint(root.night, 0.5)
                  border.width: 1
                  border.color: playlistNameInput.activeFocus ? root.turquoise : root.tint(root.sand, 0.34)
                  TextInput {
                    id: playlistNameInput
                    maximumLength: 128
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(10)
                    anchors.rightMargin: Style.space(10)
                    verticalAlignment: TextInput.AlignVCenter
                    color: root.sand
                    selectionColor: root.adobe
                    selectedTextColor: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.bodySmall
                    clip: true
                    SafeText {
                      anchors.verticalCenter: parent.verticalCenter
                      visible: playlistNameInput.text === "" && !playlistNameInput.activeFocus
                      text: "NAME A NEW PLAYLIST…"
                      color: root.sand
                      opacity: 0.82
                      font: playlistNameInput.font
                    }
                    Keys.onReturnPressed: {
                      if (root.hostWidget) root.hostWidget.createLibraryPlaylist(text)
                      text = ""
                      root.returnToPanelKeys()
                    }
                    Keys.onEnterPressed: {
                      if (root.hostWidget) root.hostWidget.createLibraryPlaylist(text)
                      text = ""
                      root.returnToPanelKeys()
                    }
                    Keys.onUpPressed: if (root.hostWidget) root.hostWidget.adjustVolume(2)
                    Keys.onDownPressed: if (root.hostWidget) root.hostWidget.adjustVolume(-2)
                    Keys.onEscapePressed: root.returnToPanelKeys()
                  }
                }
                Rectangle {
                  id: createPlaylistButton
                  width: Style.space(96)
                  height: Style.space(36)
                  radius: Style.cornerRadius
                  color: createMouse.containsMouse ? root.tint(root.adobe, 0.28) : root.tint(root.adobe, 0.15)
                  border.width: 1
                  border.color: root.adobe
                  SafeText {
                    anchors.centerIn: parent
                    text: "CREATE"
                    color: root.sand
                    font.family: root.panelFont
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                  MouseArea {
                    id: createMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                      if (root.hostWidget) root.hostWidget.createLibraryPlaylist(playlistNameInput.text)
                      playlistNameInput.text = ""
                      root.returnToPanelKeys()
                    }
                  }
                }
              }
              SafeText {
                visible: libraryBody.host && libraryBody.host.libraryTargetPlaylists.length > 0
                text: "OR PICK ONE OF YOUR PLAYLISTS"
                color: root.mutedSand
                font.family: root.panelFont
                font.pixelSize: Style.font.caption
                font.letterSpacing: 1
              }
              Grid {
                id: pickerGrid
                width: parent.width
                columns: 2
                spacing: Style.space(7)
                Repeater {
                  model: libraryBody.host ? libraryBody.host.libraryTargetPlaylists : []
                  Rectangle {
                    id: pickerCard
                    required property var modelData
                    width: (pickerGrid.width - pickerGrid.spacing) / 2
                    height: Style.space(44)
                    radius: Style.cornerRadius
                    color: pickerMouse2.containsMouse ? root.tint(root.adobe, 0.18) : root.tint(root.night, 0.46)
                    border.width: 1
                    border.color: pickerMouse2.containsMouse ? root.adobe : root.tint(root.sand, 0.3)
                    SafeText {
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.leftMargin: Style.space(12)
                      anchors.rightMargin: Style.space(12)
                      anchors.verticalCenter: parent.verticalCenter
                      text: pickerCard.modelData.name.toUpperCase()
                      color: root.sand
                      font.family: root.panelFont
                      font.pixelSize: Style.font.bodySmall
                      elide: Text.ElideRight
                      maximumLineCount: 1
                    }
                    MouseArea {
                      id: pickerMouse2
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: if (root.hostWidget)
                        root.hostWidget.addToLibraryPlaylist(pickerCard.modelData.id, pickerCard.modelData.name)
                    }
                  }
                }
              }
              Rectangle {
                width: Style.space(110)
                height: Style.space(34)
                radius: Style.cornerRadius
                color: cancelMouse.containsMouse ? root.tint(root.turquoise, 0.18) : "transparent"
                border.width: 1
                border.color: root.turquoise
                SafeText {
                  anchors.centerIn: parent
                  text: "CANCEL"
                  color: root.sand
                  font.family: root.panelFont
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
                MouseArea {
                  id: cancelMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    if (root.hostWidget) root.hostWidget.closeLibraryPicker()
                    playlistNameInput.text = ""
                    root.returnToPanelKeys()
                  }
                }
              }
            }

            SafeText {
              visible: libraryBody.showing && libraryBody.host && libraryBody.host.libraryBusy
                && !libraryBody.host.libraryLoaded && libraryBody.view !== "folders"
              text: "LOADING…"
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
            }

            SafeText {
              visible: libraryBody.showing && libraryBody.host && libraryBody.host.libraryLoaded
                && !libraryBody.host.libraryBusy
                && ((libraryBody.cardsView && libraryBody.host.libraryCards.length === 0)
                  || (libraryBody.tracksView && libraryBody.host.libraryTracks.length === 0))
              width: parent.width
              text: libraryBody.view === "search" ? "No matching tracks."
                : libraryBody.view === "tracks" ? "This album has no tracks."
                : libraryBody.view === "playlistTracks" ? "This playlist is empty."
                : libraryBody.view === "playlists"
                  ? "No playlists yet. Use ＋ on a track, or Save Album, to make one."
                  : "Nothing is indexed yet. The first scan of a large share can take several minutes; open the Folders view to watch it."
              color: root.sand
              opacity: 0.88
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            SafeText {
              visible: libraryBody.host && libraryBody.host.libraryNotice !== ""
              width: parent.width
              text: libraryBody.host ? libraryBody.host.libraryNotice : ""
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }

            SafeText {
              visible: libraryBody.host && libraryBody.host.libraryError !== ""
              width: parent.width
              text: libraryBody.host ? libraryBody.host.libraryError : ""
              color: root.adobe
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.libraryTab === "more"

            SafeText {
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
                  width: Math.min(browserColumn.width, deviceName.implicitWidth + Style.space(24))
                  height: Style.space(30)
                  radius: height / 2
                  color: modelData.active ? root.tint(root.turquoise, 0.18) : "transparent"
                  border.width: 1
                  border.color: modelData.active ? root.turquoise : root.tint(root.mutedSand, 0.22)
                  SafeText {
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

            SafeText {
              visible: root.hostWidget && root.hostWidget.audioDevices.length === 0
              text: "No switchable devices reported; CLIAMP is using the system default."
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
            }

            SafeText {
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
                width: browserColumn.width
                height: Style.space(34)
                radius: Style.cornerRadius
                color: historyMouse.containsMouse ? root.tint(root.turquoise, 0.09) : "transparent"
                SafeText {
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
            SafeText {
              visible: root.hostWidget && root.hostWidget.historyItems.length === 0
              width: parent.width
              text: "No recently played tracks yet."
              color: root.mutedSand
              font.family: root.panelFont
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignHCenter
              wrapMode: Text.WordWrap
            }

            SafeText {
              text: "LYRICS"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            SafeText {
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
                controls.push({ title: "VIS " + (root.hostWidget ? root.hostWidget.panelVisualizerName.toUpperCase() : "MESA"), active: false, action: "visualizer" })
                controls.push({ title: "MONO", active: root.hostWidget && root.hostWidget.mono, action: "mono" })
                return controls
              }
              Rectangle {
                required property var modelData
                width: (browserColumn.width - utilityRow.spacing * (utilityRepeater.count - 1)) / utilityRepeater.count
                height: Style.space(34)
                radius: Style.cornerRadius
                color: modelData.active ? root.tint(root.turquoise, 0.17)
                  : utilityMouse.containsMouse ? root.tint(root.adobe, 0.14) : "transparent"
                border.width: 1
                border.color: modelData.active ? root.turquoise : root.tint(root.mutedSand, 0.2)
                SafeText {
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
                maximumLength: 4096
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
                SafeText {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: queueInput.text === "" && !queueInput.activeFocus
                  text: "PASTE A TRACK PATH OR STREAM URL…"
                  color: root.mutedSand
                  font: queueInput.font
                }
                Keys.onReturnPressed: {
                  if (root.hostWidget) root.hostWidget.queueMedia(text)
                  text = ""
                  root.returnToPanelKeys()
                }
                Keys.onUpPressed: if (root.hostWidget) root.hostWidget.adjustVolume(2)
                Keys.onDownPressed: if (root.hostWidget) root.hostWidget.adjustVolume(-2)
                Keys.onEscapePressed: root.returnToPanelKeys()
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
              SafeText {
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
                  root.returnToPanelKeys()
                }
              }
            }
          }

          Column {
            id: pickerBlock
            width: parent.width
            spacing: Style.space(6)
            visible: root.libraryTab === "queue"
            SafeText {
              text: "ADD AUDIO FROM THIS COMPUTER"
              color: root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
            }
            Row {
              id: fileActionRow
              width: parent.width
              spacing: Style.space(8)
              Repeater {
                model: [
                  { mode: "files", label: "CHOOSE FILES…" },
                  { mode: "folder", label: "ADD FOLDER…" }
                ]
                Rectangle {
                  required property var modelData
                  readonly property bool available: root.hostWidget && root.hostWidget.sessionReady
                  width: (fileActionRow.width - fileActionRow.spacing) / 2
                  height: Style.space(38)
                  radius: Style.cornerRadius
                  color: pickerMouse.containsMouse
                    ? root.tint(root.adobe, 0.22) : root.tint(root.adobe, 0.12)
                  border.width: 1
                  border.color: root.tint(root.adobe, available ? 1 : 0.3)
                  SafeText {
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
            SafeText {
              width: parent.width
              visible: root.filePickerStatus !== ""
              text: root.filePickerStatus
              color: root.filePickerFailed ? root.adobe : root.turquoise
              font.family: root.panelFont
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          SafeText {
            visible: root.hostWidget && root.hostWidget.errorText !== ""
            width: parent.width
            text: root.hostWidget ? root.hostWidget.errorText : ""
            color: root.adobe
            font.family: root.panelFont
            font.pixelSize: Style.font.bodySmall
            horizontalAlignment: Text.AlignHCenter
          }

                }
              }
            }
          }

          SafeText {
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

  Component.onDestruction: {
    root.destroying = true
    pickerLaunch.stop()
    audioPickerWatchdog.stop()
    audioPickerKill.stop()
    // Teardown cannot rely on a Timer that is being destroyed. The helper's
    // guardian observes its death and kills the complete Zenity process group.
    if (audioPicker.running) audioPicker.signal(9)
  }
}
