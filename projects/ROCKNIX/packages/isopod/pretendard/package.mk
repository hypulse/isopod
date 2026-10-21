# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present isoPod (https://github.com/isopod-player/isopod)

PKG_NAME="pretendard"
PKG_VERSION="1.3.9"
PKG_SHA256="04be351a74d6bf7d60c480a3087e51d185485d35a52023142af1df19eb8c428a"
PKG_LICENSE="OFL-1.1"
PKG_SITE="https://github.com/orioncactus/pretendard"
PKG_URL="${PKG_SITE}/releases/download/v${PKG_VERSION}/Pretendard-${PKG_VERSION}.zip"
PKG_DEPENDS_TARGET="toolchain"
PKG_LONGDESC="Pretendard, the Korean and Latin typeface of the isoPod shell"
PKG_TOOLCHAIN="manual"

unpack() {
  mkdir -p ${PKG_BUILD}
  unzip -q -o ${SOURCES}/${PKG_NAME}/${PKG_SOURCE_NAME} "public/static/*.otf" LICENSE.txt -d ${PKG_BUILD}
}

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/share/isopod/fonts
  for weight in Regular Medium SemiBold Bold; do
    cp ${PKG_BUILD}/public/static/Pretendard-${weight}.otf ${INSTALL}/usr/share/isopod/fonts/
  done
  cp ${PKG_BUILD}/LICENSE.txt ${INSTALL}/usr/share/isopod/fonts/OFL.txt
}
