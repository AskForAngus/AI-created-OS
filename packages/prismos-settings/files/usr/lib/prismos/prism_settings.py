#!/usr/bin/env python3
"""Prism Einstellungen – Akku, Gaming, Snapshots und Verschlüsselung von PrismOS.

Reads status without special rights; every change goes through
pkexec /usr/lib/prismos/prism-admin, which asks for the password once.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prism_gaming  # noqa: E402

ADMIN = "/usr/lib/prismos/prism-admin"
BATTERY_CONF = Path("/etc/prismos/battery.conf")
CRYPT_STATE = Path("/etc/prismos/crypt.json")


# --------------------------------------------------------------------------- status (no root needed)

def read_conf(path):
    values = {}
    try:
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*([A-Z_]+)=(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip().strip('"')
    except OSError:
        pass
    return values


def batteries():
    result = []
    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        def val(name):
            try:
                return (bat / name).read_text().strip()
            except OSError:
                return None
        result.append({"name": bat.name, "capacity": val("capacity"), "status": val("status"),
                       "limit_supported": (bat / "charge_control_end_threshold").exists(),
                       "limit": val("charge_control_end_threshold")})
    return result


def root_fs():
    out = subprocess.run(["findmnt", "-no", "SOURCE,FSTYPE", "/"], capture_output=True, text=True).stdout.split()
    return (re.sub(r"\[.*\]$", "", out[0]), out[1]) if len(out) >= 2 else ("", "")


def snapshots():
    if not shutil.which("snapper"):
        return None
    proc = subprocess.run(["snapper", "-c", "root", "--jsonout", "list"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        items = json.loads(proc.stdout).get("root", [])
    except ValueError:
        return None
    return [s for s in items if s.get("number", 0) != 0]


def crypt_status():
    source, _ = root_fs()
    state = {}
    try:
        state = json.loads(CRYPT_STATE.read_text())
    except (OSError, ValueError):
        pass
    return {"encrypted": source.startswith("/dev/mapper/"), "pin": bool(state.get("tpm")),
            "usb": state.get("usb"), "tpm_available": Path("/sys/class/tpm/tpm0").exists()}


def usb_partitions():
    """Partitions on removable USB devices: (path, label, size)."""
    proc = subprocess.run(["lsblk", "-J", "-o", "PATH,TYPE,TRAN,RM,LABEL,SIZE,FSTYPE"], capture_output=True, text=True)
    try:
        devices = json.loads(proc.stdout).get("blockdevices", [])
    except ValueError:
        return []
    found = []
    for disk in devices:
        if disk.get("tran") != "usb":
            continue
        for part in disk.get("children", []) or [disk]:
            if part.get("type") == "part" and part.get("fstype"):
                found.append((part["path"], part.get("label") or "", part.get("size") or ""))
    return found


def admin(*args, secrets=None):
    proc = subprocess.run(["pkexec", ADMIN, *args], input=json.dumps(secrets or {}), text=True,
                          capture_output=True)
    return proc.returncode, (proc.stderr or proc.stdout).strip()


# --------------------------------------------------------------------------- GUI

def main():
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
                                 QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                                 QPushButton, QSlider, QTabWidget, QVBoxLayout, QWidget)

    class Main(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Prism Einstellungen")
            self.setWindowIcon(QIcon.fromTheme("prismos"))
            self.resize(820, 600)
            self.tabs = QTabWidget()
            self.setCentralWidget(self.tabs)
            self.rebuild()

        def rebuild(self):
            idx = self.tabs.currentIndex()
            self.tabs.clear()
            self.tabs.addTab(self.battery_page(), QIcon.fromTheme("battery"), "Akku")
            self.tabs.addTab(self.gaming_page(), QIcon.fromTheme("input-gaming"), "Gaming")
            self.tabs.addTab(self.snapshot_page(), QIcon.fromTheme("document-revert"), "Snapshots")
            self.tabs.addTab(self.security_page(), QIcon.fromTheme("security-high"), "Sicherheit")
            self.tabs.setCurrentIndex(max(idx, 0))

        def done(self, rc, msg, ok_text):
            if rc == 0:
                QMessageBox.information(self, "Prism Einstellungen", ok_text)
            elif rc not in (126, 127):  # 126/127: password dialog cancelled
                QMessageBox.warning(self, "Prism Einstellungen", f"Das hat nicht geklappt:\n{msg}")
            self.rebuild()

        def ask_password(self, text="Passwort der Festplattenverschlüsselung:"):
            pw, ok = QInputDialog.getText(self, "Passwort", text, QLineEdit.EchoMode.Password)
            return pw if ok and pw else None

        # -- battery
        def battery_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            conf = read_conf(BATTERY_CONF)
            bats = batteries()
            if not bats:
                lay.addWidget(QLabel("Kein Akku gefunden – auf einem Desktop-PC gibt es hier nichts einzustellen."))
            for b in bats:
                state = {"Charging": "lädt", "Discharging": "Akkubetrieb", "Full": "voll",
                         "Not charging": "lädt nicht (Ladegrenze erreicht)"}.get(b["status"], b["status"] or "unbekannt")
                lay.addWidget(QLabel(f"<b>Akku</b>: {b['capacity'] or '?'} % – {state}"))
            box = QGroupBox("Ladegrenze")
            bl = QVBoxLayout(box)
            info = QLabel("Ein Akku altert langsamer, wenn er nicht ständig auf 100 % geladen wird. "
                          "Für eine Reise kannst du kurz auf 100 % stellen.")
            info.setWordWrap(True)
            bl.addWidget(info)
            limit = QSlider(Qt.Orientation.Horizontal)
            limit.setRange(50, 100)
            limit.setSingleStep(5)
            limit.setPageStep(5)
            limit.setValue(int(conf.get("CHARGE_LIMIT", "80") or 80))
            value = QLabel()
            limit.valueChanged.connect(lambda v: value.setText(f"Laden bis {v} %" + (" (keine Grenze)" if v >= 100 else "")))
            limit.valueChanged.emit(limit.value())
            bl.addWidget(limit)
            bl.addWidget(value)
            if bats and not any(b["limit_supported"] for b in bats):
                warn = QLabel("Hinweis: Dein Laptop unterstützt keine Ladegrenze über Linux.")
                warn.setWordWrap(True)
                bl.addWidget(warn)
            lay.addWidget(box)
            auto = QCheckBox("Im Akkubetrieb automatisch „Energiesparen“, am Netzteil „Ausgewogen“")
            auto.setChecked(conf.get("AUTO_PROFILE", "yes") == "yes")
            lay.addWidget(auto)
            row = QHBoxLayout()
            full = QPushButton("Einmal voll laden (100 %)")
            full.clicked.connect(lambda: limit.setValue(100))
            save = QPushButton("Übernehmen")
            save.setDefault(True)
            save.clicked.connect(lambda: self.done(*admin("battery", str(limit.value()),
                                                          "yes" if auto.isChecked() else "no"),
                                                   "Akku-Einstellungen gespeichert."))
            row.addWidget(full)
            row.addStretch()
            row.addWidget(save)
            lay.addLayout(row)
            lay.addStretch()
            return page

        # -- gaming
        def gaming_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            info = QLabel("Der Gaming-Modus lädt mit einem Klick die besten Einstellungen zum Spielen – "
                          "über den Controller-Knopf in der Leiste oder hier. Beim Ausschalten wird alles "
                          "wiederhergestellt.")
            info.setWordWrap(True)
            lay.addWidget(info)
            cfg = prism_gaming.load_config()
            parts = [
                ("performance", "Volle Leistung",
                 "Energieprofil „Leistung“, Ladegrenze kurz aufgehoben, keine automatische Umschaltung im Akkubetrieb."),
                ("quiet", "Ruhe beim Spielen",
                 "Keine Benachrichtigungen, kein Bildschirmschoner, Wartung im Hintergrund (Updates, Aufräumen) "
                 "pausiert, aufwendige Fenstereffekte aus."),
                ("display", "Bildschirm optimieren",
                 "Höchste Bildwiederholrate bei gleicher Auflösung und Adaptive Sync (VRR), wenn der Bildschirm es kann."),
            ]
            boxes = {}
            for key, title, text in parts:
                box = QCheckBox(title)
                box.setChecked(bool(cfg.get(key)))
                hint = QLabel(text)
                hint.setWordWrap(True)
                hint.setContentsMargins(24, 0, 0, 8)
                hint.setEnabled(False)
                lay.addWidget(box)
                lay.addWidget(hint)
                boxes[key] = box

            def save():
                prism_gaming.save_config({k: b.isChecked() for k, b in boxes.items()})
            for b in boxes.values():
                b.toggled.connect(save)

            row = QHBoxLayout()
            state = QLabel()
            switch = QPushButton()

            def show():
                on = prism_gaming.is_on()
                state.setText("<b>Gaming-Modus ist an</b>" if on else "Gaming-Modus ist aus")
                switch.setText("Gaming-Modus ausschalten" if on else "Gaming-Modus einschalten")
                switch.setIcon(QIcon.fromTheme("input-gaming"))

            def toggle():
                save()
                switch.setEnabled(False)
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    subprocess.run(["prism-gaming", "toggle"], timeout=120)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    QApplication.restoreOverrideCursor()
                    switch.setEnabled(True)
                show()

            switch.clicked.connect(toggle)
            show()
            row.addWidget(state)
            row.addStretch()
            row.addWidget(switch)
            lay.addLayout(row)
            lay.addStretch()
            return page

        # -- snapshots
        def snapshot_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            _, fstype = root_fs()
            snaps = snapshots() if fstype == "btrfs" else None
            if snaps is None:
                lay.addWidget(QLabel("Snapshots gibt es, wenn PrismOS mit dem Dateisystem Btrfs installiert wurde "
                                     "(Standard im Installer)."))
                lay.addStretch()
                return page
            info = QLabel("Vor und nach jeder Installation oder jedem Update legt PrismOS automatisch einen Snapshot an. "
                          "Geht etwas schief, setzt du das System hier auf einen früheren Stand zurück – "
                          "deine persönlichen Dateien bleiben dabei unberührt.")
            info.setWordWrap(True)
            lay.addWidget(info)
            lst = QListWidget()
            for s in reversed(snaps):
                text = f"#{s['number']}  {s.get('date', '')[:16]}  –  {s.get('description') or s.get('type', '')}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, s["number"])
                lst.addItem(item)
            lay.addWidget(lst)
            row = QHBoxLayout()

            def selected():
                item = lst.currentItem()
                return item.data(Qt.ItemDataRole.UserRole) if item else None

            def create():
                desc, ok = QInputDialog.getText(self, "Snapshot", "Beschreibung:", text="Mein Snapshot")
                if ok:
                    self.done(*admin("snapshot-create", desc or "Manuell"), "Snapshot erstellt.")

            def rollback():
                n = selected()
                if n is None:
                    return
                if QMessageBox.question(self, "Zurücksetzen",
                                        f"Das System auf Snapshot #{n} zurücksetzen?\n\nDer aktuelle Stand wird "
                                        "gesichert. Die Änderung wirkt nach einem Neustart. Persönliche Dateien "
                                        "bleiben unverändert.") == QMessageBox.StandardButton.Yes:
                    rc, msg = admin("snapshot-rollback", str(n))
                    self.done(rc, msg, "Fertig. Starte den Computer neu, um den früheren Stand zu laden.")

            def delete():
                n = selected()
                if n is not None and QMessageBox.question(self, "Löschen", f"Snapshot #{n} löschen?") \
                        == QMessageBox.StandardButton.Yes:
                    self.done(*admin("snapshot-delete", str(n)), "Snapshot gelöscht.")

            for label, cb in [("Snapshot erstellen", create), ("Auf diesen Stand zurücksetzen …", rollback),
                              ("Löschen", delete)]:
                b = QPushButton(label)
                b.clicked.connect(cb)
                row.addWidget(b)
            lay.addLayout(row)
            return page

        # -- encryption
        def security_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            st = crypt_status()
            if not st["encrypted"]:
                lay.addWidget(QLabel("Die Festplatte ist nicht verschlüsselt. Verschlüsselung kann nur bei der "
                                     "Installation eingeschaltet werden („Festplatte verschlüsseln“)."))
                lay.addStretch()
                return page
            intro = QLabel("Deine Festplatte ist verschlüsselt. Beim Start entsperrst du sie mit deinem Passwort. "
                           "Zusätzlich kannst du eine PIN oder einen USB-Stick als Schlüssel einrichten. "
                           "Das Passwort funktioniert immer als Notfall-Zugang.")
            intro.setWordWrap(True)
            lay.addWidget(intro)

            pin = QGroupBox("PIN (mit dem TPM-Chip des Computers)")
            pl = QVBoxLayout(pin)
            if not st["tpm_available"]:
                pl.addWidget(QLabel("Dieser Computer hat keinen TPM-Chip – PIN-Entsperrung ist nicht möglich."))
            else:
                pl.addWidget(QLabel("Eingerichtet ✓" if st["pin"] else "Nicht eingerichtet"))
                note = QLabel("Nach einem BIOS-Update oder geänderten Sicherheitseinstellungen im BIOS fragt "
                              "PrismOS beim nächsten Start nach dem Passwort. Richte die PIN danach einfach neu ein.")
                note.setWordWrap(True)
                note.setEnabled(False)
                pl.addWidget(note)
                b = QPushButton("PIN ändern …" if st["pin"] else "PIN einrichten …")
                b.clicked.connect(self.add_pin)
                pl.addWidget(b)
                if st["pin"]:
                    r = QPushButton("PIN entfernen")
                    r.clicked.connect(lambda: self.done(*admin("crypt-remove-pin"), "PIN entfernt."))
                    pl.addWidget(r)
            lay.addWidget(pin)

            usb = QGroupBox("USB-Stick als Schlüssel")
            ul = QVBoxLayout(usb)
            if st["usb"]:
                ul.addWidget(QLabel("Eingerichtet ✓ – steckt der Stick beim Start, entsperrt PrismOS automatisch."))
                r = QPushButton("USB-Stick entfernen …")
                r.clicked.connect(self.remove_usb)
                ul.addWidget(r)
            else:
                hint = QLabel("Auf dem Stick wird eine kleine Schlüsseldatei gespeichert. Pass gut auf ihn auf: "
                              "Wer ihn hat, kann deinen Computer entsperren.")
                hint.setWordWrap(True)
                ul.addWidget(hint)
                b = QPushButton("USB-Stick einrichten …")
                b.clicked.connect(self.add_usb)
                ul.addWidget(b)
            lay.addWidget(usb)

            pw = QPushButton("Passwort ändern …")
            pw.clicked.connect(self.change_password)
            lay.addWidget(pw)
            lay.addStretch()
            return page

        def add_pin(self):
            pw = self.ask_password()
            if not pw:
                return
            pin, ok = QInputDialog.getText(self, "PIN", "Neue PIN (mindestens 4 Zeichen):", QLineEdit.EchoMode.Password)
            if not ok or not pin:
                return
            again, ok = QInputDialog.getText(self, "PIN", "PIN wiederholen:", QLineEdit.EchoMode.Password)
            if not ok or again != pin:
                QMessageBox.warning(self, "PIN", "Die PINs stimmen nicht überein.")
                return
            self.done(*admin("crypt-add-pin", secrets={"password": pw, "pin": pin}),
                      "PIN eingerichtet. Beim nächsten Start fragt PrismOS nach der PIN.")

        def add_usb(self):
            parts = usb_partitions()
            if not parts:
                QMessageBox.information(self, "USB-Stick", "Bitte steck einen USB-Stick ein und versuch es erneut.")
                return
            names = [f"{p} {label} ({size})".strip() for p, label, size in parts]
            choice, ok = QInputDialog.getItem(self, "USB-Stick", "Welchen USB-Stick verwenden?", names, 0, False)
            if not ok:
                return
            pw = self.ask_password()
            if pw:
                part = parts[names.index(choice)][0]
                self.done(*admin("crypt-add-usb", part, secrets={"password": pw}),
                          "USB-Stick eingerichtet. Steckt er beim Start, wird kein Passwort gebraucht.")

        def remove_usb(self):
            pw = self.ask_password()
            if pw:
                self.done(*admin("crypt-remove-usb", secrets={"password": pw}), "USB-Stick-Schlüssel entfernt.")

        def change_password(self):
            old = self.ask_password("Aktuelles Passwort:")
            if not old:
                return
            new, ok = QInputDialog.getText(self, "Passwort", "Neues Passwort:", QLineEdit.EchoMode.Password)
            if not ok or not new:
                return
            again, ok = QInputDialog.getText(self, "Passwort", "Neues Passwort wiederholen:", QLineEdit.EchoMode.Password)
            if not ok or again != new:
                QMessageBox.warning(self, "Passwort", "Die Passwörter stimmen nicht überein.")
                return
            self.done(*admin("crypt-change-password", secrets={"password": old, "new": new}), "Passwort geändert.")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setDesktopFileName("prism-settings")
    win = Main()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
