import QtQuick

Item {
  id: root
  property var bands: []
  property bool playing: false
  property color sand: "#f2dfc8"
  property color turquoise: "#55aaa6"
  property color adobe: "#c8784f"
  property color sky: "#181b23"
  property var peaks: []
  readonly property int columnCount: 32
  readonly property real horizonY: height * 0.72
  readonly property color skyMid: Qt.tint(sky, Qt.rgba(turquoise.r, turquoise.g, turquoise.b, 0.10))
  readonly property color skyLow: Qt.tint(sky, Qt.rgba(adobe.r, adobe.g, adobe.b, 0.14))

  function sample(column) {
    if (!bands || bands.length === 0) return 0.025
    var position = column * (bands.length - 1) / Math.max(1, columnCount - 1)
    var lower = Math.floor(position)
    var upper = Math.min(bands.length - 1, lower + 1)
    var mix = position - lower
    var raw = Number(bands[lower] || 0) * (1 - mix) + Number(bands[upper] || 0) * mix
    return Math.max(0.018, Math.min(1, Math.sqrt(Math.max(0, raw))))
  }

  function blend(a, b, amount) {
    var t = Math.max(0, Math.min(1, amount))
    return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t,
      a.b + (b.b - a.b) * t, 1)
  }

  function frequencyColor(column, intensity) {
    var p = column / Math.max(1, columnCount - 1)
    var base = p < 0.5 ? blend(turquoise, sand, p * 2)
      : blend(sand, adobe, (p - 0.5) * 2)
    return blend(base, sand, Math.max(0, intensity - 0.72) * 0.55)
  }

  function averageEnergy() {
    if (!bands || !bands.length) return 0
    var sum = 0
    for (var i = 0; i < bands.length; ++i) sum += Number(bands[i] || 0)
    return Math.min(1, sum / bands.length)
  }

  Rectangle {
    anchors.fill: parent
    radius: 4
    gradient: Gradient {
      orientation: Gradient.Vertical
      GradientStop { position: 0; color: root.sky }
      GradientStop { position: 0.62; color: root.skyMid }
      GradientStop { position: 1; color: root.skyLow }
    }
  }

  Rectangle {
    anchors.centerIn: parent
    width: parent.width * (0.56 + root.averageEnergy() * 0.36)
    height: parent.height * (0.34 + root.averageEnergy() * 0.42)
    radius: width / 2
    color: root.turquoise
    opacity: root.playing ? 0.025 + root.averageEnergy() * 0.09 : 0
    Behavior on width { NumberAnimation { duration: 90 } }
    Behavior on height { NumberAnimation { duration: 90 } }
  }

  Repeater {
    model: 13
    Rectangle {
      required property int index
      x: ((index * 79 + 23) % 97) / 97 * root.width
      y: ((index * 47 + 11) % 53) / 53 * root.height * 0.45
      width: index % 3 === 0 ? 2 : 1
      height: width
      radius: width / 2
      color: index % 4 === 0 ? root.turquoise : root.sand
      opacity: root.playing ? 0.32 + (index % 4) * 0.1 : 0.16

      SequentialAnimation on opacity {
        running: root.playing
        loops: Animation.Infinite
        NumberAnimation { to: 0.16; duration: 700 + index * 43 }
        NumberAnimation { to: 0.7; duration: 900 + index * 31 }
      }
    }
  }

  Canvas {
    id: mesaCanvas
    anchors.fill: parent
    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      ctx.beginPath()
      ctx.moveTo(0, root.horizonY + 4)
      ctx.lineTo(width * 0.08, root.horizonY - 5)
      ctx.lineTo(width * 0.18, root.horizonY - 1)
      ctx.lineTo(width * 0.31, root.horizonY - 14)
      ctx.lineTo(width * 0.43, root.horizonY - 3)
      ctx.lineTo(width * 0.57, root.horizonY - 8)
      ctx.lineTo(width * 0.71, root.horizonY - 2)
      ctx.lineTo(width * 0.86, root.horizonY - 11)
      ctx.lineTo(width, root.horizonY + 2)
      ctx.lineTo(width, height)
      ctx.lineTo(0, height)
      ctx.closePath()
      ctx.globalAlpha = 0.3
      ctx.fillStyle = root.adobe
      ctx.fill()
      ctx.beginPath()
      ctx.moveTo(0, root.horizonY + 7)
      ctx.lineTo(width * 0.25, root.horizonY + 1)
      ctx.lineTo(width * 0.48, root.horizonY + 8)
      ctx.lineTo(width * 0.72, root.horizonY + 2)
      ctx.lineTo(width, root.horizonY + 9)
      ctx.lineTo(width, height)
      ctx.lineTo(0, height)
      ctx.closePath()
      ctx.globalAlpha = 0.7
      ctx.fillStyle = root.sky
      ctx.fill()
      ctx.globalAlpha = 1
    }
  }

  Canvas {
    id: traceCanvas
    anchors.fill: parent
    opacity: root.playing ? 0.78 : 0.18
    onPaint: {
      var ctx = getContext("2d")
      ctx.clearRect(0, 0, width, height)
      if (!root.bands || !root.bands.length) return
      var left = 12
      var usable = width - 24
      ctx.lineWidth = 1.4
      ctx.beginPath()
      for (var i = 0; i < root.columnCount; ++i) {
        var x = left + i * usable / (root.columnCount - 1)
        var y = root.horizonY - root.sample(i) * height * 0.61 - 5
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.strokeStyle = root.sand
      ctx.globalAlpha = 0.62
      ctx.stroke()
      ctx.lineWidth = 3.5
      ctx.strokeStyle = root.turquoise
      ctx.globalAlpha = 0.13
      ctx.stroke()
      ctx.globalAlpha = 1
    }
  }

  Connections {
    target: root
    function onBandsChanged() { traceCanvas.requestPaint() }
    function onWidthChanged() { mesaCanvas.requestPaint(); traceCanvas.requestPaint() }
    function onHeightChanged() { mesaCanvas.requestPaint(); traceCanvas.requestPaint() }
    function onAdobeChanged() { mesaCanvas.requestPaint() }
    function onSkyChanged() { mesaCanvas.requestPaint() }
  }

  Timer {
    interval: 55
    running: root.visible
    repeat: true
    onTriggered: {
      var next = root.peaks && root.peaks.length === root.columnCount
        ? root.peaks.slice() : new Array(root.columnCount).fill(0)
      var changed = false
      for (var i = 0; i < root.columnCount; ++i) {
        var value = root.sample(i)
        var updated = value >= next[i] ? value : Math.max(0, next[i] - 0.026)
        if (updated !== next[i]) changed = true
        next[i] = updated
      }
      if (changed) root.peaks = next
    }
  }

  Row {
    id: spectrum
    x: 12
    width: parent.width - 24
    height: parent.height
    spacing: 3

    Repeater {
      model: root.columnCount
      Item {
        required property int index
        width: (spectrum.width - spectrum.spacing * (root.columnCount - 1)) / root.columnCount
        height: spectrum.height
        readonly property real value: root.sample(index)
        readonly property real peakValue: root.peaks.length > index ? root.peaks[index] : value
        readonly property real barHeight: Math.max(2, value * root.height * 0.58)

        Rectangle {
          width: parent.width
          height: parent.barHeight
          y: root.horizonY - height
          radius: width / 2
          opacity: root.playing ? 0.92 : 0.28
          color: root.frequencyColor(index, parent.value)
          Behavior on height { NumberAnimation { duration: 65; easing.type: Easing.OutCubic } }
          Behavior on color { ColorAnimation { duration: 100 } }
        }

        Rectangle {
          width: parent.width
          height: Math.max(1, parent.barHeight * 0.26)
          y: root.horizonY + 2
          radius: width / 2
          color: root.frequencyColor(index, parent.value)
          opacity: root.playing ? 0.28 : 0.06
          Behavior on height { NumberAnimation { duration: 90 } }
        }

        Rectangle {
          width: Math.max(2, parent.width)
          height: 2
          radius: 1
          y: root.horizonY - Math.max(2, parent.peakValue * root.height * 0.58) - 4
          color: root.frequencyColor(index, parent.peakValue)
          opacity: root.playing ? 0.9 : 0.15
          Behavior on y { NumberAnimation { duration: 115; easing.type: Easing.OutQuad } }
        }
      }
    }
  }

  Rectangle {
    id: scanline
    x: 8
    width: parent.width - 16
    height: 1
    color: root.turquoise
    opacity: root.playing ? 0.2 : 0

    NumberAnimation on y {
      running: root.playing
      loops: Animation.Infinite
      from: 5
      to: root.height - 5
      duration: 2600
      easing.type: Easing.Linear
    }
  }

  Rectangle {
    x: 10
    y: root.horizonY
    width: parent.width - 20
    height: 1
    color: root.adobe
    opacity: 0.38
  }

  Row {
    x: 12
    y: parent.height - 12
    width: parent.width - 24
    Repeater {
      model: ["SUB", "BASS", "LOW", "MID", "HIGH", "AIR"]
      Text {
        required property string modelData
        required property int index
        width: parent.width / 6
        text: modelData
        color: root.frequencyColor(index * (root.columnCount - 1) / 5, 0.5)
        opacity: 0.55
        font.pixelSize: 7
        horizontalAlignment: Text.AlignHCenter
      }
    }
  }
}
