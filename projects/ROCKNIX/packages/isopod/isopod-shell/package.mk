# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present isoPod (https://github.com/isopod-player/isopod)

PKG_NAME="isopod-shell"
PKG_VERSION=""
PKG_LICENSE="GPL-2.0-or-later"
PKG_SITE="https://github.com/isopod-player/isopod"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain SDL2 SDL2_ttf SDL2_image"
PKG_NEED_UNPACK="${ROOT}/isopod/shell"
PKG_LONGDESC="isoPod shell: the iPod-inspired user interface"
PKG_TOOLCHAIN="make"

unpack() {
  mkdir -p ${PKG_BUILD}
  cp -a ${ROOT}/isopod/shell/. ${PKG_BUILD}/
  rm -f ${PKG_BUILD}/isopod-shell ${PKG_BUILD}/isopod-splash ${PKG_BUILD}/isopod-thumb
}

make_target() {
  make CC="${CC}"
}

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/bin ${INSTALL}/usr/share/isopod
  cp ${PKG_BUILD}/isopod-shell ${PKG_BUILD}/isopod-splash ${PKG_BUILD}/isopod-thumb ${INSTALL}/usr/bin/
  cp -r ${PKG_BUILD}/assets/. ${INSTALL}/usr/share/isopod/
}
