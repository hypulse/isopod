import asyncio
import datetime
import hashlib
import logging
import os
import secrets
import zlib

from . import news
from .apps import installed_ports, port_spec
from .ebook import Glossary, annotate_epub, follow_position, settle_position
from .radio import RadioError
from .system import human_size
from .util import enc, sort_key, spawn, split_path

log = logging.getLogger("isopod.pages")

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".wmv",
              ".asf", ".flv", ".f4v", ".3gp", ".ogv", ".vob", ".divx", ".rm", ".rmvb")
BOOK_EXTS = (".epub", ".pdf", ".mobi", ".azw3", ".azw", ".fb2", ".fb2.zip", ".cbz", ".cbr", ".djvu",
             ".txt", ".md", ".rtf", ".docx", ".odt", ".html", ".htm", ".chm", ".xps")
TIMEOUTS = (15, 30, 60, 120, 300, 0)
LIMITS = (100, 90, 80, 70, 60, 50)


def item(key, title, accessory="chevron", **extra):
    out = {"key": key, "title": title, "accessory": accessory}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def header(title):
    return {"header": True, "title": title}


def page(path, title, items, **extra):
    out = {"path": path, "title": title, "items": items, "style": "list"}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def empty(icon, title, text=None):
    return {"icon": icon, "title": title, "text": text}


def _when(stamp):
    """A time of day for today, the date before it for older days."""
    if not stamp:
        return None
    then = datetime.datetime.fromtimestamp(stamp)
    return f"{then:%H:%M}" if then.date() == datetime.date.today() else f"{then.month}/{then.day} {then:%H:%M}"


def _strip_ext(name, exts):
    lower = name.lower()
    ext = max((e for e in exts if lower.endswith(e)), key=len, default="")
    return name[:len(name) - len(ext)] if ext else os.path.splitext(name)[0]


