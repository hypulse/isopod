import asyncio
import hashlib
import logging
import os
import random
import time
import unicodedata

from .mpd import MPD, MPDError, records, values
from .lyrics import decode, parse_lrc
from .util import matches, sort_key, spawn

log = logging.getLogger("isopod.music")

LYRIC_EXTS = (".lrc", ".txt")


def _int(value, default=0):
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fold(name):
    return unicodedata.normalize("NFC", name).casefold()


class Song(dict):
    @classmethod
    def from_mpd(cls, rec):
        title = rec.get("title") or os.path.splitext(os.path.basename(rec["file"]))[0]
        artist = rec.get("artist") or ""
        return cls(
            file=rec["file"],
            title=title,
            artist=artist,
            albumartist=rec.get("albumartist") or artist,
            album=rec.get("album") or "",
            genre=rec.get("genre") or "",
            track=_int(rec.get("track")),
            disc=_int(rec.get("disc")),
            duration=_float(rec.get("duration") or rec.get("time")),
        )


class Music:
    def __init__(self, mpd: MPD, state, cache_dir, music_dir="/storage/music"):
        self.mpd = mpd
        self.state = state
        self.music_dir = music_dir
        self.art_dir = os.path.join(cache_dir, "art")
        self.song_changed = lambda: None
        self.songs = []
        self.updating = False
        self._by_file = {}
        self._elapsed = (0.0, time.monotonic())
        self._no_art = set()
        self._listeners = []
        state.adjusters.append(self._live_elapsed)

    def on_library_change(self, fn):
        self._listeners.append(fn)

    async def start(self):
        spawn(self.mpd.idle_forever(self._changed))

    async def _changed(self, subsystems):
        if subsystems & {"reconnect", "database"}:
            await self.load_library()
        if subsystems & {"reconnect", "update"}:
            status = await self._status()
            self.updating = "updating_db" in status
        if subsystems & {"reconnect", "player", "playlist", "options", "mixer"}:
            await self.refresh()

    async def load_library(self):
        try:
            pairs = await self.mpd.call("listallinfo")
        except MPDError as e:
            log.warning("library load failed: %s", e)
            return
        recs = records(pairs, start_keys=("file", "directory", "playlist"))
        self.songs = [Song.from_mpd(r) for r in recs if "file" in r]
        self._by_file = {s["file"]: s for s in self.songs}
        for fn in self._listeners:
            fn()

    async def update_library(self):
        await self.mpd.call("update")
        self.updating = True

    async def _status(self):
        return {k: v for k, v in await self.mpd.call("status")}

    async def refresh(self):
        try:
            status = await self._status()
            current = records(await self.mpd.call("currentsong"))
        except MPDError as e:
            log.warning("status failed: %s", e)
            return
        song = current[0] if current else {}
        playing = status.get("state", "stop")
        elapsed = _float(status.get("elapsed"))
        self._elapsed = (elapsed, time.monotonic())
        uri = song.get("file", "")
        if uri != self.state.get("player").get("file"):
            self.song_changed()
        s = Song.from_mpd(song) if song else Song(title="", artist="", album="")
        art = self._art_for(uri) if uri else None
        if uri and art is None and uri not in self._no_art:
            spawn(self._fetch_art(uri))
        self.state.update(
            "player", state=playing if song else "stop", file=uri, elapsed=elapsed,
            duration=_float(status.get("duration") or song.get("duration")),
            pos=_int(status.get("song"), -1) + 1, count=_int(status.get("playlistlength")),
            shuffle=status.get("random") == "1", repeat=status.get("repeat") == "1",
            single=status.get("single") == "1", title=s["title"], artist=s["artist"], album=s["album"],
            art=art)

    def _live_elapsed(self, snap):
        player = snap["player"]
        if player["state"] == "play":
            base, at = self._elapsed
            player["elapsed"] = base + (time.monotonic() - at)

    def _art_key(self, uri):
        """One cover per album. A song without an album tag keeps its own, and a folder is
        no guide: twenty songs from twenty albums can sit side by side in one."""
        song = self._by_file.get(uri)
        name = f"{song['albumartist']}\0{song['album']}" if song and song["album"] else uri
        return hashlib.sha1(name.encode()).hexdigest()[:16]

    def _art_for(self, uri):
        base = os.path.join(self.art_dir, self._art_key(uri))
        for ext in (".jpg", ".png"):
            if os.path.exists(base + ext):
                return base + ext
        return None

    async def art_path(self, uri):
        return self._art_for(uri) or await self._download_art(uri)

    async def _download_art(self, uri):
        picture = await self.mpd.picture(uri)
        if not picture:
            return None
        data, mime = picture
        path = os.path.join(self.art_dir, self._art_key(uri)) + (".png" if "png" in mime else ".jpg")
        os.makedirs(self.art_dir, exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return path

    async def _fetch_art(self, uri):
        path = await self.art_path(uri)
        if not path:
            self._no_art.add(uri)
        elif self.state.get("player").get("file") == uri:
            self.state.update("player", art=path)

    # library views ---------------------------------------------------------

    def artists(self):
        names = {s["albumartist"] for s in self.songs}
        return sorted(names, key=sort_key)

    def _lyric_files(self, uri):
        """The .lrc, then the .txt, named like the song. Names are compared the way they
        read: Song.LRC belongs to song.mp3, and so does a name typed on a Mac, which
        stores Hangul and accents taken apart."""
        folder, name = os.path.split(os.path.splitext(os.path.join(self.music_dir, uri))[0])
        try:
            names = {_fold(n): n for n in os.listdir(folder)}
        except OSError:
            return []
        return [os.path.join(folder, names[key]) for key in (_fold(name + ext) for ext in LYRIC_EXTS)
                if key in names]

    async def lyrics(self):
        """(lines, times or None) for the song playing: an .lrc or .txt beside the file first,
        then lyrics tags MPD can read. None when there are none."""
        uri = self.state.get("player").get("file") or ""
        if not uri:
            return None
        for path in self._lyric_files(uri):
            try:
                with open(path, "rb") as f:
                    found = parse_lrc(decode(f.read()))
                if found:
                    return found
            except OSError:
                continue
        try:
            pairs = await self.mpd.call("readcomments", uri)
        except MPDError:
            return None
        text = "\n".join(v for k, v in pairs if k.lower().replace(" ", "") in
                         ("lyrics", "unsyncedlyrics", "syncedlyrics"))
        return parse_lrc(text) if text else None

    def coverflow_albums(self):
        """Albums in iPod Cover Flow order: by artist, then by album."""
        return sorted(self.albums(), key=lambda kv: (sort_key(kv[0][0]), sort_key(kv[0][1])))

    def search(self, query, limit=50):
        """Artists, albums and songs whose names contain the query."""
        if not query.strip():
            return [], [], []
        artists = [a for a in self.artists() if matches(query, a)]
        albums = [(key, f) for key, f in self.albums() if matches(query, key[1])]
        songs = [s for s in self.all_songs() if matches(query, s["title"])]
        return artists[:limit], albums[:limit], songs[:limit]

    def albums(self, artist=None, genre=None):
        """Albums by name, each with one of its songs. Songs that name no album do not
        make one: they are under Songs and under their artist."""
        found = {}
        for s in self.songs:
            if not s["album"] or (artist is not None and s["albumartist"] != artist):
                continue
            if genre is not None and s["genre"] != genre:
                continue
            key = (s["albumartist"], s["album"])
            found.setdefault(key, s["file"])
        return sorted(found.items(), key=lambda kv: (sort_key(kv[0][1]), sort_key(kv[0][0])))

    def album_songs(self, artist, album):
        songs = [s for s in self.songs if s["albumartist"] == artist and s["album"] == album]
        return sorted(songs, key=lambda s: (s["disc"], s["track"], sort_key(s["title"])))

    def artist_songs(self, artist):
        songs = [s for s in self.songs if s["albumartist"] == artist]
        return sorted(songs, key=lambda s: (sort_key(s["album"]), s["disc"], s["track"]))

    def genres(self):
        return sorted({s["genre"] for s in self.songs if s["genre"]}, key=sort_key)

    def genre_songs(self, genre):
        songs = [s for s in self.songs if s["genre"] == genre]
        return sorted(songs, key=lambda s: (sort_key(s["albumartist"]), sort_key(s["album"]),
                                            s["disc"], s["track"]))

    def all_songs(self):
        return sorted(self.songs, key=lambda s: sort_key(s["title"]))

    async def playlists(self):
        pairs = await self.mpd.call("listplaylists")
        return sorted(values(pairs, "playlist"), key=sort_key)

    async def playlist_songs(self, name):
        pairs = await self.mpd.call("listplaylistinfo", name)
        return [Song.from_mpd(r) for r in records(pairs)]

    async def queue(self):
        pairs = await self.mpd.call("playlistinfo")
        return [Song.from_mpd(r) for r in records(pairs)]

    # playback ----------------------------------------------------------------

    async def play_songs(self, songs, index=0):
        if not songs:
            return
        cmds = [("clear",), ("random", "0")] + [("add", s["file"]) for s in songs]
        cmds.append(("play", str(max(0, min(index, len(songs) - 1)))))
        await self.mpd.call_list(cmds)

    async def shuffle_all(self):
        songs = list(self.songs)
        random.shuffle(songs)
        await self.play_songs(songs, 0)

    async def enqueue(self, song, next_up=False):
        if next_up:
            pos = max(self.state.get("player")["pos"], 0)
            await self.mpd.call("add", song["file"], str(pos))
        else:
            await self.mpd.call("add", song["file"])

    async def clear_up_next(self):
        pos = self.state.get("player")["pos"]
        count = self.state.get("player")["count"]
        if pos and count > pos:
            await self.mpd.call("delete", f"{pos}:")

    async def play_position(self, index):
        await self.mpd.call("play", str(index))

    async def toggle(self):
        player = self.state.get("player")
        if player["state"] == "play":
            await self.pause()
        elif player["count"]:
            await self.mpd.call("play")

    async def pause(self):
        if self.state.get("player")["state"] == "play":
            await self.mpd.call("pause", "1")

    async def next(self):
        await self.mpd.call("next")

    async def previous(self):
        player = self.state.get("player")
        base, at = self._elapsed
        elapsed = base + (time.monotonic() - at if player["state"] == "play" else 0)
        if elapsed > 3 or player["pos"] <= 1:
            await self.mpd.call("seekcur", "0")
        else:
            await self.mpd.call("previous")

    async def seek(self, delta):
        await self.mpd.call("seekcur", f"{delta:+d}")

    async def set_shuffle(self, on):
        await self.mpd.call("random", "1" if on else "0")

    async def cycle_repeat(self):
        player = self.state.get("player")
        if not player["repeat"]:
            await self.mpd.call_list([("repeat", "1"), ("single", "0")])
        elif not player["single"]:
            await self.mpd.call("single", "1")
        else:
            await self.mpd.call_list([("repeat", "0"), ("single", "0")])

    async def reset_output(self):
        """Reopen the output so the next play follows the current default sink."""
        try:
            await self.mpd.call_list([("disableoutput", "0"), ("enableoutput", "0")])
        except MPDError:
            pass

    async def wait_ready(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                await self.mpd.call("ping")
                return True
            except MPDError:
                await asyncio.sleep(0.5)
        return False
