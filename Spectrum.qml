import QtQuick

Item {
  id: root
  property var bands: []
  property color lowColor: "#55aaa6"
  property color midColor: "#f2dfc8"
  property color highColor: "#c8784f"
  property int gap: 2
  readonly property int bandCount: Math.max(10, bands ? bands.length : 0)

  implicitWidth: 180
  implicitHeight: 48

  Row {
    id: spectrumRow
    anchors.fill: parent
    spacing: root.gap

    Repeater {
      model: root.bandCount
      Rectangle {
        required property int index
        anchors.bottom: parent.bottom
        width: Math.max(1, (spectrumRow.width - spectrumRow.spacing * (root.bandCount - 1)) / root.bandCount)
        height: Math.max(2, parent.height * Math.max(0.035, Math.min(1, root.bands[index] || 0)))
        radius: width / 2
        color: index < root.bandCount * 0.48 ? root.lowColor
          : index < root.bandCount * 0.78 ? root.midColor : root.highColor

        Behavior on height { NumberAnimation { duration: 70 } }
      }
    }
  }
}
