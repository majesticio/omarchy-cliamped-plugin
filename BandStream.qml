import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root
  property int fps: 18
  property var bands: []
  property string mode: ""
  property int failureCount: 0
  property bool shuttingDown: false
  property double streamStartedAt: 0
  readonly property var processEnvironment: ({
    "PATH": "/usr/bin",
    "HOME": Quickshell.env("HOME"),
    "XDG_CONFIG_HOME": Quickshell.env("XDG_CONFIG_HOME"),
    "XDG_RUNTIME_DIR": Quickshell.env("XDG_RUNTIME_DIR"),
    "DBUS_SESSION_BUS_ADDRESS": Quickshell.env("DBUS_SESSION_BUS_ADDRESS"),
    "WAYLAND_DISPLAY": Quickshell.env("WAYLAND_DISPLAY"),
    "DISPLAY": Quickshell.env("DISPLAY"),
    "LANG": Quickshell.env("LANG") || "C.UTF-8",
    "PIPEWIRE_REMOTE": Quickshell.env("PIPEWIRE_REMOTE"),
    "PULSE_SERVER": Quickshell.env("PULSE_SERVER")
  })

  function helperPath() {
    return String(Qt.resolvedUrl("cliamped_process.py")).replace(/^file:\/\//, "")
  }

  function startStream() {
    if (!root.enabled || root.shuttingDown || stream.running || root.failureCount >= 6) return
    stream.command = ["/usr/bin/python3", "-I", root.helperPath(), "visstream",
      String(Math.max(1, Math.min(30, root.fps)))]
    root.streamStartedAt = Date.now()
    stream.launchPending = true
    stream.running = true
  }

  function handleStreamExit() {
    root.bands = []
    if (!root.enabled || root.shuttingDown) return
    root.failureCount = Math.min(6, root.failureCount + 1)
    if (root.failureCount >= 6) return
    restartTimer.interval = Math.min(30000, 1000 * Math.pow(2, root.failureCount - 1))
    restartTimer.restart()
  }

  function parseLine(line) {
    if (!line) return
    try {
      var response = JSON.parse(line)
      if (!response || !response.ok) return
      if (!Array.isArray(response.bands) || response.bands.length !== 10) return
      var safeBands = []
      for (var i = 0; i < 10; ++i) {
        if (typeof response.bands[i] !== "number") return
        var value = response.bands[i]
        if (!isFinite(value) || value < 0 || value > 1) return
        safeBands.push(value)
      }
      root.bands = safeBands
      if (typeof response.visualizer === "string") root.mode = response.visualizer.slice(0, 32)
      if (Date.now() - root.streamStartedAt >= 30000) root.failureCount = 0
    } catch (e) {
      // The supervisor rejects malformed frames; retain the last trusted frame.
    }
  }

  Process {
    id: stream
    property bool launchPending: false
    command: ["/usr/bin/python3", "-I", root.helperPath(), "visstream", String(root.fps)]
    clearEnvironment: true
    environment: root.processEnvironment
    running: false
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { root.parseLine(line) }
    }
    onStarted: launchPending = false
    onRunningChanged: {
      if (!running && launchPending) {
        launchPending = false
        root.handleStreamExit()
      }
    }
    onExited: function(_exitCode) {
      launchPending = false
      root.handleStreamExit()
    }
  }

  Component.onCompleted: root.startStream()
  Component.onDestruction: {
    root.shuttingDown = true
    restartTimer.stop()
    // The supervisor guardian tears down CLIAMP's complete process group.
    if (stream.running) stream.signal(9)
  }
  onEnabledChanged: {
    if (enabled) {
      root.shuttingDown = false
      root.failureCount = 0
      root.startStream()
    }
    else {
      root.shuttingDown = true
      restartTimer.stop()
      root.bands = []
      if (stream.running) {
        stream.signal(15)
        killTimer.restart()
      }
    }
  }

  Timer {
    id: restartTimer
    interval: 1000
    repeat: false
    onTriggered: root.startStream()
  }

  Timer {
    id: killTimer
    interval: 2500
    repeat: false
    onTriggered: if (!root.enabled && stream.running) stream.signal(9)
  }
}
