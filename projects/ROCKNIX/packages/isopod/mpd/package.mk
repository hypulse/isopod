# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026-present isoPod (https://github.com/hypulse/isopod)

PKG_NAME="mpd"
PKG_VERSION="0.24.13"
PKG_SHA256="9f215a081cc1f7c98fcccc6620f4cb705b14ba2d87fd99e062dd0804e2e3d96e"
PKG_LICENSE="GPL-2.0-or-later"
PKG_SITE="https://www.musicpd.org"
PKG_URL="https://www.musicpd.org/download/mpd/$(get_pkg_version_maj_min)/mpd-${PKG_VERSION}.tar.xz"
PKG_DEPENDS_TARGET="toolchain alsa-lib curl faad2 ffmpeg flac libfmt libid3tag libogg libsamplerate \
                    libvorbis opus pipewire pulseaudio sqlite systemd zlib"
PKG_LONGDESC="Music Player Daemon, the playback engine behind isoPod's music and radio"
PKG_TOOLCHAIN="meson"

PKG_MESON_OPTS_TARGET="-Ddocumentation=disabled -Dhtml_manual=false -Dmanpages=false \
                       -Dtest=false -Dfuzzer=false -Dsyslog=disabled -Dsystemd=enabled \
                       -Dio_uring=disabled -Dlocal_socket=true -Dipv6=enabled \
                       -Ddatabase=true -Ddsd=true -Dcue=true -Dneighbor=false -Dudisks=disabled \
                       -Dupnp=disabled -Dlibmpdclient=disabled -Dzeroconf=disabled \
                       -Dcurl=enabled -Dmms=disabled -Dnfs=disabled -Dsmbclient=disabled \
                       -Dqobuz=disabled -Dwebdav=disabled -Dcdio_paranoia=disabled \
                       -Dbzip2=disabled -Diso9660=disabled -Dzzip=disabled -Did3tag=enabled \
                       -Dchromaprint=disabled -Dadplug=disabled -Daudiofile=disabled \
                       -Dfaad=enabled -Dffmpeg=enabled -Dflac=enabled -Dfluidsynth=disabled \
                       -Dgme=disabled -Dmad=disabled -Dmikmod=disabled -Dmodplug=disabled \
                       -Dopenmpt=disabled -Dmpcdec=disabled -Dmpg123=disabled -Dopus=enabled \
                       -Dsidplay=disabled -Dsndfile=disabled -Dtremor=disabled -Dvorbis=enabled \
                       -Dwavpack=disabled -Dwildmidi=disabled -Dvorbisenc=disabled -Dlame=disabled \
                       -Dtwolame=disabled -Dshine=disabled -Dwave_encoder=false \
                       -Dlibsamplerate=enabled -Dsoxr=disabled \
                       -Dalsa=enabled -Dao=disabled -Dfifo=false -Dhttpd=false -Djack=disabled \
                       -Dopenal=disabled -Doss=disabled -Dpipe=false -Dpipewire=enabled -Dpulse=enabled \
                       -Drecorder=false -Dshout=disabled -Dsndio=disabled -Dsolaris_output=disabled \
                       -Dexpat=disabled -Dicu=disabled -Diconv=disabled -Dnlohmann_json=disabled \
                       -Dpcre=disabled -Dsqlite=enabled -Dzlib=enabled"

post_makeinstall_target() {
  safe_remove ${INSTALL}/usr/lib/systemd
  safe_remove ${INSTALL}/usr/share/doc
  mkdir -p ${INSTALL}/etc
  cp ${PKG_DIR}/config/mpd.conf ${INSTALL}/etc/mpd.conf
}

post_install() {
  enable_service mpd.service
}
