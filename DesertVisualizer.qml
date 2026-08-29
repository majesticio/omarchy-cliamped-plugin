import QtQuick

Item {
  id: root
  property var bands: []
  property bool playing: false
  property int mode: 0
  property bool compact: false
  property color sand: "#f2dfc8"
  property color turquoise: "#55aaa6"
  property color adobe: "#c8784f"
  property color sky: "#181b23"
  property real phase: 0
  property var reactiveBands: []
  property var previousRawBands: []
  property var pulseHistory: []
  property real adaptivePeak: 0.12
  property real sensitivity: 1.05
  readonly property int sampleCount: 32
  signal cycleRequested()

  function updateReactiveBands() {
    if (!bands || !bands.length) {
      reactiveBands = []
      previousRawBands = []
      return
    }
    var activeCount = bands.length
    while (activeCount > 1 && Number(bands[activeCount - 1] || 0) < 0.000001) activeCount -= 1
    var rawBands = []
    var peak = 0
    for (var i = 0; i < activeCount; ++i) {
      var value = Math.max(0, Number(bands[i] || 0))
      rawBands.push(value)
      peak = Math.max(peak, value)
    }
    // Follow loud attacks immediately, then release the normalization ceiling
    // gradually. Positive frame deltas get an extra transient kick.
    adaptivePeak = peak > adaptivePeak ? peak : Math.max(0.025, adaptivePeak * 0.955)
    var next = []
    for (var j = 0; j < sampleCount; ++j) {
      // Interpolate only across the frequency axis. Temporal response remains
      // direct, so the shape is fluid without making beats sluggish again.
      var position = j * (rawBands.length - 1) / Math.max(1, sampleCount - 1)
      var lower = Math.floor(position)
      var upper = Math.min(rawBands.length - 1, lower + 1)
      var mix = position - lower
      var easedMix = mix * mix * (3 - 2 * mix)
      var raw = rawBands[lower] * (1 - easedMix) + rawBands[upper] * easedMix
      var previousLower = previousRawBands.length > lower
        ? Number(previousRawBands[lower] || 0) : rawBands[lower]
      var previousUpper = previousRawBands.length > upper
        ? Number(previousRawBands[upper] || 0) : rawBands[upper]
      var previousRaw = previousLower * (1 - easedMix) + previousUpper * easedMix
      var previous = reactiveBands.length > j ? Number(reactiveBands[j] || 0) : 0
      var normalized = Math.min(1, raw / Math.max(0.025, adaptivePeak))
      var transient = Math.max(0, raw - previousRaw) / Math.max(0.025, adaptivePeak)
      // CLIAMP's stream already applies temporal smoothing. A steeper curve
      // restores contrast between bands; almost-direct attack/release avoids
      // smoothing the smoothed source a second time.
      var target = Math.min(1, Math.pow(normalized, 1.7) * sensitivity + transient * 3.2)
      next.push(target > previous ? previous * 0.02 + target * 0.98
        : previous * 0.18 + target * 0.82)
    }
    previousRawBands = rawBands
    reactiveBands = next
    var total = 0
    var low = 0
    var high = 0
    for (var k = 0; k < next.length; ++k) {
      total += next[k]
      if (k < next.length / 3) low += next[k]
      if (k >= next.length * 2 / 3) high += next[k]
    }
    var rawTotal = 0
    var rawBass = 0
    var bassCount = Math.min(2, rawBands.length)
    for (var rawIndex = 0; rawIndex < rawBands.length; ++rawIndex) {
      rawTotal += rawBands[rawIndex]
      if (rawIndex < bassCount) rawBass += rawBands[rawIndex]
    }
    var bassLevel = rawBass / Math.max(1, bassCount) / Math.max(0.025, adaptivePeak)
    var spectrumLevel = rawTotal / Math.max(1, rawBands.length) / Math.max(0.025, adaptivePeak)
    var frameLevel = Math.min(1, Math.pow(bassLevel * 0.72 + spectrumLevel * 0.28, 1.45))
    var previousLevel = pulseHistory.length
      ? Number(pulseHistory[pulseHistory.length - 1].raw || 0) : frameLevel
    var groupSize = Math.max(1, Math.floor(next.length / 3))
    var mid = Math.max(0, total - low - high)
    var history = pulseHistory.slice()
    history.push({
      raw: root.playing ? frameLevel : 0.025,
      bass: root.playing ? low / groupSize : 0.025,
      mid: root.playing ? mid / Math.max(1, next.length - groupSize * 2) : 0.025,
      treble: root.playing ? high / groupSize : 0.025,
      tone: Math.min(1, high / Math.max(0.001, low + high)),
      hot: frameLevel - previousLevel > 0.16
    })
    while (history.length > 56) history.shift()
    pulseHistory = history
  }

  function sample(index) {
    var source = reactiveBands.length ? reactiveBands : bands
    if (!source || !source.length) return 0.025
    // reactiveBands is already spatially shaped; sample it one column at a time.
    var sourceIndex = Math.min(source.length - 1,
      Math.floor(index * source.length / Math.max(1, sampleCount)))
    var value = Number(source[sourceIndex] || 0)
    return Math.max(0.018, Math.min(1, value))
  }

  function energy() {
    var source = reactiveBands.length ? reactiveBands : bands
    if (!source || !source.length) return 0
    var total = 0
    for (var i = 0; i < source.length; ++i) total += Number(source[i] || 0)
    return Math.min(1, total / source.length)
  }

  function blend(a, b, amount, alpha) {
    var t = Math.max(0, Math.min(1, amount))
    return Qt.rgba(a.r + (b.r - a.r) * t, a.g + (b.g - a.g) * t,
      a.b + (b.b - a.b) * t, alpha === undefined ? 1 : alpha)
  }

  function frequencyColor(index, alpha) {
    var p = index / Math.max(1, sampleCount - 1)
    return p < 0.5 ? blend(turquoise, sand, p * 2, alpha)
      : blend(sand, adobe, (p - 0.5) * 2, alpha)
  }

  Rectangle {
    anchors.fill: parent
    visible: !root.compact
    radius: 4
    gradient: Gradient {
      orientation: Gradient.Vertical
      GradientStop { position: 0; color: root.sky }
      GradientStop { position: 0.65; color: root.blend(root.sky, root.turquoise, 0.08, 1) }
      GradientStop { position: 1; color: root.blend(root.sky, root.adobe, 0.14, 1) }
    }
  }

  Repeater {
    visible: !root.compact
    model: 18
    Rectangle {
      required property int index
      x: ((index * 79 + 23) % 101) / 101 * root.width
      y: ((index * 47 + 11) % 59) / 59 * root.height * 0.78
      width: index % 4 === 0 ? 2 : 1
      height: width
      radius: width / 2
      color: index % 3 === 0 ? root.turquoise : root.sand
      opacity: root.playing ? 0.18 + (index % 5) * 0.08 : 0.12
    }
  }

  Canvas {
    id: visualCanvas
    anchors.fill: parent
    anchors.margins: root.compact ? 0 : 8
    antialiasing: true

    function roundedBar(ctx, x, y, width, height, radius) {
      var r = Math.max(0, Math.min(radius, width / 2, height / 2))
      ctx.beginPath()
      ctx.moveTo(x + r, y)
      ctx.lineTo(x + width - r, y)
      ctx.quadraticCurveTo(x + width, y, x + width, y + r)
      ctx.lineTo(x + width, y + height - r)
      ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height)
      ctx.lineTo(x + r, y + height)
      ctx.quadraticCurveTo(x, y + height, x, y + height - r)
      ctx.lineTo(x, y + r)
      ctx.quadraticCurveTo(x, y, x + r, y)
      ctx.closePath()
      ctx.fill()
    }

    function spectrum(ctx, w, h) {
      var mid = h * 0.51
      var count = root.compact ? 12 : root.sampleCount
      var gap = root.compact ? 1 : Math.max(2, w / 420)
      var barW = (w - gap * (count - 1)) / count
      ctx.fillStyle = root.blend(root.turquoise, root.sand, 0.45, 0.12)
      ctx.fillRect(0, mid - 1, w, 2)
      for (var i = 0; i < count; ++i) {
        var band = Math.min(root.sampleCount - 1, Math.floor(i * root.sampleCount / count))
        var pulse = 0.96 + Math.sin(root.phase * 2.2 + i * 0.35) * 0.04
        var bh = Math.max(root.compact ? 1 : 3, root.sample(band) * h * 0.41 * pulse)
        var x = i * (barW + gap)
        var gradient = ctx.createLinearGradient(0, mid - bh, 0, mid + bh)
        gradient.addColorStop(0, root.frequencyColor(band, root.playing ? 0.98 : 0.42))
        gradient.addColorStop(0.5, root.frequencyColor(band, root.playing ? 0.72 : 0.28))
        gradient.addColorStop(1, root.blend(root.adobe, root.sand, i / count, root.playing ? 0.62 : 0.18))
        ctx.fillStyle = gradient
        roundedBar(ctx, x, mid - bh, barW, bh * 2, Math.min(5, barW * 0.24))
        ctx.fillStyle = root.blend(root.sky, root.sand, 0.14, 0.42)
        ctx.fillRect(x, mid - 1, barW, 2)
      }
    }

    function canyon(ctx, w, h) {
      var layers = root.compact ? 2 : 4
      var count = root.compact ? 14 : root.sampleCount
      for (var layer = 0; layer < layers; ++layer) {
        var inset = layer * h * 0.055
        var amplitude = h * (0.38 - layer * 0.055)
        ctx.beginPath()
        ctx.moveTo(0, inset)
        for (var i = 0; i < count; ++i) {
          var x = i * w / (count - 1)
          var ridgeBand = Math.floor(i * root.sampleCount / count)
          var ridge = root.sample((ridgeBand + layer * 3) % root.sampleCount)
          var drift = Math.abs(Math.sin(i * 0.41 + root.phase * 0.38 + layer)) * h * 0.055
          var y = inset + ridge * amplitude + drift
          ctx.lineTo(x, y)
        }
        ctx.lineTo(w, 0)
        ctx.closePath()
        ctx.fillStyle = root.blend(root.turquoise, root.sand, layer / 4, 0.08 + layer * 0.045)
        ctx.fill()
        ctx.lineWidth = layer === 3 ? 2.5 : 1.2
        ctx.strokeStyle = root.blend(root.turquoise, root.sand, layer / 4, 0.16 + layer * 0.1)
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(0, h - inset)
        for (var j = 0; j < count; ++j) {
          var bx = j * w / (count - 1)
          var reverseBand = Math.floor(j * root.sampleCount / count)
          var br = root.sample((root.sampleCount - 1 - reverseBand + layer * 2) % root.sampleCount)
          var bd = Math.abs(Math.cos(j * 0.37 + root.phase * 0.31 - layer)) * h * 0.05
          ctx.lineTo(bx, h - inset - br * amplitude - bd)
        }
        ctx.lineTo(w, h)
        ctx.closePath()
        ctx.fillStyle = root.blend(root.adobe, root.sand, layer / 4, 0.09 + layer * 0.05)
        ctx.fill()
        ctx.strokeStyle = root.blend(root.adobe, root.sand, layer / 4, 0.18 + layer * 0.09)
        ctx.stroke()
      }
    }

    // Inspired by Voxtype Pulse's rolling, symmetric microphone history, but
    // driven by CLIAMP's whole-frame music energy and spectral balance.
    function pulse(ctx, w, h) {
      var center = h / 2
      var half = Math.max(1, center - (root.compact ? 1 : 7))
      var columns = root.compact ? 14 : 56
      var step = w / columns
      var historyStart = Math.max(0, root.pulseHistory.length - columns)
      var values = root.pulseHistory.slice(historyStart)
      var start = columns - values.length
      var minima = { bass: 1, mid: 1, treble: 1 }
      var maxima = { bass: 0, mid: 0, treble: 0 }
      for (var historyIndex = 0; historyIndex < values.length; ++historyIndex) {
        var historyItem = values[historyIndex]
        minima.bass = Math.min(minima.bass, Number(historyItem.bass || 0))
        minima.mid = Math.min(minima.mid, Number(historyItem.mid || 0))
        minima.treble = Math.min(minima.treble, Number(historyItem.treble || 0))
        maxima.bass = Math.max(maxima.bass, Number(historyItem.bass || 0))
        maxima.mid = Math.max(maxima.mid, Number(historyItem.mid || 0))
        maxima.treble = Math.max(maxima.treble, Number(historyItem.treble || 0))
      }
      ctx.beginPath()
      ctx.moveTo(0, center)
      ctx.lineTo(w, center)
      ctx.lineWidth = 1
      ctx.strokeStyle = root.blend(root.sand, root.sky, 0.3, 0.16)
      ctx.stroke()
      ctx.lineCap = "round"
      for (var i = 0; i < values.length; ++i) {
        var item = values[i]
        var x = (start + i) * step + step / 2
        var keys = ["bass", "mid", "treble"]
        var colors = [root.turquoise, root.sand, root.adobe]
        var widths = [0.84, 0.56, 0.3]
        var heightScales = [1, 0.9, 0.8]
        for (var layer = 0; layer < keys.length; ++layer) {
          var key = keys[layer]
          var range = Math.max(0.035, maxima[key] - minima[key])
          var normalized = Math.max(0, Math.min(1,
            (Number(item[key] || 0) - minima[key]) / range))
          var level = 0.055 + Math.pow(normalized, 0.78) * 0.945
          var lineHalf = Math.max(root.compact ? 1 : 2, level * half * heightScales[layer])
          var color = item.hot
            ? root.blend(colors[layer], root.sand, 0.32, 1) : colors[layer]
          ctx.beginPath()
          ctx.moveTo(x, center - lineHalf)
          ctx.lineTo(x, center + lineHalf)
          ctx.lineWidth = Math.max(root.compact ? 1 : 1.5, step * widths[layer])
          ctx.strokeStyle = root.blend(color, root.sky, 0.04,
            root.playing ? 0.58 + level * 0.4 : 0.24)
          ctx.stroke()
        }
      }
    }

    onPaint: {
      var ctx = getContext("2d")
      ctx.reset()
      if (root.mode === 0) spectrum(ctx, width, height)
      else if (root.mode === 1) canyon(ctx, width, height)
      else pulse(ctx, width, height)
    }
  }

  Rectangle {
    visible: !root.compact
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    anchors.margins: 7
    width: modeLabel.implicitWidth + 18
    height: modeLabel.implicitHeight + 8
    radius: height / 2
    color: root.blend(root.sky, root.turquoise, 0.18, 0.82)
    border.width: 1
    border.color: root.blend(root.turquoise, root.sand, 0.25, 0.5)
    Text {
      id: modeLabel
      anchors.centerIn: parent
      text: ["SPECTRUM", "CANYON", "PULSE"][root.mode]
      color: root.sand
      font.pixelSize: 8
      font.bold: true
      font.letterSpacing: 1
    }
  }

  MouseArea {
    anchors.fill: parent
    enabled: !root.compact
    cursorShape: Qt.PointingHandCursor
    onClicked: root.cycleRequested()
  }

  Connections {
    target: root
    function onBandsChanged() {
      root.updateReactiveBands()
      visualCanvas.requestPaint()
    }
    function onModeChanged() { visualCanvas.requestPaint() }
    function onWidthChanged() { visualCanvas.requestPaint() }
    function onHeightChanged() { visualCanvas.requestPaint() }
    function onTurquoiseChanged() { visualCanvas.requestPaint() }
    function onSandChanged() { visualCanvas.requestPaint() }
    function onAdobeChanged() { visualCanvas.requestPaint() }
  }

  Timer {
    interval: 33
    running: root.visible
    repeat: true
    onTriggered: {
      root.phase = (root.phase + (root.playing ? 0.075 : 0.018)) % (Math.PI * 200)
      visualCanvas.requestPaint()
    }
  }
}
