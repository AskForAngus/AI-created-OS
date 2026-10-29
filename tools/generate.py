#!/usr/bin/env python3
"""Generate PrismOS artwork and Plasma look-and-feel packages.

Single source of truth is packages/prismos-looks/files/usr/share/prism-looks/presets.json.
From it this script writes, into the prismos-looks package and the live ISO overlay:

  * /usr/share/plasma/look-and-feel/<id>/   Plasma global themes (defaults, panel layout, previews)
  * /usr/share/wallpapers/PrismOS/<key>.jpg wallpapers
  * /usr/share/icons/hicolor/...            PrismOS logo and app icons
  * /etc/calamares/branding/prismos/        installer branding images and slideshow
  * iso/syslinux/splash.png                 BIOS boot menu background

Requires: python3, cairosvg, Pillow. Run from anywhere:  python3 tools/generate.py
The generated files are committed, so the ISO build does not need to run this.
"""

import base64
import io
import json
import math
import textwrap
from pathlib import Path

import cairosvg
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
ISO = REPO / "iso"
ROOTFS = ISO / "airootfs"  # live medium only (installer branding)
LOOKS = REPO / "packages/prismos-looks/files"  # installed system
PRESETS = json.loads((LOOKS / "usr/share/prism-looks/presets.json").read_text())["presets"]

W, H = 3840, 2160
FONT = "Inter, 'Noto Sans', sans-serif"


# --------------------------------------------------------------------------- helpers

def render_png(svg: str, width: int, height: int) -> bytes:
    return cairosvg.svg2png(bytestring=svg.encode(), output_width=width, output_height=height)


def save_png(svg: str, path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_png(svg, width, height))


def save_jpg(svg: str, path: Path, width: int, height: int, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(io.BytesIO(render_png(svg, width, height))).convert("RGB")
    img.save(path, "JPEG", quality=quality, optimize=True, progressive=True)


def svg_doc(body: str, width: int = W, height: int = H, defs: str = "") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<defs>{defs}</defs>{body}</svg>')


def lin(gid, stops, x1=0, y1=0, x2=0, y2=1):
    s = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in stops)
    return f'<linearGradient id="{gid}" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">{s}</linearGradient>'


def rad(gid, stops, cx=0.5, cy=0.5, r=0.5):
    s = "".join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in stops)
    return f'<radialGradient id="{gid}" cx="{cx}" cy="{cy}" r="{r}">{s}</radialGradient>'


RAINBOW = ["#ff4d6d", "#ff9f43", "#ffd93d", "#4cd97b", "#38b6ff", "#7c5cff"]


# --------------------------------------------------------------------------- logo

def logo_svg(size: int = 512, beams: bool = True) -> str:
    """PrismOS logo: a glass prism splitting a white beam into a spectrum."""
    defs = (lin("pf", [(0, "#9b7dff", 1), (1, "#3d5afe", 1)], 0, 0, 1, 1)
            + lin("ph", [(0, "#ffffff", 0.55), (1, "#ffffff", 0)], 0, 0, 1, 1))
    body = ""
    if beams:
        body += '<path d="M10 300 L214 262 L222 280 Z" fill="#ffffff" opacity="0.95"/>'
        for i, c in enumerate(RAINBOW):
            y0 = 236 + i * 10
            y1 = 300 + i * 34
            body += f'<path d="M300 {y0} L512 {y1 - 20} L512 {y1 + 14} L302 {y0 + 10} Z" fill="{c}" opacity="0.92"/>'
    body += ('<path d="M256 70 L452 410 L60 410 Z" fill="url(#pf)" stroke="#ffffff" stroke-opacity="0.35" '
             'stroke-width="10" stroke-linejoin="round"/>'
             '<path d="M256 70 L330 410 L60 410 Z" fill="url(#ph)"/>')
    return svg_doc(body, 512, 512, defs).replace('width="512" height="512"', f'width="{size}" height="{size}"', 1)


def start_icon_svg() -> str:
    """Compact logo variant for the application launcher button."""
    defs = lin("pf", [(0, "#a48bff", 1), (1, "#4a63ff", 1)], 0, 0, 1, 1)
    body = ('<path d="M32 6 L60 56 L4 56 Z" fill="url(#pf)" stroke="#ffffff" stroke-opacity="0.5" '
            'stroke-width="3" stroke-linejoin="round"/>')
    body += '<path d="M32 6 L41 56 L4 56 Z" fill="#ffffff" opacity="0.28"/>'
    return svg_doc(body, 64, 64, defs)


def looks_icon_svg() -> str:
    """Icon for the Prism Looks design studio: palette with a prism."""
    defs = lin("bg", [(0, "#7c5cff", 1), (1, "#38b6ff", 1)], 0, 0, 1, 1)
    body = '<rect x="4" y="4" width="56" height="56" rx="14" fill="url(#bg)"/>'
    for i, c in enumerate(RAINBOW):
        a = math.radians(-150 + i * 40)
        body += f'<circle cx="{32 + 17 * math.cos(a):.1f}" cy="{34 + 17 * math.sin(a):.1f}" r="5.5" fill="{c}" stroke="#fff" stroke-width="1.5"/>'
    body += '<path d="M32 26 L41 42 L23 42 Z" fill="#ffffff" opacity="0.95"/>'
    return svg_doc(body, 64, 64, defs)


