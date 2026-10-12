#!/bin/sh
# Run MPD, isopodd (simulated hardware) and isopod-shell inside the isopod-dev container.
# usage: run.sh [script] [screenshot-dir]; with ISOPOD_RECORD=out.mp4 the walk is also recorded.
set -e
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
WORK=${ISOPOD_SIM_DIR:-/tmp/isopod-sim}
SCRIPT=$1
SHOTS=${2:-/out}

mkdir -p "$WORK/share" "$WORK/config" "$WORK/cache" "$SHOTS"
[ -d "$WORK/music" ] || python3 "$ROOT/tools/gen_test_music.py" "$WORK"

ln -sfn /usr/share/fonts/pretendard "$WORK/share/fonts"
ln -sfn "$ROOT/shell/assets/icons" "$WORK/share/icons"
for f in "$ROOT"/shell/assets/*.png; do ln -sf "$f" "$WORK/share/"; done
cp -rn "$ROOT/system/mpv" "$WORK/share/" 2>/dev/null || true

cat >"$WORK/mpd.conf" <<EOF
music_directory "$WORK/music"
playlist_directory "$WORK/playlists"
db_file "$WORK/mpd.db"
state_file "$WORK/mpd.state"
bind_to_address "$WORK/mpd.sock"
auto_update "yes"
audio_output {
    type "null"
    name "null"
    mixer_type "none"
}
EOF
pkill -x mpd 2>/dev/null || true
mpd "$WORK/mpd.conf"

pkill -f "isopodd --sim" 2>/dev/null || true
export PATH="$ROOT/shell:$PATH"  # isopod-thumb, which isopodd runs on station logos
python3 "$ROOT/daemon/isopodd" --sim --socket "$WORK/isopodd.sock" --config "$WORK/config" \
    --cache "$WORK/cache" --music "$WORK/music" --videos "$WORK/videos" --data "$WORK/share" \
    --mpd "$WORK/mpd.sock" >"$WORK/isopodd.log" 2>&1 &
DAEMON=$!

make -s -C "$ROOT/shell"
cd "$SHOTS"
if [ -n "$SCRIPT" ] && [ -n "$ISOPOD_RECORD" ]; then
    # Record the walk as a video: the screen plus a caption strip, scaled 2x.
    SDL_VIDEODRIVER=offscreen "$ROOT/shell/isopod-shell" --data "$WORK/share" --socket "$WORK/isopodd.sock" \
        --script "$SCRIPT" --record "ffmpeg -loglevel error -y -f rawvideo -pix_fmt rgb24 -s 640x544 -r 30 \
        -i - -vf scale=1280:1088:flags=lanczos -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
        -movflags +faststart $ISOPOD_RECORD"
elif [ -n "$SCRIPT" ]; then
    SDL_VIDEODRIVER=offscreen "$ROOT/shell/isopod-shell" --data "$WORK/share" --socket "$WORK/isopodd.sock" \
        --script "$SCRIPT"
fi
kill $DAEMON 2>/dev/null || true
pkill -x mpd 2>/dev/null || true
