#!/usr/bin/env python3
"""Network overview for the PrismOS network widget (prints JSON).

  prism-netinfo              all local details of active connections, live traffic
  prism-netinfo --public     public IP address and provider (asks ipinfo.io)
  prism-netinfo --ping       latency to the router and to the internet
  prism-netinfo --speedtest  short download/upload test (speed.cloudflare.com)

Only the last three contact external services, and only when asked to.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
COUNTERS = RUNTIME / "prism-netinfo-counters.json"
UA = {"User-Agent": "PrismOS-netinfo/1.0"}


def run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def human_rate(bytes_per_s):
    bits = bytes_per_s * 8
    if bits >= 1e6:
        return f"{bits / 1e6:.1f} Mbit/s"
    return f"{bits / 1e3:.0f} kbit/s"


def device_details(dev):
    info = {"ipv4": [], "ipv6": [], "dns": []}
    for line in run(["nmcli", "-t", "-e", "no", "device", "show", dev]).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if not value or value == "--":
            continue
        key = re.sub(r"\[\d+\]$", "", key)
        if key == "GENERAL.HWADDR":
            info["mac"] = value
        elif key == "GENERAL.MTU":
            info["mtu"] = value
        elif key == "IP4.ADDRESS":
            info["ipv4"].append(value)
        elif key == "IP6.ADDRESS" and not value.lower().startswith("fe80"):
            info["ipv6"].append(value)
        elif key == "IP4.GATEWAY":
            info["gateway4"] = value
        elif key == "IP6.GATEWAY":
            info["gateway6"] = value
        elif key in ("IP4.DNS", "IP6.DNS"):
            info["dns"].append(value)
        elif key == "IP4.DOMAIN":
            info["domain"] = value
    return info


def wifi_details(dev):
    out = run(["nmcli", "-t", "-e", "no", "-f", "IN-USE,SIGNAL,FREQ,CHAN,RATE,SECURITY,SSID",
               "device", "wifi", "list", "ifname", dev, "--rescan", "no"])
    for line in out.splitlines():
        parts = line.split(":", 6)
        if len(parts) == 7 and parts[0] == "*":
            return {"signal": int(parts[1] or 0), "frequency": parts[2], "channel": parts[3],
                    "rate": parts[4], "security": parts[5] or "offen", "ssid": parts[6]}
    return {}


def traffic(devs):
    now = time.monotonic()
    try:
        old = json.loads(COUNTERS.read_text())
    except (OSError, ValueError):
        old = {}
    result, new = {}, {"time": now}
    for dev in devs:
        stats = Path("/sys/class/net") / dev / "statistics"
        try:
            rx = int((stats / "rx_bytes").read_text())
            tx = int((stats / "tx_bytes").read_text())
        except (OSError, ValueError):
            continue
        new[dev] = [rx, tx]
        entry = {"rx_total": human_bytes(rx), "tx_total": human_bytes(tx)}
        if dev in old and now > old.get("time", now):
            dt = now - old["time"]
            entry["rx_rate"] = human_rate(max(0, rx - old[dev][0]) / dt)
            entry["tx_rate"] = human_rate(max(0, tx - old[dev][1]) / dt)
        result[dev] = entry
    try:
        COUNTERS.write_text(json.dumps(new))
    except OSError:
        pass
    return result


def overview():
    conns = []
    for line in run(["nmcli", "-t", "-e", "no", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"]).splitlines():
        parts = line.split(":", 3)
        if len(parts) < 4 or parts[1] in ("loopback", "wifi-p2p") or not parts[2].startswith("connected"):
            continue
        dev, kind, state, name = parts
        c = {"device": dev, "type": kind, "name": name, **device_details(dev)}
        if kind == "wifi":
            c["wifi"] = wifi_details(dev)
        speed = Path("/sys/class/net") / dev / "speed"
        try:
            mbit = int(speed.read_text())
            if mbit > 0:
                c["link_speed"] = f"{mbit} Mbit/s"
        except (OSError, ValueError):
            pass
        conns.append(c)
    rates = traffic([c["device"] for c in conns])
    for c in conns:
        c.update(rates.get(c["device"], {}))
    state = run(["nmcli", "-t", "-f", "CONNECTIVITY", "general"]).strip()
    return {"kind": "overview", "hostname": socket.gethostname(), "connectivity": state, "connections": conns}


def public_ip():
    try:
        with urllib.request.urlopen(urllib.request.Request("https://ipinfo.io/json", headers=UA), timeout=8) as r:
            data = json.load(r)
        return {"kind": "public", "ip": data.get("ip"), "provider": data.get("org"),
                "location": ", ".join(x for x in (data.get("city"), data.get("country")) if x)}
    except Exception as exc:  # shown in the widget
        return {"kind": "public", "error": f"Nicht erreichbar ({exc.__class__.__name__})"}


def ping(host):
    out = run(["ping", "-c", "4", "-W", "2", host], timeout=20)
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
    avg = re.search(r"= [\d.]+/([\d.]+)/", out)
    return {"host": host, "avg_ms": float(avg.group(1)) if avg else None,
            "loss": float(loss.group(1)) if loss else 100.0}


def ping_test():
    gateway = None
    for line in run(["ip", "route", "show", "default"]).splitlines():
        m = re.search(r"via (\S+)", line)
        if m:
            gateway = m.group(1)
            break
    result = {"kind": "ping", "internet": ping("1.1.1.1")}
    if gateway:
        result["router"] = ping(gateway)
    return result


def speedtest():
    result = {"kind": "speedtest"}
    try:
        size = 25_000_000
        start = time.monotonic()
        req = urllib.request.Request(f"https://speed.cloudflare.com/__down?bytes={size}", headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r:
            got = 0
            while chunk := r.read(1 << 16):
                got += len(chunk)
        result["download"] = f"{got * 8 / (time.monotonic() - start) / 1e6:.0f} Mbit/s"
        payload = os.urandom(5_000_000)
        start = time.monotonic()
        req = urllib.request.Request("https://speed.cloudflare.com/__up", data=payload, headers=UA, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        result["upload"] = f"{len(payload) * 8 / (time.monotonic() - start) / 1e6:.0f} Mbit/s"
    except Exception as exc:
        result["error"] = f"Test fehlgeschlagen ({exc.__class__.__name__})"
    return result


def main(argv):
    if "--public" in argv:
        data = public_ip()
    elif "--ping" in argv:
        data = ping_test()
    elif "--speedtest" in argv:
        data = speedtest()
    else:
        data = overview()
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
