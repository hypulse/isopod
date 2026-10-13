import asyncio
import logging
import os
import secrets
import zlib

from .countries import NAMES as COUNTRIES, country_name
from .radio import DECADES, GENRES, RadioError
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
        if e.name.startswith("."):
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
        self._art_jobs = set()
        self._lineup = (None, [], 0)

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
        result = fn(value)
        if asyncio.iscoroutine(result):
            result = await result
        return result or {"none": True}

    async def slider(self, path, value):
        if path == "/settings/display/brightness":
            self.app.settings.set("brightness", int(value))
            self.app.power.apply_brightness()

    def leave(self, path):
        if path == "/settings/bluetooth":
            self.app.bluetooth.end_discovery()

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
            item("settings", t("settings"), preview="settings"),
            item("shuffle", t("shuffle_songs"), "none", preview="art"),
        ]
        player = self.app.state.get("player")
        if player["count"] or player["state"] != "stop":
            items.append(item("nowplaying", t("now_playing"), preview="now"))
        apps = self.app.state.get("apps")
        if apps:
            items.append(header(t("open_apps")))
            items += [item(f"app:{a['key']}", a["title"], "none",
                           preview="book" if a["key"] == "koreader" else "video") for a in apps]
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
        if key == "home":
            # Home pressed on the home screen: back to the app it was last pressed in.
            if self.app.apps.left in self.app.apps.running:
                await self.app.resume_app(self.app.apps.left)
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

    def _song_items(self, songs, playing_file=None, stars=True):
        favorites = set(self.app.music.favorites) if stars else ()
        return [item(f"s{i}", s["title"], "playing" if s["file"] == playing_file else "none",
                     star=s["file"] in favorites or None) for i, s in enumerate(songs)]

    def _favorite_row(self, on):
        return {"key": "favorite", "title": self.t("favorite"), "value": self.t("on") if on else None}

    def _favorite_option(self, file):
        return self._favorite_row(file in self.app.music.favorites)

    async def _toggle_favorite(self, file):
        await self.app.music.set_favorite(file, file not in self.app.music.favorites)

    async def _songs_for(self, rest):
        m = self.app.music
        kind = rest[0] if rest else ""
        if kind == "songs":
            return m.all_songs()
        if kind == "favorites":
            return m.favorite_songs()
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
                favorites = set(m.favorites)
                items += [item(f"s{i}", s["title"], "playing" if s["file"] == playing else "none",
                               value=s["artist"] or None, star=s["file"] in favorites or None)
                          for i, s in enumerate(songs)]
            return page(path, t("search_for", q=rest[1]), items, live=True,
                        empty=empty("search", t("no_results")))
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
            items = [item("favorites", t("favorites"))]
            items += [item("p:" + name, title) for title, name in await m.playlists()]
            return page(path, t("playlists"), items)
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
                  "artist": rest[1] if len(rest) > 1 else "", "genre": rest[-1],
                  "playlist": m.playlist_title(rest[-1]),
                  "favorites": t("favorites")}
        nothing = (empty("star", t("no_favorites_title"), t("no_favorites_text"))
                   if kind == "favorites" else self._empty_music())
        return page(path, titles.get(kind) or t("unknown_album"),
                    self._song_items(songs, playing, stars=kind != "favorites"), empty=nothing,
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
            return {"push": "/music/favorites" if key == "favorites" else "/music/playlist/" + enc(key[2:])}
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
                   {"key": "later", "title": self.t("add_to_up_next")},
                   self._favorite_option(song["file"])]
        if rest[0] != "album" and song["album"]:
            options.append({"key": "album", "title": self.t("go_to_album")})

        async def choose(choice):
            if choice in ("next", "later"):
                await self.app.music.enqueue(song, next_up=choice == "next")
                self.app.toast(self.t("added_up_next"), "music")
                return {"none": True}
            if choice == "favorite":
                await self._toggle_favorite(song["file"])
            if choice == "album":
                return {"push": "/music/album/" + enc(song["albumartist"], song["album"])}
        return {"sheet": {"title": song["title"], "items": options, "token": self._token(choose)}}

    def _music_search(self, text):
        text = (text or "").strip()
        return {"push": "/music/search/" + enc(text)} if text else {"none": True}

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
        # Beside the cover: a line about the song, and its words where they follow the song.
        found = await self.app.music.lyrics()
        lines, times = found if found and found[1] else ([], None)
        radio = self.app.state.get("player")["kind"] == "radio"
        return {"path": path, "title": self.t("now_playing"), "style": "nowplaying", "live": True,
                "items": [item(f"l{i}", line, "none") for i, line in enumerate(lines)], "times": times,
                "note": self._on_air() if radio else self.app.music.details()}

    def _on_air(self):
        """A line about the station that is on: where it is from and what it sends."""
        s = self.app.music.station or {}
        sends = " ".join(filter(None, (s.get("codec"), f"{s['bitrate']}k" if s.get("bitrate") else None)))
        return " · ".join(filter(None, (self._country(s.get("country")), sends)))

    async def now_playing_options(self):
        t = self.t
        player = self.app.state.get("player")
        left = self.app.power.sleep_timer_left()
        timer = {"key": "timer", "title": t("sleep_timer"), "value": t("minutes", n=left) if left else None}
        repeat = (t("repeat_one") if player["single"] else t("on")) if player["repeat"] else None
        file = player.get("file")
        station = self.app.music.station if player["kind"] == "radio" else None
        if player["kind"] == "radio":
            options = [self._favorite_row(self.app.radio.is_favorite(station["uuid"]))] if station else []
        else:
            options = [self._favorite_option(file)] if file else []
            options += [
                {"key": "shuffle", "title": t("shuffle"), "value": t("on") if player["shuffle"] else None},
                {"key": "repeat", "title": t("repeat"), "value": repeat},
                {"key": "queue", "title": t("up_next")},
            ]
            if player["album"]:
                options.append({"key": "album", "title": t("go_to_album")})
        options.append(timer)

        async def choose(choice):
            m = self.app.music
            if choice == "favorite" and station:
                self._toggle_station(station)
            elif choice == "favorite":
                await self._toggle_favorite(file)
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

    def unreachable(self):
        """Why the radio is out of reach: no Wi-Fi to reach it over, or no answer over it."""
        return self.t("radio_unreachable" if self.app.state.get("wifi")["ssid"] else "wifi_needed")

    def _country(self, code):
        return country_name(code, self.t.lang) if code else None

    def _station_items(self, path, stations, country=True, genres=1):
        """A station's row says where it is from and what it plays, less what its list says."""
        radio = self.app.radio
        on = self.app.music.station if self.app.state.get("player")["kind"] == "radio" else None
        items = []
        for s in stations:
            about = [self._country(s["country"])] if country and s["country"] else []
            about += [GENRES[g][0] for g in s.get("genres", [])[:genres] if g in GENRES]
            mark = "playing" if on and s["uuid"] == on["uuid"] else "none"
            items.append(item("st:" + s["uuid"], s["name"], mark, subtitle=" · ".join(about) or None,
                              art=radio.logo(s), star=radio.is_favorite(s["uuid"]) or None))
        radio.want_logos(stations, lambda station: self._logo_arrived(station, path))
        return items

    def _logo_arrived(self, station, path=None):
        if path:
            self.app.page_changed(path)
        self.app.music.station_logo(station, self.app.radio.logo(station))

    async def _stations_for(self, rest):
        r = self.app.radio
        kind = rest[0] if rest else ""
        if kind == "favorites":
            r.check_favorites()
            return r.favorites
        if kind == "top":
            return await r.top()
        if kind == "genre" and len(rest) >= 2:
            return await r.genre(rest[1])
        if kind == "country" and len(rest) >= 2:
            return await r.country(rest[1])
        if kind == "search" and len(rest) >= 2:
            return await r.search(rest[1])
        return None

    async def _page_radio(self, path, rest):
        t = self.t
        r = self.app.radio
        if not rest:
            items = [item("favorites", t("favorites")), item("top", t("popular")), item("genres", t("genres")),
                     item("countries", t("countries")), item("search", t("search"))]
            return page(path, t("radio"), items)
        kind = rest[0]
        name = rest[1] if len(rest) > 1 else ""
        if kind == "genres":
            names = sorted((g for g in GENRES if g not in DECADES), key=lambda g: sort_key(GENRES[g][0]))
            return page(path, t("genres"), [item("g:" + g, GENRES[g][0]) for g in (*names, *DECADES)], index=True)
        title = {"favorites": t("favorites"), "top": t("popular"), "countries": t("countries"),
                 "genre": GENRES[name][0] if name in GENRES else name, "country": self._country(name),
                 "search": t("search_for", q=name)}.get(kind) or ""
        try:
            if kind == "countries":
                names = sorted(((self._country(c), c) for c in await r.countries() if c in COUNTRIES),
                               key=lambda pair: sort_key(pair[0]))
                return page(path, title, [item("c:" + c, n) for n, c in names], index=True)
            stations = await self._stations_for(rest)
        except RadioError:
            return page(path, title, [], empty=empty("wifi", self.unreachable()))
        if stations is None:
            return page(path, "", [])
        nothing = {"favorites": empty("star", t("no_favorites_title"), t("no_favorites_text")),
                   "search": empty("search", t("no_results"))}.get(kind)
        about = {"genre": dict(genres=0), "country": dict(country=False, genres=2)}.get(kind, {})
        return page(path, title, self._station_items(path, stations, **about), rows="art", empty=nothing)

    async def _do_radio(self, path, rest, key):
        if not rest:
            if key == "search":
                return {"input": {"title": self.t("search"), "placeholder": self.t("search_radio_prompt"),
                                  "token": self._token(self._radio_search)}}
            return {"push": "/radio/" + key}
        if rest[0] == "genres":
            return {"push": "/radio/genre/" + enc(key[2:])}
        if rest[0] == "countries":
            return {"push": "/radio/country/" + enc(key[2:])}
        stations, index = await self._station(rest, key)
        if index is not None:
            await self._tune(path, stations, index)
            return {"nowplaying": True}

    async def _station(self, rest, key):
        """The list a station's row is in and where in it, (list, None) if it is not there."""
        try:
            stations = await self._stations_for(rest) or []
        except RadioError:
            stations = []
        return stations, next((i for i, s in enumerate(stations) if "st:" + s["uuid"] == key), None)

    async def _tune(self, path, stations, index):
        radio = self.app.radio
        station = dict(stations[index], art=radio.logo(stations[index]))
        self._lineup = (path, stations, index)
        await self.app.music.play_stream(station)
        radio.want_logos([station], self._logo_arrived)
        self.app.page_changed(path)

    async def step_station(self, delta):
        """The station after or before the one that is on, in the list it was picked from."""
        path, stations, index = self._lineup
        if len(stations) > 1:
            await self._tune(path, stations, (index + delta) % len(stations))

    def _radio_search(self, text):
        text = (text or "").strip()
        return {"push": "/radio/search/" + enc(text)} if text else {"none": True}

    def _toggle_station(self, station):
        radio = self.app.radio
        radio.toggle_favorite(station)
        if (self.app.music.station or {}).get("uuid") == station["uuid"]:
            self.app.state.update("player", favorite=radio.is_favorite(station["uuid"]))
        self.app.page_changed("/radio/*")

    async def _alt_radio(self, path, rest, key):
        stations, index = await self._station(rest, key)
        if index is None:
            return None
        station = stations[index]

        def choose(choice):
            if choice == "favorite":
                self._toggle_station(station)
        return {"sheet": {"title": station["name"], "token": self._token(choose),
                          "items": [self._favorite_row(self.app.radio.is_favorite(station["uuid"]))]}}

    # videos, books ---------------------------------------------------------

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
        dirs, files = folder_listing(self._media_dir("books", rest), BOOK_EXTS)
        # A library brought from another KOReader has a folder of notes beside each book.
        items = [item("d:" + d, d) for d in dirs if not d.endswith(".sdr")]
        items += [item("f:" + f, _strip_ext(f, BOOK_EXTS), "none") for f in files]
        return page(path, rest[-1] if rest else self.t("books"), items,
                    empty=empty("book", self.t("no_books_title"), self.t("no_books_text")))

    async def _do_books(self, path, rest, key):
        if key.startswith("d:"):
            return {"push": path.rstrip("/") + "/" + enc(key[2:])}
        if key.startswith("f:"):
            file = os.path.join(self._media_dir("books", rest), key[2:])
            if self.app.apps.opened_file("koreader") == file:
                await self.app.resume_app("koreader")
            else:
                # The book that was open keeps its page as KOReader closes.
                await self.app.launch(self.app.catalog["koreader"], _strip_ext(key[2:], BOOK_EXTS), [file])

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
            item("about", t("about")),
        ]
        return page(path, t("settings"), items, live=True)

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
                joined = any(n["active"] and n["ssid"] == saved["name"] for n in self.app.wifi.networks())
                options = [] if joined else [{"key": "join", "title": t("connect")}]
                options.append({"key": "forget", "title": t("forget"), "destructive": True})
                return {"sheet": {"title": saved["name"], "items": options,
                                  "token": self._token(lambda c: self._network_choice(saved, c))}}
        if sub == "bluetooth" and key.startswith("d:"):
            dev = self.app.bluetooth.find(key[2:])
            if dev and dev in self.app.bluetooth.my_devices():
                options = [{"key": "toggle", "title": t("disconnect") if dev.ready else t("connect")},
                           {"key": "forget", "title": t("forget"), "destructive": True}]
                return {"sheet": {"title": dev.name, "items": options,
                                  "token": self._token(lambda c: self._device_choice(dev.path, c))}}

    async def _network_choice(self, saved, choice):
        if choice == "join":
            spawn(self.app.wifi.join(saved["name"]))
        elif choice == "forget":
            await self.app.wifi.forget(saved["uuid"])
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
        wifi = self.app.state.get("wifi")
        items = [
            item("bluetooth", t("bluetooth"), value=t("on") if self.app.bluetooth.enabled else t("off")),
            item("wifi", t("wifi"), value=wifi["ssid"] or (t("on") if wifi["enabled"] else t("off"))),
            item("brightness", t("brightness"), value=f"{self.app.settings['brightness']}%"),
            item("sleep", t("sleep_now"), "none"),
        ]
        return page(path, "", items, presentation="sheet", live=True)

    async def _do_control(self, path, rest, key):
        if key == "sleep":
            spawn(self.app.power.sleep())
            return {"dismiss": True}
        target = {"bluetooth": "/settings/bluetooth", "wifi": "/settings/wifi",
                  "brightness": "/settings/display/brightness"}[key]
        return {"dismiss": True, "push": target}
