#!/usr/bin/env python3
"""PrismOS Calamares job: programs, drivers and look of the new system.

Builds /var/lib/prismos/plan.json in the target from
  * the programs ticked on the netinstall page ("flathub:" entries are Flatpaks),
  * matching language packs (e.g. firefox-i18n-de),
  * hardware: NVIDIA driver for Turing and newer, guest tools in virtual machines,
  * the look picked on the plasmalnf page,
and runs prism-apply-plan in the target. Without internet the plan stays and
is completed automatically after the first boot with a connection.
"""

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import libcalamares
from libcalamares.utils import debug, target_env_call, warning

LIVE_USER_HOME = Path("/home/liveuser")
DEFAULT_LOOK = "org.prismos.prism.desktop"

# extra packages pulled in when a program is chosen ({lang} = language code)
LANG_PACKS = {
    "firefox": ["firefox-i18n-{lang}"],
    "thunderbird": ["thunderbird-i18n-{lang}"],
    "libreoffice-fresh": ["libreoffice-fresh-{lang}", "hunspell-{lang}"],
}
COMPANIONS = {
    "vlc": ["vlc-plugins-all"],
    "docker": ["docker-buildx"],
    "podman": ["podman-compose"],
    "cups": ["cups-pdf", "system-config-printer"],
}
SERVICES = {
    "cups": ["cups.socket"],
    "docker": ["docker.socket"],
    "ufw": ["ufw.service"],
    "kdeconnect": [],
}
GROUPS = {"docker": "docker"}

_status = "Programme werden vorbereitet …"


def pretty_name():
    return "Programme, Treiber und Look werden eingerichtet"


def pretty_status_message():
    return _status


def set_status(msg, progress):
    global _status
    _status = msg
    libcalamares.job.setprogress(progress)
    debug(f"prismapps: {msg}")


# --------------------------------------------------------------------------- selection

def selected_items():
    """Package names ticked on the netinstall page."""
    names = []
    for op in libcalamares.globalstorage.value("packageOperations") or []:
        for kind in ("install", "try_install"):
            for item in op.get(kind) or []:
                name = item if isinstance(item, str) else (item.get("package") or item.get("name"))
                if name:
                    names.append(name.strip())
    return list(dict.fromkeys(names))


def language_code():
    conf = libcalamares.globalstorage.value("localeConf") or {}
    lang = conf.get("LANG") or "de_DE.UTF-8"
    return lang.split("_")[0].split(".")[0].lower()


# --------------------------------------------------------------------------- hardware

def pci_display_devices():
    devices = []
    for dev in Path("/sys/bus/pci/devices").glob("*"):
        try:
            cls = (dev / "class").read_text().strip()
            if cls.startswith("0x03"):
                devices.append(((dev / "vendor").read_text().strip(), int((dev / "device").read_text(), 16)))
        except (OSError, ValueError):
            continue
    return devices


def gpu_plan():
    """NVIDIA's open kernel modules support Turing (device id 0x1e00) and newer.
    Older cards keep the open nouveau driver that is already installed."""
    gpus = pci_display_devices()
    nvidia = [d for v, d in gpus if v == "0x10de"]
    other = [v for v, _ in gpus if v in ("0x8086", "0x1002")]
    if not nvidia:
        return [], [], False
    if max(nvidia) < 0x1E00:
        warning("prismapps: ältere NVIDIA-Karte erkannt, nouveau bleibt aktiv")
        return [], [], False
    pkgs = ["nvidia-open-dkms", "nvidia-utils", "nvidia-settings", "linux-zen-headers", "linux-lts-headers"]
    if other:
        pkgs.append("nvidia-prime")  # "prime-run" starts programs on the NVIDIA card
    services = ["nvidia-suspend.service", "nvidia-hibernate.service", "nvidia-resume.service"]
    return pkgs, services, True


