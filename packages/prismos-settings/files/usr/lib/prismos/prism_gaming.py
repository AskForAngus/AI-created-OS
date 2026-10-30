#!/usr/bin/env python3
"""PrismOS Gaming-Modus: one switch for the best gaming settings.

  prism-gaming on | off | toggle   switch the gaming mode
  prism-gaming status              prints "on" or "off"
  prism-gaming --hold              (internal) keeps "do not disturb" and the
                                   screen-saver inhibition alive while gaming

Everything that is changed is remembered and restored when switching off.
Which parts are used is set in ~/.config/prism-gaming.json (tab "Gaming" in
Prism Einstellungen): performance, quiet, display.
"""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "prism-gaming.json"
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "prism-gaming.json"
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
HELPER = "/usr/lib/prismos/prism-gaming-helper"
DEFAULTS = {"performance": True, "quiet": True, "display": True}
# Effects that cost GPU time and are paused while gaming
HEAVY_EFFECTS = ["blur", "contrast", "wobblywindows", "magiclamp", "squash", "slide", "fadingpopups",
                 "scale", "glide", "sheet", "dimscreen", "backgroundcontrast", "translucency",
                 "slidingpopups", "maximize", "kwin4_effect_fadingpopups"]


def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kw)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(cmd, 127, "", "")


def load_config():
    try:
        return {**DEFAULTS, **json.loads(CONFIG.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return None


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def hold_alive(state):
    """True if the --hold process of this state still runs (and is really ours,
    not another program that got the same process number after a restart)."""
    pid = state.get("hold_pid")
    if not pid or state.get("boot") != boot_id():
        return False
    try:
        cmdline = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
    except (OSError, ValueError):
        return False
    return b"--hold" in cmdline and any(arg.endswith(b"prism_gaming.py") for arg in cmdline)


def is_on():
    """On while the state file exists. After a restart (or if the helper process
    died) the old state is undone, so nothing stays changed by accident."""
    state = load_state()
    if not state:
        return False
    stale = state.get("boot") != boot_id() or (state.get("hold_pid") and not hold_alive(state))
    if stale:
        turn_off(quiet=True)
        return False
    return True


def notify(title, body):
    run(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
         "--object-path", "/org/freedesktop/Notifications", "--method", "org.freedesktop.Notifications.Notify",
         "PrismOS", "0", "input-gaming", title, body, "[]", "{\"transient\": <true>}", "4000"])


# --------------------------------------------------------------------------- parts

def kwin_effects(method, name=None):
    if method == "loaded":
        out = run(["gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Effects",
                   "--method", "org.freedesktop.DBus.Properties.Get", "org.kde.kwin.Effects", "loadedEffects"]).stdout
        return [e for e in out.replace("(<[", "").replace("]>,)", "").replace("'", "").split(", ") if e.strip()]
    run(["gdbus", "call", "--session", "--dest", "org.kde.KWin", "--object-path", "/Effects",
         "--method", f"org.kde.kwin.Effects.{method}", name])
    return []


def displays():
    out = run(["kscreen-doctor", "-j"]).stdout
    try:
        return json.loads(out).get("outputs", [])
    except ValueError:
        return []


def best_mode(output):
    """Highest refresh rate at the current resolution."""
    current = next((m for m in output.get("modes", []) if m.get("id") == output.get("currentModeId")), None)
    if not current:
        return None
    same_size = [m for m in output["modes"] if m.get("size") == current.get("size")]
    return max(same_size, key=lambda m: m.get("refreshRate", 0), default=None)


def start_hold():
    proc = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--hold"], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.pid


def hold():
    """Keep "do not disturb" and the idle inhibition until we are terminated.
    Both are bound to this process' D-Bus connection and end with it."""
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBusConnection, QDBusInterface
    app = QCoreApplication(sys.argv)
    bus = QDBusConnection.sessionBus()
    notes = QDBusInterface("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                           "org.freedesktop.Notifications", bus)
    notes.call("Inhibit", "prism-gaming", "Gaming-Modus", {})
    saver = QDBusInterface("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", bus)
    saver.call("Inhibit", "prism-gaming", "Gaming-Modus")
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    from PyQt6.QtCore import QTimer
    tick = QTimer()  # lets Python handle the signals
    tick.start(500)
    tick.timeout.connect(lambda: None)
    app.exec()


# --------------------------------------------------------------------------- on / off

def turn_on():
    if is_on():
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    state = {"since": time.time(), "boot": boot_id()}
    if cfg["performance"]:
        state["profile"] = run(["powerprofilesctl", "get"]).stdout.strip() or "balanced"
        run(["powerprofilesctl", "set", "performance"])
        run(["pkexec", HELPER, "charge-full"])
    if cfg["quiet"]:
        run(["pkexec", HELPER, "maintenance-pause"])
        loaded = kwin_effects("loaded")
        paused = [e for e in loaded if e in HEAVY_EFFECTS]
        for e in paused:
            kwin_effects("unloadEffect", e)
        state["effects"] = paused
        state["discover_notifier"] = run(["pkill", "-x", "DiscoverNotifier"]).returncode == 0
    if cfg["performance"] or cfg["quiet"]:
        state["hold_pid"] = start_hold()
    if cfg["display"]:
        restore = []
        for out in displays():
            if not out.get("enabled"):
                continue
            name = out.get("name")
            best = best_mode(out)
            restore.append({"name": name, "mode": out.get("currentModeId"), "vrr": out.get("vrrPolicy")})
            if best and best.get("id") != out.get("currentModeId"):
                run(["kscreen-doctor", f"output.{name}.mode.{best['id']}"])
            run(["kscreen-doctor", f"output.{name}.vrrpolicy.automatic"])
        state["displays"] = restore
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))
    notify("Gaming-Modus an", "Volle Leistung, keine Störungen. Viel Spaß!")


def turn_off(quiet=False):
    state = load_state()
    if not state:
        return
    if "profile" in state:
        run(["powerprofilesctl", "set", state["profile"]])
        run(["pkexec", HELPER, "charge-restore"])
    if "effects" in state:
        run(["pkexec", HELPER, "maintenance-resume"])
        for e in state["effects"]:
            kwin_effects("loadEffect", e)
        if state.get("discover_notifier") and Path("/usr/lib/DiscoverNotifier").exists():
            subprocess.Popen(["/usr/lib/DiscoverNotifier"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if hold_alive(state):
        try:
            os.kill(int(state["hold_pid"]), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    vrr_names = {0: "never", 1: "always", 2: "automatic"}
    for d in state.get("displays", []):
        if d.get("mode"):
            run(["kscreen-doctor", f"output.{d['name']}.mode.{d['mode']}"])
        if d.get("vrr") is not None:
            run(["kscreen-doctor", f"output.{d['name']}.vrrpolicy.{vrr_names.get(d['vrr'], d['vrr'])}"])
    STATE.unlink(missing_ok=True)
    if not quiet:
        notify("Gaming-Modus aus", "Alle Einstellungen sind wiederhergestellt.")


def single_instance():
    """A second click while switching is still running is ignored."""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock = open(RUNTIME / "prism-gaming.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return lock


def main(argv):
    cmd = argv[0] if argv else "toggle"
    if cmd in ("on", "off", "toggle"):
        lock = single_instance()
        if lock is None:
            return 0
    if cmd == "--hold":
        hold()
    elif cmd == "on":
        turn_on()
    elif cmd == "off":
        turn_off()
    elif cmd == "toggle":
        turn_off() if is_on() else turn_on()
    elif cmd == "status":
        print("on" if is_on() else "off")
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
