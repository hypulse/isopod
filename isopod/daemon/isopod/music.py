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
STREAM_PREFIXES = ("http://", "https://", "hls+http://", "hls+https://")
FAVORITES = "Favorites"


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


def stream_urls(url):
    """The addresses to try a station under. MPD on isoPod plays some HLS streams only as a
    plain address and others only under FFmpeg's hls+ scheme; no telling which from the address."""
    return [url[4:], url] if url.startswith("hls+") else [url]


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
            year=(rec.get("date") or "")[:4],
            track=_int(rec.get("track")),
            disc=_int(rec.get("disc")),
            duration=_float(rec.get("duration") or rec.get("time")),
        )


class Music:
    RESCAN_AFTER = 10

    def __init__(self, mpd: MPD, state, cache_dir, music_dir="/storage/music"):
        self.mpd = mpd
        self.state = state
        self.music_dir = music_dir
        self.art_dir = os.path.join(cache_dir, "art")
        self.song_changed = lambda: None
        self.favorites_changed = lambda: None
        self.stream_failed = lambda: None
        self.station_favorite = lambda station: False
        self.step_station = None
        self.station = None
        self.songs = []
        self.favorites = []
        self.updating = False
        self._by_file = {}
        self._elapsed = (0.0, time.monotonic())
        self._no_art = set()
        self._listeners = []
        self._rescan = None
        self._stream_watch = None
        self._stream_url = None
        state.adjusters.append(self._live_elapsed)

    def on_library_change(self, fn):
        self._listeners.append(fn)

    async def start(self):
        spawn(self.mpd.idle_forever(self._changed))

    async def _changed(self, subsystems):
        if "reconnect" in subsystems:
            self._rescan_soon()
        if subsystems & {"reconnect", "database"}:
            await self.load_library()
        if subsystems & {"reconnect", "stored_playlist"}:
            await self.load_favorites()
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

    async def load_favorites(self):
        """The favourites are a playlist MPD keeps, in the order they were added. There is
        none until the first song is added."""
        try:
            favorites = values(await self.mpd.call("listplaylist", FAVORITES), "file")
        except MPDError:
            favorites = []
        if favorites == self.favorites:
            return
        self.favorites = favorites
        if not self._on_radio():
            self.state.update("player", favorite=self.state.get("player").get("file") in self.favorites)
        self.favorites_changed()

    async def set_favorite(self, file, on):
        if on:
            await self.mpd.call("playlistadd", FAVORITES, file)
        else:
            await self.mpd.call_list([("playlistdelete", FAVORITES, str(i))
                                      for i, f in reversed(list(enumerate(self.favorites))) if f == file])
        await self.load_favorites()

    def _rescan_soon(self):
        """MPD scans nothing when it starts, so songs copied while isoPod was off would stay
        unseen: one scan a little after it has come up. At boot it comes up twice, the
        second time with the card in sight, and the scan follows the second."""
        if self._rescan:
            self._rescan.cancel()
        self._rescan = asyncio.get_running_loop().call_later(
            self.RESCAN_AFTER, lambda: spawn(self.update_library()))

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
        shown = self._position()
        self._elapsed = (elapsed, time.monotonic())
        uri = song.get("file", "")
        if uri != self.state.get("player").get("file"):
            self.song_changed()
        radio = uri.startswith(STREAM_PREFIXES)
        if radio:
            # A station is shown by its own name; what it says is on is the line under it.
            station = self.station if self.station and uri == self._stream_url else {}
            about = dict(title=station.get("name") or song.get("name") or uri, artist=song.get("title", ""),
                         album="", art=station.get("art"), favorite=self.station_favorite(station),
                         duration=0.0, buffering=playing == "play" and not status.get("audio"))
        else:
            s = Song.from_mpd(song) if song else Song(title="", artist="", album="")
            art = self._art_for(uri) if uri else None
            if uri and art is None and uri not in self._no_art:
                spawn(self._fetch_art(uri))
            about = dict(title=s["title"], artist=s["artist"], album=s["album"], art=art,
                         favorite=uri in self.favorites, buffering=False,
                         duration=_float(status.get("duration") or song.get("duration")))
        self.state.update(
            "player", state=playing if song else "stop", kind="radio" if radio else "song", file=uri,
            elapsed=elapsed, pos=_int(status.get("song"), -1) + 1, count=_int(status.get("playlistlength")),
            shuffle=status.get("random") == "1", repeat=status.get("repeat") == "1",
            single=status.get("single") == "1", **about)
        if abs(shown - elapsed) > 1:
            self.state.resend()

    def _position(self):
        base, at = self._elapsed
        return base + (time.monotonic() - at if self.state.get("player")["state"] == "play" else 0)

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

    def details(self):
        """A line about the song playing: its year, its genre and the kind of file it is."""
        uri = self.state.get("player").get("file") or ""
        song = self._by_file.get(uri) or {}
        kind = os.path.splitext(uri)[1][1:].upper()
        return " · ".join(part for part in (song.get("year"), song.get("genre"), kind) if part)

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
        if not uri or uri.startswith(STREAM_PREFIXES):
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

    def favorite_songs(self):
        return [self._by_file[f] for f in self.favorites if f in self._by_file]

    async def playlists(self):
        pairs = await self.mpd.call("listplaylists")
        return sorted((p for p in values(pairs, "playlist") if p != FAVORITES), key=sort_key)

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
        self.station = None
        cmds = [("clear",), ("random", "0")] + [("add", s["file"]) for s in songs]
        cmds.append(("play", str(max(0, min(index, len(songs) - 1)))))
        await self.mpd.call_list(cmds)

    async def shuffle_all(self):
        songs = list(self.songs)
        random.shuffle(songs)
        await self.play_songs(songs, 0)

    async def play_stream(self, station):
        """Show the station at once; MPD stays silent while the stream connects."""
        self.station = station
        urls = stream_urls(station["url"])
        if urls[0] != self.state.get("player").get("file"):
            self.song_changed()
        self.state.update("player", state="play", kind="radio", file=urls[0], buffering=True,
                          title=station.get("name") or urls[0], artist="", album="", art=station.get("art"),
                          favorite=self.station_favorite(station), elapsed=0.0, duration=0.0, pos=1, count=1)
        if self._stream_watch:
            self._stream_watch.cancel()
        await self._start_stream(urls[0])
        self._stream_watch = spawn(self._watch_stream(station, urls))

    async def _start_stream(self, url):
        self._stream_url = url
        await self.mpd.call_list([("clear",), ("add", url), ("play", "0")])

    async def _watch_stream(self, station, urls, patience=20):
        """See that the station sounds, under the next of its addresses if not under this one."""
        for i, url in enumerate(urls):
            if i:
                await self._start_stream(url)
            deadline = time.monotonic() + patience / len(urls)
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                if self.station is not station:
                    return
                try:
                    status = await self._status()
                except MPDError:
                    return
                if status.get("error"):
                    break
                if status.get("audio") or status.get("state") != "play":
                    await self.refresh()
                    return
            try:
                await self.mpd.call_list([("clearerror",), ("stop",)])
            except MPDError:
                pass
        log.warning("station did not start: %s", urls[0])
        self.stream_failed()
        await self.refresh()

    def station_logo(self, station, path):
        """The logo of the station that is on came in after it was tuned in."""
        if self.station and self.station.get("favicon") == station.get("favicon"):
            self.station["art"] = path
            if self.state.get("player")["kind"] == "radio":
                self.state.update("player", art=path)

    def _on_radio(self):
        return self.state.get("player")["kind"] == "radio"

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
        if self._on_radio():
            return await self._step(1)
        await self.mpd.call("next")

    async def _step(self, delta):
        """On the radio the next and the previous are stations, not songs."""
        if self.step_station:
            await self.step_station(delta)

    async def previous(self):
        if self._on_radio():
            return await self._step(-1)
        if self._position() > 3 or self.state.get("player")["pos"] <= 1:
            await self.mpd.call("seekcur", "0")
        else:
            await self.mpd.call("previous")

    async def seek(self, delta):
        if self._on_radio():
            return
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
