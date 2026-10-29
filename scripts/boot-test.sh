#!/bin/bash
# Boot the PrismOS ISO in QEMU (BIOS and UEFI) and take screenshots of the boot
# menu and the live desktop. Screenshots are saved to OUT_DIR and also printed
# as base64 between BEGIN-SCREENSHOT/END-SCREENSHOT markers in the CI log.
#
#   scripts/boot-test.sh ISO [OUT_DIR]
#   scripts/boot-test.sh --disk DISK_IMAGE [OUT_DIR]   boot an installed system (BIOS)
#   scripts/boot-test.sh --uefi-disk DISK_IMAGE [OUT_DIR]
#       boot an encrypted UEFI install: types the disk password when asked,
#       then boots once more with Secure Boot enabled (shim should start)
#
# Needs: qemu-system-x86_64, socat, ImageMagick, OVMF (for UEFI).
set -uo pipefail

DISK=""
UEFI_DISK=""
if [[ $1 == --disk ]]; then
    DISK=$2
    shift 2
    ISO=""
elif [[ $1 == --uefi-disk ]]; then
    UEFI_DISK=$2
    shift 2
    ISO=""
else
    ISO=$1
    shift
fi
OUT=${1:-boot-test}
mkdir -p "$OUT"
OVMF_CODE=$(ls /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd 2>/dev/null | head -1)
OVMF_VARS=$(ls /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd 2>/dev/null | head -1)
accel=(-accel tcg)
[[ -w /dev/kvm ]] && accel=(-enable-kvm -cpu host)

shot() {  # monitor-socket name
    local ppm="$OUT/$2.ppm"
    echo "screendump $ppm" | socat - "UNIX-CONNECT:$1" >/dev/null
    sleep 2
    if [[ -s $ppm ]]; then
        convert "$ppm" -resize 800x "$OUT/$2.png"
        convert "$ppm" -resize 720x -quality 50 "$OUT/$2.jpg"
        rm -f "$ppm"
        echo "BEGIN-SCREENSHOT $2"
        base64 -w0 "$OUT/$2.jpg"
        echo
        echo "END-SCREENSHOT $2"
    else
        echo "Kein Screenshot: $2"
    fi
}

boot() {  # mode
    local mode=$1 mon="/tmp/prismos-qmon-$1" firmware=()
    rm -f "$mon"
    if [[ $mode == uefi ]]; then
        [[ -n $OVMF_CODE ]] || { echo "OVMF fehlt, UEFI-Test übersprungen"; return; }
        cp "$OVMF_VARS" /tmp/prismos-vars.fd
        firmware=(-drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE"
                  -drive "if=pflash,format=raw,file=/tmp/prismos-vars.fd")
    fi
    echo "== Boot-Test $mode =="
    qemu-system-x86_64 "${accel[@]}" -m 4096 -smp 2 -vga virtio -display none \
        -cdrom "$ISO" -boot d -monitor "unix:$mon,server,nowait" \
        -nic user,model=virtio-net-pci "${firmware[@]}" &
    local pid=$!
    # Without GPU acceleration Plasma renders in software, so give it time.
    sleep 6
    shot "$mon" "$mode-1-bootmenue"
    sleep 54
    shot "$mon" "$mode-2-start"
    sleep 180
    shot "$mon" "$mode-3-live-desktop-installer"
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
}

serial_helper() {  # socket, log file: log the serial console, answer the disk password prompt
    python3 - "$1" "$2" <<'PY' &
import socket, sys, time
sock_path, log_path = sys.argv[1], sys.argv[2]
for _ in range(100):
    try:
        s = socket.socket(socket.AF_UNIX); s.connect(sock_path); break
    except OSError:
        time.sleep(0.2)
buf = b""
answered = False
with open(log_path, "ab") as log:
    while True:
        data = s.recv(4096)
        if not data:
            break
        log.write(data); log.flush()
        buf = (buf + data)[-4096:]
        if not answered and b"assphrase" in buf:
            time.sleep(1)
            s.sendall(b"prismos\r")
            answered = True
            log.write(b"\n[boot-test] Passwort eingegeben\n"); log.flush()
PY
}

