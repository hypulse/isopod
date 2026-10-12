#!/bin/sh
# Record isoPod with a real app (mpv) under a headless sway, inside isopod-desk.
# usage: run.sh OUT_DIR
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-/out}
W=/tmp/isopod-desk

export XDG_RUNTIME_DIR=/tmp/xdg HOME=/root LIBGL_ALWAYS_SOFTWARE=1 TZ=Asia/Seoul
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
mkdir -p "$W/share" "$W/config" "$W/cache" "$OUT" /storage/.config /storage/.cache/isopod/mpv

# the data directory as the image lays it out
ln -sfn /usr/share/fonts/pretendard "$W/share/fonts"
ln -sfn "$ROOT/shell/assets/icons" "$W/share/icons"
ln -sfn "$ROOT/system/mpv" "$W/share/mpv"
for f in "$ROOT"/shell/assets/*.png; do ln -sf "$f" "$W/share/"; done
ln -sfn "$W/share" /usr/share/isopod

[ -d "$W/music" ] || python3 "$ROOT/tools/gen_test_music.py" "$W" >/dev/null
# real media only: the simulator's empty stand-ins would open as broken files here
find "$W/videos" -type f -size 0 -delete 2>/dev/null || true
rm -rf "$W/videos/Concerts"
[ -f "$W/videos/Loop.webm" ] || python3 "$ROOT/tools/desk/media.py" "$W/videos"
cat >"$W/sway.conf" <<CONF
output HEADLESS-1 resolution 640x480 position 0 0
default_border none
seat * hide_cursor 1
include $ROOT/system/sway.conf
CONF
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 sway -c "$W/sway.conf" >"$W/sway.log" 2>&1 &
sleep 2
export WAYLAND_DISPLAY=wayland-1 SWAYSOCK=$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock | head -1)

cat >"$W/mpd.conf" <<CONF
music_directory "$W/music"
playlist_directory "$W/playlists"
db_file "$W/mpd.db"
state_file "$W/mpd.state"
bind_to_address "$W/mpd.sock"
audio_output {
    type "null"
    name "null"
    mixer_type "none"
}
CONF
mpd "$W/mpd.conf" 2>/dev/null

PATH="$ROOT/shell:$PATH" python3 "$ROOT/daemon/isopodd" --sim --apps direct --socket "$W/isopodd.sock" --config "$W/config" \
    --cache "$W/cache" --music "$W/music" --videos "$W/videos" --data "$W/share" --mpd "$W/mpd.sock" \
    >"$W/isopodd.log" 2>&1 &
make -s -C "$ROOT/shell"
sleep 1
SDL_VIDEODRIVER=wayland "$ROOT/shell/isopod-shell" --data "$W/share" --socket "$W/isopodd.sock" >"$W/shell.log" 2>&1 &
sleep 3

python3 "$ROOT/tools/desk/demo.py" "$W" "$OUT"
