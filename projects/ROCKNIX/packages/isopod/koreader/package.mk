# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present isoPod (https://github.com/isopod-player/isopod)

PKG_NAME="koreader"
PKG_VERSION="2026.07.1"
PKG_SHA256="68b399a99a88bf3f036f5e8b67d043215a28e11d375cf0823900aef7b1523a0b"
PKG_LICENSE="AGPL-3.0-only"
PKG_SITE="https://github.com/koreader/koreader"
PKG_URL="${PKG_SITE}/releases/download/v${PKG_VERSION}/koreader-linux-arm64-v${PKG_VERSION}.tar.xz"
PKG_DEPENDS_TARGET="toolchain SDL2"
PKG_LONGDESC="KOReader, the e-book reader behind isoPod's Books"
PKG_TOOLCHAIN="manual"

unpack() {
  mkdir -p ${PKG_BUILD}
  tar -xJf ${SOURCES}/${PKG_NAME}/${PKG_SOURCE_NAME} -C ${PKG_BUILD}
}

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/lib ${INSTALL}/usr/bin
  cp -a ${PKG_BUILD}/lib/koreader ${INSTALL}/usr/lib/
  cp -a ${PKG_BUILD}/bin/koreader ${INSTALL}/usr/bin/koreader
}
