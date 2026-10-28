#!/bin/bash
# Boot the PrismOS ISO in QEMU (BIOS and UEFI) and take screenshots of the boot
# menu and the live desktop. Screenshots are saved to OUT_DIR and also printed
# as base64 between BEGIN-SCREENSHOT/END-SCREENSHOT markers in the CI log.
#
#   scripts/boot-test.sh ISO [OUT_DIR]
#
# Needs: qemu-system-x86_64, socat, ImageMagick, OVMF (for UEFI).
set -uo pipefail

ISO=$1
OUT=${2:-boot-test}
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

monitor() {  # socket, command
    echo "$2" | socat - "UNIX-CONNECT:$1" >/dev/null
}

type_text() {  # socket, text – types into the VM via QEMU sendkey
    local i c key
    for ((i = 0; i < ${#2}; i++)); do
        c=${2:i:1}
        case $c in
            " ") key=spc ;; "=") key=equal ;; ",") key=comma ;; ".") key=dot ;;
            "_") key=shift-minus ;; "-") key=minus ;; *) key=$c ;;
        esac
        monitor "$1" "sendkey $key"
        sleep 0.08
    done
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
        -serial "file:$OUT/$mode-serial.log" \
        -nic user,model=virtio-net-pci "${firmware[@]}" &
    local pid=$!
    sleep 6
    shot "$mon" "$mode-1-bootmenue"
    # Add a serial console to the default boot entry so the boot log can be read.
    if [[ $mode == uefi ]]; then
        monitor "$mon" "sendkey e"; sleep 1; monitor "$mon" "sendkey end"
    else
        monitor "$mon" "sendkey tab"; sleep 1
    fi
    type_text "$mon" " console=tty0 console=ttyS0,115200"
    monitor "$mon" "sendkey ret"
    sleep 40
    shot "$mon" "$mode-2-nach-40s"
    sleep 110
    shot "$mon" "$mode-3-live-desktop"
    sleep 60
    shot "$mon" "$mode-4-installer"
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    echo "== Serielle Konsole $mode (Auszug) =="
    grep -a -iE "error|fail|panic|warn|archiso|mount|Reached target|Started .*(sddm|Display|PrismOS)" \
        "$OUT/$mode-serial.log" | head -80
    echo "== Serielle Konsole $mode (Ende) =="
    tail -n 40 "$OUT/$mode-serial.log"
}

boot bios
boot uefi
ls -l "$OUT"