def vm_plan():
    try:
        virt = subprocess.run(["systemd-detect-virt", "--vm"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return [], []
    return {
        "oracle": (["virtualbox-guest-utils"], ["vboxservice.service"]),
        "kvm": (["qemu-guest-agent", "spice-vdagent"], []),
        "qemu": (["qemu-guest-agent", "spice-vdagent"], []),
        "vmware": (["open-vm-tools"], ["vmtoolsd.service", "vmware-vmblock-fuse.service"]),
    }.get(virt, ([], []))


# --------------------------------------------------------------------------- look

def chosen_look(root):
    """The look picked on the plasmalnf page. plasmalnf previews the choice on the
    live desktop, so the live user's kdeglobals is the reliable source."""
    gs = libcalamares.globalstorage
    candidates = [gs.value("plasmaLnf"), gs.value("plasmalnf")]
    kdeglobals = LIVE_USER_HOME / ".config/kdeglobals"
    if kdeglobals.exists():
        section = None
        for line in kdeglobals.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line
            elif section == "[KDE]" and line.startswith("LookAndFeelPackage="):
                candidates.append(line.split("=", 1)[1].strip())
    for lnf in candidates:
        if isinstance(lnf, str) and lnf and (Path(root) / "usr/share/plasma/look-and-feel" / lnf).is_dir():
            return lnf
    return DEFAULT_LOOK


def write_look(root, lnf):
    path = Path(root) / "etc/xdg/kdeglobals"
    lines = path.read_text().splitlines() if path.exists() else []
    out, in_kde, written = [], False, False
    for line in lines:
        if line.strip().startswith("["):
            if in_kde and not written:
                out.append(f"LookAndFeelPackage={lnf}")
                written = True
            in_kde = line.strip() == "[KDE]"
        elif in_kde and line.startswith("LookAndFeelPackage="):
            line = f"LookAndFeelPackage={lnf}"
            written = True
        out.append(line)
    if not written:
        if not in_kde:
            out.append("[KDE]")
        out.append(f"LookAndFeelPackage={lnf}")
    path.write_text("\n".join(out) + "\n")


# --------------------------------------------------------------------------- run

def is_online():
    try:
        with socket.create_connection(("geo.mirror.pkgbuild.com", 443), timeout=5):
            return True
    except OSError:
        return False


def run():
    gs = libcalamares.globalstorage
    root = gs.value("rootMountPoint")
    user = gs.value("username")

    set_status("Look wird übernommen …", 0.02)
    lnf = chosen_look(root)
    write_look(root, lnf)
    debug(f"prismapps: Look {lnf}")

    set_status("Auswahl wird zusammengestellt …", 0.05)
    lang = language_code()
    pacman, optional, flatpak, services, groups = [], [], [], [], {}
    for name in selected_items():
        if name.startswith("flathub:"):
            flatpak.append(name.split(":", 1)[1])
            continue
        pacman.append(name)
        optional += [p.format(lang=lang) for p in LANG_PACKS.get(name, [])]
        optional += COMPANIONS.get(name, [])
        services += SERVICES.get(name, [])
        if name in GROUPS and user:
            groups.setdefault(GROUPS[name], []).append(user)

    gpu_pkgs, gpu_services, drop_kms = gpu_plan()
    vm_pkgs, vm_services = vm_plan()
    pacman = list(dict.fromkeys(pacman + gpu_pkgs + vm_pkgs))

    plan = {
        "refresh_mirrors": True,
        "pacman": pacman,
        "pacman_optional": list(dict.fromkeys(optional)),
        "flatpak": list(dict.fromkeys(flatpak)),
        "services": list(dict.fromkeys(services + gpu_services + vm_services)),
        "groups": groups,
        "drop_kms_hook": drop_kms,
    }
    plan_path = Path(root) / "var/lib/prismos/plan.json"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2))
    debug(f"prismapps: Plan {plan}")

    if not is_online():
        warning("prismapps: offline – Programme werden nach dem ersten Start nachinstalliert")
        target_env_call(["systemctl", "enable", "prism-pending-apps.timer"])
        return None

    # The chroot needs working name resolution for pacman and flatpak.
    resolv = Path(root) / "etc/resolv.conf"
    backup = None
    if resolv.is_symlink() or resolv.exists():
        backup = Path(root) / "etc/resolv.conf.prismos-backup"
        os.replace(resolv, backup)
    try:
        shutil.copy2("/etc/resolv.conf", resolv)
    except OSError as exc:
        warning(f"prismapps: resolv.conf: {exc}")

    set_status("Programme und Treiber werden heruntergeladen und installiert …", 0.15)
    rc = target_env_call(["/usr/local/lib/prismos/prism-apply-plan"])
    debug(f"prismapps: prism-apply-plan beendet mit {rc}")

    resolv.unlink(missing_ok=True)
    if backup is not None:
        os.replace(backup, resolv)

    set_status("Fertig", 1.0)
    if rc not in (0, 2):
        warning("prismapps: einige Programme werden nach dem ersten Start nachinstalliert")
        target_env_call(["systemctl", "enable", "prism-pending-apps.timer"])
    return None
