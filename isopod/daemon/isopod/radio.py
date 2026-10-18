import asyncio
import concurrent.futures
import hashlib
import http.client
import json
import logging
import os
import random
import re
import socket
import subprocess
import time
import unicodedata
import urllib.parse
import urllib.request

from . import __version__
from .util import load_json, save_json, spawn

log = logging.getLogger("isopod.radio")

DIRECTORY = "all.api.radio-browser.info"
USER_AGENT = f"isopod/{__version__}"
# The genres isoPod lists: the name each goes by, in English as the genres of songs are, and
# the tags the directory is asked for it, since stations tag themselves in their own words.
# Their order is the order a station's own genre is picked in, the narrower before the wider.
GENRES = {
    "classicrock": ("Classic Rock", "classic rock"), "hardrock": ("Hard Rock", "hard rock"),
    "alternative": ("Alternative", "alternative", "alternative rock"), "indie": ("Indie", "indie", "indie rock"),
    "punk": ("Punk", "punk", "punk rock"), "metal": ("Metal", "metal", "heavy metal"),
    "newwave": ("New Wave", "new wave"), "rock": ("Rock", "rock"), "jazz": ("Jazz", "jazz"),
    "blues": ("Blues", "blues"), "soul": ("Soul", "soul"), "funk": ("Funk", "funk"), "rnb": ("R&B", "rnb", "r&b"),
    "disco": ("Disco", "disco"), "hiphop": ("Hip-Hop", "rap", "hiphop", "hip-hop"), "reggae": ("Reggae", "reggae"),
    "gospel": ("Gospel", "gospel"), "house": ("House", "house"), "techno": ("Techno", "techno"),
    "trance": ("Trance", "trance"), "dnb": ("Drum & Bass", "drum and bass"), "ambient": ("Ambient", "ambient"),
    "lofi": ("Lo-Fi", "lofi", "lo-fi"), "chillout": ("Chillout", "chillout", "chill"),
    "lounge": ("Lounge", "lounge"), "dance": ("Dance", "dance", "edm"),
    "electronic": ("Electronic", "electronic", "electro"), "opera": ("Opera", "opera"), "piano": ("Piano", "piano"),
    "soundtrack": ("Soundtrack", "soundtrack"), "instrumental": ("Instrumental", "instrumental"),
    "classical": ("Classical", "classical"), "easy": ("Easy Listening", "easy listening"),
    "kpop": ("K-Pop", "kpop", "k-pop"), "jpop": ("J-Pop", "jpop", "j-pop"), "anime": ("Anime", "anime"),
    "country": ("Country", "country"), "folk": ("Folk", "folk"), "salsa": ("Salsa", "salsa"),
    "reggaeton": ("Reggaeton", "reggaeton"), "latin": ("Latin", "latino", "latin"),
    "world": ("World", "world music", "world"), "christian": ("Christian", "christian"),
    "kids": ("Kids", "kids", "children"), "pop": ("Pop", "pop"), "oldies": ("Oldies", "oldies"),
    "news": ("News", "news"), "talk": ("Talk", "talk"), "sports": ("Sports", "sports", "sport"),
    "comedy": ("Comedy", "comedy"), "culture": ("Culture", "culture"), "50s": ("50s", "50s"),
    "60s": ("60s", "60s", "60's"), "70s": ("70s", "70s", "70's"), "80s": ("80s", "80s", "80's"),
    "90s": ("90s", "90s", "90's"), "00s": ("2000s", "2000s", "00s"), "10s": ("2010s", "2010s", "10s"),
}
DECADES = ("50s", "60s", "70s", "80s", "90s", "00s", "10s")
ALIASES = {tag: genre for genre, (_, *tags) in GENRES.items() for tag in tags}
ALIASES.update({"pop music": "pop", "top 40": "pop", "hits": "pop", "smooth jazz": "jazz", "hip hop": "hiphop",
                "classical music": "classical", "electronica": "electronic", "deep house": "house",
                "country music": "country", "classic hits": "oldies", "information": "news", "local news": "news",
                "news talk": "talk", "talk radio": "talk"})