def folder_listing(folder, exts):
    """Visible subfolders and matching files, folders first, both in library order."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return [], []
    dirs, files = [], []
    for e in entries:
        if e.name.startswith(".") or e.name.endswith(".sdr"):
            continue
        try:
            if e.is_dir():
                dirs.append(e.name)
            elif e.name.lower().endswith(exts):
                files.append(e.name)
        except OSError:
            continue
    return sorted(dirs, key=sort_key), sorted(files, key=lambda n: sort_key(_strip_ext(n, exts)))


class Pages:
    def __init__(self, app):
        self.app = app
        self._tokens = {}
        self._stations = {}
        self._art_jobs = set()
        self._busy_books = set()
        self._glossaries = {}
        self._news_job = None

    @property
    def t(self):
        return self.app.t

    def _token(self, fn):
        token = secrets.token_hex(6)
        self._tokens[token] = fn
        return token

    def _duration_label(self, seconds):
        if seconds == 0:
            return self.t("never")
        if seconds < 60:
            return self.t("seconds", n=seconds)
        return self.t("minutes", n=seconds // 60)

    # entry points ------------------------------------------------------------

    async def build(self, path):
        parts = split_path(path)
        handler = getattr(self, "_page_" + (parts[0] if parts else "root"), None)
        if handler is None:
            return page(path, "", [], empty=empty(None, self.t("empty_list")))
        return await handler(path, parts[1:])

    async def activate(self, path, key):
        parts = split_path(path)
        handler = getattr(self, "_do_" + (parts[0] if parts else "root"), None)
        if handler is None:
            return {"none": True}
        return await handler(path, parts[1:], key) or {"none": True}

    async def alt(self, path, key):
        parts = split_path(path)
        handler = getattr(self, "_alt_" + (parts[0] if parts else "root"), None)
        if handler is None:
            return {"none": True}
        return await handler(path, parts[1:], key) or {"none": True}

    async def resolve(self, token, value=None):
        fn = self._tokens.pop(token, None)
        if fn is None:
            return {"none": True}
        result = fn(value) if value is not None else fn()
        if asyncio.iscoroutine(result):
            result = await result
        return result or {"none": True}

    async def slider(self, path, value):
        if path == "/settings/display/brightness":
            self.app.settings.set("brightness", int(value))
            self.app.power.apply_brightness()

    def leave(self, path):
        if path in ("/settings/bluetooth",):
            self.app.bluetooth.end_discovery()
        elif path == "/settings/transfer":
            self.app.transfer.stop()

    # root --------------------------------------------------------------------

    def _art_pool(self, path, limit=16):
        """Album covers for the artwork panel beside split menus, in a stable shuffled order."""
        m = self.app.music
        albums = m.albums()
        pool = [a for a in (m._art_for(f) for _, f in albums) if a]
        if len(pool) < min(limit, len(albums)):
            self._prefetch_art(path, [f for _, f in albums[:limit * 2]])
        pool.sort(key=lambda p: zlib.crc32(os.path.basename(p).encode()))
        return pool[:limit]

    async def _page_root(self, path, rest):
        t = self.t
        items = [
            item("music", t("music"), preview="art"),
            item("radio", t("radio"), preview="radio"),
            item("videos", t("videos"), preview="video"),
            item("books", t("books"), preview="book"),
            item("games", t("games"), preview="game"),
            item("settings", t("settings"), preview="settings"),
            item("shuffle", t("shuffle_songs"), "none", preview="art"),
        ]
        player = self.app.state.get("player")
        if player["count"] or player["state"] != "stop":
            items.append(item("nowplaying", t("now_playing"), preview="now"))
        apps = self.app.state.get("apps")
        if apps:
            items.append(header(t("open_apps")))
            for a in apps:
                kind = {"koreader": "book", "video": "video"}.get(a["key"], "game")
                items.append(item(f"app:{a['key']}", a["title"], "none", preview=kind))
        return page("/", "isoPod", items, live=True, layout="split", art=self._art_pool(path))

    async def _do_root(self, path, rest, key):
        if key == "shuffle":
            if not self.app.music.songs:
                return {"push": "/music/songs"}
            await self.app.music.shuffle_all()
            return {"nowplaying": True}
        if key == "nowplaying":
            return {"nowplaying": True}
        if key.startswith("app:"):
            await self.app.resume_app(key[4:])
            return {"none": True}
        return {"push": "/" + key}

    async def _alt_root(self, path, rest, key):
        if key.startswith("app:"):
            app_key = key[4:]
            return {"sheet": {"title": "", "token": self._token(
                lambda choice: self._app_sheet(app_key, choice)),
                "items": [{"key": "close", "title": self.t("close_app"), "destructive": True}]}}

    async def _app_sheet(self, app_key, choice):
        if choice == "close":
            await self.app.apps.close(app_key)
        return {"reload": True}

    # music -------------------------------------------------------------------

    def _empty_music(self):
        return empty("music", self.t("no_music_title"), self.t("no_music_text"))

    def _song_items(self, songs, playing_file=None):
        return [item(f"s{i}", s["title"], "playing" if s["file"] == playing_file else "none")
                for i, s in enumerate(songs)]

    async def _songs_for(self, rest):
        m = self.app.music
        kind = rest[0] if rest else ""
        if kind == "songs":
            return m.all_songs()
        if kind == "album" and len(rest) >= 3:
            return m.album_songs(rest[1], rest[2])
        if kind == "artist" and len(rest) >= 3 and rest[2] == "all":
            return m.artist_songs(rest[1])
        if kind == "genre" and len(rest) >= 2:
            return m.genre_songs(rest[1])
        if kind == "playlist" and len(rest) >= 2:
            return await m.playlist_songs(rest[1])
        if kind == "search" and len(rest) >= 2:
            return m.search(rest[1])[2]
        return None

    async def _page_music(self, path, rest):
        t = self.t
        m = self.app.music
        playing = self.app.state.get("player").get("file")
        if not rest:
            items = [item("coverflow", t("cover_flow"), preview="art"),
                     item("playlists", t("playlists"), preview="playlist"),
                     item("artists", t("artists"), preview="art"),
                     item("albums", t("albums"), preview="art"),
                     item("songs", t("songs"), preview="art"),
                     item("genres", t("genres"), preview="genre"),
                     item("queue", t("up_next"), preview="now"),
                     item("search", t("search"), preview="search")]
            return page(path, t("music"), items, layout="split", art=self._art_pool(path))
        kind = rest[0]
        if kind == "coverflow":
            albums = m.coverflow_albums()
            items = [item(f"alb{i}", album or t("unknown_album"), "none",
                          subtitle=artist or t("unknown_artist"), art=self._album_art(file),
                          link="/music/album/" + enc(artist, album))
                     for i, ((artist, album), file) in enumerate(albums)]
            self._prefetch_art(path, [f for _, f in albums])
            return page(path, t("cover_flow"), items, style="coverflow", live=True,
                        empty=self._empty_music())
        if kind == "search" and len(rest) >= 2:
            artists, albums, songs = m.search(rest[1])
            items = []
            if artists:
                items.append(header(t("artists")))
                items += [item("a:" + a, a) for a in artists]
            if albums:
                items.append(header(t("albums")))
                items += [item(f"alb{i}", album, value=artist or None)
                          for i, ((artist, album), _) in enumerate(albums)]
            if songs:
                items.append(header(t("songs")))
                items += [item(f"s{i}", s["title"], "playing" if s["file"] == playing else "none",
                               value=s["artist"] or None) for i, s in enumerate(songs)]
            return page(path, rest[1], items, live=True, empty=empty("search", t("no_results")))
        if kind == "artists":
            names = m.artists()
            items = [item("a:" + n, n or t("unknown_artist")) for n in names]
            return page(path, t("artists"), items, empty=self._empty_music(), index=True)
        if kind == "artist" and len(rest) == 2:
            artist = rest[1]
            albums = m.albums(artist=artist)
            items = [item("all", t("all_songs"))]
            items += [item(f"al:{i}", album or t("unknown_album"), art=self._album_art(file))
                      for i, ((a, album), file) in enumerate(albums)]
            self._prefetch_art(path, [f for _, f in albums])
            return page(path, artist or t("unknown_artist"), items, rows="art")
        if kind == "albums":
            albums = m.albums()
            items = [item(f"al:{i}", album or t("unknown_album"), subtitle=a or t("unknown_artist"),
                          art=self._album_art(file))
                     for i, ((a, album), file) in enumerate(albums)]
            self._prefetch_art(path, [f for _, f in albums])
            return page(path, t("albums"), items, empty=self._empty_music(), rows="art", index=True)
        if kind == "genres":
            items = [item("g:" + g, g) for g in m.genres()]
            return page(path, t("genres"), items, empty=self._empty_music())
        if kind == "playlists":
            items = [item("p:" + p, p) for p in await m.playlists()]
            return page(path, t("playlists"), items, empty=empty("music", t("empty_list")))
        if kind == "queue":
            songs = await m.queue()
            pos = self.app.state.get("player")["pos"]
            items = [item(f"q{i}", s["title"], "playing" if i + 1 == pos else "none",
                          subtitle=s["artist"] or None) for i, s in enumerate(songs)]
            return page(path, t("up_next"), items, empty=empty("music", t("nothing_playing")),
                        selected=max(pos - 1, 0), rows="tall", live=True)
        songs = await self._songs_for(rest)
        if songs is None:
            return page(path, "", [])
        titles = {"songs": t("songs"), "album": rest[-1] if len(rest) > 2 else "",
                  "artist": rest[1] if len(rest) > 1 else "", "genre": rest[-1], "playlist": rest[-1]}
        return page(path, titles.get(kind) or t("unknown_album"),
                    self._song_items(songs, playing), empty=self._empty_music(),
                    index=kind == "songs", live=True)

    async def _do_music(self, path, rest, key):
        m = self.app.music
        if not rest:
            if key == "search":
                return {"input": {"title": self.t("search"), "placeholder": self.t("search_music_prompt"),
                                  "token": self._token(self._music_search)}}
            return {"push": f"/music/{key}"}
        kind = rest[0]
        if kind == "coverflow":
            (artist, album), _ = m.coverflow_albums()[int(key[3:])]
            return {"push": "/music/album/" + enc(artist, album)}
        if kind == "search" and key.startswith("a:"):
            return {"push": "/music/artist/" + enc(key[2:])}
        if kind == "search" and key.startswith("alb") and len(rest) >= 2:
            (artist, album), _ = m.search(rest[1])[1][int(key[3:])]
            return {"push": "/music/album/" + enc(artist, album)}
        if kind == "artists":
            return {"push": "/music/artist/" + enc(key[2:])}
        if kind == "artist" and len(rest) == 2:
            if key == "all":
                return {"push": path + "/all"}
            (artist, album), _ = m.albums(artist=rest[1])[int(key[3:])]
            return {"push": "/music/album/" + enc(artist, album)}
        if kind == "albums":
            (artist, album), _ = m.albums()[int(key[3:])]
            return {"push": "/music/album/" + enc(artist, album)}
        if kind == "genres":
            return {"push": "/music/genre/" + enc(key[2:])}
        if kind == "playlists":
            return {"push": "/music/playlist/" + enc(key[2:])}
        if kind == "queue":
            await m.play_position(int(key[1:]))
            return {"nowplaying": True}
        songs = await self._songs_for(rest)
        if songs and key.startswith("s"):
            await m.play_songs(songs, int(key[1:]))
            return {"nowplaying": True}

    async def _alt_music(self, path, rest, key):
        songs = await self._songs_for(rest)
        if not songs or not key.startswith("s"):
            return None
        song = songs[int(key[1:])]
        options = [{"key": "next", "title": self.t("play_next")},
                   {"key": "later", "title": self.t("add_to_up_next")}]
        if rest[0] != "album":
            options.append({"key": "album", "title": self.t("go_to_album")})

        async def choose(choice):
            if choice in ("next", "later"):
                await self.app.music.enqueue(song, next_up=choice == "next")
                self.app.toast(self.t("added_up_next"), "music")
                return {"none": True}
            if choice == "album":
                return {"push": "/music/album/" + enc(song["albumartist"], song["album"])}
        return {"sheet": {"title": song["title"], "items": options, "token": self._token(choose)}}

    def _album_art(self, file):
        return self.app.music._art_for(file)

    def _prefetch_art(self, path, files):
        missing = [f for f in files if not self.app.music._art_for(f)]
        if not missing or path in self._art_jobs:
            return
        self._art_jobs.add(path)

        async def job():
            try:
                found = False
                for f in missing[:60]:
                    if await self.app.music.art_path(f):
                        found = True
                if found:
                    self.app.page_changed(path)
            finally:
                self._art_jobs.discard(path)
        spawn(job())

    # now playing -------------------------------------------------------------

    async def _page_nowplaying(self, path, rest):
        if rest == ["lyrics"]:
            player = self.app.state.get("player")
            found = await self.app.music.lyrics()
            lines, times = found if found else ([], None)
            return page(path, player.get("title") or self.t("now_playing"),
                        [item(f"l{i}", line, "none") for i, line in enumerate(lines)],
                        style="lyrics", times=times, live=True,
                        empty=empty("music", self.t("no_lyrics")))
        return {"path": path, "title": self.t("now_playing"), "style": "nowplaying", "items": []}

    async def now_playing_options(self):
        t = self.t
        player = self.app.state.get("player")
        left = self.app.power.sleep_timer_left()
        timer = {"key": "timer", "title": t("sleep_timer"), "value": t("minutes", n=left) if left else None}
        if player["kind"] == "radio":
            station = self.app.music.station
            if not station:
                return {"none": True}
            fav = self.app.radio.is_favorite(station["uuid"])
            options = [{"key": "fav", "title": t("remove_favorite") if fav else t("add_favorite")}, timer]
            on_air = self.app.songs.on_air
            if on_air:
                kept = (self.app.songs.find(on_air[0]) or {}).get("saved")
                options.insert(0, {"key": "song", "title": t("unsave_song") if kept else t("save_song")})
        else:
            repeat = (t("repeat_one") if player["single"] else t("on")) if player["repeat"] else None
            options = [
                {"key": "shuffle", "title": t("shuffle"), "value": t("on") if player["shuffle"] else None},
                {"key": "repeat", "title": t("repeat"), "value": repeat},
                {"key": "queue", "title": t("up_next")},
            ]
            if player["album"]:
                options.append({"key": "album", "title": t("go_to_album")})
            options.append(timer)

        async def choose(choice):
            m = self.app.music
            if choice == "fav" and m.station:
                added = self.app.radio.toggle_favorite(m.station)
                self.app.toast(t("added_favorite") if added else t("removed_favorite"), "star")
            elif choice == "song":
                self.app.songs.set_saved(on_air[0], not kept, on_air[1])
                self.app.toast(t("song_unsaved") if kept else t("song_saved"), "star")
            elif choice == "shuffle":
                await m.set_shuffle(not player["shuffle"])
            elif choice == "repeat":
                await m.cycle_repeat()
            elif choice == "queue":
                return {"push": "/music/queue"}
            elif choice == "timer":
                return self._sleep_timer_sheet()
            elif choice == "album":
                song = next((s for s in m.songs if s["file"] == player.get("file")), None)
                if song:
                    return {"push": "/music/album/" + enc(song["albumartist"], song["album"])}
            return {"none": True}
        return {"sheet": {"title": player["title"], "items": options, "token": self._token(choose)}}

    def _sleep_timer_sheet(self):
        t = self.t
        choices = [(15, t("minutes", n=15)), (30, t("minutes", n=30)), (45, t("minutes", n=45)),
                   (60, t("one_hour")), (0, t("off"))]

        def choose(choice):
            minutes = int(choice)
            self.app.power.set_sleep_timer(minutes)
            if minutes:
                self.app.toast(t("sleep_timer_set", n=minutes), "moon")
            return {"none": True}
        return {"sheet": {"title": t("sleep_timer"), "token": self._token(choose),
                          "items": [{"key": str(m), "title": label} for m, label in choices]}}

    # radio -------------------------------------------------------------------

    def _station_items(self, path, stations):
        self._stations[path] = stations
        items = []
        for i, s in enumerate(stations):
            items.append(item(f"st{i}", s["name"], "star" if self.app.radio.is_favorite(s["uuid"]) else "none",
                              subtitle=", ".join(s["tags"][:2]) or None, art=self._icon_path(s)))
        self._prefetch_icons(path, stations)
        return items

    def _icon_path(self, station):
        return self.app.radio.cached_icon(station)

    def _heard_items(self):
        songs = self.app.songs
        items = []
        for title, entries in ((self.t("saved_songs"), songs.saved()), (self.t("heard_songs"), songs.recent())):
            if entries:
                items.append(header(title))
                items += [item("g:" + e["title"], e["title"], "star" if e["saved"] else "none",
                               subtitle=" · ".join(filter(None, (e.get("station"), _when(e.get("time"))))))
                          for e in entries]
        return items

    def _prefetch_icons(self, path, stations):
        missing = [s for s in stations[:40] if s.get("favicon") and not self._icon_path(s)]
        if not missing or ("icons", path) in self._art_jobs:
            return
        self._art_jobs.add(("icons", path))

        async def job():
            try:
                results = await asyncio.gather(*(self.app.radio.icon(s) for s in missing))
                if any(results):
                    self.app.page_changed(path)
            finally:
                self._art_jobs.discard(("icons", path))
        spawn(job())

    async def _page_radio(self, path, rest):
        t = self.t
        r = self.app.radio
        if not rest:
            items = [item("favorites", t("favorites")), item("songs", t("song_log")),
                     item("top", t("top_stations")), item("country/KR", t("korea")),
                     item("countries", t("by_country")), item("search", t("search"))]
            return page(path, t("radio"), items)
        kind = rest[0]
        net_error = empty("wifi", t("network_error"), t("network_error_text"))
        try:
            if kind == "favorites":
                items = self._station_items(path, list(r.favorites))
                return page(path, t("favorites"), items, rows="art", live=True,
                            empty=empty("star", t("no_favorites_title"), t("no_favorites_text")))
            if kind == "songs":
                return page(path, t("song_log"), self._heard_items(), rows="tall", live=True,
                            empty=empty("music", t("no_songs_title"), t("no_songs_text")))
            if kind == "top":
                return page(path, t("top_stations"), self._station_items(path, await r.top()),
                            rows="art", empty=net_error)
            if kind == "country" and len(rest) >= 2:
                title = rest[2] if len(rest) > 2 else (t("korea") if rest[1] == "KR" else rest[1])
                return page(path, title, self._station_items(path, await r.by_country(rest[1])),
                            rows="art", empty=empty("search", t("no_results")))
            if kind == "countries":
                items = [item(f"c:{code}:{name}", name, value=str(n)) for code, name, n in await r.countries()]
                return page(path, t("by_country"), items, empty=net_error)
            if kind == "search" and len(rest) >= 2:
                return page(path, rest[1], self._station_items(path, await r.search(rest[1])),
                            rows="art", empty=empty("search", t("no_results")))
        except RadioError:
            return page(path, t("radio"), [], empty=net_error)
        return page(path, t("radio"), [])

    async def _do_radio(self, path, rest, key):
        if not rest:
            if key == "search":
                return {"input": {"title": self.t("search"), "placeholder": self.t("search_prompt"),
                                  "token": self._token(self._radio_search)}}
            return {"push": "/radio/" + key}
        if rest[0] == "countries":
            _, code, name = key.split(":", 2)
            return {"push": "/radio/country/" + enc(code, name)}
        if rest[0] == "songs":
            entry = self.app.songs.find(key[2:])
            if entry:
                self.app.songs.set_saved(entry["title"], not entry["saved"])
                self.app.toast(self.t("song_saved" if entry["saved"] else "song_unsaved"), "star")
            return {"reload": True}
        stations = self._stations.get(path) or []
        if key.startswith("st") and int(key[2:]) < len(stations):
            station = dict(stations[int(key[2:])])
            station["art"] = self._icon_path(station)
            await self.app.music.play_stream(station)
            self.app.radio.report_click(station)
            return {"nowplaying": True}

    def _delete_song(self, title):
        self.app.songs.remove(title)
        return {"reload": True}

    def _music_search(self, text):
        text = (text or "").strip()
        return {"push": "/music/search/" + enc(text)} if text else {"none": True}

    def _radio_search(self, text):
        text = (text or "").strip()
        return {"push": "/radio/search/" + enc(text)} if text else {"none": True}

    async def _alt_radio(self, path, rest, key):
        if rest and rest[0] == "songs":
            entry = self.app.songs.find(key[2:])
            if not entry:
                return None
            title = entry["title"]
            return {"confirm": {"title": self.t("delete_song_q", name=title), "ok": self.t("delete"),
                                "destructive": True, "token": self._token(lambda: self._delete_song(title))}}
        stations = self._stations.get(path) or []
        if key.startswith("st") and int(key[2:]) < len(stations):
            added = self.app.radio.toggle_favorite(stations[int(key[2:])])
            self.app.toast(self.t("added_favorite") if added else self.t("removed_favorite"), "star")
            return {"reload": True}

    # videos, books, games ----------------------------------------------------

    def _media_dir(self, kind, rest):
        root = os.path.normpath(self.app.paths[kind])
        path = os.path.normpath(os.path.join(root, *rest))
        return path if path == root or path.startswith(root + os.sep) else root

    async def _page_videos(self, path, rest):
        dirs, files = folder_listing(self._media_dir("videos", rest), VIDEO_EXTS)
        items = [item("d:" + d, d) for d in dirs]
        items += [item("f:" + f, _strip_ext(f, VIDEO_EXTS), "none") for f in files]
        return page(path, rest[-1] if rest else self.t("videos"), items,
                    empty=empty("video", self.t("no_videos_title"), self.t("no_videos_text")))

    async def _do_videos(self, path, rest, key):
        if key.startswith("d:"):
            return {"push": path.rstrip("/") + "/" + enc(key[2:])}
        if key.startswith("f:"):
            file = os.path.join(self._media_dir("videos", rest), key[2:])
            await self.app.launch(self.app.catalog["video"], _strip_ext(key[2:], VIDEO_EXTS), [file])

    async def _page_books(self, path, rest):
        t = self.t
        if not self.app.catalog["koreader"].available():
            return page(path, t("books"), [], empty=empty("book", t("no_reader_title"), t("no_reader_text")))
        folder = self._media_dir("books", rest)
        dirs, files = folder_listing(folder, BOOK_EXTS)
        items = [] if rest else [item("news", t("news"))]
        items += [item("d:" + d, d) for d in dirs]
        items += [item("f:" + f, _strip_ext(f, BOOK_EXTS),
                       "spinner" if os.path.join(folder, f) in self._busy_books else "none") for f in files]
        return page(path, rest[-1] if rest else t("books"), items, live=True,
                    empty=empty("book", t("no_books_title"), t("no_books_text")))

    async def _do_books(self, path, rest, key):
        if key == "news":
            return {"push": "/news"}
        if key.startswith("d:"):
            return {"push": path.rstrip("/") + "/" + enc(key[2:])}
        if not key.startswith("f:"):
            return None
        await self.open_book(os.path.join(self._media_dir("books", rest), key[2:]))

    async def open_book(self, file, prepared=False):
        apps = self.app.apps
        lang = self.app.settings["wordwise"].get(file)
        if lang and file.lower().endswith(".epub"):
            target = self._wordwise_copy(file, lang)
            if not prepared and not self._wordwise_fresh(file, lang, target):
                spawn(self._prepare_wordwise(file, lang, target))
                return
            file_to_open = target
        else:
            file_to_open = file
        if apps.opened_file("koreader") == file_to_open:
            await self.app.resume_app("koreader")
            return
        # The open book saves its page as it closes; then this one opens at the page last
        # read in any of its versions, with Word Wise or without.
        await apps.close("koreader")
        if file.lower().endswith(".epub"):
            follow_position([file] + [self._wordwise_copy(file, lang) for lang in ("ko", "en")], file_to_open)
        await self.app.launch(self.app.catalog["koreader"], _strip_ext(os.path.basename(file), BOOK_EXTS),
                              [file_to_open])

    # word wise ---------------------------------------------------------------

    def _glossary_path(self, lang):
        return os.path.join(self.app.paths["data"], "wordwise", f"en-{lang}.tsv")

    def _glossary(self, lang):
        if lang not in self._glossaries:
            self._glossaries[lang] = Glossary(self._glossary_path(lang))
        return self._glossaries[lang]

    def _wordwise_copy(self, file, lang):
        """One place per book and language, so the page read there outlives a rebuild."""
        key = hashlib.sha1(f"{file}|{lang}".encode()).hexdigest()[:16]
        return os.path.join(self.app.paths["cache"], "wordwise", key, os.path.basename(file))

    def _wordwise_stamp(self, file, lang):
        """The book and glossary a copy was made from; compared, never ordered, as clocks drift."""
        try:
            return f"{os.path.getmtime(file)}|{os.path.getmtime(self._glossary_path(lang))}"
        except OSError:
            return None

    def _wordwise_fresh(self, file, lang, target):
        try:
            with open(target + ".stamp") as f:
                return os.path.exists(target) and f.read() == self._wordwise_stamp(file, lang)
        except OSError:
            return False

    async def _prepare_wordwise(self, file, lang, target):
        """Hints go into a copy once; the reader opens the copy from then on."""
        self._busy_books.add(file)
        self.app.page_changed("/books/*")
        try:
            loop = asyncio.get_running_loop()
            glossary = await loop.run_in_executor(None, self._glossary, lang)
            stamp = self._wordwise_stamp(file, lang)
            await loop.run_in_executor(None, annotate_epub, file, target, glossary)
            settle_position(target)
            with open(target + ".stamp", "w") as f:
                f.write(stamp or "")
        except Exception as e:
            log.warning("word wise for %s: %s", file, e)
            self.app.toast(self.t("wordwise_failed"))
            return
        finally:
            self._busy_books.discard(file)
            self.app.page_changed("/books/*")
        await self.open_book(file, prepared=True)

    async def _alt_books(self, path, rest, key):
        if not key.startswith("f:") or not key.lower().endswith(".epub"):
            return None
        file = os.path.join(self._media_dir("books", rest), key[2:])
        current = self.app.settings["wordwise"].get(file)
        t = self.t
        options = [{"key": lang, "title": t(f"wordwise_{lang}"), "value": t("on") if current == lang else None}
                   for lang in ("ko", "en")]
        if current:
            options.append({"key": "off", "title": t("wordwise_off")})

        def choose(choice):
            chosen = dict(self.app.settings["wordwise"])
            if choice == "off":
                chosen.pop(file, None)
            else:
                chosen[file] = choice
            self.app.settings.set("wordwise", chosen)
            return {"none": True}
        return {"sheet": {"title": _strip_ext(key[2:], BOOK_EXTS), "items": options, "token": self._token(choose)}}

    # news ----------------------------------------------------------------------

    def _news_dir(self):
        return os.path.join(self.app.paths["books"], ".news")

    def _edition_label(self, name):
        try:
            day = datetime.date.fromisoformat(name[:10])
        except ValueError:
            return name
        return self.t("news_date", m=day.month, d=day.day)

    async def _page_news(self, path, rest):
        t = self.t
        folder = self._news_dir()
        names = news.editions(folder)
        wifi = bool(self.app.state.get("wifi")["ssid"]) or self.app.sim
        today = news.edition_name()
        if wifi and today not in names and self._news_job is None:
            self._news_job = spawn(self._fetch_news())
        fetching = self._news_job is not None
        items = [item("fetch", t("news_fetch"), "spinner" if fetching else "none")]
        items += [item("e:" + n, self._edition_label(n), "none") for n in names]
        return page(path, t("news"), items, live=True)

    async def _do_news(self, path, rest, key):
        if key == "fetch":
            if not self.app.state.get("wifi")["ssid"] and not self.app.sim:
                self.app.toast(self.t("transfer_no_wifi"), "wifi")
            elif self._news_job is None:
                self._news_job = spawn(self._fetch_news())
                self.app.page_changed("/news")
            return None
        if key.startswith("e:"):
            await self.app.launch(self.app.catalog["koreader"], self.t("news"),
                                  [os.path.join(self._news_dir(), key[2:])])

    async def _fetch_news(self):
        t = self.t
        folder = self._news_dir()
        name = news.edition_name()
        title = t("news_title", date=self._edition_label(name))
        feeds = news.load_feeds(os.path.join(self.app.paths["config"], "news-feeds.txt"))
        try:
            count = await asyncio.get_running_loop().run_in_executor(
                None, lambda: news.build_edition(os.path.join(folder, name), title, feeds, lang=t.lang))
            if not count:
                self.app.toast(t("news_failed"))
            news.prune(folder)
        except Exception as e:
            log.warning("news: %s", e)
            self.app.toast(t("news_failed"))
        finally:
            self._news_job = None
            self.app.page_changed("/news")

    async def _page_games(self, path, rest):
        items = [item("port:" + p, title, "none") for title, p in installed_ports(self.app.paths["ports"])]
        items += [item(key, spec.title_key, "none") for key, spec in self.app.catalog.items()
                  if key in ("portmaster", "retroarch") and spec.available()]
        return page(path, self.t("games"), items,
                    empty=empty("game", self.t("no_games_title"), self.t("no_games_text")))

    async def _do_games(self, path, rest, key):
        if key.startswith("port:"):
            script = key[5:]
            title = os.path.splitext(os.path.basename(script))[0]
            await self.app.launch(port_spec(title, script), title)
            return None
        spec = self.app.catalog.get(key)
        if spec:
            await self.app.launch(spec, spec.title_key)

    # settings ----------------------------------------------------------------

    async def _page_settings(self, path, rest):
        handler = getattr(self, "_settings_" + "_".join(rest), None) if rest else None
        if rest and handler is None:
            return page(path, "", [])
        return await handler(path) if handler else await self._settings_root(path)

    async def _settings_root(self, path):
        t = self.t
        s = self.app.state
        wifi = s.get("wifi")
        bt = s.get("bluetooth")
        lang = {"ko": "한국어", "en": "English"}[self.app.settings["lang"]]
        items = [
            item("wifi", t("wifi"), value=wifi["ssid"] if wifi["ssid"] else (t("on") if wifi["enabled"] else t("off"))),
            item("bluetooth", t("bluetooth"), value=bt["audio"] or (t("on") if bt["enabled"] else t("off"))),
            item("display", t("display")),
            item("limit", t("volume_limit"), value=self._limit_label()),
            item("language", t("language"), value=lang),
            item("transfer", t("add_files")),
            item("about", t("about")),
        ]
        return page(path, t("settings"), items, live=True)

    async def _settings_transfer(self, path):
        t = self.t
        tr = self.app.transfer
        if not self.app.state.get("wifi")["ssid"] and not self.app.sim:
            tr.stop()
            return page(path, t("add_files"), [], live=True,
                        empty=empty("wifi", t("transfer_no_wifi"), t("transfer_no_wifi_text")))
        url = tr.url() if await tr.start() else None
        if not url:
            return page(path, t("add_files"), [], empty=empty("wifi", t("transfer_failed")))
        text = t("transfer_code", code=tr.code)
        if tr.received:
            text += "\n" + t("transfer_received", n=tr.received)
        return page(path, t("add_files"), [], live=True, empty=empty("upload", url, text))

    async def _settings_wifi(self, path):
        t = self.t
        w = self.app.wifi
        items = [item("toggle", t("wifi"), "switch", on=w.enabled)]
        if w.enabled:
            items.append(header(""))
            for n in w.networks():
                accessory = "spinner" if w.busy == n["ssid"] else "check" if n["active"] else "none"
                items.append(item("n:" + n["ssid"], n["ssid"], accessory, lock=n["secure"],
                                  signal=min(3, n["signal"] // 25)))
            items.append(item("saved", t("saved_networks")))
            w.scan_if_stale()
        return page(path, t("wifi"), items, live=True)

    async def _settings_wifi_saved(self, path):
        items = [item("u:" + s["uuid"], s["name"], "none") for s in self.app.wifi.saved()]
        return page(path, self.t("saved_networks"), items,
                    empty=empty("wifi", self.t("no_saved_networks")), live=True)

    async def _settings_bluetooth(self, path):
        t = self.t
        b = self.app.bluetooth
        items = [item("toggle", t("bluetooth"), "switch", on=b.enabled)]
        if b.enabled:
            mine = b.my_devices()
            if mine:
                items.append(header(t("my_devices")))
                for d in mine:
                    busy = d.path in b.busy
                    items.append(item("d:" + d.path, d.name, "spinner" if busy else "none",
                                      value=t("connected") if d.ready and not busy else None,
                                      icon=d.kind))
            b.begin_discovery()
            others = b.other_devices()
            if others or b.discovering:
                items.append({"header": True, "title": t("other_devices"), "spinner": b.discovering})
            for d in others:
                busy = d.path in b.busy
                items.append(item("d:" + d.path, d.name, "spinner" if busy else "none", icon=d.kind))
        return page(path, t("bluetooth"), items, live=True)

    def _limit_label(self):
        limit = self.app.settings["volume_limit"]
        return None if limit >= 100 else f"{limit}%"

    async def _settings_limit(self, path):
        current = self.app.settings["volume_limit"]
        items = [item(f"l{v}", self.t("no_limit") if v == 100 else f"{v}%",
                      "check" if v == current else "none") for v in LIMITS]
        return page(path, self.t("volume_limit"), items)

    async def _settings_display(self, path):
        t = self.t
        st = self.app.settings
        items = [item("brightness", t("brightness"), value=f"{st['brightness']}%"),
                 item("timeout", t("screen_timeout"), value=self._duration_label(st["screen_timeout"])),
                 item("lid", t("when_lid_closes"),
                      value=t("lid_keep") if st["lid_keep_playing"] else t("lid_sleep"))]
        return page(path, t("display"), items)

    async def _settings_display_brightness(self, path):
        return {"path": path, "title": self.t("brightness"), "style": "slider", "items": [],
                "value": self.app.settings["brightness"], "min": 5, "max": 100, "step": 5}

    async def _settings_display_timeout(self, path):
        current = self.app.settings["screen_timeout"]
        items = [item(f"t{v}", self._duration_label(v), "check" if v == current else "none")
                 for v in TIMEOUTS]
        return page(path, self.t("screen_timeout"), items)

    async def _settings_display_lid(self, path):
        keep = self.app.settings["lid_keep_playing"]
        items = [item("keep", self.t("lid_keep"), "check" if keep else "none"),
                 item("sleep", self.t("lid_sleep"), "none" if keep else "check")]
        return page(path, self.t("when_lid_closes"), items)

    async def _settings_language(self, path):
        lang = self.app.settings["lang"]
        items = [item("ko", "한국어", "check" if lang == "ko" else "none"),
                 item("en", "English", "check" if lang == "en" else "none")]
        return page(path, self.t("language"), items)

    async def _settings_about(self, path):
        t = self.t
        info = self.app.system.about(self.app.paths["music"])
        m = self.app.music
        rows = [
            ("songs", str(len(m.songs))),
            ("albums", str(len(m.albums()))),
            ("capacity", human_size(info["capacity"])),
            ("available", human_size(info["available"])),
            ("version", info["version"]),
            ("model", info["model"]),
        ]
        if self.app.ui_ready_at:
            rows.append(("boot_time", t("seconds_short", n=f"{self.app.ui_ready_at:.1f}")))
        items = [item(k, t(k), "none", value=v) for k, v in rows]
        return page(path, t("about"), items, style="about")

    async def _do_settings(self, path, rest, key):
        t = self.t
        st = self.app.settings
        sub = "/".join(rest)
        if not rest:
            return {"push": f"/settings/{key}"}
        if sub == "wifi":
            if key == "toggle":
                await self.app.wifi.set_enabled(not self.app.wifi.enabled)
                return {"reload": True}
            if key == "saved":
                return {"push": "/settings/wifi/saved"}
            ssid = key[2:]
            net = next((n for n in self.app.wifi.networks() if n["ssid"] == ssid), None)
            if net and net["active"]:
                return {"none": True}
            if net and net["secure"] and not self.app.wifi.saved_for(ssid):
                return {"input": {"title": t("password_for", ssid=ssid), "secret": True,
                                  "token": self._token(lambda pw: self._join(ssid, pw))}}
            spawn(self.app.wifi.join(ssid))
            return {"reload": True}
        if sub == "wifi/saved":
            saved = next((s for s in self.app.wifi.saved() if s["uuid"] == key[2:]), None)
            if saved:
                spawn(self.app.wifi.join(saved["name"]))
            return {"back": True}
        if sub == "bluetooth":
            if key == "toggle":
                await self.app.bluetooth.set_enabled(not self.app.bluetooth.enabled)
                return {"reload": True}
            spawn(self.app.bluetooth.activate(key[2:]))
            return {"reload": True}
        if sub == "limit":
            st.set("volume_limit", int(key[1:]))
            await self.app.audio.clamp_to_limit()
            return {"back": True}
        if sub == "display":
            return {"push": f"/settings/display/{key}"}
        if sub == "display/lid":
            st.set("lid_keep_playing", key == "keep")
            await self.app.power.playback_changed()
            return {"back": True}
        if sub == "display/timeout":
            st.set("screen_timeout", int(key[1:]))
            self.app.power.poke()
            return {"back": True}
        if sub == "language":
            st.set("lang", key)
            self.app.set_language(key)
            return {"relabel": True, "back": True}
        return {"none": True}

    async def _join(self, ssid, password):
        spawn(self.app.wifi.join(ssid, password))
        return {"none": True}

    async def _alt_settings(self, path, rest, key):
        t = self.t
        sub = "/".join(rest)
        if sub == "wifi/saved" and key.startswith("u:"):
            saved = next((s for s in self.app.wifi.saved() if s["uuid"] == key[2:]), None)
            if saved:
                return {"confirm": {"title": t("forget_network_q", name=saved["name"]), "ok": t("forget"),
                                    "destructive": True,
                                    "token": self._token(lambda: self._forget_wifi(saved["uuid"]))}}
        if sub == "bluetooth" and key.startswith("d:"):
            dev = self.app.bluetooth.find(key[2:])
            if dev and dev in self.app.bluetooth.my_devices():
                options = [{"key": "toggle", "title": t("disconnect") if dev.ready else t("connect")},
                           {"key": "forget", "title": t("forget"), "destructive": True}]
                return {"sheet": {"title": dev.name, "items": options,
                                  "token": self._token(lambda c: self._device_choice(dev.path, c))}}

    async def _forget_wifi(self, uuid):
        await self.app.wifi.forget(uuid)
        return {"reload": True}

    async def _device_choice(self, dev_path, choice):
        if choice == "toggle":
            spawn(self.app.bluetooth.activate(dev_path))
        elif choice == "forget":
            await self.app.bluetooth.forget(dev_path)
        return {"reload": True}

    # control center --------------------------------------------------------

    async def _page_control(self, path, rest):
        t = self.t
        b = self.app.bluetooth
        items = []
        if b.enabled:
            for d in b.recent_audio():
                busy = d.path in b.busy
                items.append(item("d:" + d.path, d.name, "spinner" if busy else "none", icon="audio",
                                  value=t("connected") if d.ready and not busy else None))
        wifi = self.app.state.get("wifi")
        items += [
            item("bluetooth", t("bluetooth"), value=t("on") if b.enabled else t("off")),
            item("wifi", t("wifi"), value=wifi["ssid"] or (t("on") if wifi["enabled"] else t("off"))),
            item("brightness", t("brightness"), value=f"{self.app.settings['brightness']}%"),
            item("sleep", t("sleep_now"), "none"),
        ]
        return page(path, "", items, presentation="sheet", live=True)

    async def _do_control(self, path, rest, key):
        if key.startswith("d:"):
            spawn(self.app.bluetooth.activate(key[2:]))
            return {"reload": True}
        if key == "sleep":
            spawn(self.app.power.sleep())
            return {"dismiss": True}
        target = {"bluetooth": "/settings/bluetooth", "wifi": "/settings/wifi",
                  "brightness": "/settings/display/brightness"}[key]
        return {"dismiss": True, "push": target}
