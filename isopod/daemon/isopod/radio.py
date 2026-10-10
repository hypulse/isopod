import asyncio
import hashlib
import json
import logging
import os
import random
import time
import urllib.parse
import urllib.request

from . import __version__
from .util import load_json, save_json, spawn

log = logging.getLogger("isopod.radio")

SERVERS = [
    "https://de1.api.radio-browser.info",
    "https://de2.api.radio-browser.info",
    "https://fi1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
]
USER_AGENT = f"isopod/{__version__}"
CACHE_SECONDS = 600


class RadioError(Exception):
    pass


def station_from_api(raw):
    tags = [t.strip() for t in (raw.get("tags") or "").split(",") if t.strip()][:3]
    codec = (raw.get("codec") or "").strip()
    url = raw.get("url_resolved") or raw.get("url", "")
    if str(raw.get("hls")) in ("1", "True", "true") and url.startswith(("http://", "https://")):
        url = "hls+" + url  # MPD plays live HLS through FFmpeg only under this scheme
    return {
        "uuid": raw.get("stationuuid", ""),
        "name": (raw.get("name") or "").strip() or "Radio",
        "url": url,
        "favicon": raw.get("favicon") or "",
        "tags": tags,
        "country": raw.get("countrycode") or "",
        "codec": "" if codec.upper() == "UNKNOWN" else codec,
        "bitrate": int(raw.get("bitrate") or 0),
    }


class Radio:
    def __init__(self, config_dir, cache_dir, fetch=None):
        self.fav_path = os.path.join(config_dir, "radio-favorites.json")
        self.icon_dir = os.path.join(cache_dir, "radio")
        self.favorites = load_json(self.fav_path, [])
        self._cache = {}
        self._servers = SERVERS[:]
        random.shuffle(self._servers)
        self._fetch = fetch or self._http_get

    def _http_get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.read()

    async def _api(self, path, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        key = f"{path}?{query}"
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        last = None
        for server in list(self._servers):
            try:
                body = await asyncio.to_thread(self._fetch, f"{server}{path}?{query}")
                data = json.loads(body)
                self._cache[key] = (time.monotonic(), data)
                return data
            except Exception as e:
                last = e
                self._servers.remove(server)
                self._servers.append(server)
        raise RadioError(str(last))

    async def _stations(self, path, **params):
        raw = await self._api(path, hidebroken="true", **params)
        seen, out = set(), []
        for item in raw:
            station = station_from_api(item)
            if station["url"] and station["uuid"] not in seen:
                seen.add(station["uuid"])
                out.append(station)
        return out

    async def top(self):
        return await self._stations("/json/stations/topvote/100")

    async def by_country(self, code):
        return await self._stations("/json/stations/search", countrycode=code,
                                    order="votes", reverse="true", limit=150)

    async def search(self, name):
        return await self._stations("/json/stations/search", name=name,
                                    order="votes", reverse="true", limit=100)

    async def countries(self):
        raw = await self._api("/json/countries", order="stationcount", reverse="true")
        return [(c["iso_3166_1"], c["name"], int(c.get("stationcount", 0)))
                for c in raw if c.get("iso_3166_1") and int(c.get("stationcount", 0)) >= 20][:80]

    def is_favorite(self, uuid):
        return any(s["uuid"] == uuid for s in self.favorites)

    def toggle_favorite(self, station):
        if self.is_favorite(station["uuid"]):
            self.favorites = [s for s in self.favorites if s["uuid"] != station["uuid"]]
            added = False
        else:
            self.favorites.insert(0, {k: station[k] for k in
                                      ("uuid", "name", "url", "favicon", "tags", "country",
                                       "codec", "bitrate")})
            added = True
        save_json(self.fav_path, self.favorites)
        return added

    def report_click(self, station):
        if station.get("uuid"):
            spawn(self._click(station["uuid"]))

    async def _click(self, uuid):
        try:
            await self._api(f"/json/url/{uuid}")
        except RadioError:
            pass

    def _icon_file(self, station):
        url = station.get("favicon")
        return os.path.join(self.icon_dir, hashlib.sha1(url.encode()).hexdigest()[:16]) if url else None

    def cached_icon(self, station):
        path = self._icon_file(station)
        return path if path and os.path.exists(path) else None

    async def icon(self, station):
        path = self._icon_file(station)
        if not path:
            return None
        if os.path.exists(path):
            return path
        url = station["favicon"]
        try:
            data = await asyncio.to_thread(self._fetch, url)
        except Exception:
            return None
        if len(data) < 64 or not _looks_like_image(data):
            return None
        os.makedirs(self.icon_dir, exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(data)
        os.replace(path + ".part", path)
        return path


class SongLog:
    """What the radio has played, newest first, so that a song can be looked up later. A title
    counts once it has been on for a while, which leaves out the stations only passed through.
    Songs the listener saved stay; the rest roll off."""

    DWELL = 10.0
    RECENT = 100

    def __init__(self, config_dir, changed=None, clock=time.time):
        self.path = os.path.join(config_dir, "radio-songs.json")
        self.entries = load_json(self.path, [])
        self.changed = changed or (lambda: None)
        self.on_air = None
        self._clock = clock
        self._timer = None

    def playing(self, title, station):
        """Told what the station gives as its song, or nothing while it gives none."""
        title = " ".join((title or "").split())
        now = (title, station) if title and title.casefold() != (station or "").casefold() else None
        if now == self.on_air:
            return
        self.on_air = now
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if now:
            self._timer = asyncio.get_running_loop().call_later(self.DWELL, self._heard, now)

    def _heard(self, song):
        self._timer = None
        self._put(*song)

    def find(self, title):
        key = title.casefold()
        return next((e for e in self.entries if e["title"].casefold() == key), None)

    def saved(self):
        return [e for e in self.entries if e["saved"]]

    def recent(self):
        return [e for e in self.entries if not e["saved"]]

    def set_saved(self, title, saved, station=""):
        entry = self.find(title)
        if entry:
            entry["saved"] = saved
            self._store()
        elif saved:
            self._put(title, station, saved=True)

    def remove(self, title):
        entry = self.find(title)
        if entry:
            self.entries.remove(entry)
            self._store()

    def _put(self, title, station, saved=False):
        """A station plays its songs again: one heard before moves back to the top."""
        entry = self.find(title)
        if entry:
            self.entries.remove(entry)
        else:
            entry = {"title": title, "saved": False}
        entry.update(station=station, time=int(self._clock()), saved=entry["saved"] or saved)
        self.entries.insert(0, entry)
        self._store()

    def _store(self):
        kept, room = [], self.RECENT
        for entry in self.entries:
            if entry["saved"] or room:
                kept.append(entry)
                room -= not entry["saved"]
        self.entries = kept
        save_json(self.path, self.entries)
        self.changed()


def _looks_like_image(data):
    return (data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:4] in (b"GIF8", b"\x00\x00\x01\x00") or data[:4] == b"RIFF")