FETCH, KEEP = 150, 100
LIFE = 7 * 24 * 3600  # what is kept is fetched again once it is a week old
RETRY = 600
MIN_STATIONS = 10
LOGO_BYTES, LOGO_SIDE, LOGO_SECONDS = 1_000_000, 256, 5
# Wikimedia draws a picture at a few widths only, and many logos ask it for another.
WIKIMEDIA = re.compile(r"(//upload\.wikimedia\.org/.+/thumb/.+/)\d+(px-)")
WEB = ("http://", "https://")


class RadioError(Exception):
    pass


def station_from_api(raw):
    tags = {ALIASES.get(t, t) for t in (t.strip().lower() for t in (raw.get("tags") or "").split(","))}
    codec = (raw.get("codec") or "").strip()
    url = raw.get("url_resolved") or raw.get("url") or ""
    if not url.startswith(WEB):
        url = ""
    elif str(raw.get("hls")) in ("1", "True", "true"):
        url = "hls+" + url  # the mark of an HLS stream, which music.stream_urls reads
    return {
        "uuid": raw.get("stationuuid", ""),
        "name": (raw.get("name") or "").strip() or "Radio",
        "url": url,
        "favicon": raw.get("favicon") or "",
        "genres": [g for g in GENRES if g in tags][:2],
        "country": raw.get("countrycode") or "",
        "codec": "" if codec.upper() == "UNKNOWN" else codec,
        "bitrate": int(raw.get("bitrate") or 0),
    }


def _fold(name):
    return "".join(c for c in unicodedata.normalize("NFKC", name).casefold() if c.isalnum())


def _age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return float("inf")


def unique(stations):
    """Anyone can add a station to the directory, and the same one is there many times over:
    of those with one address, or one name in one country, the first stays."""
    seen, out = set(), []
    for s in stations:
        keys = {("url", s["url"])}
        if _fold(s["name"]):
            keys.add((_fold(s["name"]), s["country"]))
        if s["url"] and not keys & seen:
            out.append(s)
        seen |= keys
    return out


class Directory:
    """Radio Browser, over one connection that stays open. Its one name answers with every
    server it runs at the time, and one of those is asked."""

    def __init__(self, timeout=10):
        self.timeout = timeout
        self._conn = None
        self._lock = asyncio.Lock()

    def _server(self):
        names = set()
        for info in socket.getaddrinfo(DIRECTORY, 443, proto=socket.IPPROTO_TCP):
            try:
                names.add(socket.gethostbyaddr(info[4][0])[0])
            except OSError:
                pass
        names = sorted(n for n in names if n.endswith(DIRECTORY.partition(".")[2]))
        return random.choice(names) if names else DIRECTORY

    def _get(self, path):
        while True:
            kept = self._conn is not None
            try:
                if not kept:
                    self._conn = http.client.HTTPSConnection(self._server(), timeout=self.timeout)
                self._conn.request("GET", path, headers={"User-Agent": USER_AGENT})
                response = self._conn.getresponse()
                body = response.read()
                if response.status != 200:
                    raise RadioError(f"HTTP {response.status}")
                return json.loads(body)
            except (OSError, http.client.HTTPException, ValueError, RadioError) as e:
                if self._conn:
                    self._conn.close()
                self._conn = None
                if not kept:  # a connection kept open may have been closed at the far end
                    raise RadioError(str(e) or type(e).__name__) from None

    async def get(self, path, **params):
        if params:
            path += "?" + urllib.parse.urlencode(params)
        async with self._lock:
            return await asyncio.to_thread(self._get, path)


