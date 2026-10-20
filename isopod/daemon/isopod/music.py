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
PLAYLIST_EXTS = (".m3u", ".m3u8")
STREAM_PREFIXES = ("http://", "https://", "hls+http://", "hls+https://")
FAVORITES = "Favorites.m3u"
SKIP_BACK, SKIP_AHEAD = 15, 30


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


def _tails(files):
    """Each song under every ending of its path, so that a playlist written on another
    computer finds it by as much of the path as the two share. An ending that two songs
    share finds neither."""
    tails = {}
    for file in files:
        parts = _fold(file).split("/")
        for i in range(1, len(parts)):
            tail = "/".join(parts[i:])
            tails[tail] = None if tail in tails else file
    tails.update((_fold(file), file) for file in files)
    return tails


def local(uri):
    """A file outside the music folder, as MPD may give it back: with file:// or without."""
    return uri[7:] if uri.startswith("file://") else uri


class Spoken:
    """What is listened to rather than played: an episode of a podcast or an audiobook. It
    keeps its place, and the one who made it is told where that is as it plays."""

    kind = "podcast"

    def __init__(self, key, uris, title, artist="", art=None):
        self.key = key
        self.uris = list(uris)
        self.title = title
        self.artist = artist
        self.art = art

    def index(self, uri):
        """Which of its files the address is, None if it is none of them."""
        plain = [local(u) for u in self.uris]
        return plain.index(local(uri)) if local(uri) in plain else None

    length = 0.0

    async def ready(self):
        """Made ready to be given to MPD, which may change its addresses."""

    def album(self, index, elapsed):
        """The line under the artist's: the chapter, where there are chapters."""
        return ""

    def heard(self, index, elapsed, duration, force=False):
        pass


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
        self.cannot_play = lambda: None
        self.station_favorite = lambda station: False
        self.step_station = None
        self.station = None
        # Podcasts and audiobooks know their own files, by their addresses.
        self.identify = lambda uri: None
        self.spoken = None
        self.songs = []
        self.favorites = []
        self.playlist_files = []
        self.updating = False
        self._by_file = {}
        self._tails = {}
        self._elapsed = (0.0, time.monotonic())
        self._no_art = set()
        self._listeners = []
        self._rescan = None
        self._stream_watch = None
        self._stream_url = None
        self._ticker = None
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
        self._tails = _tails(self._by_file)
        self.playlist_files = [r["playlist"] for r in recs
                               if r.get("playlist", "").lower().endswith(PLAYLIST_EXTS)]
        self.load_favorites()
        for fn in self._listeners:
            fn()

    def load_favorites(self):
        """The favourites are Favorites.m3u in the music folder, a playlist like the others
        there, so they go wherever the music is copied. There is none until the first song
        is added."""
        favorites = [s["file"] for s in self._playlist_file_songs(FAVORITES)]
        if favorites == self.favorites:
            return
        self.favorites = favorites
        if not self._on_radio():
            self.state.update("player", favorite=self.state.get("player").get("file") in self.favorites)
        self.favorites_changed()

    async def set_favorite(self, file, on):
        """A line that finds no song, one on a card that is out, stays for when it is back."""
        lines = [line for line in self._playlist_lines(FAVORITES) if self._playlist_song(line) != file]
        if on:
            lines.append(file)
        path = os.path.join(self.music_dir, FAVORITES)
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.writelines(line + "\n" for line in lines)
            os.replace(path + ".tmp", path)
        except OSError as e:
            log.warning("favorites not saved: %s", e)
        self.load_favorites()

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
        tuned = self.station and uri == self._stream_url
        spoken = None if tuned else self._spoken_for(uri)
        radio = not spoken and uri.startswith(STREAM_PREFIXES)
        kind = "radio" if radio else spoken.kind if spoken else "song"
        if spoken:
            index = spoken.index(uri)
            duration = _float(status.get("duration") or song.get("duration"))
            about = dict(title=spoken.title, artist=spoken.artist, album=spoken.album(index, elapsed),
                         art=spoken.art, favorite=False, duration=duration,
                         buffering=playing == "play" and not status.get("audio"))
            before = self.state.get("player")
            if uri == before.get("file") and about["album"] != before["album"]:
                self.song_changed()  # wound into another chapter: Now Playing says which
            if playing != "stop":
                spoken.heard(index, elapsed, duration, force=playing == "pause")
            if playing == "play" and not self._ticker:
                self._ticker = spawn(self._tick())
        elif radio:
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
            "player", state=playing if song else "stop", kind=kind, file=uri,
            elapsed=elapsed, pos=_int(status.get("song"), -1) + 1, count=_int(status.get("playlistlength")),
            shuffle=status.get("random") == "1", repeat=status.get("repeat") == "1",
            single=status.get("single") == "1", **about)
        if abs(shown - elapsed) > 1:
            self.state.resend()

    def _spoken_for(self, uri):
        if not uri:
            return None
        if self.spoken and self.spoken.index(uri) is not None:
            return self.spoken
        found = self.identify(uri)
        if found:
            self.spoken = found
        return found

    async def _tick(self, every=5):
        """While an episode or a book plays it is told where it is, and its chapter follows."""
        try:
            while True:
                await asyncio.sleep(every)
                player = self.state.get("player")
                spoken = self.spoken
                index = spoken.index(player.get("file") or "") if spoken else None
                if index is None or player["state"] != "play":
                    return
                elapsed = self._position()
                spoken.heard(index, elapsed, player["duration"])
                chapter = spoken.album(index, elapsed)
                if chapter != player["album"]:
                    self.state.update("player", album=chapter)
                    self.song_changed()
        finally:
            self._ticker = None

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
        if not uri or uri.startswith(STREAM_PREFIXES) or self._on_spoken():
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
        """(title, name) of the playlists MPD keeps and of every .m3u in the music folder,
        which goes by its path there."""
        pairs = await self.mpd.call("listplaylists")
        names = values(pairs, "playlist") + [p for p in self.playlist_files if p != FAVORITES]
        return sorted(((self.playlist_title(n), n) for n in names), key=lambda p: sort_key(p[0]))

    def playlist_title(self, name):
        if name not in self.playlist_files:
            return name
        return unicodedata.normalize("NFC", os.path.splitext(os.path.basename(name))[0])

    async def playlist_songs(self, name):
        if name in self.playlist_files:
            return self._playlist_file_songs(name)
        pairs = await self.mpd.call("listplaylistinfo", name)
        return [Song.from_mpd(r) for r in records(pairs)]

    def _playlist_lines(self, path):
        try:
            with open(os.path.join(self.music_dir, path), "rb") as f:
                lines = map(str.strip, decode(f.read()).splitlines())
        except OSError:
            return []
        return [line for line in lines if line and not line.startswith("#")]

    def _playlist_song(self, line, folder=""):
        """A line is a path from the playlist's own folder or from the music folder, or one
        from the computer the playlist was made on."""
        line = _fold(line.replace("\\", "/"))
        parts = line.strip("/").split("/")
        tries = [os.path.normpath(os.path.join(folder, line))]
        tries += ["/".join(parts[i:]) for i in range(len(parts))]
        return next(filter(None, map(self._tails.get, tries)), None)

    def _playlist_file_songs(self, path):
        folder = _fold(os.path.dirname(path))
        files = (self._playlist_song(line, folder) for line in self._playlist_lines(path))
        return [self._by_file[f] for f in files if f]

    async def queue(self):
        pairs = await self.mpd.call("playlistinfo")
        return [Song.from_mpd(r) for r in records(pairs)]

    # playback ----------------------------------------------------------------

    async def play_songs(self, songs, index=0):
        if not songs:
            return
        self.station = self.spoken = None
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
        self.spoken = None
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

    def show_spoken(self, spoken, index=0, start=0.0):
        """Now Playing shows an episode or a book at once, while it is made ready."""
        self.station = None
        self.spoken = spoken
        if spoken.uris[index] != self.state.get("player").get("file"):
            self.song_changed()
        self.state.update("player", state="play", kind=spoken.kind, file=spoken.uris[index], title=spoken.title,
                          artist=spoken.artist, album=spoken.album(index, start), art=spoken.art, favorite=False,
                          buffering=True, elapsed=float(start), duration=float(spoken.length), pos=1, count=1)

    async def play_spoken(self, spoken, index=0, start=0.0):
        """An episode or a book, from where it was left. Repeat and shuffle are a song's."""
        if self._stream_watch:
            self._stream_watch.cancel()
        self.show_spoken(spoken, index, start)
        await spoken.ready()
        if self.spoken is not spoken:
            return  # something else was picked meanwhile
        cmds = [("clear",), ("random", "0"), ("repeat", "0"), ("single", "0")]
        cmds += [("add", uri) for uri in spoken.uris]
        if start < 1:
            await self.mpd.call_list(cmds + [("play", str(index))])
        else:
            await self.mpd.call_list(cmds)
            try:
                await self.mpd.call("seek", str(index), f"{start:.1f}")
            except MPDError:
                # A stream that cannot be wound starts from its beginning.
                await self.mpd.call("play", str(index))
        if spoken.uris[index].startswith(STREAM_PREFIXES):
            self._stream_watch = spawn(self._watch_spoken(spoken))

    async def _watch_spoken(self, spoken, patience=20):
        """An episode from the web that does not start: out of reach when nothing came, and
        not to be played when MPD could not make sound of what came (it can be downloaded)."""
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if self.spoken is not spoken:
                return
            try:
                status = await self._status()
            except MPDError:
                return
            if status.get("error"):
                break
            if status.get("audio") or status.get("state") != "play":
                return
        error = status.get("error")
        try:
            await self.mpd.call_list([("clearerror",), ("stop",)])
        except MPDError:
            pass
        log.warning("episode did not start: %s: %s", spoken.uris[0], error or "no sound")
        (self.cannot_play if error else self.stream_failed)()
        await self.refresh()

    async def seek_to(self, seconds):
        await self.mpd.call("seekcur", f"{max(0.0, seconds):.1f}")

    def station_logo(self, station, path):
        """The logo of the station that is on came in after it was tuned in."""
        if self.station and self.station.get("favicon") == station.get("favicon"):
            self.station["art"] = path
            if self.state.get("player")["kind"] == "radio":
                self.state.update("player", art=path)

    def _on_radio(self):
        return self.state.get("player")["kind"] == "radio"

    def _on_spoken(self):
        return self.state.get("player")["kind"] in ("podcast", "audiobook")

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
        if self._on_spoken():
            return await self.skip(SKIP_AHEAD)
        await self.mpd.call("next")

    async def skip(self, delta):
        """Spoken words go back and ahead by a sentence or two, not to the next file."""
        duration = self.state.get("player")["duration"]
        target = self._position() + delta
        await self.seek_to(min(target, duration - 1) if duration > 0 else target)

    async def _step(self, delta):
        """On the radio the next and the previous are stations, not songs."""
        if self.step_station:
            await self.step_station(delta)

    async def previous(self):
        if self._on_radio():
            return await self._step(-1)
        if self._on_spoken():
            return await self.skip(-SKIP_BACK)
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
