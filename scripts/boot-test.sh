#!/bin/bash
# Boot the PrismOS ISO in QEMU (BIOS and UEFI) and take screenshots of the boot
# menu and the live desktop. Screenshots are saved to OUT_DIR and also printed
# as base64 between BEGIN-SCREENSHOT/END-SCREENSHOT markers in the CI log.
#
#   scripts/boot-test.sh ISO [OUT_DIR]
#   scripts/boot-test.sh --disk DISK_IMAGE [OUT_DIR]   boot an installed system (BIOS)
#
# Needs: qemu-system-x86_64, socat, ImageMagick, OVMF (for UEFI).
set -uo pipefail

DISK=""
if [[ $1 == --disk ]]; then
    DISK=$2
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
else
    boot bios
    boot uefi
fi
ls -l "$OUT"
