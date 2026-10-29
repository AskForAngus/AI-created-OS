#!/bin/bash
# Simulated installation of a PrismOS ISO onto a virtual disk.
#   bios: MBR, ext4, GRUB for BIOS, Firefox from the internet
#   uefi: GPT, encrypted (LUKS2, password "prismos") Btrfs with snapshots,
#         kernels on the EFI partition, shim + signed GRUB (Secure Boot)
#
# Calamares itself cannot be driven headlessly, so this script performs the same
# steps its modules do (partition, unpack, users, services, display manager) and
# then runs the PrismOS parts unchanged: prism-target-cleanup, prism-apply-plan
# (installs Firefox from the internet), the look selection and GRUB. The
# resulting disk image can be booted with QEMU.
#
#   scripts/install-test.sh ISO OUT_DIR [bios|uefi]
#
# Runs as root on Arch Linux with access to loop devices (docker --privileged -v /dev:/dev).
set -euo pipefail

ISO=$1
OUT=$2
MODE=${3:-bios}
CRYPT_PASSWORD=prismos
TARGET=/mnt/prismos-target
USER_NAME=tester
LOOK=org.prismos.macos.desktop

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
in_target() { arch-chroot "$TARGET" "$@"; }

log "Werkzeuge installieren"
pacman -Syu --noconfirm --needed arch-install-scripts squashfs-tools parted e2fsprogs libarchive \
    btrfs-progs dosfstools cryptsetup

mkdir -p "$OUT" "$TARGET"
DISK="$OUT/disk.img"
rm -f "$DISK"
truncate -s 20G "$DISK"

LOOP=$(losetup -fP --show "$DISK")
cleanup() {
    umount -R "$TARGET" 2>/dev/null || true
    [[ -n ${MAPPER:-} ]] && cryptsetup close "$MAPPER" 2>/dev/null
    losetup -d "$LOOP" 2>/dev/null || true
}
trap cleanup EXIT
wait_part() { for _ in $(seq 20); do [[ -b $1 ]] && return; sleep 0.5; done; }

if [[ $MODE == bios ]]; then
    log "Partitionieren (MBR, eine ext4-Partition)"
    parted -s "$LOOP" mklabel msdos mkpart primary ext4 1MiB 100% set 1 boot on
    partx -u "$LOOP" 2>/dev/null || true
    wait_part "${LOOP}p1"
    mkfs.ext4 -q -L PrismOS "${LOOP}p1"
    mount "${LOOP}p1" "$TARGET"
else
    log "Partitionieren (GPT, EFI + verschlüsseltes Btrfs mit Subvolumes)"
    parted -s "$LOOP" mklabel gpt mkpart EFI fat32 1MiB 513MiB set 1 esp on mkpart PrismOS 513MiB 100%
    partx -u "$LOOP" 2>/dev/null || true
    wait_part "${LOOP}p2"
    mkfs.fat -F32 -n EFI "${LOOP}p1" >/dev/null
    printf '%s' "$CRYPT_PASSWORD" | cryptsetup luksFormat --type luks2 --batch-mode \
        --pbkdf argon2id --pbkdf-memory 65536 --iter-time 500 "${LOOP}p2" -
    MAPPER="luks-$(blkid -s UUID -o value "${LOOP}p2")"
    printf '%s' "$CRYPT_PASSWORD" | cryptsetup open "${LOOP}p2" "$MAPPER" -
    mkfs.btrfs -q -L PrismOS "/dev/mapper/$MAPPER"
    mount "/dev/mapper/$MAPPER" "$TARGET"
    for sv in @ @home @cache @log; do btrfs subvolume create "$TARGET/$sv" >/dev/null; done
    umount "$TARGET"
    opts=noatime,compress=zstd
    mount -o "subvol=/@,$opts" "/dev/mapper/$MAPPER" "$TARGET"
    mkdir -p "$TARGET"/{home,var/cache,var/log,boot/efi}
    mount -o "subvol=/@home,$opts" "/dev/mapper/$MAPPER" "$TARGET/home"
    mount -o "subvol=/@cache,$opts" "/dev/mapper/$MAPPER" "$TARGET/var/cache"
    mount -o "subvol=/@log,$opts" "/dev/mapper/$MAPPER" "$TARGET/var/log"
    mount -o umask=0077 "${LOOP}p1" "$TARGET/boot/efi"
fi

log "System kopieren (unpackfs)"
bsdtar -xOf "$ISO" arch/x86_64/airootfs.sfs > "$OUT/airootfs.sfs"
unsquashfs -f -q -d "$TARGET" "$OUT/airootfs.sfs"
rm -f "$OUT/airootfs.sfs"

log "Calamares-Schritte nachstellen (fstab, machine-id, Benutzer, Dienste, Anmeldung)"
genfstab -U "$TARGET" >> "$TARGET/etc/fstab"
if [[ $MODE == uefi ]]; then
    # what Calamares writes for an encrypted root
    echo "$MAPPER UUID=$(blkid -s UUID -o value "${LOOP}p2") none luks" >> "$TARGET/etc/crypttab"
fi
rm -f "$TARGET/etc/machine-id"
systemd-machine-id-setup --root="$TARGET"
echo "prismos-test" > "$TARGET/etc/hostname"
in_target useradd -m -G wheel,audio,video,network,storage,input -s /bin/bash "$USER_NAME"
echo "$USER_NAME:prismos" | in_target chpasswd
echo "%wheel ALL=(ALL:ALL) ALL" > "$TARGET/etc/sudoers.d/10-installer"
chmod 440 "$TARGET/etc/sudoers.d/10-installer"
for unit in NetworkManager.service sddm.service bluetooth.service power-profiles-daemon.service \
            switcheroo-control.service systemd-timesyncd.service fstrim.timer paccache.timer \
            reflector.timer prism-flathub.service prism-battery.service prism-power-profile.service; do
    in_target systemctl enable "$unit"
