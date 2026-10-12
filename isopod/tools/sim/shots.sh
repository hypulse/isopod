#!/bin/sh
# Capture every screen. Each tour in shots/ runs against a fresh simulator and writes its
# screenshots to <out>/<tour>/, with a sheet of thumbnails, <out>/<tour>.png, beside it.
# usage, in the isopod-dev container: shots.sh <out-dir> [tour.txt ...]
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$1
shift
WORK=${ISOPOD_SIM_DIR:-/tmp/isopod-sim}
for TOUR in ${@:-$HERE/shots/*.txt}; do
    NAME=$(basename "$TOUR" .txt)
    DIR=$WORK
    if [ "$NAME" = 08-empty ]; then
        # A library with nothing in it: run.sh fills only a missing one.
        DIR=$WORK-empty
        mkdir -p "$DIR/music" "$DIR/videos" "$DIR/playlists"
    fi
    rm -rf "$DIR/config" "$DIR/cache" "$DIR/mpd.state" "$DIR/playlists/Favorites.m3u" "$OUT/$NAME"
    ISOPOD_SIM_DIR=$DIR "$HERE/run.sh" "$TOUR" "$OUT/$NAME"
    python3 "$HERE/sheet.py" "$OUT/$NAME" "$OUT/$NAME.png"
done