boot_uefi_disk() {
    local mon=/tmp/prismos-qmon-uefi-disk serial="$OUT/installiert-uefi-serial.log"
    rm -f "$mon" "$serial" /tmp/prismos-serial.sock
    cp "$OVMF_VARS" /tmp/prismos-vars-disk.fd
    echo "== Boot-Test verschlüsselte UEFI-Installation =="
    qemu-system-x86_64 "${accel[@]}" -m 4096 -smp 2 -vga virtio -display none \
        -drive "if=pflash,format=raw,readonly=on,file=$OVMF_CODE" \
        -drive "if=pflash,format=raw,file=/tmp/prismos-vars-disk.fd" \
        -drive "file=$UEFI_DISK,format=raw,if=virtio" -monitor "unix:$mon,server,nowait" \
        -chardev "socket,id=ser0,path=/tmp/prismos-serial.sock,server=on,wait=off" -serial chardev:ser0 \
        -nic user,model=virtio-net-pci &
    local pid=$!
    sleep 1
    serial_helper /tmp/prismos-serial.sock "$serial"
    sleep 8
    shot "$mon" "installiert-uefi-1-grub"
    sleep 60
    shot "$mon" "installiert-uefi-2-start"
    sleep 170
    shot "$mon" "installiert-uefi-3-desktop"
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    echo "== Serielle Konsole (Auszug) =="
    grep -a -iE "passphrase|Passwort eingegeben|Graphical Interface|Dependency failed|Failed to" "$serial" | head -40
    # With "quiet" systemd prints only problems; the desktop is checked on the screenshot.
    if grep -aq "Passwort eingegeben" "$serial" && grep -aq "hostname=prismos" "$serial" &&
        ! grep -aqi "emergency mode" "$serial"; then
        echo "BOOT-UEFI-VERSCHLÜSSELT: Festplatte entsperrt, System gestartet"
    else
        echo "BOOT-UEFI-VERSCHLÜSSELT: Start fehlgeschlagen"
    fi

    # Same disk with Secure Boot on (Microsoft keys enrolled): firmware must start
    # shim, which asks to enrol PrismOS' key (MokManager) because it is not trusted yet.
    local sb_code sb_vars
    sb_code=$(ls /usr/share/OVMF/OVMF_CODE_4M.secboot.fd 2>/dev/null)
    sb_vars=$(ls /usr/share/OVMF/OVMF_VARS_4M.ms.fd 2>/dev/null)
    if [[ -n $sb_code && -n $sb_vars ]]; then
        cp "$sb_vars" /tmp/prismos-vars-sb.fd
        rm -f "$mon"
        echo "== Boot-Test mit Secure Boot =="
        qemu-system-x86_64 "${accel[@]}" -machine q35,smm=on -global driver=cfi.pflash01,property=secure,value=on \
            -m 2048 -vga virtio -display none \
            -drive "if=pflash,format=raw,unit=0,readonly=on,file=$sb_code" \
            -drive "if=pflash,format=raw,unit=1,file=/tmp/prismos-vars-sb.fd" \
            -drive "file=$UEFI_DISK,format=raw,if=virtio" -monitor "unix:$mon,server,nowait" &
        pid=$!
        sleep 25
        shot "$mon" "installiert-uefi-4-secureboot"
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
    else
        echo "OVMF mit Secure Boot nicht gefunden, Test übersprungen"
    fi
}

boot_disk() {
    local mon=/tmp/prismos-qmon-disk
    rm -f "$mon"
    echo "== Boot-Test installiertes System =="
    qemu-system-x86_64 "${accel[@]}" -m 4096 -smp 2 -vga virtio -display none \
        -drive "file=$DISK,format=raw,if=virtio" -monitor "unix:$mon,server,nowait" \
        -nic user,model=virtio-net-pci &
    local pid=$!
    sleep 5
    shot "$mon" "installiert-1-grub"
    sleep 115
    shot "$mon" "installiert-2-start"
    sleep 150
    shot "$mon" "installiert-3-desktop"
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
}

if [[ -n $DISK ]]; then
    boot_disk
elif [[ -n $UEFI_DISK ]]; then
    boot_uefi_disk
else
    boot bios
    boot uefi
fi
ls -l "$OUT"
