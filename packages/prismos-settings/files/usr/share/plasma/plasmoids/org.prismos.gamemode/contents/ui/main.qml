// PrismOS gaming mode: one click in the panel switches prism-gaming on or off.
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property bool active: false

    preferredRepresentation: compactRepresentation
    Plasmoid.icon: "input-gaming"
    toolTipMainText: active ? "Gaming-Modus an" : "Gaming-Modus aus"
    toolTipSubText: active ? "Klicken stellt alles wieder her."
                           : "Klicken für volle Leistung, Ruhe und die höchste Bildwiederholrate."

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source)
            if (source.indexOf("status") >= 0)
                root.active = String(data["stdout"]).trim() === "on"
            else
                statusTimer.restart()
        }
    }

    function refresh() {
        exec.connectSource("prism-gaming status # " + Date.now())
    }

    Timer {
        id: statusTimer
        interval: 5000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    compactRepresentation: MouseArea {
        id: button
        hoverEnabled: true
        onClicked: {
            root.active = !root.active
            exec.connectSource("prism-gaming toggle # " + Date.now())
        }

        Kirigami.Icon {
            anchors.fill: parent
            source: "input-gaming"
            active: button.containsMouse
            opacity: root.active ? 1.0 : 0.6
        }

        Rectangle {
            visible: root.active
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            width: parent.width * 0.6
            height: Math.max(2, Kirigami.Units.smallSpacing / 2)
            radius: height / 2
            color: Kirigami.Theme.highlightColor
        }
    }

    fullRepresentation: Item {}
}