done
# Autologin as offered by the Calamares users page, so the boot test reaches the desktop.
mkdir -p "$TARGET/etc/sddm.conf.d"
printf '[Autologin]\nUser=%s\nSession=plasma\n' "$USER_NAME" > "$TARGET/etc/sddm.conf.d/90-autologin.conf"

log "prism-target-cleanup"
in_target /usr/local/lib/prismos-live/prism-target-cleanup
for f in /usr/local/bin/prism-installer /etc/calamares /etc/mkinitcpio.conf.d/archiso.conf; do
    [[ ! -e $TARGET$f ]] || { echo "Live-Datei nicht entfernt: $f" >&2; exit 1; }
done
! in_target pacman -Q calamares 2>/dev/null || { echo "calamares noch installiert" >&2; exit 1; }
ls -l "$TARGET"/boot

log "prismapps: Look und Programmplan"
sed -i "s/^LookAndFeelPackage=.*/LookAndFeelPackage=$LOOK/" "$TARGET/etc/xdg/kdeglobals"
mkdir -p "$TARGET/var/lib/prismos"
if [[ $MODE == bios ]]; then
    PLAN_PKGS='["linux-lts", "firefox"]'
    CHECK_PKGS="linux-lts firefox firefox-i18n-de"
else
    PLAN_PKGS='["linux-lts"]'
    CHECK_PKGS="linux-lts"
fi
cat > "$TARGET/var/lib/prismos/plan.json" <<PLAN
{"refresh_mirrors": true, "pacman": $PLAN_PKGS, "pacman_optional": ["firefox-i18n-de", "gibt-es-nicht"],
 "flatpak": ["org.example.Later"], "services": [], "groups": {}, "drop_kms_hook": false}
PLAN
cp --remove-destination /etc/resolv.conf "$TARGET/etc/resolv.conf"
in_target /usr/lib/prismos/prism-apply-plan --installer || true
# Flatpaks stay in the plan for the first boot, everything else is done
in_target python3 -c "import json; p = json.load(open('/var/lib/prismos/plan.json')); \
assert p['flatpak'] == ['org.example.Later'] and not p['pacman'], p"
rm -f "$TARGET/var/lib/prismos/plan.json"
in_target systemctl disable prism-pending-apps.timer
in_target pacman -Q $CHECK_PKGS

log "GRUB (grubcfg + bootloader)"
cat >> "$TARGET/etc/default/grub" <<'EOF'
GRUB_DISTRIBUTOR="PrismOS"
GRUB_TIMEOUT=3
GRUB_DEFAULT=0
GRUB_DISABLE_SUBMENU=true
GRUB_DISABLE_RECOVERY=true
GRUB_DISABLE_OS_PROBER=false
EOF
if [[ $MODE == bios ]]; then
    in_target grub-install --target=i386-pc "$LOOP"
    in_target grub-mkconfig -o /boot/grub/grub.cfg
    grep -E "^menuentry|^submenu" "$TARGET/boot/grub/grub.cfg"
    grep -q "linux-lts" "$TARGET/boot/grub/grub.cfg" || { echo "LTS-Kernel fehlt im Bootmenü" >&2; exit 1; }
    in_target /usr/local/lib/prismos-live/prism-target-finalize
else
    # serial console so the boot test can see the passphrase prompt
    sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="/GRUB_CMDLINE_LINUX_DEFAULT="console=tty0 console=ttyS0,115200 /' \
        "$TARGET/etc/default/grub"
    in_target grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=PrismOS --no-nvram
    in_target grub-mkconfig -o /boot/grub/grub.cfg
    log "prism-target-finalize (Snapshots, Verschlüsselung, Secure Boot)"
    in_target env PRISMOS_FORCE_UEFI=1 /usr/local/lib/prismos-live/prism-target-finalize
    log "Ergebnis prüfen"
    ls -l "$TARGET"/boot/efi/EFI/PrismOS "$TARGET"/boot/efi/EFI/BOOT "$TARGET"/boot/efi/prismos
    in_target sbverify --cert /etc/prismos/secureboot/MOK.crt /boot/efi/EFI/PrismOS/grubx64.efi
    in_target sbverify --cert /etc/prismos/secureboot/MOK.crt /boot/efi/prismos/vmlinuz-linux-zen
    cat "$TARGET/etc/crypttab.initramfs"
    grep -E "^menuentry" "$TARGET/boot/efi/grub/grub.cfg"
    grep -q "LTS-Notfallkernel" "$TARGET/boot/efi/grub/grub.cfg" || { echo "LTS-Eintrag fehlt" >&2; exit 1; }
    ! grep -q "cryptomount" "$TARGET/boot/efi/grub/grub.cfg" || { echo "GRUB würde die Platte entsperren" >&2; exit 1; }
    grep snapshots "$TARGET/etc/fstab"
    in_target snapper --no-dbus -c root list
fi
[[ ! -e $TARGET/usr/local/lib/prismos-live ]] || { echo "Live-Skripte nicht entfernt" >&2; exit 1; }
ls "$TARGET/usr/share/Kvantum" || true

log "Installation simuliert ($MODE): $DISK"
