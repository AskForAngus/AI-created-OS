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
pacman -Syu --noconfirm --needed archiso base-devel git sudo pacman-contrib

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

log "Pakete bauen"
rm -rf "$WORK"
mkdir -p "$WORK/pkgbuild" "$WORK/localrepo" "$OUT"
id builder &>/dev/null || useradd -m builder
echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
export PRISMOS_PKGVER=${PRISMOS_PKGVER:-$(date +%Y.%m.%d.%H%M)}
REPO_PACKAGES_EXTRA="$WORK/extra-packages"
: > "$REPO_PACKAGES_EXTRA"

build_pkg() {  # name [makepkg options]
    local name=$1
    shift
    cp -r "$REPO/packages/$name" "$WORK/pkgbuild/"
    chown -R builder: "$WORK/pkgbuild/$name"
    (cd "$WORK/pkgbuild/$name" &&
        sudo -u builder --preserve-env=PRISMOS_PKGVER MAKEFLAGS="-j$(nproc)" makepkg --noconfirm "$@")
    cp "$WORK/pkgbuild/$name/$name"-[0-9]*.pkg.tar.zst "$WORK/localrepo/"
}
build_pkg calamares --syncdeps --needed
# PrismOS' own packages only contain files; their dependencies are installed with the ISO.
build_pkg prismos-settings --nodeps
build_pkg prismos-looks --nodeps
build_pkg shim-signed --nodeps

# Signed PrismOS repository for updates (only with a signing key, see README).
if [[ -n ${PRISMOS_SIGNING_KEY:-} ]]; then
    log "PrismOS-Pakete signieren"
    export GNUPGHOME="$WORK/gnupg"
    install -d -m700 "$GNUPGHOME"
    gpg --batch --quiet --import <<<"$PRISMOS_SIGNING_KEY"
    FPR=$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr/ { print $10; exit }')
    echo "Signierschlüssel: $FPR"
    # prismos-keyring: lets pacman trust the key on installed systems
    kr="$WORK/pkgbuild/prismos-keyring"
    mkdir -p "$kr"
    gpg --batch --export "$FPR" > "$kr/prismos.gpg"
    echo "$FPR:4:" > "$kr/prismos-trusted"
    : > "$kr/prismos-revoked"
    cat > "$kr/PKGBUILD" <<'PKG'
pkgname=prismos-keyring
pkgver=${PRISMOS_PKGVER:-0.1}
pkgrel=1
pkgdesc='PrismOS: Schlüssel der PrismOS-Paketquelle'
arch=('any')
license=('GPL-3.0-or-later')
install=prismos-keyring.install
package() {
  install -Dm644 -t "$pkgdir/usr/share/pacman/keyrings" "$startdir/prismos.gpg" \
      "$startdir/prismos-trusted" "$startdir/prismos-revoked"
}
PKG
    cat > "$kr/prismos-keyring.install" <<'PKG'
post_install() {
  if [ -f /etc/pacman.d/gnupg/trustdb.gpg ]; then
    pacman-key --populate prismos >/dev/null 2>&1 || true
  fi
}
post_upgrade() {
  post_install
}
PKG
    chown -R builder: "$kr"
    (cd "$kr" && sudo -u builder --preserve-env=PRISMOS_PKGVER makepkg --noconfirm --nodeps)
    cp "$kr"/prismos-keyring-[0-9]*.pkg.tar.zst "$WORK/localrepo/"
    # Installed systems use the repository only if it is published (from main).
    if [[ ${PRISMOS_REPO_ENABLED:-} == 1 ]]; then
        echo prismos-keyring >> "$REPO_PACKAGES_EXTRA"
    fi

    mkdir -p "$OUT/repo"
    for pkg in "$WORK"/localrepo/prismos-*.pkg.tar.zst; do
        gpg --batch --yes --detach-sign -u "$FPR" "$pkg"
        cp "$pkg" "$pkg.sig" "$OUT/repo/"
    done
    repo-add --sign --key "$FPR" "$OUT/repo/prismos.db.tar.gz" "$OUT"/repo/prismos-*.pkg.tar.zst
    # GitHub release assets cannot be symlinks
    for f in prismos.db prismos.db.sig prismos.files prismos.files.sig; do
        [ -L "$OUT/repo/$f" ] && cp --remove-destination "$(readlink -f "$OUT/repo/$f")" "$OUT/repo/$f"
    done
    ls -l "$OUT/repo"
else
    echo "Hinweis: kein PRISMOS_SIGNING_KEY – Pakete unsigniert, keine Paketquelle für Updates." >&2
fi
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
cat "$REPO_PACKAGES_EXTRA" >> "$PROFILE/packages.x86_64"
sed -i "s|@VERSION@|$VERSION|g" "$PROFILE/airootfs/etc/calamares/branding/prismos/branding.desc"
if [[ -n ${NO_PLASMALNF:-} ]]; then
    sed -i '/^  - plasmalnf$/d' "$PROFILE/airootfs/etc/calamares/settings.conf"
fi

log "ISO bauen (das dauert eine Weile)"
mkarchiso -v -w "$WORK/iso" -o "$OUT" "$PROFILE"

log "Größte Pakete im Live-System (installierte Größe)"
pacman --root "$WORK/iso/x86_64/airootfs" --dbpath "$WORK/iso/x86_64/airootfs/var/lib/pacman" -Qi |
    awk -F': ' '/^Name/ {n=$2} /^Installed Size/ {
        split($2, a, " "); s=a[1]; u=a[2]
        if (u ~ /KiB/) s/=1024; else if (u ~ /GiB/) s*=1024; else if (u ~ /B$/ && u !~ /MiB/) s/=1048576
        printf "%8.1f MiB  %s\n", s, n }' | sort -rn | head -40 || true
for big in qt6-webengine gtk3; do
    echo "== Wer braucht $big? =="
    pactree -r -d 6 -b "$WORK/iso/x86_64/airootfs/var/lib/pacman" "$big" 2>/dev/null || true
done

cd "$OUT"
for iso in prismos-*.iso; do
    sha256sum "$iso" > "$iso.sha256"
    size=$(stat -c %s "$iso")
    echo "ISO-Größe: $size Bytes ($((size / 1048576)) MiB), Grenze für GitHub-Releases: 2147483648 Bytes"
    (( size < 2147483648 )) || echo "Hinweis: ISO ist größer als 2 GiB und wird im Release geteilt." >&2
done
ls -lh "$OUT"
log "Fertig: $OUT"