def installer_icon_svg() -> str:
    defs = lin("bg", [(0, "#1b1240", 1), (1, "#2a1060", 1)], 0, 0, 0, 1)
    inner = logo_svg(512).split("<defs>", 1)[1]
    inner_defs, inner_body = inner.split("</defs>", 1)
    inner_body = inner_body.replace("</svg>", "")
    body = (f'<rect x="4" y="4" width="56" height="56" rx="14" fill="url(#bg)"/>'
            f'<g transform="translate(8 6) scale(0.094)">{inner_body}</g>'
            '<path d="M32 40 v14 M25 48 l7 7 l7 -7" stroke="#fff" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    return svg_doc(body, 64, 64, defs + inner_defs)


# --------------------------------------------------------------------------- wallpapers

def wp_prism():
    defs = (lin("bg", [(0, "#07071a", 1), (0.55, "#1a1145", 1), (1, "#2b0d5e", 1)], 0, 0, 1, 1)
            + rad("glow", [(0, "#8f6bff", 0.55), (1, "#8f6bff", 0)], 0.5, 0.5, 0.5)
            + lin("glass", [(0, "#b9a6ff", 0.55), (1, "#5a6dff", 0.25)], 0, 0, 1, 1)
            + lin("beam", [(0, "#ffffff", 0), (1, "#ffffff", 0.95)], 0, 0, 1, 0))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    body += '<ellipse cx="1920" cy="1120" rx="1500" ry="1000" fill="url(#glow)"/>'
    body += '<path d="M0 1270 L1640 1080 L1660 1120 Z" fill="url(#beam)"/>'
    for i, c in enumerate(RAINBOW):
        y0 = 1040 + i * 22
        y1 = 900 + i * 230
        body += (f'<path d="M2130 {y0} L3840 {y1} L3840 {y1 + 200} L2140 {y0 + 22} Z" '
                 f'fill="{c}" opacity="0.55"/>')
    body += ('<path d="M1920 640 L2330 1400 L1510 1400 Z" fill="url(#glass)" stroke="#ffffff" '
             'stroke-opacity="0.55" stroke-width="10" stroke-linejoin="round"/>'
             '<path d="M1920 640 L2050 1400 L1510 1400 Z" fill="#ffffff" opacity="0.10"/>')
    for i in range(140):  # stars
        x = (i * 9973) % 3840
        y = (i * 6151) % 2160
        r = 1.5 + (i % 4)
        body += f'<circle cx="{x}" cy="{y}" r="{r}" fill="#ffffff" opacity="{0.15 + (i % 5) * 0.08:.2f}"/>'
    return defs, body


def wp_win11():
    defs = (lin("bg", [(0, "#e6f0fd", 1), (1, "#9cc2f7", 1)], 0, 0, 1, 1)
            + lin("p1", [(0, "#1d6fe0", 0.95), (1, "#0a3fae", 0.95)], 0, 0, 1, 1)
            + lin("p2", [(0, "#6fb0ff", 0.9), (1, "#2a7bea", 0.9)], 0, 0, 1, 1)
            + lin("p3", [(0, "#cfe4ff", 0.85), (1, "#7fb2f5", 0.85)], 0, 0, 1, 1))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    cx, cy = 1920, 1180
    petals = [(-60, "p3", 1.0), (-20, "p2", 0.92), (20, "p1", 0.88), (60, "p2", 0.92), (100, "p3", 1.0), (0, "p1", 0.75)]
    for angle, grad, scale in petals:
        body += (f'<g transform="translate({cx} {cy}) rotate({angle}) scale({scale})">'
                 f'<path d="M0 0 C 260 -220 520 -620 0 -980 C -520 -620 -260 -220 0 0 Z" fill="url(#{grad})"/></g>')
    return defs, body


def wp_win7():
    defs = (lin("bg", [(0, "#0a2b63", 1), (0.5, "#1563c0", 1), (1, "#082252", 1)], 0, 0, 1, 1)
            + rad("glow", [(0, "#8fd3ff", 0.55), (1, "#8fd3ff", 0)], 0.5, 0.5, 0.5))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    body += '<ellipse cx="2100" cy="1000" rx="1300" ry="700" fill="url(#glow)"/>'
    for i in range(7):
        o = i * 70
        body += (f'<path d="M-200 {1500 - o} C 900 {900 - o} 2400 {1700 - o} 4100 {700 - o}" stroke="#ffffff" '
                 f'stroke-opacity="{0.08 + i * 0.025:.3f}" stroke-width="{40 - i * 4}" fill="none"/>')
    return defs, body


def wp_macos():
    defs = (lin("bg", [(0, "#ffd7a8", 1), (0.45, "#f59a8f", 1), (1, "#8a6fd8", 1)], 0, 0, 0, 1)
            + rad("sun", [(0, "#fff6e0", 0.95), (1, "#fff6e0", 0)], 0.5, 0.5, 0.5))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    body += '<circle cx="2600" cy="880" r="520" fill="url(#sun)"/>'
    hills = [("#b56fb6", 1250, 0.85), ("#8e5bb8", 1450, 0.9), ("#6a48a8", 1650, 0.95), ("#46307d", 1850, 1)]
    for i, (c, base, a) in enumerate(hills):
        d = f"M0 {base} "
        for k in range(9):
            x = k * 480
            y = base + (130 if (k + i) % 2 else -130)
            d += f"S {x + 240} {y} {x + 480} {base} "
        d += "L3840 2160 L0 2160 Z"
        body += f'<path d="{d}" fill="{c}" opacity="{a}"/>'
    return defs, body


def wp_ubuntu():
    defs = lin("bg", [(0, "#2c001e", 1), (0.55, "#77216f", 1), (1, "#e95420", 1)], 0, 0, 1, 1)
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    for i in range(9):
        r = 380 + i * 190
        body += (f'<circle cx="2900" cy="1500" r="{r}" fill="none" stroke="#ffffff" '
                 f'stroke-opacity="{0.16 - i * 0.014:.3f}" stroke-width="{70 - i * 6}"/>')
    return defs, body


def wp_gnome():
    defs = (lin("bg", [(0, "#241f31", 1), (1, "#3d3846", 1)], 0, 0, 1, 1)
            + lin("a", [(0, "#62a0ea", 1), (1, "#1a5fb4", 1)], 0, 0, 1, 1)
            + lin("b", [(0, "#99c1f1", 1), (1, "#3584e4", 1)], 0, 0, 1, 1))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    body += '<path d="M1400 2160 C 1600 1300 2500 900 3840 760 L3840 2160 Z" fill="url(#a)" opacity="0.95"/>'
    body += '<path d="M2200 2160 C 2400 1600 3000 1300 3840 1260 L3840 2160 Z" fill="url(#b)" opacity="0.9"/>'
    body += '<circle cx="1050" cy="700" r="260" fill="#3584e4" opacity="0.35"/>'
    return defs, body


def wp_chromeos():
    defs = lin("bg", [(0, "#eef4ff", 1), (1, "#d2e3fc", 1)], 0, 0, 0, 1)
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    waves = [("#80deea", 1250, 0.8), ("#4fc3f7", 1450, 0.85), ("#1a73e8", 1700, 0.9), ("#0b4fb3", 1950, 1)]
    for i, (c, base, a) in enumerate(waves):
        d = f"M0 {base} C 900 {base - 320 + i * 60} 2200 {base + 280} 3840 {base - 200} L3840 2160 L0 2160 Z"
        body += f'<path d="{d}" fill="{c}" opacity="{a}"/>'
    return defs, body


def wp_minimal():
    defs = (lin("bg", [(0, "#f1f3f4", 1), (1, "#cfd8dc", 1)], 0, 0, 1, 1)
            + rad("c", [(0, "#ffffff", 0.7), (1, "#ffffff", 0)], 0.5, 0.5, 0.5))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    body += '<circle cx="2500" cy="900" r="900" fill="url(#c)"/>'
    body += '<circle cx="2500" cy="900" r="420" fill="none" stroke="#90a4ae" stroke-opacity="0.35" stroke-width="6"/>'
    return defs, body


def wp_winxp():
    """Rolling green hills under a blue sky with soft clouds."""
    defs = (lin("sky", [(0, "#1d5fd0", 1), (0.55, "#5b9cf0", 1), (1, "#a9d0ff", 1)], 0, 0, 0, 1)
            + lin("hill", [(0, "#79c142", 1), (1, "#3f8a1f", 1)], 0, 0, 0, 1)
            + rad("cloud", [(0, "#ffffff", 0.95), (1, "#ffffff", 0)], 0.5, 0.5, 0.5))
    body = '<rect width="3840" height="2160" fill="url(#sky)"/>'
    for cx, cy, rx, ry in [(700, 520, 520, 150), (1150, 480, 380, 120), (2600, 360, 640, 170), (3300, 620, 420, 120)]:
        body += f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="url(#cloud)"/>'
    body += '<path d="M0 1500 C 900 1150 2000 1250 3840 1420 L3840 2160 L0 2160 Z" fill="url(#hill)"/>'
    body += '<path d="M0 1700 C 1200 1450 2600 1750 3840 1600 L3840 2160 L0 2160 Z" fill="#5aa832" opacity="0.6"/>'
    return defs, body


def wp_win10():
    """Dark blue space crossed by glowing diagonal light shards."""
    defs = (lin("bg", [(0, "#00142e", 1), (1, "#003a7a", 1)], 0, 0, 1, 1)
            + lin("shard", [(0, "#4fc3ff", 0), (0.6, "#4fc3ff", 0.55), (1, "#bfe9ff", 0.9)], 0, 1, 1, 0))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    for x0, w, top, alpha in [(1500, 260, 0, 0.5), (2000, 420, 120, 0.8), (2650, 180, 60, 0.6), (3050, 340, 0, 0.7)]:
        body += (f'<path d="M{x0} 2160 L{x0 + w} 2160 L{x0 + w + 900} {top} L{x0 + 900} {top} Z" '
                 f'fill="url(#shard)" opacity="{alpha}"/>')
    body += '<path d="M0 1900 C 1200 1700 2600 2000 3840 1750 L3840 2160 L0 2160 Z" fill="#001a3d" opacity="0.8"/>'
    return defs, body


def wp_elementary():
    """Calm mountain lake at dusk."""
    defs = (lin("sky", [(0, "#f6c7a1", 1), (0.5, "#b7a6d8", 1), (1, "#5b6ea8", 1)], 0, 0, 0, 1)
            + lin("lake", [(0, "#4b5f97", 1), (1, "#26335c", 1)], 0, 0, 0, 1))
    body = '<rect width="3840" height="2160" fill="url(#sky)"/>'
    body += '<path d="M0 1350 L700 800 L1150 1150 L1700 650 L2350 1180 L2900 900 L3840 1350 Z" fill="#6f6aa6" opacity="0.9"/>'
    body += '<path d="M0 1350 L900 1050 L1500 1250 L2300 1000 L3000 1200 L3840 1100 L3840 1350 Z" fill="#4e5490"/>'
    body += '<rect y="1350" width="3840" height="810" fill="url(#lake)"/>'
    body += '<path d="M0 1350 L700 1700 L1150 1450 L1700 1850 L2350 1420 L2900 1650 L3840 1350 Z" fill="#6f6aa6" opacity="0.25"/>'
    return defs, body


def wp_deepin():
    """Soft glassy gradient with floating translucent discs."""
    defs = (lin("bg", [(0, "#d9ecff", 1), (0.5, "#a7c8ff", 1), (1, "#6f8fff", 1)], 0, 0, 1, 1)
            + rad("disc", [(0, "#ffffff", 0.75), (1, "#ffffff", 0.05)], 0.35, 0.35, 0.7))
    body = '<rect width="3840" height="2160" fill="url(#bg)"/>'
    for cx, cy, r in [(900, 700, 520), (2700, 1300, 760), (3300, 450, 300), (1700, 1650, 360)]:
        body += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#disc)" stroke="#ffffff" stroke-opacity="0.5" stroke-width="4"/>'
    return defs, body


def wp_wood():
    """Warm wooden planks."""
    defs = lin("plank", [(0, "#8a5a32", 1), (0.5, "#a46d3f", 1), (1, "#7a4c27", 1)], 0, 0, 0, 1)
    body = '<rect width="3840" height="2160" fill="#6b4423"/>'
    plank_h = 360
    for row in range(6):
        y = row * plank_h
        body += f'<rect x="0" y="{y}" width="3840" height="{plank_h - 6}" fill="url(#plank)"/>'
        for k in range(14):  # grain
            gy = y + 20 + k * 24
            wob = 18 + (k * 7 + row * 11) % 30
            tone = ["#5e3a1d", "#b47a48", "#714625"][k % 3]
            body += (f'<path d="M0 {gy} C 900 {gy - wob} 1900 {gy + wob} 3840 {gy - wob / 2}" stroke="{tone}" '
                     f'stroke-opacity="0.35" stroke-width="{3 + k % 4}" fill="none"/>')
        knot_x = (row * 1373) % 3400 + 200
        body += (f'<ellipse cx="{knot_x}" cy="{y + plank_h / 2}" rx="70" ry="26" fill="#4a2c14" opacity="0.6"/>'
                 f'<ellipse cx="{knot_x}" cy="{y + plank_h / 2}" rx="120" ry="48" fill="none" stroke="#4a2c14" stroke-opacity="0.35" stroke-width="5"/>')
        body += f'<rect x="0" y="{y + plank_h - 6}" width="3840" height="6" fill="#3e2511"/>'
    return defs, body


def wp_nord():
    """Arctic night: snowy peaks under an aurora."""
    defs = (lin("sky", [(0, "#2e3440", 1), (1, "#434c5e", 1)], 0, 0, 0, 1)
            + lin("aurora", [(0, "#88c0d0", 0), (0.5, "#8fbcbb", 0.55), (1, "#a3be8c", 0)], 0, 0, 1, 0))
    body = '<rect width="3840" height="2160" fill="url(#sky)"/>'
    body += '<path d="M0 700 C 900 300 1800 900 2700 450 S 3600 600 3840 350 L3840 650 C 3300 900 2600 700 1800 1050 S 600 800 0 1000 Z" fill="url(#aurora)"/>'
    body += '<path d="M0 1500 L900 900 L1500 1300 L2200 800 L3000 1350 L3840 1000 L3840 2160 L0 2160 Z" fill="#4c566a"/>'
    body += ('<path d="M900 900 L1020 980 L960 965 L900 1000 L820 975 L720 1020 Z" fill="#eceff4"/>'
             '<path d="M2200 800 L2360 910 L2290 900 L2220 945 L2140 905 L2060 900 Z" fill="#eceff4"/>')
    body += '<path d="M0 1800 L1300 1450 L2400 1750 L3840 1500 L3840 2160 L0 2160 Z" fill="#3b4252"/>'
    for i in range(80):
        body += f'<circle cx="{(i * 7919) % 3840}" cy="{(i * 3571) % 900}" r="{1.5 + i % 3}" fill="#eceff4" opacity="0.5"/>'
    return defs, body


WALLPAPERS = {"prism": wp_prism, "win11": wp_win11, "win7": wp_win7, "macos": wp_macos,
              "ubuntu": wp_ubuntu, "gnome": wp_gnome, "chromeos": wp_chromeos, "minimal": wp_minimal,
              "winxp": wp_winxp, "win10": wp_win10, "elementary": wp_elementary, "deepin": wp_deepin,
              "wood": wp_wood, "nord": wp_nord}


# --------------------------------------------------------------------------- panel layouts
#
# Each layout is described once as data. It is turned into the Plasma layout
# script (applied by plasmashell) and into the preview image (drawn here), so
# both always agree.

LAUNCHERS = ["preferred://filemanager", "preferred://browser", "applications:org.kde.konsole.desktop",
             "applications:org.kde.discover.desktop", "applications:prism-looks.desktop",
             "applications:systemsettings.desktop"]

# widget shorthands: (plugin, {config group: {key: value}})
def kickoff(icon="prismos-start"):
    return ("org.kde.plasma.kickoff", {"General": {"icon": icon}, "Shortcuts": {"global": "Alt+F1"}})


def dash(icon="view-grid"):
    return ("org.kde.plasma.kickerdash", {"General": {"icon": icon}})


def tasks(fill=True):
    return ("org.kde.plasma.icontasks", {"General": {"launchers": LAUNCHERS, "fill": fill}})


SPACER = ("org.kde.plasma.panelspacer", None)
SEP = ("org.kde.plasma.marginsseparator", None)
TRAY = ("org.kde.plasma.systemtray", None)
SHOWDESK = ("org.kde.plasma.showdesktop", None)
PAGER = ("org.kde.plasma.pager", None)
APPMENU = ("org.kde.plasma.appmenu", None)
TRASH = ("org.kde.plasma.trash", None)


def clock(show_date=True, custom=None):
    cfg = {"Appearance": {"showDate": show_date}}
    if custom:
        cfg["Appearance"].update({"dateFormat": "custom", "customDateFormat": custom, "dateDisplayFormat": "BesideTime"})
    return ("org.kde.plasma.digitalclock", cfg)


# panel: location, thickness (in gridUnits), floating, lengthMode (fill/fit), hiding, widgets
LAYOUTS = {
    "prism": [dict(location="bottom", thickness=2.9, floating=True, length="fit", hiding="none",
                   widgets=[kickoff(), tasks(False), SEP, TRAY, clock(False)])],
    "win11": [dict(location="bottom", thickness=2.7, floating=False, length="fill", hiding="none",
                   widgets=[SPACER, kickoff(), tasks(False), SPACER, TRAY, clock(True), SHOWDESK])],
    "win7": [dict(location="bottom", thickness=2.3, floating=False, length="fill", hiding="none",
                  widgets=[kickoff(), tasks(True), TRAY, clock(True), SHOWDESK])],
    "macos": [dict(location="top", thickness=1.6, floating=False, length="fill", hiding="none",
                   widgets=[kickoff(), APPMENU, SPACER, TRAY, clock(False, "ddd d. MMM")]),
              dict(location="bottom", thickness=3.4, floating=True, length="fit", hiding="dodgewindows",
                   widgets=[dash(), tasks(False), SEP, TRASH])],
    "ubuntu": [dict(location="top", thickness=1.7, floating=False, length="fill", hiding="none",
                    widgets=[PAGER, SPACER, clock(True, "ddd d. MMM"), SPACER, TRAY]),
               dict(location="left", thickness=3.2, floating=False, length="fill", hiding="none",
                    widgets=[tasks(False), SPACER, dash()])],
    "gnome": [dict(location="top", thickness=1.7, floating=False, length="fill", hiding="none",
                   widgets=[dash("view-grid"), SPACER, clock(False, "ddd d. MMM"), SPACER, TRAY]),
              dict(location="bottom", thickness=3.2, floating=True, length="fit", hiding="autohide",
                   widgets=[tasks(False)])],
    "chromeos": [dict(location="bottom", thickness=2.7, floating=True, length="fill", hiding="none",
                      widgets=[dash("view-grid"), SPACER, tasks(False), SPACER, TRAY, clock(False)])],
    "minimal": [dict(location="bottom", thickness=2.5, floating=True, length="fit", hiding="autohide",
                     widgets=[dash("view-grid"), tasks(False), SEP, TRAY, clock(False)])],
    "winxp": [dict(location="bottom", thickness=2.1, floating=False, length="fill", hiding="none",
                   widgets=[kickoff(), tasks(True), TRAY, clock(False)])],
    "win10": [dict(location="bottom", thickness=2.3, floating=False, length="fill", hiding="none",
                   widgets=[kickoff(), tasks(True), TRAY, clock(True), SHOWDESK])],
    "elementary": [dict(location="top", thickness=1.5, floating=False, length="fill", hiding="none",
                        widgets=[dash("view-grid"), SPACER, clock(True, "ddd d. MMM"), SPACER, TRAY]),
                   dict(location="bottom", thickness=3.2, floating=True, length="fit", hiding="dodgewindows",
                        widgets=[tasks(False)])],
    "deepin": [dict(location="bottom", thickness=3.4, floating=True, length="fill", hiding="none",
                    widgets=[dash("view-grid"), SPACER, tasks(False), SPACER, TRAY, clock(True)])],
    "wood": [dict(location="top", thickness=1.6, floating=False, length="fill", hiding="none",
                  widgets=[kickoff(), SPACER, clock(True, "dddd, d. MMMM"), SPACER, TRAY]),
             dict(location="bottom", thickness=3.0, floating=True, length="fit", hiding="none",
                  widgets=[tasks(False), SEP, TRASH])],
    "nord": [dict(location="bottom", thickness=2.6, floating=True, length="fill", hiding="none",
                  widgets=[kickoff(), SPACER, tasks(False), SPACER, TRAY, clock(False)])],
}

LAYOUT_PRELUDE = r"""
// Generated by tools/generate.py from presets.json – do not edit by hand.
function prismAdd(panel, plugin, config) {
    var widget = null;
    try {
        widget = panel.addWidget(plugin);
    } catch (e) {
        print("PrismOS: Widget " + plugin + " fehlt: " + e);
    }
    if (!widget || !config) {
        return widget;
    }
    for (var group in config) {
        widget.currentConfigGroup = group.split("/");
        for (var key in config[group]) {
            widget.writeConfig(key, config[group][key]);
        }
    }
    return widget;
}

function prismPanel(spec) {
    var panel = new Panel;
    panel.location = spec.location;
    panel.height = 2 * Math.ceil(gridUnit * spec.thickness / 2);
    try { panel.floating = spec.floating; } catch (e) {}
    try { panel.hiding = spec.hiding; } catch (e) {}
    if (spec.length === "fit") {
        try { panel.lengthMode = "fit"; } catch (e) {}
        panel.alignment = "center";
    }
    for (var i = 0; i < spec.widgets.length; i++) {
        prismAdd(panel, spec.widgets[i][0], spec.widgets[i][1]);
    }
    return panel;
}

function prismWallpaper(path) {
    var desktops = desktopsForActivity(currentActivity());
    for (var j = 0; j < desktops.length; j++) {
        desktops[j].wallpaperPlugin = "org.kde.image";
        desktops[j].currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
        desktops[j].writeConfig("Image", "file://" + path);
    }
}
"""


def layout_js(preset) -> str:
    panels = json.dumps(LAYOUTS[preset["key"]], indent=2, ensure_ascii=False)
    wallpaper = f"/usr/share/wallpapers/PrismOS/{preset['key']}.jpg"
    return (LAYOUT_PRELUDE
            + f"\n// {preset['name']}\nvar prismPanels = {panels};\n"
            + "for (var p = 0; p < prismPanels.length; p++) {\n    prismPanel(prismPanels[p]);\n}\n"
            + f'prismWallpaper("{wallpaper}");\n')


# --------------------------------------------------------------------------- global theme files

# --------------------------------------------------------------------------- colour schemes
#
# Looks whose colours Breeze does not cover get their own KDE colour scheme.
# panel/panel_fg are only used for the preview images.

COLOR_SCHEMES = {
    "PrismXP": dict(title="PrismOS XP", window="#ece9d8", view="#ffffff", button="#f4f3ee", fg="#000000",
                    inactive="#7f7c6f", selection="#316ac5", selection_fg="#ffffff",
                    header="#245edb", header_fg="#ffffff", wm_inactive="#7a96df",
                    complementary="#1c4fb8", complementary_fg="#ffffff", link="#0b4fc4",
                    panel="#245edb", panel_fg="#ffffff"),
    "PrismWood": dict(title="PrismOS Holz", window="#f3e9dc", view="#fffaf2", button="#ecdcc6", fg="#3b2a1a",
                      inactive="#8a7560", selection="#a65e2e", selection_fg="#fff8ef",
                      header="#6b4423", header_fg="#f8ecdc", wm_inactive="#8c6a4c",
                      complementary="#4a2e17", complementary_fg="#f3e2c9", link="#8b4513",
                      panel="#5a381b", panel_fg="#f8ecdc"),
    "PrismNord": dict(title="PrismOS Nord", window="#3b4252", view="#2e3440", button="#434c5e", fg="#eceff4",
                      inactive="#8a94a8", selection="#5e81ac", selection_fg="#eceff4",
                      header="#2e3440", header_fg="#e5e9f0", wm_inactive="#3b4252",
                      complementary="#2e3440", complementary_fg="#eceff4", link="#88c0d0",
                      panel="#2e3440", panel_fg="#e5e9f0"),
}


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def _mix(a, b, t):
    pa = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def colorscheme_file(name, c) -> str:
    def group(title, bg, fg, alt=None):
        return textwrap.dedent(f"""\
            [{title}]
            BackgroundAlternate={_rgb(alt or _mix(bg, fg, 0.04))}
            BackgroundNormal={_rgb(bg)}
            DecorationFocus={_rgb(c['selection'])}
            DecorationHover={_rgb(c['selection'])}
            ForegroundActive={_rgb(c['selection'])}
            ForegroundInactive={_rgb(_mix(fg, bg, 0.4))}
            ForegroundLink={_rgb(c['link'])}
            ForegroundNegative=218,68,83
            ForegroundNeutral=246,116,0
            ForegroundNormal={_rgb(fg)}
            ForegroundPositive=39,174,96
            ForegroundVisited={_rgb(_mix(c['link'], fg, 0.4))}

            """)
    out = group("Colors:Button", c["button"], c["fg"])
    out += group("Colors:Complementary", c["complementary"], c["complementary_fg"])
    out += group("Colors:Header", c["header"], c["header_fg"])
    out += group("Colors:Header][Inactive", c["wm_inactive"], c["header_fg"])
    out += group("Colors:Selection", c["selection"], c["selection_fg"])
    out += group("Colors:Tooltip", c["view"], c["fg"])
    out += group("Colors:View", c["view"], c["fg"])
    out += group("Colors:Window", c["window"], c["fg"])
    out += textwrap.dedent(f"""\
        [General]
        ColorScheme={name}
        Name={c['title']}
        shadeSortColumn=true

        [KDE]
        contrast=4

        [WM]
        activeBackground={_rgb(c['header'])}
        activeBlend={_rgb(c['header_fg'])}
        activeForeground={_rgb(c['header_fg'])}
        inactiveBackground={_rgb(c['wm_inactive'])}
        inactiveBlend={_rgb(c['header_fg'])}
        inactiveForeground={_rgb(_mix(c['header_fg'], c['wm_inactive'], 0.35))}
        """)
    return out


def qt_font(family: str, size: int) -> str:
    return f"{family},{size},-1,5,400,0,0,0,0,0,0,0,0,0,0,1"


def defaults_ini(preset) -> str:
    f = preset["font"]
    return textwrap.dedent(f"""\
        [kdeglobals][KDE]
        widgetStyle=Breeze

        [kdeglobals][General]
        ColorScheme={preset['colorScheme']}
        AccentColor={preset['accent']}
        accentColorFromWallpaper=false
        font={qt_font(f, 10)}
        menuFont={qt_font(f, 10)}
        toolBarFont={qt_font(f, 10)}
        smallestReadableFont={qt_font(f, 8)}

        [kdeglobals][WM]
        activeFont={qt_font(f, 10)}

        [kdeglobals][Icons]
        Theme={preset['icons']}

        [plasmarc][Theme]
        name={preset.get('plasmaTheme', 'default')}

        [kcminputrc][Mouse]
        cursorTheme={preset['cursor']}

        [kwinrc][org.kde.kdecoration2]
        library=org.kde.breeze
        theme=Breeze
        ButtonsOnLeft={preset['buttonsLeft']}
        ButtonsOnRight={preset['buttonsRight']}

        [ksplashrc][KSplash]
        Engine=KSplashQML
        Theme=org.kde.breeze.desktop
        """)


def metadata(preset) -> str:
    return json.dumps({
        "KPackageStructure": "Plasma/LookAndFeel",
        "KPlugin": {
            "Authors": [{"Name": "PrismOS"}],
            "Category": "",
            "Description": preset["description"],
            "Id": preset["id"],
            "License": "GPL-2.0-or-later",
            "Name": preset["name"],
            "Version": "1.0",
            "Website": "https://github.com/AskForAngus/AI-created-OS",
        },
        "X-Plasma-APIVersion": "2",
    }, indent=4, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- previews

def rgb(s):
    return "#%02x%02x%02x" % tuple(int(v) for v in s.split(","))


def preview_svg(preset) -> str:
    """Draw a 1920x1080 mock-up of the desktop (wallpaper, panels, a window)."""
    key = preset["key"]
    dark = preset["dark"]
    accent = rgb(preset["accent"])
    wdefs, wbody = WALLPAPERS[key]()
    gu = 22  # grid unit at preview scale (slightly enlarged for readability)
    scheme = COLOR_SCHEMES.get(preset["colorScheme"])
    dark_panel = dark or preset.get("plasmaTheme") == "breeze-dark"
    panel_bg = scheme["panel"] if scheme else ("#1f2026" if dark_panel else "#f5f6f8")
    panel_fg = scheme["panel_fg"] if scheme else ("#e8e8ee" if dark_panel else "#2b2d33")
    body = f'<g transform="scale(0.5)">{wbody}</g>'
    sw, sh = 1920, 1080
    reserved = {"top": 0, "bottom": 0, "left": 0}
    shapes = ""

    def icon_at(x, y, size, plugin, idx):
        if plugin == "org.kde.plasma.kickoff":
            return (f'<g transform="translate({x} {y}) scale({size / 64})">'
                    f'<path d="M32 6 L60 56 L4 56 Z" fill="{accent}"/></g>')
        if plugin == "org.kde.plasma.kickerdash":
            s = size / 3.2
            out = ""
            for gx in range(3):
                for gy in range(3):
                    out += f'<rect x="{x + gx * s + s * 0.15}" y="{y + gy * s + s * 0.15}" width="{s * 0.7}" height="{s * 0.7}" rx="{s * 0.2}" fill="{panel_fg}" opacity="0.85"/>'
            return out
        if plugin == "org.kde.plasma.trash":
            return f'<rect x="{x + size * 0.2}" y="{y + size * 0.15}" width="{size * 0.6}" height="{size * 0.75}" rx="{size * 0.12}" fill="{panel_fg}" opacity="0.6"/>'
        colors = ["#4a90e2", "#ff7043", "#43a047", "#ab47bc", "#ffca28", "#26c6da"]
        return f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{size * 0.25}" fill="{colors[idx % len(colors)]}"/>'

    for spec in LAYOUTS[key]:
        thick = 2 * math.ceil(gu * spec["thickness"] / 2)
        horizontal = spec["location"] in ("top", "bottom")
        floating = spec["floating"]
        margin = 8 if floating else 0
        # measure content
        items = []
        for plugin, cfg in spec["widgets"]:
            if plugin == "org.kde.plasma.icontasks":
                items += [("task", i) for i in range(len(LAUNCHERS))]
            elif plugin == "org.kde.plasma.systemtray":
                items.append(("tray", 0))
            elif plugin == "org.kde.plasma.digitalclock":
                items.append(("clock", 0))
            elif plugin == "org.kde.plasma.panelspacer":
                items.append(("spacer", 0))
            elif plugin == "org.kde.plasma.marginsseparator":
                items.append(("sep", 0))
            elif plugin == "org.kde.plasma.appmenu":
                items.append(("menu", 0))
            elif plugin == "org.kde.plasma.pager":
                items.append(("pager", 0))
            elif plugin == "org.kde.plasma.showdesktop":
                items.append(("showdesk", 0))
            else:
                items.append((plugin, 0))
        isz = thick * 0.62
        widths = {"task": thick * 0.92, "tray": thick * 2.4, "clock": thick * (2.6 if horizontal else 1),
                  "sep": thick * 0.3, "menu": thick * 6, "pager": thick * 1.6, "showdesk": thick * 0.35}
        fixed = sum(widths.get(k, thick) for k, _ in items if k != "spacer")
        if horizontal:
            full = sw - 2 * margin
            length = min(full, fixed + thick * 0.6) if spec["length"] == "fit" else full
            px = (sw - length) / 2
            py = margin if spec["location"] == "top" else sh - thick - margin
            if spec["hiding"] == "autohide":
                py = sh - thick - margin
            spacers = sum(1 for k, _ in items if k == "spacer")
            spare = max(0, length - fixed - thick * 0.6)
            spacer_w = spare / spacers if spacers else 0
            radius = thick * 0.3 if floating else 0
            opacity = 0.55 if spec["hiding"] == "autohide" else 0.92
            shapes += (f'<rect x="{px}" y="{py}" width="{length}" height="{thick}" rx="{radius}" '
                       f'fill="{panel_bg}" opacity="{opacity}"/>')
            if spec["hiding"] != "autohide":
                reserved[spec["location"]] = thick + 2 * margin
            cx = px + thick * 0.3
            for kind, idx in items:
                w = spacer_w if kind == "spacer" else widths.get(kind, thick)
                cy = py + (thick - isz) / 2
                if kind == "task":
                    shapes += icon_at(cx + (w - isz) / 2, cy, isz, "task", idx)
                    if idx in (0, 2):
                        shapes += f'<rect x="{cx + w / 2 - 6}" y="{py + thick - 4}" width="12" height="3" rx="1.5" fill="{accent}"/>'
                elif kind == "tray":
                    for t in range(4):
                        shapes += f'<circle cx="{cx + w * (0.15 + t * 0.22)}" cy="{py + thick / 2}" r="{thick * 0.11}" fill="{panel_fg}" opacity="0.7"/>'
                elif kind == "clock":
                    fs = thick * (0.32 if spec["thickness"] > 2 else 0.5)
                    shapes += (f'<text x="{cx + w / 2}" y="{py + thick / 2 + fs * 0.35}" font-family="{FONT}" '
                               f'font-size="{fs}" fill="{panel_fg}" text-anchor="middle">14:37</text>')
                elif kind == "menu":
                    fs = thick * 0.5
                    tx = cx
                    for t, label in enumerate(["Dateien", "Datei", "Bearbeiten", "Ansicht", "Hilfe"]):
                        shapes += (f'<text x="{tx}" y="{py + thick / 2 + fs * 0.35}" font-family="{FONT}" '
                                   f'font-size="{fs}" font-weight="{700 if t == 0 else 400}" fill="{panel_fg}">{label}</text>')
                        tx += fs * (0.62 * len(label) + 1.4)
                elif kind == "pager":
                    for t in range(3):
                        shapes += f'<circle cx="{cx + w * (0.25 + t * 0.25)}" cy="{py + thick / 2}" r="{thick * (0.14 if t else 0.18)}" fill="{panel_fg}" opacity="{0.9 if t == 0 else 0.45}"/>'
                elif kind == "sep":
                    shapes += f'<rect x="{cx + w / 2 - 1}" y="{py + thick * 0.2}" width="2" height="{thick * 0.6}" fill="{panel_fg}" opacity="0.3"/>'
                elif kind == "showdesk":
                    shapes += f'<rect x="{cx + w / 2 - 1}" y="{py + thick * 0.25}" width="2" height="{thick * 0.5}" fill="{panel_fg}" opacity="0.4"/>'
                elif kind != "spacer":
                    shapes += icon_at(cx + (w - isz) / 2, cy, isz, kind, idx)
                cx += w
        else:  # vertical (left)
            px, py = 0, reserved["top"]
            length = sh - py
            shapes += f'<rect x="{px}" y="{py}" width="{thick}" height="{length}" fill="{panel_bg}" opacity="0.92"/>'
            reserved["left"] = thick
            cy = py + thick * 0.2
            for kind, idx in items:
                if kind == "task":
                    shapes += icon_at(px + (thick - isz) / 2, cy, isz, "task", idx)
                    cy += thick * 0.95
                elif kind == "spacer":
                    pass
                else:
                    shapes += icon_at(px + (thick - isz) / 2, sh - thick * 1.1, isz, kind, idx)

    # one application window
    wx, wy = reserved["left"] + 170, reserved["top"] + 150
    ww, wh = 900, 540
    win_bg = scheme["view"] if scheme else ("#2a2b31" if dark else "#ffffff")
    title_bg = scheme["header"] if scheme else ("#232429" if dark else "#eef0f3")
    fg = scheme["fg"] if scheme else ("#e8e8ee" if dark else "#2b2d33")
    title_fg = scheme["header_fg"] if scheme else fg
    shapes += (f'<rect x="{wx + 8}" y="{wy + 14}" width="{ww}" height="{wh}" rx="12" fill="#000" opacity="0.25"/>'
               f'<rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="12" fill="{win_bg}"/>'
               f'<path d="M{wx} {wy + 12} a12 12 0 0 1 12 -12 h{ww - 24} a12 12 0 0 1 12 12 v34 h-{ww} Z" fill="{title_bg}"/>'
               f'<text x="{wx + ww / 2}" y="{wy + 30}" font-family="{FONT}" font-size="17" fill="{title_fg}" '
               f'text-anchor="middle" opacity="0.8">Dateien – Persönlicher Ordner</text>')
    mac = preset["buttonsLeft"].startswith("X")
    left = preset["buttonsLeft"].replace("M", "").replace("S", "")
    right = preset["buttonsRight"].replace("H", "")
    traffic = {"X": "#ff5f57", "I": "#febc2e", "A": "#28c840"}
    for i, b in enumerate(left):
        color = traffic[b] if mac else fg
        shapes += f'<circle cx="{wx + 24 + i * 26}" cy="{wy + 23}" r="8" fill="{color}" opacity="{1 if mac else 0.6}"/>'
    for i, b in enumerate(reversed(right)):
        bx = wx + ww - 26 - i * 34
        if b == "X":
            shapes += f'<path d="M{bx - 6} {wy + 17} l12 12 M{bx + 6} {wy + 17} l-12 12" stroke="{fg}" stroke-width="2"/>'
        elif b == "A":
            shapes += f'<rect x="{bx - 6}" y="{wy + 17}" width="12" height="12" fill="none" stroke="{fg}" stroke-width="2"/>'
        elif b == "I":
            shapes += f'<path d="M{bx - 6} {wy + 29} h12" stroke="{fg}" stroke-width="2"/>'
    # sidebar + content tiles
    shapes += f'<rect x="{wx}" y="{wy + 46}" width="210" height="{wh - 58}" fill="{title_bg}" opacity="0.6"/>'
    for i in range(6):
        shapes += f'<rect x="{wx + 22}" y="{wy + 72 + i * 38}" width="{150 - (i % 3) * 25}" height="14" rx="7" fill="{fg}" opacity="0.25"/>'
    shapes += f'<rect x="{wx + 22}" y="{wy + 66}" width="170" height="26" rx="6" fill="{accent}" opacity="0.35"/>'
    for r in range(3):
        for c in range(4):
            tx, ty = wx + 250 + c * 155, wy + 80 + r * 150
            shapes += (f'<rect x="{tx}" y="{ty}" width="90" height="72" rx="8" fill="{accent}" opacity="{0.55 + 0.1 * ((r + c) % 3)}"/>'
                       f'<rect x="{tx + 5}" y="{ty + 86}" width="80" height="10" rx="5" fill="{fg}" opacity="0.3"/>')
    return svg_doc(body + shapes, sw, sh, wdefs)


# --------------------------------------------------------------------------- installer + boot artwork

def banner_svg(width, height, title, lines, show_logo=True, title_size=64):
    defs = (lin("bg", [(0, "#0d0b24", 1), (0.6, "#1d1352", 1), (1, "#33106e", 1)], 0, 0, 1, 1)
            + rad("glow", [(0, "#8f6bff", 0.45), (1, "#8f6bff", 0)], 0.5, 0.5, 0.5))
    body = (f'<rect width="{width}" height="{height}" fill="url(#bg)"/>'
            f'<ellipse cx="{width * 0.75}" cy="{height * 0.5}" rx="{width * 0.5}" ry="{height * 0.7}" fill="url(#glow)"/>')
    for i, c in enumerate(RAINBOW):
        y = height * 0.82 + i * height * 0.025
        body += f'<rect x="0" y="{y}" width="{width}" height="{height * 0.02}" fill="{c}" opacity="0.18"/>'
    x = width * 0.07
    if show_logo:
        logo = logo_svg(512)
        ldefs, lbody = logo.split("<defs>", 1)[1].split("</defs>", 1)
        lbody = lbody.replace("</svg>", "")
        s = height * 0.5 / 512
        body += f'<g transform="translate({width * 0.68} {height * 0.16}) scale({s})">{lbody}</g>'
        defs += ldefs
    y = height * 0.3
    body += (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{title_size}" font-weight="700" '
             f'fill="#ffffff">{title}</text>')
    y += title_size * 0.95
    for line in lines:
        body += (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{title_size * 0.42}" '
                 f'fill="#d9d2ff">{line}</text>')
        y += title_size * 0.62
    return svg_doc(body, width, height, defs)


SLIDES = [
    ("Willkommen bei PrismOS", ["Dein System. Dein Look.", "Nur das, was du wirklich willst."]),
    ("Jeder Look ist möglich", ["Prism Looks: Windows-, macOS-, Ubuntu-,", "GNOME- oder ChromeOS-Stil mit einem Klick,",
                                "eigene Looks speichern und teilen."]),
    ("Keine Bloatware", ["Vorinstalliert sind nur die Grundlagen.", "Alles andere hast du selbst ausgewählt."]),
    ("Schnell und sparsam", ["Zen-Kernel für flüssiges Arbeiten,", "Energieprofile, zram und",
                             "Stromspar-Optimierungen für lange Akkulaufzeit."]),
    ("Software, wie du willst", ["Discover installiert Programme aus Arch Linux", "und Flathub. Updates per Klick."]),
]


# --------------------------------------------------------------------------- main

def main():
    wp_dir = LOOKS / "usr/share/wallpapers/PrismOS"
    for preset in PRESETS:
        key = preset["key"]
        defs, body = WALLPAPERS[key]()
        save_jpg(svg_doc(body, W, H, defs), wp_dir / f"{key}.jpg", W, H)

        pkg = LOOKS / "usr/share/plasma/look-and-feel" / preset["id"]
        (pkg / "contents/layouts").mkdir(parents=True, exist_ok=True)
        (pkg / "metadata.json").write_text(metadata(preset))
        (pkg / "contents/defaults").write_text(defaults_ini(preset))
        (pkg / "contents/layouts/org.kde.plasma.desktop-layout.js").write_text(layout_js(preset))
        prev = preview_svg(preset)
        save_png(prev, pkg / "contents/previews/preview.png", 640, 360)
        save_jpg(prev, pkg / "contents/previews/fullscreenpreview.jpg", 1920, 1080, 88)
        print("look:", preset["id"])

    schemes = LOOKS / "usr/share/color-schemes"
    schemes.mkdir(parents=True, exist_ok=True)
    for name, colors in COLOR_SCHEMES.items():
        (schemes / f"{name}.colors").write_text(colorscheme_file(name, colors))

    icons = LOOKS / "usr/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True, exist_ok=True)
    (icons / "prismos.svg").write_text(logo_svg(512))
    (icons / "prismos-start.svg").write_text(start_icon_svg())
    (icons / "prismos-looks.svg").write_text(looks_icon_svg())
    live_icons = ROOTFS / "usr/share/icons/hicolor/scalable/apps"
    live_icons.mkdir(parents=True, exist_ok=True)
    (live_icons / "prismos-installer.svg").write_text(installer_icon_svg())

    brand = ROOTFS / "etc/calamares/branding/prismos"
    save_png(logo_svg(512), brand / "logo.png", 256, 256)
    save_png(logo_svg(512), brand / "icon.png", 128, 128)
    save_png(banner_svg(1000, 360, "PrismOS", ["Dein System. Dein Look."], title_size=96),
             brand / "welcome.png", 1000, 360)
    for i, (title, lines) in enumerate(SLIDES, 1):
        save_png(banner_svg(1000, 560, title, lines, show_logo=(i == 1), title_size=58),
                 brand / f"slide{i}.png", 1000, 560)

    splash = banner_svg(640, 480, "", [], show_logo=False)
    save_png(splash, ISO / "syslinux/splash.png", 640, 480)
    print("artwork done")


if __name__ == "__main__":
    main()
