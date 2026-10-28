#!/bin/bash
# Simulated installation of a PrismOS ISO onto a virtual disk (BIOS/MBR).
#
# Calamares itself cannot be driven headlessly, so this script performs the same
# steps its modules do (partition, unpack, users, services, display manager) and
# then runs the PrismOS parts unchanged: prism-target-cleanup, prism-apply-plan
# (installs Firefox from the internet), the look selection and GRUB. The
# resulting disk image can be booted with QEMU.
#
#   scripts/install-test.sh ISO OUT_DIR
#
# Runs as root on Arch Linux with access to loop devices (docker --privileged -v /dev:/dev).
set -euo pipefail

ISO=$1
OUT=$2
TARGET=/mnt/prismos-target
USER_NAME=tester
LOOK=org.prismos.macos.desktop

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
in_target() { arch-chroot "$TARGET" "$@"; }

log "Werkzeuge installieren"
pacman -Syu --noconfirm --needed arch-install-scripts squashfs-tools parted e2fsprogs libarchive

mkdir -p "$OUT" "$TARGET"
DISK="$OUT/disk.img"
rm -f "$DISK"
truncate -s 20G "$DISK"

log "Partitionieren (MBR, eine ext4-Partition)"
LOOP=$(losetup -fP --show "$DISK")
cleanup() {
    umount -R "$TARGET" 2>/dev/null || true
    losetup -d "$LOOP" 2>/dev/null || true
}
trap cleanup EXIT
parted -s "$LOOP" mklabel msdos mkpart primary ext4 1MiB 100% set 1 boot on
partx -u "$LOOP" 2>/dev/null || true
for _ in $(seq 20); do [[ -b ${LOOP}p1 ]] && break; sleep 0.5; done
mkfs.ext4 -q -L PrismOS "${LOOP}p1"
mount "${LOOP}p1" "$TARGET"

log "System kopieren (unpackfs)"
bsdtar -xOf "$ISO" arch/x86_64/airootfs.sfs > "$OUT/airootfs.sfs"
unsquashfs -f -q -d "$TARGET" "$OUT/airootfs.sfs"
rm -f "$OUT/airootfs.sfs"

log "Calamares-Schritte nachstellen (fstab, machine-id, Benutzer, Dienste, Anmeldung)"
genfstab -U "$TARGET" >> "$TARGET/etc/fstab"
rm -f "$TARGET/etc/machine-id"
systemd-machine-id-setup --root="$TARGET"
echo "prismos-test" > "$TARGET/etc/hostname"
in_target useradd -m -G wheel,audio,video,network,storage,input -s /bin/bash "$USER_NAME"
echo "$USER_NAME:prismos" | in_target chpasswd
echo "%wheel ALL=(ALL:ALL) ALL" > "$TARGET/etc/sudoers.d/10-installer"
chmod 440 "$TARGET/etc/sudoers.d/10-installer"
for unit in NetworkManager.service sddm.service bluetooth.service power-profiles-daemon.service \
            switcheroo-control.service systemd-timesyncd.service fstrim.timer paccache.timer \
            reflector.timer prism-flathub.service; do
    in_target systemctl enable "$unit"
done
# Autologin as offered by the Calamares users page, so the boot test reaches the desktop.
printf '[Autologin]\nUser=%s\nSession=plasma\n' "$USER_NAME" > "$TARGET/etc/sddm.conf.d/90-autologin.conf"

log "prism-target-cleanup"
in_target /usr/local/lib/prismos/prism-target-cleanup
for f in /usr/local/bin/prism-installer /etc/calamares /etc/mkinitcpio.conf.d/archiso.conf; do
    [[ ! -e $TARGET$f ]] || { echo "Live-Datei nicht entfernt: $f" >&2; exit 1; }
done
! in_target pacman -Q calamares 2>/dev/null || { echo "calamares noch installiert" >&2; exit 1; }
ls -l "$TARGET"/boot

log "prismapps: Look und Programmplan"
sed -i "s/^LookAndFeelPackage=.*/LookAndFeelPackage=$LOOK/" "$TARGET/etc/xdg/kdeglobals"
mkdir -p "$TARGET/var/lib/prismos"
cat > "$TARGET/var/lib/prismos/plan.json" <<EOF
{"refresh_mirrors": true, "pacman": ["firefox"], "pacman_optional": ["firefox-i18n-de", "gibt-es-nicht"],
 "flatpak": [], "services": [], "groups": {}, "drop_kms_hook": false}
EOF
cp --remove-destination /etc/resolv.conf "$TARGET/etc/resolv.conf"
in_target /usr/local/lib/prismos/prism-apply-plan
[[ ! -e $TARGET/var/lib/prismos/plan.json ]] || { echo "Plan nicht abgearbeitet" >&2; exit 1; }
in_target pacman -Q firefox firefox-i18n-de

log "GRUB (grubcfg + bootloader)"
cat >> "$TARGET/etc/default/grub" <<'EOF'
GRUB_DISTRIBUTOR="PrismOS"
GRUB_TIMEOUT=3
GRUB_DEFAULT=0
GRUB_DISABLE_SUBMENU=true
GRUB_DISABLE_RECOVERY=true
GRUB_DISABLE_OS_PROBER=false
EOF
in_target grub-install --target=i386-pc "$LOOP"
in_target grub-mkconfig -o /boot/grub/grub.cfg
grep -E "^menuentry|^submenu" "$TARGET/boot/grub/grub.cfg"

log "Installation simuliert: $DISK"
