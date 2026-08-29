import QtQuick
import Quickshell.Io

Item {
  id: root
  property int fps: 18
  property var bands: []
  property string mode: ""

  function parseLine(line) {
    if (!line) return
    try {
      var response = JSON.parse(line)
      if (!response || !response.ok) return
      if (response.bands) root.bands = response.bands
      if (response.visualizer) root.mode = response.visualizer
    } catch (e) {
      // CLIAMP may disappear between frames; the retry timer reconnects.
    }
  }

  Process {
    id: stream
    command: ["cliamp", "visstream", "--fps", String(root.fps)]
    running: false
    stdout: SplitParser {
      splitMarker: "\n"
      onRead: function(line) { root.parseLine(line) }
    }
  }

  Component.onCompleted: if (enabled) stream.running = true
  onEnabledChanged: stream.running = enabled

  Timer {
    interval: 2000
    running: root.enabled && !stream.running
    repeat: true
    onTriggered: stream.running = true
  }
}
