# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present isoPod (https://github.com/hypulse/isopod)

PKG_NAME="isopod"
PKG_VERSION=""
PKG_LICENSE="GPL-2.0-or-later"
PKG_SITE="https://github.com/hypulse/isopod"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain isopodd isopod-shell mpd pretendard"
PKG_NEED_UNPACK="${ROOT}/isopod/system"
PKG_LONGDESC="isoPod: an iPod-inspired, music-first shell with its system services"
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/share/isopod
  cp ${ROOT}/isopod/system/sway.conf ${INSTALL}/usr/share/isopod/
  cp -r ${ROOT}/isopod/system/mpv ${INSTALL}/usr/share/isopod/

  # isopodd reads the buttons, the lid and the power key itself, and isoPod reports
  # nothing to ROCKNIX's install statistics.
  mkdir -p ${INSTALL}/etc/systemd/system
  for UNIT in input.service rocknix-report-stats.service rocknix-report-stats.timer; do
    ln -sf /dev/null ${INSTALL}/etc/systemd/system/${UNIT}
  done
}

post_install() {
  enable_service isopodd.service
}
