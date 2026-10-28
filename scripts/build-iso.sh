#!/bin/bash
# Build the PrismOS ISO. Must run as root on Arch Linux (CI uses the archlinux
# Docker image, see .github/workflows/build-iso.yml).
#
#   scripts/build-iso.sh [OUTPUT_DIR]
#
# Environment: WORK_DIR (default /tmp/prismos-build), PRISMOS_VERSION (default date).
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=$(realpath -m "${1:-$REPO/out}")
WORK=$(realpath -m "${WORK_DIR:-/tmp/prismos-build}")
VERSION=${PRISMOS_VERSION:-$(date +%Y.%m.%d)}
RELENG=/usr/share/archiso/configs/releng

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen." >&2; exit 1; }
[[ -f /etc/arch-release ]] || { echo "Muss unter Arch Linux laufen." >&2; exit 1; }

log "Build-Werkzeuge installieren"
pacman -Syu --noconfirm --needed archiso base-devel git sudo

log "Profil prüfen"
# Every executable in airootfs needs an entry in file_permissions (mkarchiso drops file modes).
missing=0
while IFS= read -r f; do
    rel=${f#"$REPO/iso/airootfs"}
    if ! grep -qF "[\"$rel\"]" "$REPO/iso/profiledef.sh"; then
        echo "Fehlt in file_permissions: $rel" >&2
        missing=1
    fi
done < <(find "$REPO/iso/airootfs" -type f -perm -u+x)
(( missing == 0 )) || exit 1

log "Calamares bauen"
rm -rf "$WORK"
mkdir -p "$WORK/pkgbuild" "$WORK/localrepo" "$OUT"
id builder &>/dev/null || useradd -m builder
echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
cp -r "$REPO/packages/calamares" "$WORK/pkgbuild/"
chown -R builder: "$WORK/pkgbuild"
(cd "$WORK/pkgbuild/calamares" && sudo -u builder MAKEFLAGS="-j$(nproc)" makepkg -s --noconfirm --needed)
cp "$WORK"/pkgbuild/calamares/calamares-[0-9]*.pkg.tar.zst "$WORK/localrepo/"
repo-add "$WORK/localrepo/prismos-local.db.tar.gz" "$WORK"/localrepo/*.pkg.tar.zst

log "Calamares-Module prüfen"
modules=$(bsdtar -tf "$WORK"/localrepo/calamares-[0-9]*.pkg.tar.zst | sed -n 's#^usr/lib/calamares/modules/\([^/]*\)/$#\1#p' | sort -u)
echo "$modules" | tr '\n' ' '; echo
for m in partition netinstall users unpackfs bootloader grubcfg displaymanager services-systemd shellprocess; do
    grep -qx "$m" <<<"$modules" || { echo "Calamares-Modul fehlt: $m" >&2; exit 1; }
done
if ! grep -qx plasmalnf <<<"$modules"; then
    echo "Hinweis: plasmalnf wurde nicht gebaut, Look-Auswahl im Installer wird ausgeblendet." >&2
    NO_PLASMALNF=1
fi

log "archiso-Profil zusammenstellen"
PROFILE="$WORK/profile"
mkdir -p "$PROFILE"
cp -r "$RELENG/efiboot" "$RELENG/grub" "$RELENG/syslinux" "$PROFILE/"
cp -r "$REPO/iso/airootfs" "$REPO/iso/packages.x86_64" "$REPO/iso/profiledef.sh" "$PROFILE/"
cp "$REPO/iso/syslinux/splash.png" "$PROFILE/syslinux/splash.png"

# Boot modes and boot menus come from archiso's releng profile; adapt them to PrismOS.
bootmodes=$(sed -n '/^bootmodes=(/,/)/p' "$RELENG/profiledef.sh" | tr '\n' ' ' | sed 's/^bootmodes=(//; s/).*$//')
sed -i "s|@BOOTMODES@|$bootmodes|" "$PROFILE/profiledef.sh"
find "$PROFILE/efiboot" "$PROFILE/grub" "$PROFILE/syslinux" -type f \( -name '*.cfg' -o -name '*.conf' \) -print0 |
    xargs -0 sed -i -E \
        -e 's/vmlinuz-linux([^-]|$)/vmlinuz-linux-zen\1/g' \
        -e 's/initramfs-linux\.img/initramfs-linux-zen.img/g' \
        -e 's/Arch Linux install medium/PrismOS/g' \
        -e 's/Arch Linux/PrismOS/g' \
        -e 's/(archisobasedir=%INSTALL_DIR%)/\1 copytoram=n/g'
grep -rq "vmlinuz-linux-zen" "$PROFILE/efiboot" "$PROFILE/grub" "$PROFILE/syslinux" ||
    { echo "Bootmenüs konnten nicht angepasst werden." >&2; exit 1; }

sed "s|@LOCALREPO@|$WORK/localrepo|" "$REPO/iso/pacman.conf.in" > "$PROFILE/pacman.conf"
sed -i "s|@VERSION@|$VERSION|g" "$PROFILE/airootfs/etc/calamares/branding/prismos/branding.desc"
if [[ -n ${NO_PLASMALNF:-} ]]; then
    sed -i '/^  - plasmalnf$/d' "$PROFILE/airootfs/etc/calamares/settings.conf"
fi

log "ISO bauen (das dauert eine Weile)"
mkarchiso -v -w "$WORK/iso" -o "$OUT" "$PROFILE"

cd "$OUT"
for iso in prismos-*.iso; do
    sha256sum "$iso" > "$iso.sha256"
done
ls -lh "$OUT"
log "Fertig: $OUT"
