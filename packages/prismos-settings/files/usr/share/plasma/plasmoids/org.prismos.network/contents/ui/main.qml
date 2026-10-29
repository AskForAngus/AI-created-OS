// PrismOS network overview: a click on the network icon in the panel shows
// every detail of the active connections. Data comes from prism-netinfo.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property var overview: ({ connections: [] })
    property var publicInfo: null
    property var pingInfo: null
    property var speedInfo: null
    property string busy: ""

    readonly property var mainConnection: overview.connections && overview.connections.length > 0
                                          ? overview.connections[0] : null

    function iconName() {
        const c = mainConnection
        if (!c)
            return "network-disconnect"
        if (c.type === "wifi" && c.wifi) {
            const s = c.wifi.signal || 0
            if (s >= 75) return "network-wireless-signal-excellent"
            if (s >= 50) return "network-wireless-signal-good"
            if (s >= 25) return "network-wireless-signal-ok"
            return "network-wireless-signal-weak"
        }
        return "network-wired-activated"
    }

    function summary() {
        const c = mainConnection
        if (!c)
            return "Keine Verbindung"
        const name = c.type === "wifi" && c.wifi ? c.wifi.ssid : c.name
        const ip = c.ipv4 && c.ipv4.length > 0 ? c.ipv4[0].split("/")[0] : ""
        return name + (ip ? " · " + ip : "")
    }

    Plasmoid.icon: iconName()
    Plasmoid.status: PlasmaCore.Types.ActiveStatus
    toolTipMainText: "Netzwerk"
    toolTipSubText: summary()
    switchWidth: Kirigami.Units.gridUnit * 18
    switchHeight: Kirigami.Units.gridUnit * 14

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source)
            let result = null
            try {
                result = JSON.parse(data["stdout"])
            } catch (e) {
                return
            }
            if (result.kind === "overview") root.overview = result
            else if (result.kind === "public") root.publicInfo = result
            else if (result.kind === "ping") root.pingInfo = result
            else if (result.kind === "speedtest") root.speedInfo = result
            if (result.kind !== "overview") root.busy = ""
        }
        function run(args, tag) {
            // a unique comment makes every call a new source
            connectSource("prism-netinfo " + args + " # " + tag + Date.now())
        }
    }

    Timer {
        interval: root.expanded ? 2000 : 15000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: exec.run("", "o")
    }

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 20
        Layout.minimumHeight: Kirigami.Units.gridUnit * 18
        Layout.preferredWidth: Kirigami.Units.gridUnit * 24
        Layout.preferredHeight: Kirigami.Units.gridUnit * 30

        header: PlasmaExtras.PlasmoidHeading {
            RowLayout {
                anchors.fill: parent
                Kirigami.Heading {
                    level: 3
                    text: "Netzwerk"
                    Layout.fillWidth: true
                }
                PlasmaComponents.ToolButton {
                    icon.name: "configure"
                    text: "Verbindungen verwalten"
                    display: QQC2.AbstractButton.IconOnly
                    onClicked: exec.connectSource("systemsettings kcm_networkmanagement # " + Date.now())
                    PlasmaComponents.ToolTip { text: parent.text }
                }
            }
        }

        PlasmaComponents.ScrollView {
            anchors.fill: parent
            contentWidth: availableWidth

            ColumnLayout {
                width: parent.width
                spacing: Kirigami.Units.largeSpacing

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    text: "Gerätename: " + (root.overview.hostname || "–") + "   ·   Internet: "
                          + ({ "full": "verbunden", "limited": "eingeschränkt", "portal": "Anmeldung nötig",
                               "none": "keine Verbindung" }[root.overview.connectivity] || "unbekannt")
                }

                PlasmaComponents.Label {
                    visible: !root.mainConnection
                    text: "Keine aktive Verbindung."
                }

                Repeater {
                    model: root.overview.connections || []

                    ColumnLayout {
                        id: card
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing

                        Kirigami.Heading {
                            level: 4
                            text: (card.modelData.type === "wifi" ? "WLAN: " : card.modelData.type === "ethernet"
                                   ? "Kabel: " : card.modelData.type + ": ")
                                  + (card.modelData.wifi && card.modelData.wifi.ssid ? card.modelData.wifi.ssid
                                                                                     : card.modelData.name)
                        }

                        GridLayout {
                            columns: 2
                            columnSpacing: Kirigami.Units.largeSpacing
                            rowSpacing: Kirigami.Units.smallSpacing / 2
                            Layout.fillWidth: true

                            Repeater {
                                model: {
                                    const c = card.modelData
                                    const rows = []
                                    const add = (k, v) => { if (v !== undefined && v !== null && v !== "") rows.push([k, v]) }
                                    if (c.wifi) {
                                        add("Signal", c.wifi.signal + " %")
                                        add("Frequenz / Kanal", c.wifi.frequency + " / " + c.wifi.channel)
                                        add("Geschwindigkeit", c.wifi.rate)
                                        add("Sicherheit", c.wifi.security)
                                    }
                                    add("Verbindungsgeschwindigkeit", c.link_speed)
                                    add("IPv4", (c.ipv4 || []).join("\n"))
                                    add("IPv6", (c.ipv6 || []).join("\n"))
                                    add("Gateway", [c.gateway4, c.gateway6].filter(x => x).join("\n"))
                                    add("DNS", (c.dns || []).join("\n"))
                                    add("Domain", c.domain)
                                    add("MAC-Adresse", c.mac)
                                    add("Schnittstelle", c.device + (c.mtu ? " (MTU " + c.mtu + ")" : ""))
                                    add("Download jetzt", c.rx_rate)
                                    add("Upload jetzt", c.tx_rate)
                                    add("Empfangen / Gesendet", c.rx_total ? c.rx_total + " / " + c.tx_total : "")
                                    const flat = []
                                    rows.forEach(r => { flat.push({ label: r[0], value: String(r[1]) }) })
                                    return flat
                                }

                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.columnSpan: 2
                                    Layout.fillWidth: true
                                    PlasmaComponents.Label {
                                        text: modelData.label
                                        opacity: 0.7
                                        Layout.preferredWidth: Kirigami.Units.gridUnit * 9
                                        Layout.alignment: Qt.AlignTop
                                    }
                                    PlasmaComponents.Label {
                                        text: modelData.value
                                        textFormat: Text.PlainText
                                        Layout.fillWidth: true
                                        wrapMode: Text.WrapAnywhere
                                    }
                                }
                            }
                        }
                    }
                }

                Kirigami.Separator { Layout.fillWidth: true }

                Kirigami.Heading { level: 4; text: "Tests" }

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                    text: "Diese Knöpfe fragen externe Dienste (ipinfo.io, Cloudflare). Ohne Klick wird nichts gesendet."
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing
                    PlasmaComponents.Button {
                        text: "Öffentliche IP"
                        icon.name: "globe"
                        enabled: root.busy === ""
                        onClicked: { root.busy = "public"; exec.run("--public", "p") }
                    }
                    PlasmaComponents.Button {
                        text: "Ping"
                        icon.name: "network-connect"
                        enabled: root.busy === ""
                        onClicked: { root.busy = "ping"; exec.run("--ping", "i") }
                    }
                    PlasmaComponents.Button {
                        text: "Geschwindigkeitstest"
                        icon.name: "speedometer"
                        enabled: root.busy === ""
                        onClicked: { root.busy = "speed"; exec.run("--speedtest", "s") }
                    }
                }

                PlasmaComponents.BusyIndicator {
                    visible: root.busy !== ""
                    running: visible
                }

                PlasmaComponents.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    visible: text !== ""
                    text: {
                        const lines = []
                        const p = root.publicInfo
                        if (p) lines.push(p.error ? "Öffentliche IP: " + p.error
                                                  : "Öffentliche IP: " + p.ip + "\nAnbieter: " + (p.provider || "–")
                                                    + "\nOrt: " + (p.location || "–"))
                        const g = root.pingInfo
                        const fmt = x => x ? (x.avg_ms !== null ? x.avg_ms.toFixed(1) + " ms" : "keine Antwort")
                                             + (x.loss > 0 ? " (" + x.loss + " % Verlust)" : "") : "–"
                        if (g) lines.push("Ping Router: " + fmt(g.router) + "\nPing Internet: " + fmt(g.internet))
                        const s = root.speedInfo
                        if (s) lines.push(s.error ? s.error : "Download: " + s.download + "\nUpload: " + s.upload)
                        return lines.join("\n\n")
                    }
                }

                PlasmaComponents.Button {
                    text: "WLAN wählen / Verbindungen …"
                    icon.name: "network-wireless"
                    onClicked: exec.connectSource("plasmawindowed org.kde.plasma.networkmanagement # " + Date.now())
                }
            }
        }
    }
}
