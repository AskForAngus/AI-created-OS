#!/usr/bin/env python3
"""Prism Looks – das Design-Studio von PrismOS.

Applies the bundled look presets (Plasma global themes plus the extra settings
a global theme cannot carry), saves the current desktop as a personal look,
and exports/imports looks as *.prismlook files.

Usage:
  prism-looks                 open the design studio
  prism-looks --firstrun      first login: finish applying the selected look
  prism-looks --apply ID      apply a preset (global theme id or key, e.g. "macos")
  prism-looks --save NAME     save the current desktop as a personal look
  prism-looks --list          list presets and personal looks
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("PRISM_LOOKS_DATA", "/usr/share/prism-looks"))
LNF_DIRS = [Path.home() / ".local/share/plasma/look-and-feel", Path("/usr/share/plasma/look-and-feel")]
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
USER_LOOKS = DATA_HOME / "prism-looks"
STATE_DIR = CONFIG_HOME / "prism-looks"
FIRSTRUN_MARKER = STATE_DIR / "firstrun-done"
LIVE_USER = "liveuser"
AUTO_BACKUP = "Automatische Sicherung"
WALLPAPER_TOKEN = "@PRISM_LOOK_WALLPAPERS@"

# Everything that makes up "the look" of a Plasma desktop. Paths are relative to ~/.config.
LOOK_CONFIG_FILES = [
    "kdeglobals", "kwinrc", "plasmarc", "kcminputrc", "breezerc", "ksplashrc", "kscreenlockerrc",
    "plasma-org.kde.plasma.desktop-appletsrc", "plasmashellrc",
    "gtkrc", "gtkrc-2.0", "gtk-3.0/settings.ini", "gtk-4.0/settings.ini",
]
LOOK_CONFIG_DIRS = ["Kvantum"]
# Theme assets downloaded by the user. Paths are relative to ~/.local/share.
LOOK_DATA_DIRS = ["color-schemes", "aurorae", "plasma/desktoptheme", "plasma/look-and-feel", "wallpapers"]


# --------------------------------------------------------------------------- small helpers

def run(cmd, check=False, quiet=True):
    """Run a command, never raise for a missing binary."""
    try:
        return subprocess.run(cmd, check=check, text=True,
                              stdout=subprocess.PIPE if quiet else None,
                              stderr=subprocess.STDOUT if quiet else None).returncode
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"prism-looks: {cmd[0]}: {exc}", file=sys.stderr)
        return 127


def have(binary):
    return shutil.which(binary) is not None


def libexec(name):
    for d in ("/usr/lib", "/usr/libexec", "/usr/lib/x86_64-linux-gnu/libexec"):
        p = Path(d) / name
        if p.exists():
            return str(p)
    return None


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "look"


def read_ini_value(path, group, key):
    """Minimal KConfig reader (enough for [Group] key=value lookups)."""
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
    except OSError:
        return None
    current = None
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == group and "=" in line:
            k, v = line.split("=", 1)
            if k.split("[", 1)[0].strip() == key:
                return v.strip()
    return None


def kread(file, group, key):
    if have("kreadconfig6"):
        try:
            out = subprocess.run(["kreadconfig6", "--file", file, "--group", group, "--key", key],
                                 capture_output=True, text=True).stdout.strip()
            if out:
                return out
        except OSError:
            pass
    for base in (CONFIG_HOME, Path("/etc/xdg")):
        value = read_ini_value(base / file, group, key)
        if value:
            return value
    return None


def kwrite(file, groups, key, value):
    if isinstance(groups, str):
        groups = [groups]
    cmd = ["kwriteconfig6", "--notify", "--file", file]
    for g in groups:
        cmd += ["--group", g]
    cmd += ["--key", key, str(value)]
    if run(cmd) != 0:
        print(f"prism-looks: konnte {file}/{key} nicht schreiben", file=sys.stderr)


def dbus_call(dest, path, method, *args):
    if have("dbus-send"):
        run(["dbus-send", "--session", "--type=method_call", f"--dest={dest}", path, method, *args])


def kwin_reconfigure():
    dbus_call("org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure")


def notify_settings_changed():
    # KGlobalSettings change types: 0 palette, 2 style, 4 icons, 5 cursor
    if have("dbus-send"):
        for kind in ("0", "2", "4", "5"):
            run(["dbus-send", "--session", "--type=signal", "/KGlobalSettings",
                 "org.kde.KGlobalSettings.notifyChange", f"int32:{kind}", "int32:0"])


def restart_plasmashell(between=None):
    """Stop plasmashell, optionally run a callback, start it again."""
    systemd = run(["systemctl", "--user", "is-active", "--quiet", "plasma-plasmashell.service"]) == 0
    if systemd:
        run(["systemctl", "--user", "stop", "plasma-plasmashell.service"])
    else:
        run(["kquitapp6", "plasmashell"])
        time.sleep(1.5)
    if between:
        between()
    if systemd:
        run(["systemctl", "--user", "start", "plasma-plasmashell.service"])
    elif have("kstart"):
        run(["kstart", "plasmashell"])
    else:
        subprocess.Popen(["plasmashell"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------- presets

def load_presets():
    data = json.loads((DATA_DIR / "presets.json").read_text())
    presets = data["presets"]
    for p in presets:
        p["preview"] = str(lnf_path(p["id"]) / "contents/previews/preview.png")
        p["wallpaper"] = f"/usr/share/wallpapers/PrismOS/{p['key']}.jpg"
    return presets


def lnf_path(lnf_id):
    for d in LNF_DIRS:
        if (d / lnf_id).is_dir():
            return d / lnf_id
    return LNF_DIRS[-1] / lnf_id


def find_preset(ident, presets=None):
    for p in presets or load_presets():
        if ident in (p["id"], p["key"]):
            return p
    return None


def current_lnf():
    return kread("kdeglobals", "KDE", "LookAndFeelPackage")


def apply_style(preset):
    """Apply the parts of a preset a global theme does not apply on its own."""
    if have("plasma-apply-colorscheme"):
        run(["plasma-apply-colorscheme", preset["colorScheme"]])
        r, g, b = (int(v) for v in preset["accent"].split(","))
        run(["plasma-apply-colorscheme", "--accent-color", f"#{r:02x}{g:02x}{b:02x}"])
    else:
        kwrite("kdeglobals", "General", "ColorScheme", preset["colorScheme"])
    kwrite("kdeglobals", "General", "AccentColor", preset["accent"])
    kwrite("kdeglobals", "General", "accentColorFromWallpaper", "false")

    changeicons = libexec("plasma-changeicons")
    if changeicons:
        run([changeicons, preset["icons"]])
    kwrite("kdeglobals", "Icons", "Theme", preset["icons"])

    if have("plasma-apply-cursortheme"):
        run(["plasma-apply-cursortheme", preset["cursor"]])
    kwrite("kcminputrc", "Mouse", "cursorTheme", preset["cursor"])

    font = f"{preset['font']},10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
    small = f"{preset['font']},8,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"
    for key in ("font", "menuFont", "toolBarFont"):
        kwrite("kdeglobals", "General", key, font)
    kwrite("kdeglobals", "General", "smallestReadableFont", small)
    kwrite("kdeglobals", "WM", "activeFont", font)

    theme = preset.get("plasmaTheme", "default")
    if have("plasma-apply-desktoptheme"):
        run(["plasma-apply-desktoptheme", theme])
    kwrite("plasmarc", "Theme", "name", theme)

    # Optional Kvantum theme: used only if it is installed, otherwise Breeze.
    kvantum = next((k for k in preset.get("kvantum", []) if Path(f"/usr/share/Kvantum/{k}").is_dir()), None)
    if kvantum:
        kwrite("Kvantum/kvantum.kvconfig", "General", "theme", kvantum)
    kwrite("kdeglobals", "KDE", "widgetStyle", "kvantum" if kvantum else "Breeze")

    kwrite("kwinrc", "org.kde.kdecoration2", "library", "org.kde.breeze")
    kwrite("kwinrc", "org.kde.kdecoration2", "theme", "Breeze")
    kwrite("kwinrc", "org.kde.kdecoration2", "ButtonsOnLeft", preset["buttonsLeft"])
    kwrite("kwinrc", "org.kde.kdecoration2", "ButtonsOnRight", preset["buttonsRight"])
    kwin_reconfigure()
    notify_settings_changed()


def apply_preset(preset, backup=True):
    if backup:
        save_look(AUTO_BACKUP, screenshot=False, overwrite=True)
    if have("plasma-apply-lookandfeel"):
        run(["plasma-apply-lookandfeel", "--resetLayout", "--apply", preset["id"]])
    else:
        kwrite("kdeglobals", "KDE", "LookAndFeelPackage", preset["id"])
    apply_style(preset)
    if have("plasma-apply-wallpaperimage") and Path(preset["wallpaper"]).exists():
        time.sleep(1)  # let plasmashell finish loading the new layout
        run(["plasma-apply-wallpaperimage", preset["wallpaper"]])


# --------------------------------------------------------------------------- personal looks

def list_looks():
    looks = []
    if USER_LOOKS.is_dir():
        for d in sorted(USER_LOOKS.iterdir()):
            meta = d / "look.json"
            if meta.is_file():
                try:
                    info = json.loads(meta.read_text())
                except (OSError, ValueError):
                    continue
                info["path"] = str(d)
                info["preview"] = str(d / "preview.png") if (d / "preview.png").exists() else ""
                looks.append(info)
    looks.sort(key=lambda l: (l.get("name") != AUTO_BACKUP, l.get("created", "")), reverse=False)
    return looks


def _wallpaper_paths(appletsrc_text):
    home = str(Path.home())
    found = set()
    for m in re.finditer(r"^Image=(?:file://)?(/[^\n]+)$", appletsrc_text, re.M):
        path = m.group(1).strip()
        if path.startswith(home) and Path(path).is_file():
            found.add(path)
    return found


def save_look(name, screenshot=True, overwrite=False):
    target = USER_LOOKS / slugify(name)
    if target.exists():
        if not overwrite:
            raise FileExistsError(name)
        shutil.rmtree(target)
    (target / "config").mkdir(parents=True)

    for rel in LOOK_CONFIG_FILES:
        src = CONFIG_HOME / rel
        if src.is_file():
            dst = target / "config" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    for rel in LOOK_CONFIG_DIRS:
        src = CONFIG_HOME / rel
        if src.is_dir():
            shutil.copytree(src, target / "config" / rel, symlinks=False)
    for rel in LOOK_DATA_DIRS:
        src = DATA_HOME / rel
        if src.is_dir() and rel != "wallpapers":
            shutil.copytree(src, target / "data" / rel, symlinks=False)

    # Wallpapers from the home directory travel with the look.
    appletsrc = target / "config/plasma-org.kde.plasma.desktop-appletsrc"
    if appletsrc.is_file():
        text = appletsrc.read_text(errors="replace")
        for path in _wallpaper_paths(text):
            (target / "wallpapers").mkdir(exist_ok=True)
            shutil.copy2(path, target / "wallpapers" / Path(path).name)
            text = text.replace(path, f"{WALLPAPER_TOKEN}/{Path(path).name}")
        appletsrc.write_text(text)

    info = {"name": name, "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "lookAndFeel": current_lnf() or "", "format": 1}
    (target / "look.json").write_text(json.dumps(info, indent=2, ensure_ascii=False))

    if screenshot and have("spectacle"):
        run(["spectacle", "--background", "--nonotify", "--fullscreen", "--delay", "800",
             "--output", str(target / "screenshot.png")])
        shot = target / "screenshot.png"
        if shot.exists():
            try:
                from PyQt6.QtGui import QImage
                from PyQt6.QtCore import Qt
                img = QImage(str(shot)).scaled(640, 360, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                               Qt.TransformationMode.SmoothTransformation)
                img.save(str(target / "preview.png"))
            except ImportError:
                shutil.copy2(shot, target / "preview.png")
            shot.unlink()
    return target


def apply_look(path, backup=True):
    path = Path(path)
    if backup and path.name != slugify(AUTO_BACKUP):
        save_look(AUTO_BACKUP, screenshot=False, overwrite=True)

    def copy_files():
        wall_dir = DATA_HOME / "wallpapers/prism-looks" / path.name
        if (path / "wallpapers").is_dir():
            wall_dir.mkdir(parents=True, exist_ok=True)
            for f in (path / "wallpapers").iterdir():
                shutil.copy2(f, wall_dir / f.name)
        for src in (path / "config").rglob("*"):
            if src.is_file():
                dst = CONFIG_HOME / src.relative_to(path / "config")
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.name == "plasma-org.kde.plasma.desktop-appletsrc":
                    dst.write_text(src.read_text(errors="replace").replace(WALLPAPER_TOKEN, str(wall_dir)))
                else:
                    shutil.copy2(src, dst)
        if (path / "data").is_dir():
            shutil.copytree(path / "data", DATA_HOME, dirs_exist_ok=True)

    restart_plasmashell(between=copy_files)
    scheme = read_ini_value(CONFIG_HOME / "kdeglobals", "General", "ColorScheme")
    if scheme and have("plasma-apply-colorscheme"):
        run(["plasma-apply-colorscheme", scheme])
    cursor = read_ini_value(CONFIG_HOME / "kcminputrc", "Mouse", "cursorTheme")
    if cursor and have("plasma-apply-cursortheme"):
        run(["plasma-apply-cursortheme", cursor])
    kwin_reconfigure()
    notify_settings_changed()


def export_look(path, destination):
    path = Path(path)
    with tarfile.open(destination, "w:gz") as tar:
        tar.add(path, arcname=path.name)


def import_look(archive):
    """Import a *.prismlook archive, refusing anything that could escape the looks folder."""
    USER_LOOKS.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        roots = {m.name.split("/", 1)[0] for m in members}
        if len(roots) != 1:
            raise ValueError("Die Datei enthält keinen gültigen Look.")
        for m in members:
            if m.name.startswith("/") or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                raise ValueError(f"Unsichere Datei im Archiv: {m.name}")
        with tempfile.TemporaryDirectory() as tmp:
            tar.extractall(tmp, members=members, filter="data")
            src = Path(tmp) / roots.pop()
            info = json.loads((src / "look.json").read_text())
            dest = USER_LOOKS / slugify(info.get("name", src.name))
            n = 2
            while dest.exists():
                dest = USER_LOOKS / f"{slugify(info.get('name', src.name))}-{n}"
                n += 1
            shutil.copytree(src, dest)
    return dest


def delete_look(path):
    shutil.rmtree(path)


# --------------------------------------------------------------------------- light/dark switching

AUTO_THEME = STATE_DIR / "auto-theme.json"
AUTO_THEME_DEFAULTS = {"enabled": False, "mode": "times", "light": "07:00", "dark": "19:00",
                       "lightScheme": "BreezeLight", "darkScheme": "BreezeDark", "current": ""}


def load_auto_theme():
    try:
        return {**AUTO_THEME_DEFAULTS, **json.loads(AUTO_THEME.read_text())}
    except (OSError, ValueError):
        return dict(AUTO_THEME_DEFAULTS)


def save_auto_theme(cfg):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUTO_THEME.write_text(json.dumps(cfg, indent=2))
    action = "enable" if cfg["enabled"] else "disable"
    run(["systemctl", "--user", action, "--now", "prism-auto-theme.timer"])


def zone_coordinates():
    """Latitude/longitude of the configured time zone (from tzdata, no location access)."""
    try:
        zone = os.path.realpath("/etc/localtime").split("/zoneinfo/", 1)[1]
        for line in Path("/usr/share/zoneinfo/zone1970.tab").read_text().splitlines():
            parts = line.split("\t")
            if not line.startswith("#") and len(parts) > 2 and parts[2] == zone:
                m = re.match(r"([+-]\d{2})(\d{2})(\d{2})?([+-]\d{3})(\d{2})(\d{2})?", parts[1])
                lat = int(m.group(1)) + (1 if m.group(1)[0] == "+" else -1) * (int(m.group(2)) / 60)
                lon = int(m.group(4)) + (1 if m.group(4)[0] == "+" else -1) * (int(m.group(5)) / 60)
                return lat, lon
    except (OSError, IndexError, AttributeError):
        pass
    return 51.0, 10.0  # middle of Germany


def sun_times(day, lat, lon):
    """Local sunrise and sunset (datetime) using the NOAA approximation."""
    import math
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma) - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma) - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(math.radians(lat)) * math.cos(decl))
              - math.tan(math.radians(lat)) * math.tan(decl))
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    midnight_utc = datetime.datetime.combine(day, datetime.time(0), tzinfo=datetime.timezone.utc)
    rise = midnight_utc + datetime.timedelta(minutes=720 - 4 * (lon + ha) - eqtime)
    sets = midnight_utc + datetime.timedelta(minutes=720 - 4 * (lon - ha) - eqtime)
    return rise.astimezone(), sets.astimezone()


def is_daytime(cfg, now=None):
    now = now or datetime.datetime.now().astimezone()
    if cfg["mode"] == "sun":
        rise, sets = sun_times(now.date(), *zone_coordinates())
        return rise <= now < sets
    light = datetime.time.fromisoformat(cfg["light"])
    dark = datetime.time.fromisoformat(cfg["dark"])
    t = now.time()
    return light <= t < dark if light < dark else not (dark <= t < light)


def auto_theme_tick():
    cfg = load_auto_theme()
    if not cfg["enabled"]:
        return
    want = "light" if is_daytime(cfg) else "dark"
    if cfg["current"] == want:
        return
    scheme = cfg["lightScheme"] if want == "light" else cfg["darkScheme"]
    run(["plasma-apply-colorscheme", scheme])
    changeicons = libexec("plasma-changeicons")
    if changeicons:
        run([changeicons, "breeze" if want == "light" else "breeze-dark"])
    cfg["current"] = want
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUTO_THEME.write_text(json.dumps(cfg, indent=2))


# --------------------------------------------------------------------------- panels

PANEL_PROPS = ["location", "height", "floating", "hiding", "alignment", "lengthMode"]


def plasma_script(script):
    """Run a Plasma desktop script and return what it print()s."""
    try:
        out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.plasmashell",
                              "--object-path", "/PlasmaShell", "--method",
                              "org.kde.PlasmaShell.evaluateScript", script],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.startswith("('") and out.endswith("',)"):
        out = out[2:-3].encode().decode("unicode_escape")
    return out


def list_panels():
    props = ",".join(f"{k}: p.{k}" for k in PANEL_PROPS)
    out = plasma_script("var r = []; panels().forEach(function (p) { r.push({id: p.id, %s}); });"
                        " print(JSON.stringify(r));" % props)
    try:
        return json.loads(out)
    except ValueError:
        return []


def update_panel(panel_id, values):
    lines = [f"var p = panelById({int(panel_id)});"]
    for key, value in values.items():
        if key in PANEL_PROPS:
            lines.append(f"p.{key} = {json.dumps(value)};")
    plasma_script(" ".join(lines))


def add_dock():
    plasma_script('var p = new Panel; p.location = "bottom"; p.height = 2 * Math.ceil(gridUnit * 3.2 / 2);'
                  ' p.floating = true; p.lengthMode = "fit"; p.alignment = "center"; p.hiding = "dodgewindows";'
                  ' p.addWidget("org.kde.plasma.icontasks");')


def remove_panel(panel_id):
    plasma_script(f"panelById({int(panel_id)}).remove();")


# --------------------------------------------------------------------------- first login

def firstrun(open_gui=True):
    if FIRSTRUN_MARKER.exists():
        return False
    preset = find_preset(current_lnf() or "")
    if preset:
        apply_style(preset)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FIRSTRUN_MARKER.write_text(datetime.datetime.now().isoformat())
    return open_gui and os.environ.get("USER") != LIVE_USER


# --------------------------------------------------------------------------- GUI

def run_gui(welcome=False):
    from PyQt6.QtCore import Qt, QSize, QThread, QTime, pyqtSignal
    from PyQt6.QtGui import QColor, QIcon, QPixmap
    from PyQt6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QFileDialog, QFrame,
                                 QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                                 QMessageBox, QPushButton, QScrollArea, QSlider, QTabWidget, QTimeEdit,
                                 QVBoxLayout, QWidget)

    class Worker(QThread):
        failed = pyqtSignal(str)

        def __init__(self, fn, *args):
            super().__init__()
            self.fn, self.args = fn, args

        def run(self):
            try:
                self.fn(*self.args)
            except Exception as exc:  # shown to the user, details on stderr
                print(f"prism-looks: {exc!r}", file=sys.stderr)
                self.failed.emit(str(exc))

    class Card(QFrame):
        def __init__(self, title, text, image, buttons, active=False):
            super().__init__()
            self.setFrameShape(QFrame.Shape.StyledPanel)
            self.setObjectName("card")
            lay = QVBoxLayout(self)
            pic = QLabel()
            pic.setFixedSize(320, 180)
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pix = QPixmap(image) if image else QPixmap()
            if pix.isNull():
                pic.setText("Keine Vorschau")
            else:
                pic.setPixmap(pix.scaled(QSize(320, 180), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            lay.addWidget(pic)
            head = QLabel(f"<b>{title}</b>" + ("  <span style='color:#2e9d4f'>✓ aktiv</span>" if active else ""))
            lay.addWidget(head)
            if text:
                desc = QLabel(text)
                desc.setWordWrap(True)
                desc.setFixedWidth(320)
                lay.addWidget(desc)
            row = QHBoxLayout()
            for label, cb, primary in buttons:
                b = QPushButton(label)
                b.setDefault(primary)
                b.clicked.connect(cb)
                row.addWidget(b)
            lay.addLayout(row)

    def grid_page(cards, empty_text=""):
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        if not cards and empty_text:
            lbl = QLabel(empty_text)
            lbl.setWordWrap(True)
            grid.addWidget(lbl, 0, 0)
        for i, c in enumerate(cards):
            grid.addWidget(c, i // 3, i % 3)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(holder)
        return area

    class Main(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Prism Looks")
            self.setWindowIcon(QIcon.fromTheme("prismos-looks"))
            self.resize(1120, 760)
            self.workers = []
            central = QWidget()
            self.setCentralWidget(central)
            self.layout = QVBoxLayout(central)
            title = QLabel("<h2>Prism Looks</h2>Gestalte deinen Desktop: Vorlage wählen, anpassen, "
                           "als eigenen Look speichern und teilen.")
            title.setWordWrap(True)
            self.layout.addWidget(title)
            if welcome:
                banner = QLabel("<b>Willkommen bei PrismOS!</b> Wähle unten einen Look, der zu dir passt. "
                                "Du kannst ihn jederzeit wieder ändern: Prism Looks findest du im Startmenü.")
                banner.setWordWrap(True)
                banner.setStyleSheet("background:#7c5cff;color:white;padding:10px;border-radius:8px;")
                self.layout.addWidget(banner)
            self.tabs = QTabWidget()
            self.layout.addWidget(self.tabs)
            self.status = QLabel("")
            self.layout.addWidget(self.status)
            self.rebuild()

        # -- infrastructure
        def busy(self, message, fn, *args, done=None):
            self.status.setText(message)
            self.setEnabled(False)
            w = Worker(fn, *args)

            def finished():
                self.setEnabled(True)
                self.status.setText("Fertig. Manche Programme übernehmen Änderungen erst nach einem Neustart.")
                self.rebuild()
                if done:
                    done()
            w.finished.connect(finished)
            w.failed.connect(lambda msg: QMessageBox.warning(self, "Prism Looks", f"Das hat nicht geklappt:\n{msg}"))
            self.workers.append(w)
            w.start()

        def rebuild(self):
            idx = self.tabs.currentIndex()
            self.tabs.clear()
            self.tabs.addTab(self.presets_page(), "Vorlagen")
            self.tabs.addTab(self.looks_page(), "Meine Looks")
            self.tabs.addTab(self.design_page(), "Selbst gestalten")
            self.tabs.addTab(self.panels_page(), "Leisten")
            self.tabs.addTab(self.daynight_page(), "Hell && Dunkel")
            self.tabs.setCurrentIndex(max(idx, 0))

        # -- pages
        def presets_page(self):
            active = current_lnf()
            cards = []
            for p in load_presets():
                cards.append(Card(p["name"], p["description"], p["preview"],
                                  [("Anwenden", lambda _=False, p=p: self.busy(
                                      f"„{p['name']}“ wird angewendet …", apply_preset, p), True)],
                                  active == p["id"]))
            return grid_page(cards)

        def looks_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            row = QHBoxLayout()
            save = QPushButton(QIcon.fromTheme("document-save"), "Aktuellen Look speichern …")
            save.clicked.connect(self.save_current)
            imp = QPushButton(QIcon.fromTheme("document-import"), "Look importieren …")
            imp.clicked.connect(self.import_dialog)
            row.addWidget(save)
            row.addWidget(imp)
            row.addStretch()
            lay.addLayout(row)
            cards = []
            for look in list_looks():
                path = look["path"]
                cards.append(Card(look["name"], f"Gespeichert am {look.get('created', '')[:10]}", look["preview"], [
                    ("Anwenden", lambda _=False, p=path, n=look["name"]: self.busy(
                        f"„{n}“ wird angewendet …", apply_look, p), True),
                    ("Exportieren", lambda _=False, p=path: self.export_dialog(p), False),
                    ("Löschen", lambda _=False, p=path, n=look["name"]: self.delete_dialog(p, n), False),
                ]))
            lay.addWidget(grid_page(cards, "Noch keine eigenen Looks. Gestalte deinen Desktop und klicke auf "
                                           "„Aktuellen Look speichern“. Vor jedem Wechsel legt Prism Looks "
                                           "außerdem eine automatische Sicherung an."))
            return page

        def design_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)

            quick = QGroupBox("Schnell anpassen")
            q = QGridLayout(quick)
            q.addWidget(QLabel("Hell oder dunkel:"), 0, 0)
            mode = QComboBox()
            mode.addItems(["Hell (Breeze)", "Dunkel (Breeze Dark)", "Klassisch (Breeze Classic)"])
            mode.setPlaceholderText("Auswählen …")
            mode.setCurrentIndex(-1)
            mode.activated.connect(lambda i: self.busy("Farbschema wird gesetzt …", run,
                                                       ["plasma-apply-colorscheme",
                                                        ["BreezeLight", "BreezeDark", "BreezeClassic"][i]]))
            q.addWidget(mode, 0, 1)
            accent = QPushButton("Akzentfarbe wählen …")
            accent.clicked.connect(self.pick_accent)
            q.addWidget(QLabel("Akzentfarbe:"), 1, 0)
            q.addWidget(accent, 1, 1)
            q.addWidget(QLabel("Fensterknöpfe:"), 2, 0)
            buttons = QComboBox()
            layouts = [("Rechts (Windows, Ubuntu)", "", "IAX"), ("Links (macOS)", "XIA", ""),
                       ("Nur Schließen (GNOME)", "", "X"), ("KDE-Standard", "MS", "HIAX")]
            buttons.addItems([l[0] for l in layouts])
            buttons.setPlaceholderText("Auswählen …")
            buttons.setCurrentIndex(-1)

            def set_buttons(i):
                kwrite("kwinrc", "org.kde.kdecoration2", "ButtonsOnLeft", layouts[i][1])
                kwrite("kwinrc", "org.kde.kdecoration2", "ButtonsOnRight", layouts[i][2])
                kwin_reconfigure()
                self.status.setText("Fensterknöpfe geändert.")
            buttons.activated.connect(set_buttons)
            q.addWidget(buttons, 2, 1)
            wall = QPushButton("Hintergrundbild wählen …")
            wall.clicked.connect(self.pick_wallpaper)
            q.addWidget(QLabel("Hintergrund:"), 3, 0)
            q.addWidget(wall, 3, 1)
            lay.addWidget(quick)

            deep = QGroupBox("Alles im Detail gestalten")
            d = QGridLayout(deep)
            entries = [
                ("Globales Design && KDE Store", "preferences-desktop-theme-global", ["systemsettings", "kcm_lookandfeel"]),
                ("Farben", "preferences-desktop-color", ["systemsettings", "kcm_colors"]),
                ("Plasma-Stil (Leisten)", "preferences-desktop-plasma-theme", ["systemsettings", "kcm_desktoptheme"]),
                ("Anwendungsstil", "preferences-desktop-theme-applications", ["systemsettings", "kcm_style"]),
                ("Fensterdekoration", "preferences-desktop-theme-windowdecorations", ["systemsettings", "kcm_kwindecoration"]),
                ("Symbole", "preferences-desktop-icons", ["systemsettings", "kcm_icons"]),
                ("Mauszeiger", "preferences-cursors", ["systemsettings", "kcm_cursortheme"]),
                ("Schriftarten", "preferences-desktop-font", ["systemsettings", "kcm_fonts"]),
                ("Startbildschirm", "preferences-system-splash", ["systemsettings", "kcm_splashscreen"]),
                ("Desktop-Effekte", "preferences-desktop-effects", ["systemsettings", "kcm_kwin_effects"]),
                ("Kvantum (Qt-Themes)", "kvantum", ["kvantummanager"]),
                ("Widgets hinzufügen", "list-add", ["dbus-send", "--session", "--type=method_call",
                                                    "--dest=org.kde.plasmashell", "/PlasmaShell",
                                                    "org.kde.PlasmaShell.toggleWidgetExplorer"]),
            ]
            for i, (label, icon, cmd) in enumerate(entries):
                b = QPushButton(QIcon.fromTheme(icon), label)
                b.clicked.connect(lambda _=False, c=cmd: subprocess.Popen(
                    c, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                d.addWidget(b, i // 3, i % 3)
            tip = QLabel("Tipp: Rechtsklick auf eine Leiste → „Bearbeitungsmodus“, um Leisten zu verschieben, "
                         "Widgets anzuordnen oder neue Leisten (z. B. ein Dock) anzulegen. Wenn alles passt, "
                         "speichere es unter „Meine Looks“.")
            tip.setWordWrap(True)
            lay.addWidget(deep)
            lay.addWidget(tip)
            lay.addStretch()
            return page

        def panels_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            intro = QLabel("Hier passt du Leisten und Docks an: Position, Größe, schwebend oder fest, "
                           "automatisch ausblenden. Änderungen sind sofort sichtbar.")
            intro.setWordWrap(True)
            lay.addWidget(intro)
            panels = list_panels()
            if not panels:
                lay.addWidget(QLabel("Keine Leisten gefunden (läuft die Plasma-Oberfläche?)."))
            picker = QComboBox()
            names = {"bottom": "unten", "top": "oben", "left": "links", "right": "rechts"}
            for i, pnl in enumerate(panels, 1):
                picker.addItem(f"Leiste {i} ({names.get(pnl.get('location'), pnl.get('location'))})", pnl)
            form = QGridLayout()
            location = QComboBox()
            location.addItems(["unten", "oben", "links", "rechts"])
            size = QSlider(Qt.Orientation.Horizontal)
            size.setRange(24, 120)
            size_label = QLabel()
            size.valueChanged.connect(lambda v: size_label.setText(f"{v} px"))
            floating = QCheckBox("Schwebend (mit Abstand zum Rand)")
            length = QComboBox()
            length.addItems(["Volle Breite", "An Inhalt anpassen (Dock)"])
            align = QComboBox()
            align.addItems(["Links/Oben", "Mitte", "Rechts/Unten"])
            hiding = QComboBox()
            hiding.addItems(["Immer sichtbar", "Automatisch ausblenden", "Fenstern ausweichen"])
            for row, (label, widget) in enumerate([("Position", location), ("Dicke", size), ("", size_label),
                                                   ("", floating), ("Länge", length), ("Ausrichtung", align),
                                                   ("Sichtbarkeit", hiding)]):
                form.addWidget(QLabel(label), row, 0)
                form.addWidget(widget, row, 1)
            loc_keys = ["bottom", "top", "left", "right"]
            align_keys = ["left", "center", "right"]
            hide_keys = ["none", "autohide", "dodgewindows"]

            def load(_=None):
                pnl = picker.currentData()
                if not pnl:
                    return
                location.setCurrentIndex(loc_keys.index(pnl["location"]) if pnl.get("location") in loc_keys else 0)
                size.setValue(int(pnl.get("height") or 44))
                floating.setChecked(bool(pnl.get("floating")))
                length.setCurrentIndex(1 if pnl.get("lengthMode") == "fit" else 0)
                align.setCurrentIndex(align_keys.index(pnl["alignment"]) if pnl.get("alignment") in align_keys else 1)
                hiding.setCurrentIndex(hide_keys.index(pnl["hiding"]) if pnl.get("hiding") in hide_keys else 0)

            def apply():
                pnl = picker.currentData()
                if pnl:
                    update_panel(pnl["id"], {"location": loc_keys[location.currentIndex()], "height": size.value(),
                                             "floating": floating.isChecked(),
                                             "lengthMode": "fit" if length.currentIndex() else "fill",
                                             "alignment": align_keys[align.currentIndex()],
                                             "hiding": hide_keys[hiding.currentIndex()]})
                    self.status.setText("Leiste angepasst.")

            picker.currentIndexChanged.connect(load)
            load()
            lay.addWidget(picker)
            lay.addLayout(form)
            row = QHBoxLayout()
            for label, cb in [("Übernehmen", apply),
                              ("Neues Dock hinzufügen", lambda: (add_dock(), self.rebuild())),
                              ("Diese Leiste entfernen", lambda: picker.currentData() and (
                                  QMessageBox.question(self, "Leiste entfernen", "Diese Leiste wirklich entfernen?")
                                  == QMessageBox.StandardButton.Yes) and (
                                  remove_panel(picker.currentData()["id"]), self.rebuild()))]:
                b = QPushButton(label)
                b.clicked.connect(cb)
                row.addWidget(b)
            lay.addLayout(row)
            lay.addStretch()
            return page

        def daynight_page(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            cfg = load_auto_theme()
            enabled = QCheckBox("Automatisch zwischen hell und dunkel wechseln")
            enabled.setChecked(cfg["enabled"])
            mode = QComboBox()
            mode.addItems(["Zu festen Uhrzeiten", "Bei Sonnenauf- und -untergang (nach Zeitzone)"])
            mode.setCurrentIndex(1 if cfg["mode"] == "sun" else 0)
            light = QTimeEdit(QTime.fromString(cfg["light"], "HH:mm"))
            dark = QTimeEdit(QTime.fromString(cfg["dark"], "HH:mm"))
            schemes = sorted({p.stem for d in (Path("/usr/share/color-schemes"),
                                               DATA_HOME / "color-schemes") if d.is_dir()
                              for p in d.glob("*.colors")} | {"BreezeLight", "BreezeDark"})
            light_scheme, dark_scheme = QComboBox(), QComboBox()
            light_scheme.addItems(schemes)
            dark_scheme.addItems(schemes)
            light_scheme.setCurrentText(cfg["lightScheme"])
            dark_scheme.setCurrentText(cfg["darkScheme"])
            form = QGridLayout()
            for row, (label, widget) in enumerate([("Wann", mode), ("Hell ab", light), ("Dunkel ab", dark),
                                                   ("Helles Farbschema", light_scheme),
                                                   ("Dunkles Farbschema", dark_scheme)]):
                form.addWidget(QLabel(label), row, 0)
                form.addWidget(widget, row, 1)

            def save():
                new = {**cfg, "enabled": enabled.isChecked(), "mode": "sun" if mode.currentIndex() else "times",
                       "light": light.time().toString("HH:mm"), "dark": dark.time().toString("HH:mm"),
                       "lightScheme": light_scheme.currentText(), "darkScheme": dark_scheme.currentText(),
                       "current": ""}
                save_auto_theme(new)
                auto_theme_tick()
                self.status.setText("Hell/Dunkel-Automatik " + ("eingeschaltet." if new["enabled"] else "ausgeschaltet."))
            button = QPushButton("Speichern")
            button.clicked.connect(save)
            lay.addWidget(enabled)
            lay.addLayout(form)
            lay.addWidget(button)
            lay.addStretch()
            return page

        # -- actions
        def save_current(self):
            name, ok = QInputDialog.getText(self, "Look speichern", "Name für deinen Look:")
            if not ok or not name.strip():
                return
            name = name.strip()
            overwrite = False
            if (USER_LOOKS / slugify(name)).exists():
                if QMessageBox.question(self, "Look speichern", f"„{name}“ gibt es schon. Ersetzen?") \
                        != QMessageBox.StandardButton.Yes:
                    return
                overwrite = True
            self.showMinimized()
            self.busy("Look wird gespeichert …", save_look, name, True, overwrite,
                      done=self.showNormal)

        def export_dialog(self, path):
            dest, _ = QFileDialog.getSaveFileName(self, "Look exportieren",
                                                  str(Path.home() / f"{Path(path).name}.prismlook"),
                                                  "PrismOS-Look (*.prismlook)")
            if dest:
                if not dest.endswith(".prismlook"):
                    dest += ".prismlook"
                self.busy("Look wird exportiert …", export_look, path, dest)

        def import_dialog(self):
            src, _ = QFileDialog.getOpenFileName(self, "Look importieren", str(Path.home()),
                                                 "PrismOS-Look (*.prismlook)")
            if src:
                self.busy("Look wird importiert …", import_look, src)

        def delete_dialog(self, path, name):
            if QMessageBox.question(self, "Look löschen", f"„{name}“ wirklich löschen?") \
                    == QMessageBox.StandardButton.Yes:
                delete_look(path)
                self.rebuild()

        def pick_accent(self):
            color = QColorDialog.getColor(QColor("#7c5cff"), self, "Akzentfarbe")
            if color.isValid():
                self.busy("Akzentfarbe wird gesetzt …", run,
                          ["plasma-apply-colorscheme", "--accent-color", color.name()])

        def pick_wallpaper(self):
            src, _ = QFileDialog.getOpenFileName(self, "Hintergrundbild", "/usr/share/wallpapers",
                                                 "Bilder (*.png *.jpg *.jpeg *.webp *.svg)")
            if src:
                self.busy("Hintergrund wird gesetzt …", run, ["plasma-apply-wallpaperimage", src])

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("prism-looks")
    app.setDesktopFileName("prism-looks")
    win = Main()
    win.show()
    return app.exec()


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(prog="prism-looks", description="Prism Looks – Design-Studio von PrismOS")
    ap.add_argument("--firstrun", action="store_true", help="Look nach der ersten Anmeldung vervollständigen")
    ap.add_argument("--apply", metavar="ID", help="Vorlage anwenden (z. B. macos, win11)")
    ap.add_argument("--save", metavar="NAME", help="aktuellen Desktop als eigenen Look speichern")
    ap.add_argument("--list", action="store_true", help="Vorlagen und eigene Looks auflisten")
    ap.add_argument("--auto-theme", action="store_true", help="Hell/Dunkel-Automatik prüfen (vom Timer)")
    args = ap.parse_args(argv)

    if args.auto_theme:
        auto_theme_tick()
        return 0
    if args.firstrun:
        if firstrun():
            return run_gui(welcome=True)
        return 0
    if args.apply:
        preset = find_preset(args.apply)
        if not preset:
            print(f"Unbekannte Vorlage: {args.apply}", file=sys.stderr)
            return 1
        apply_preset(preset)
        return 0
    if args.save:
        print(save_look(args.save, screenshot=False, overwrite=True))
        return 0
    if args.list:
        for p in load_presets():
            print(f"{p['key']:10} {p['name']}")
        for look in list_looks():
            print(f"{'(eigener)':10} {look['name']}")
        return 0
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
