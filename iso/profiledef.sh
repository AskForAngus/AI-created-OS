#!/usr/bin/env bash
# shellcheck disable=SC2034
# archiso profile definition for PrismOS.
# @BOOTMODES@ is replaced by scripts/build-iso.sh with the boot modes of the
# installed archiso "releng" profile, whose boot loader configuration we reuse.

iso_name="prismos"
iso_label="PRISMOS_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="PrismOS <https://github.com/AskForAngus/AI-created-OS>"
iso_application="PrismOS Live- und Installationsmedium"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="arch"
buildmodes=('iso')
bootmodes=(@BOOTMODES@)
arch="x86_64"
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--auto-threads=logical' '--long' '-19')
# mkarchiso copies airootfs without file modes, so every executable is listed here.
# scripts/build-iso.sh fails the build if an executable file is missing from this list.
file_permissions=(
  ["/usr/local/bin/prism-installer"]="0:0:755"
  ["/usr/local/bin/prism-live-setup"]="0:0:755"
  ["/usr/local/lib/prismos-live/prism-target-cleanup"]="0:0:755"
  ["/usr/local/lib/prismos-live/prism-target-finalize"]="0:0:755"
)
