# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present isoPod (https://github.com/hypulse/isopod)

PKG_NAME="isopodd"
PKG_VERSION=""
PKG_LICENSE="GPL-2.0-or-later"
PKG_SITE="https://github.com/hypulse/isopod"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain Python3 dbussy"
PKG_NEED_UNPACK="${ROOT}/isopod/daemon"
PKG_LONGDESC="isoPod system daemon: input, music, radio, Bluetooth, Wi-Fi, power and apps"
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/lib/isopod ${INSTALL}/usr/bin
  cp -r ${ROOT}/isopod/daemon/isopod ${INSTALL}/usr/lib/isopod/
  find ${INSTALL}/usr/lib/isopod -name __pycache__ -prune -exec rm -rf {} +
  cat >${INSTALL}/usr/bin/isopodd <<'EOF'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/lib/isopod")
from isopod.main import main
main()
EOF
  chmod 0755 ${INSTALL}/usr/bin/isopodd
}
