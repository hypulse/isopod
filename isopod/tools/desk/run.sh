#!/bin/sh
# Record isoPod with real apps (mpv, KOReader) under a headless sway, inside isopod-desk.
# usage: run.sh BOOKS_DIR OUT_DIR    (BOOKS_DIR holds the EPUBs to read)
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
BOOKS=$1
OUT=${2:-/out}
W=/tmp/isopod-desk

export XDG_RUNTIME_DIR=/tmp/xdg HOME=/root LIBGL_ALWAYS_SOFTWARE=1 TZ=Asia/Seoul
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
mkdir -p "$W/share" "$W/config" "$W/cache" "$W/books" "$OUT" /storage/.config /storage/.cache/isopod/mpv

# the data directory as the image lays it out
ln -sfn /usr/share/fonts/pretendard "$W/share/fonts"
ln -sfn "$ROOT/shell/assets/icons" "$W/share/icons"
ln -sfn "$ROOT/data/wordwise" "$W/share/wordwise"
ln -sfn "$ROOT/system/mpv" "$W/share/mpv"
ln -sfn "$ROOT/system/koreader" "$W/share/koreader"
for f in "$ROOT"/shell/assets/*.png; do ln -sf "$f" "$W/share/"; done
ln -sfn "$W/share" /usr/share/isopod

[ -d "$W/music" ] || python3 "$ROOT/tools/gen_test_music.py" "$W" >/dev/null
# real media only: the simulator's empty stand-ins would open as broken files here
find "$W/videos" "$W/books" -type f -size 0 -delete 2>/dev/null || true
rm -rf "$W/videos/Concerts" "$W/books/Classics" "$W/books/데미안.sdr" "$W/ports"
[ -f "$W/videos/Loop.webm" ] || python3 "$ROOT/tools/desk/media.py" "$W/videos"
cp -n "$BOOKS"/*.epub "$W/books/" 2>/dev/null || true
# a device in use: Alice is being read, from chapter one
ALICE="$W/books/Alice's Adventures in Wonderland"
if [ -f "$ALICE.epub" ] && [ ! -d "$ALICE.sdr" ]; then
    mkdir -p "$ALICE.sdr"
    printf 'return {\n    ["copt_block_rendering_mode"] = 3,\n    ["cre_dom_version"] = 20240114,\n    ["last_xpointer"] = "/body/DocFragment[3]/body/div/h2",\n    ["percent_finished"] = 0.02,\n}\n' \
        >"$ALICE.sdr/metadata.epub.lua"
fi

cat >"$W/sway.conf" <<CONF
output HEADLESS-1 resolution 640x480 position 0 0
default_border none
seat * hide_cursor 1
include $ROOT/system/sway.conf
CONF
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 sway -c "$W/sway.conf" >"$W/sway.log" 2>&1 &
sleep 2
export WAYLAND_DISPLAY=wayland-1 SWAYSOCK=$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock | head -1)

# KOReader has been opened before, as on a device in use, so no first-run notices on camera
if [ ! -f /storage/.config/koreader/settings/bookinfo_cache.sqlite3 ]; then
    python3 -c "import sys; sys.path.insert(0, '$ROOT/daemon')
from isopod.apps import prepare_koreader; prepare_koreader('$W/share', 'ko')"
    KO_HOME=/storage/.config/koreader SDL_FULLSCREEN=1 setsid koreader "$W/books/Pride and Prejudice.epub" \
        >"$W/koreader-warm.log" 2>&1 &
    sleep 8
    kill -TERM -$! 2>/dev/null || true
    sleep 3
fi

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

python3 "$ROOT/daemon/isopodd" --sim --apps direct --socket "$W/isopodd.sock" --config "$W/config" \
    --cache "$W/cache" --music "$W/music" --videos "$W/videos" --books "$W/books" --ports "$W/ports" \
    --data "$W/share" --mpd "$W/mpd.sock" >"$W/isopodd.log" 2>&1 &
make -s -C "$ROOT/shell"
sleep 1
SDL_VIDEODRIVER=wayland "$ROOT/shell/isopod-shell" --data "$W/share" --socket "$W/isopodd.sock" >"$W/shell.log" 2>&1 &
sleep 3

python3 "$ROOT/tools/desk/demo.py" "$W" "$OUT"