def download(url, limit=LOGO_BYTES, timeout=LOGO_SECONDS):
    """A logo, or nothing: what is too big, too slow or not on the web is not a logo."""
    if not url.startswith(WEB):
        return b""
    try:
        request = urllib.request.Request(WIKIMEDIA.sub(r"\g<1>250\2", url), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except (OSError, ValueError, http.client.HTTPException):
        return b""
    return data if len(data) <= limit else b""


def shrink(data, path, side=LOGO_SIDE):
    """Stations give logos of any kind and size. isopod-thumb, which reads what the shell
    reads, leaves a small PNG at path, so the shell never opens a picture it cannot or a
    large one while it draws."""
    part = path + ".part"
    with open(part, "wb") as f:
        f.write(data)
    try:
        return subprocess.run(["isopod-thumb", part, path, str(side)], timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        os.unlink(part)


class Radio:
    """Stations from Radio Browser. A list is fetched the first time it is opened and kept on
    the card. Opened again a week or more later it is shown as it is and fetched again behind
    that; if the directory is out of reach it stays as it was. Logos are kept the same way."""

    def __init__(self, config_dir, cache_dir, directory=None, download=download, shrink=shrink):
        self.fav_path = os.path.join(config_dir, "radio-favorites.json")
        self.list_dir = os.path.join(cache_dir, "radio", "lists")
        self.logo_dir = os.path.join(cache_dir, "radio", "logos")
        self.favorites = load_json(self.fav_path, [])
        self.changed = lambda: None
        self._asked = os.path.join(cache_dir, "radio", "favorites-asked")
        self._directory = directory or Directory()
        self._download = download
        self._shrink = shrink
        self._lists = {}
        self._loading = {}
        self._found = {}
        self._tried = {}
        self._fetching = set()
        self._fetchers = concurrent.futures.ThreadPoolExecutor(12, thread_name_prefix="logo")

    # lists -------------------------------------------------------------------

    def _file(self, key):
        return os.path.join(self.list_dir, key + ".json")

    async def _fetch(self, key):
        if key == "countries":
            raw = await self._directory.get("/json/countries", hidebroken="true")
            return sorted(c["iso_3166_1"] for c in raw if int(c.get("stationcount") or 0) >= MIN_STATIONS
                          and c["iso_3166_1"].isupper())
        params = {"order": "clickcount", "reverse": "true", "limit": FETCH, "hidebroken": "true"}
        kind, _, name = key.partition("-")
        if kind == "genre":
            return await self._stations(*({**params, "tag": tag, "tagExact": "true"} for tag in GENRES[name][1:]))
        if kind == "country":
            params["countrycode"] = name
        return await self._stations(params)

    async def _stations(self, *searches):
        """The most played of what the searches find together, each station once."""
        raw = []
        for params in searches:
            raw += await self._directory.get("/json/stations/search", **params)
        if len(searches) > 1:
            raw.sort(key=lambda r: -int(r.get("clickcount") or 0))
        return unique([station_from_api(r) for r in raw])[:KEEP]

    async def _keep(self, key):
        found = await self._fetch(key)
        save_json(self._file(key), found)
        self._lists[key] = found
        return found

    def _fetched(self, key):
        """A list from the directory, kept once it is had. One fetch serves all who wait."""
        if key not in self._loading:
            self._loading[key] = asyncio.ensure_future(self._keep(key))
            self._loading[key].add_done_callback(lambda _: self._loading.pop(key, None))
        return self._loading[key]

    async def _list(self, key):
        if key not in self._lists:
            self._lists[key] = load_json(self._file(key), None)
        if self._lists[key] is None:
            return await self._fetched(key)
        if _age(self._file(key)) > LIFE and self._due(key):
            spawn(self._renew(self._fetched(key)))
        return self._lists[key]

    def _due(self, key):
        """What could not be fetched again is not tried over and over."""
        now = time.monotonic()
        if now - self._tried.get(key, -RETRY) < RETRY:
            return False
        self._tried[key] = now
        return True

    async def _renew(self, fetch):
        """What has aged is fetched again behind what is shown, and stays if it cannot be."""
        try:
            await fetch
        except RadioError as e:
            log.info("kept as it was: %s", e)
            return
        await asyncio.to_thread(self._sweep_logos, [self.favorites, *self._found.values()])
        self.changed()

    async def top(self):
        return await self._list("top")

    async def genre(self, name):
        return await self._list("genre-" + name) if name in GENRES else []

    async def country(self, code):
        return await self._list("country-" + code) if len(code) == 2 and code.isalpha() else []

    async def countries(self):
        return await self._list("countries")

    async def search(self, name):
        """Asked of the directory each time; an answer is kept only while isoPod stays on."""
        key = _fold(name)
        if key not in self._found:
            found = await self._stations(dict(name=name, order="clickcount", reverse="true", limit=FETCH,
                                              hidebroken="true"))
            if len(self._found) >= 20:
                del self._found[next(iter(self._found))]
            self._found[key] = found
        return self._found[key]

    # favourites --------------------------------------------------------------

    def is_favorite(self, uuid):
        return any(s["uuid"] == uuid for s in self.favorites)

    def toggle_favorite(self, station):
        if self.is_favorite(station["uuid"]):
            self.favorites = [s for s in self.favorites if s["uuid"] != station["uuid"]]
        else:
            self.favorites.insert(0, {k: station[k] for k in
                                      ("uuid", "name", "url", "favicon", "genres", "country", "codec", "bitrate")})
        save_json(self.fav_path, self.favorites)

    def check_favorites(self):
        """A favourite follows its station: the directory is asked about them as they age."""
        if self.favorites and _age(self._asked) > LIFE and self._due("favorites"):
            spawn(self._renew(self._ask_favorites()))

    async def _ask_favorites(self):
        raw = await self._directory.get("/json/stations/byuuid", uuids=",".join(s["uuid"] for s in self.favorites))
        now = {s["uuid"]: s for s in map(station_from_api, raw) if s["url"]}
        self.favorites = [now.get(s["uuid"], s) for s in self.favorites]
        save_json(self.fav_path, self.favorites)
        save_json(self._asked, int(time.time()))

    # logos -------------------------------------------------------------------

    def _logo_base(self, station):
        url = station.get("favicon")
        return os.path.join(self.logo_dir, hashlib.sha1(url.encode()).hexdigest()[:16]) if url else None

    def logo(self, station):
        base = self._logo_base(station)
        return base + ".png" if base and os.path.exists(base + ".png") else None

    def want_logos(self, stations, arrived):
        """Fetch the logos these stations lack, some at a time, and tell of each as it comes.
        One that cannot be had leaves a mark, and is asked for again once the mark has aged."""
        for station in stations:
            base = self._logo_base(station)
            if (base and base not in self._fetching and not os.path.exists(base + ".png")
                    and _age(base + ".no") > LIFE):
                self._fetching.add(base)
                spawn(self._fetch_logo(station, base, arrived))

    async def _fetch_logo(self, station, base, arrived):
        try:
            had = await asyncio.get_running_loop().run_in_executor(
                self._fetchers, self._save_logo, station["favicon"], base)
        finally:
            self._fetching.discard(base)
        if had:
            arrived(station)

    def _save_logo(self, url, base):
        os.makedirs(self.logo_dir, exist_ok=True)
        data = self._download(url)
        if data and self._shrink(data, base + ".png"):
            return True
        open(base + ".no", "w").close()
        return False

    def _sweep_logos(self, shown):
        """When a list has changed: the logos and marks of stations no list, search or
        favourite shows any more go."""
        shown = shown + [load_json(os.path.join(self.list_dir, name), []) for name in os.listdir(self.list_dir)]
        kept = {self._logo_base(s) for stations in shown for s in stations if isinstance(s, dict)}
        try:
            names = os.listdir(self.logo_dir)
        except OSError:
            return
        for name in names:
            base, ext = os.path.splitext(os.path.join(self.logo_dir, name))
            if ext in (".png", ".no") and base not in kept:
                os.unlink(os.path.join(self.logo_dir, name))
