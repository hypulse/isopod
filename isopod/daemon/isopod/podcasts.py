import asyncio
import concurrent.futures
import datetime
import gzip
import hashlib
import http.client
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import quoteattr

from .music import Spoken, local
from .radio import USER_AGENT, download, shrink
from .util import load_json, save_json, spawn

log = logging.getLogger("isopod.podcasts")

APPLE = "https://itunes.apple.com"
STORES = {"ko": "kr", "en": "us"}
SUBSCRIPTIONS = "Subscriptions.opml"
LIFE = 3600  # a show's episodes are asked for again once what is kept is an hour old
TOP_LIFE = 24 * 3600
RETRY = 600
EPISODES = 200  # the most Apple gives of a show: its newest
TIMEOUT = 20
ART_BYTES, ART_SIDE, ART_SECONDS = 8 << 20, 400, 15
NEW = 14 * 24 * 3600  # what came out up to two weeks before a show was subscribed to is new too
SAVE_EVERY = 60
HEARD_KEPT = 2000  # places of episodes heard, the most recent kept
DONE_LEFT = 30  # an episode is heard once no more than its last half minute is left, or tenth
CHUNK = 1 << 16
WEB = ("http://", "https://")
EXTS = (".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4v", ".mov")


class PodcastError(Exception):
    pass


class NotListed(PodcastError):
    """A show Apple's directory does not have."""


def fetch(url, limit=8 << 20, timeout=TIMEOUT):
    """What is at an address, sent compressed where the server can, or PodcastError."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(limit + 1)
            if response.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
    except (OSError, ValueError, EOFError, http.client.HTTPException) as e:
        raise PodcastError(str(e) or type(e).__name__) from None
    if len(data) > limit:
        raise PodcastError("too big")
    return data


def web(url):
    """An address as a web client sends it: spaces and Hangul in it escaped."""
    return urllib.parse.quote(url.strip(), safe=":/?#[]@!$&'()*+,;=%~")


def final_address(url, timeout=10):
    """Where an episode's address leads. Listeners are counted by redirects put in front of
    the file, and MPD follows five of them where some episodes have six or more."""
    try:
        request = urllib.request.Request(web(url), headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.geturl()
    except (OSError, ValueError, http.client.HTTPException):
        return url


def _when(text):
    """Apple's times, which are in UTC: 2026-10-20T00:18:22Z."""
    try:
        return int(datetime.datetime.fromisoformat((text or "").replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


# Apple's directory -----------------------------------------------------------

def _show(raw):
    return {"id": str(raw["collectionId"]), "title": (raw.get("collectionName") or "").strip(),
            "author": (raw.get("artistName") or "").strip(),
            "image": raw.get("artworkUrl600") or raw.get("artworkUrl100") or "",
            "url": (raw.get("feedUrl") or "").strip()}


def _episode(raw):
    url = (raw.get("episodeUrl") or "").strip()
    if not url.startswith(WEB):
        return None
    ext = "." + (raw.get("episodeFileExtension") or "mp3").lower().lstrip(".")
    return {"id": str(raw["trackId"]), "title": (raw.get("trackName") or "").strip() or "Episode",
            "date": _when(raw.get("releaseDate")), "duration": int(raw.get("trackTimeMillis") or 0) // 1000,
            "url": web(url), "ext": ext if ext in EXTS else ".mp3"}


class Apple:
    """Apple's podcast directory, the one place isoPod asks: shows by name, the chart of a
    store, and a show's newest episodes as Apple has them from its feed. It reaches feeds
    that answer Apple alone, and gives small JSON where feeds can be many megabytes."""

    def __init__(self, get=fetch):
        self._get = get

    def _call(self, path, **params):
        url = APPLE + path + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            return json.loads(self._get(url))
        except ValueError:
            raise PodcastError("not JSON") from None

    def search(self, term, store):
        raw = self._call("/search", term=term, media="podcast", entity="podcast", limit=50, country=store)
        return [_show(r) for r in raw.get("results", []) if r.get("collectionId")]

    def top(self, store, limit=100):
        """The store's chart names its shows; they are looked up after, in its order."""
        entries = self._call(f"/{store}/rss/toppodcasts/limit={limit}/json").get("feed", {}).get("entry", [])
        if isinstance(entries, dict):
            entries = [entries]
        ids = [i for i in (e.get("id", {}).get("attributes", {}).get("im:id") for e in entries) if i]
        if not ids:
            return []
        found = self._call("/lookup", id=",".join(ids), entity="podcast", country=store)
        by_id = {str(r.get("collectionId")): r for r in found.get("results", []) if r.get("collectionId")}
        return [_show(by_id[i]) for i in ids if i in by_id]

    def episodes(self, sid, store):
        """(the show, its newest episodes, newest first)."""
        raw = self._call("/lookup", id=sid, entity="podcastEpisode", limit=EPISODES, country=store)
        results = raw.get("results", [])
        shows = [r for r in results if r.get("wrapperType") == "track" and r.get("kind") == "podcast"]
        if not shows:
            raise NotListed(sid)
        episodes = [e for e in map(_episode, (r for r in results if r.get("wrapperType") == "podcastEpisode")) if e]
        return _show(shows[0]), sorted(episodes, key=lambda e: -e["date"])


class SimDirectory:
    """For the simulator: shows and episodes from a file, its episodes files beside it."""

    def __init__(self, path):
        self.data = load_json(path, {"shows": [], "top": [], "episodes": {}})

    def search(self, term, store):
        return [s for s in self.data["shows"] if term.casefold() in s["title"].casefold()]

    def top(self, store, limit=100):
        return [s for i in self.data["top"] for s in self.data["shows"] if s["id"] == i]

    def episodes(self, sid, store):
        show = next((s for s in self.data["shows"] if s["id"] == sid), None)
        if not show:
            raise NotListed(sid)
        return show, self.data["episodes"].get(sid, [])


# subscriptions ---------------------------------------------------------------

def _tag(el):
    return el.tag.rpartition("}")[2] if isinstance(el.tag, str) else ""


def read_opml(path):
    """The shows an OPML file lists: isoPod's own, which knows them by Apple's id, or one
    another podcast app wrote, which knows them by their feeds."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    shows, seen = [], set()
    for el in root.iter():
        attrs = {k.lower(): v.strip() for k, v in el.attrib.items()}
        url, apple = attrs.get("xmlurl", ""), attrs.get("appleid", "")
        sid = apple or ("feed-" + hashlib.sha1(url.encode()).hexdigest()[:12] if url else "")
        if _tag(el) != "outline" or not sid or sid in seen:
            continue
        seen.add(sid)
        shows.append({"id": sid, "url": url, "title": attrs.get("title") or attrs.get("text") or "",
                      "image": attrs.get("imageurl", "")})
    return shows


def write_opml(path, shows):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<opml version="2.0">', "  <head><title>isoPod</title></head>",
             "  <body>"]
    for s in shows:
        attrs = f'type="rss" text={quoteattr(s["title"])} title={quoteattr(s["title"])} xmlUrl={quoteattr(s["url"])}'
        if not s["id"].startswith("feed-"):
            attrs += f" appleId={quoteattr(s['id'])}"
        if s.get("image"):
            attrs += f" imageUrl={quoteattr(s['image'])}"
        lines.append(f"    <outline {attrs}/>")
    lines += ["  </body>", "</opml>", ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(path + ".tmp", path)


def _name(text, limit=80):
    """A name a file can have on any card."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text or "")
    return " ".join(text.split()).strip(" .")[:limit].strip() or "Episode"


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


class Episode(Spoken):
    kind = "podcast"

    def __init__(self, podcasts, show, episode, uri):
        super().__init__(episode["id"], [uri], episode["title"], show.get("title", ""), podcasts.art(show))
        self.podcasts = podcasts
        self.show = show
        self.episode = episode
        self.length = episode["duration"]

    async def ready(self):
        """From the web, the file itself is what MPD is given, past the counters' redirects."""
        if self.uris[0].startswith(WEB):
            self.uris = [await asyncio.to_thread(final_address, self.uris[0])]
            self.podcasts.playing_from(self.uris[0])

    def heard(self, index, elapsed, duration, force=False):
        self.podcasts.heard(self.episode["id"], elapsed, duration or self.episode["duration"], force)


class Podcasts:
    """Shows from Apple's directory, and their newest episodes as Apple has them. What is
    subscribed to is an OPML file in the podcasts folder, beside the episodes that were
    downloaded, so both come and go over the cable. A show's episodes are kept on the card
    once they are had, and asked for again behind what is shown when they are an hour old.
    Nothing is downloaded unless it is asked for."""

    def __init__(self, folder, config_dir, cache_dir, lang=lambda: "ko", directory=None, download=download,
                 shrink=shrink):
        self.folder = folder
        self.opml = os.path.join(folder, SUBSCRIPTIONS)
        self.state_path = os.path.join(config_dir, "podcasts.json")
        self.show_dir = os.path.join(cache_dir, "podcasts", "shows")
        self.art_dir = os.path.join(cache_dir, "podcasts", "art")
        self.lang = lang
        self.changed = lambda: None
        self.failed = lambda episode: None
        self.directory = directory or Apple()
        self._download = download
        self._shrink = shrink
        data = load_json(self.state_path, {})
        self.played = data.get("episodes", {})
        self.added = data.get("added", {})
        self.downloads = data.get("downloads", {})
        self.last = data.get("last")
        self._saved_at = 0.0
        self._subs, self._subs_mtime, self._sub_ids = [], None, set()
        self._shows = {}
        self._known = {}
        self._loading = {}
        self._tried = {}
        self._found = {}
        self._top = None
        self._art_fetching = set()
        # Covers come many at once, from a list of a hundred: they wait for each other, not
        # for the episodes, searches and downloads in the common pool.
        self._covers = concurrent.futures.ThreadPoolExecutor(4, thread_name_prefix="cover")
        self._renewing = asyncio.Semaphore(2)
        self.progress = {}
        self._queue = []
        self._cancelled = set()
        self._current = None
        self._worker = None

    def _store(self):
        return STORES.get(self.lang(), "us")

    def _save(self):
        self._saved_at = time.monotonic()
        for eid in list(self.played)[:max(0, len(self.played) - HEARD_KEPT)]:
            del self.played[eid]
        save_json(self.state_path, {"episodes": self.played, "added": self.added, "downloads": self.downloads,
                                    "last": self.last})

    # shows -------------------------------------------------------------------

    def subscriptions(self):
        """The OPML file, read again whenever it has changed, a copy over the cable too."""
        try:
            mtime = os.path.getmtime(self.opml)
        except OSError:
            mtime = None
        if mtime != self._subs_mtime:
            self._subs, self._subs_mtime = read_opml(self.opml), mtime
            self._sub_ids = {s["id"] for s in self._subs}
            fresh = [s for s in self._subs if s["id"] not in self.added]
            for s in fresh:
                self.added[s["id"]] = int(time.time())
            if fresh:
                self._save()
        for s in self._subs:
            self._known.setdefault(s["id"], s)
        return self._subs

    def is_subscribed(self, sid):
        self.subscriptions()
        return sid in self._sub_ids

    def subscribe(self, show, on):
        shows = [s for s in self.subscriptions() if s["id"] != show["id"]]
        if on:
            kept = (self._cached(show["id"]) or {}).get("show", {})
            shows.insert(0, {"id": show["id"], "url": show.get("url") or kept.get("url", ""),
                             "title": show.get("title") or kept.get("title", ""),
                             "image": show.get("image") or kept.get("image", "")})
            self.added[show["id"]] = int(time.time())
            self._save()
        write_opml(self.opml, shows)
        self.subscriptions()

    def show(self, sid):
        """A show met in a list or a search, a subscribed one, or one whose episodes are kept."""
        self.subscriptions()
        if sid not in self._known:
            kept = self._cached(sid)
            if kept:
                self._known[sid] = kept["show"]
        return self._known.get(sid)

    def _meet(self, shows):
        for s in shows:
            self._known.setdefault(s["id"], s)
        return shows

    def meet(self, show):
        """A show known from an episode of it, so its page can be opened."""
        self._known.setdefault(show["id"], show)

    def _about(self, show, field):
        return (self._cached(show["id"]) or {}).get("show", {}).get(field) or show.get(field) or ""

    def title(self, show):
        return self._about(show, "title")

    def author(self, show):
        return self._about(show, "author")

    async def search(self, term):
        key = " ".join(term.casefold().split())
        if key not in self._found:
            found = await asyncio.to_thread(self.directory.search, term, self._store())
            if len(self._found) >= 20:
                del self._found[next(iter(self._found))]
            self._found[key] = found
        return self._meet(self._found[key])

    async def top(self):
        """The chart of the store for isoPod's language, kept a day and fetched again behind
        what is shown once it is older."""
        store = self._store()
        path = os.path.join(self.show_dir, f"top-{store}.json")
        if self._top is None or self._top[0] != store:
            self._top = (store, load_json(path, None))
        shows = self._top[1]
        if shows is None:
            shows = await self._chart(store, path)
        elif _age(path) > TOP_LIFE and self._due("top"):
            spawn(self._renew_chart(store, path))
        return self._meet(shows)

    async def _chart(self, store, path):
        shows = await asyncio.to_thread(self.directory.top, store)
        save_json(path, shows)
        self._top = (store, shows)
        return shows

    async def _renew_chart(self, store, path):
        try:
            await self._chart(store, path)
        except PodcastError as e:
            log.info("chart kept as it was: %s", e)
            return
        self.changed()

    # episodes ----------------------------------------------------------------

    def _show_file(self, sid):
        return os.path.join(self.show_dir, sid + ".json")

    def _cached(self, sid):
        if sid not in self._shows:
            self._shows[sid] = load_json(self._show_file(sid), None)
        return self._shows[sid]

    async def _keep(self, show):
        sid = show["id"]
        if sid.startswith("feed-"):
            sid = await self._find(show)
        about, episodes = await asyncio.to_thread(self.directory.episodes, sid, self._store())
        kept = {"show": about, "episodes": episodes}
        for key in {sid, show["id"]}:
            save_json(self._show_file(key), kept)
            self._shows[key] = kept
        return kept

    async def _find(self, show):
        """A show from another app's OPML, which names its feed only: found in Apple's
        directory by its name and that feed, and named by Apple's id in the file after."""
        found = await asyncio.to_thread(self.directory.search, show["title"], self._store())
        same = [s for s in found if s["url"] == show["url"]] or [s for s in found if s["title"] == show["title"]]
        if not same:
            raise NotListed(show["url"])
        apple = same[0]
        shows = [dict(s, id=apple["id"]) if s["id"] == show["id"] else s for s in self.subscriptions()]
        self.added[apple["id"]] = self.added.get(show["id"], int(time.time()))
        write_opml(self.opml, shows)
        return apple["id"]

    def _fetched(self, show):
        """One fetch of a show's episodes serves all who wait for it."""
        sid = show["id"]
        if sid not in self._loading:
            self._loading[sid] = asyncio.ensure_future(self._keep(show))
            self._loading[sid].add_done_callback(lambda _: self._loading.pop(sid, None))
        return self._loading[sid]

    def _due(self, key):
        now = time.monotonic()
        if now - self._tried.get(key, -RETRY) < RETRY:
            return False
        self._tried[key] = now
        return True

    async def load(self, show):
        """A show's episodes: kept ones at once, fetched again behind them once they have aged."""
        kept = self._cached(show["id"])
        if kept is None:
            kept = await self._fetched(show)
        elif _age(self._show_file(show["id"])) > LIFE and self._due(show["id"]):
            spawn(self._renew(show))
        return kept["episodes"]

    async def _renew(self, show):
        async with self._renewing:
            try:
                await self._fetched(show)
            except PodcastError as e:
                log.info("%s kept as it was: %s", show["id"], e)
                return
        self.changed()

    def renew_subscriptions(self):
        """Opening the list of shows asks each one that has aged for what is new in it."""
        for show in self.subscriptions():
            if _age(self._show_file(show["id"])) > LIFE and self._due(show["id"]):
                spawn(self._renew(show))

    def episodes(self, show):
        return (self._cached(show["id"]) or {}).get("episodes", [])

    def latest(self, show):
        episodes = self.episodes(show)
        return episodes[0]["date"] if episodes else 0

    def is_new(self, show, episode):
        """Not heard nor begun, and out since a little before the show was subscribed to."""
        added = self.added.get(show["id"])
        return (show["id"] in self._sub_ids and added is not None and episode["id"] not in self.played
                and episode["date"] >= added - NEW)

    def has_new(self, show):
        return any(self.is_new(show, e) for e in self.episodes(show)[:20])

    def place(self, eid):
        """(seconds in, heard to the end)."""
        mark = self.played.get(eid) or {}
        return mark.get("pos", 0), bool(mark.get("done"))

    def set_heard(self, eid, done):
        self.played.pop(eid, None)
        if done:
            self.played[eid] = {"pos": 0, "done": True}
        self._save()

    def heard(self, eid, elapsed, duration, force=False):
        if duration > 0 and elapsed >= duration - min(DONE_LEFT, duration / 10):
            mark = {"pos": 0, "done": True}
        elif elapsed >= 5:
            mark = {"pos": int(elapsed), "done": False}
        else:
            return
        if self.played.get(eid) != mark:
            self.played.pop(eid, None)  # the latest heard is the last to go
            self.played[eid] = mark
            if force or mark["done"] or time.monotonic() - self._saved_at > SAVE_EVERY:
                self._save()

    def play(self, show, episode):
        """What Music plays for an episode, and where in it: the downloaded file if there is
        one, a little before where it was left."""
        file = self.file(episode["id"])
        uri = "file://" + file if file else episode["url"]
        pos, done = self.place(episode["id"])
        self.last = {"show": {k: show.get(k, "") for k in ("id", "url", "title", "image")}, "episode": episode,
                     "uri": uri}
        self._save()
        self.meet(self.last["show"])
        return Episode(self, self.last["show"], episode, uri), (0 if done else max(0, pos - 2))

    def playing_from(self, uri):
        """The address MPD was given for the last episode, to know it by after a restart."""
        if self.last:
            self.last["uri"] = uri
            self._save()

    def identify(self, uri):
        """An episode MPD is found playing after a restart: a downloaded one, or the last one."""
        path = local(uri)
        for eid, d in self.downloads.items():
            if os.path.join(self.folder, d["path"]) == path:
                return Episode(self, d["show"], d["episode"], uri)
        if self.last and self.last.get("uri") == uri:
            return Episode(self, self.last["show"], self.last["episode"], uri)
        return None

    # downloads ---------------------------------------------------------------

    def file(self, eid):
        d = self.downloads.get(eid)
        path = os.path.join(self.folder, d["path"]) if d else None
        return path if path and os.path.exists(path) else None

    def downloaded(self):
        """(show, episode) of what is on the card, the newest first."""
        found = [(d["show"], d["episode"]) for eid, d in self.downloads.items() if self.file(eid)]
        return sorted(found, key=lambda pair: -pair[1]["date"])

    def downloading(self):
        return bool(self.progress)

    def download(self, show, episode):
        eid = episode["id"]
        if eid in self.progress or self.file(eid):
            return
        self.progress[eid] = None
        self._queue.append(({k: show.get(k, "") for k in ("id", "url", "title", "image")}, episode))
        if not self._worker:
            self._worker = spawn(self._work())
        self.changed()

    def cancel(self, eid):
        self._queue = [(s, e) for s, e in self._queue if e["id"] != eid]
        if eid == self._current:
            self._cancelled.add(eid)
        self.progress.pop(eid, None)
        self.changed()

    def remove(self, eid):
        d = self.downloads.pop(eid, None)
        if d:
            path = os.path.join(self.folder, d["path"])
            try:
                os.unlink(path)
                os.rmdir(os.path.dirname(path))  # the show's folder, once it is empty
            except OSError:
                pass
            self._save()
        self.changed()

    async def _work(self):
        """One download at a time, in the order asked for; the page is told as each whole
        percent comes, not every second, for it is drawn again each time."""
        try:
            while self._queue:
                show, episode = self._queue.pop(0)
                eid = self._current = episode["id"]
                job = asyncio.ensure_future(asyncio.to_thread(self._save_episode, show, episode))
                shown = None
                while not job.done():
                    await asyncio.wait([job], timeout=1)
                    share = self.progress.get(eid)
                    percent = int(share * 100) if share is not None else None
                    if percent != shown:
                        shown = percent
                        self.changed()
                self._current = None
                self.progress.pop(eid, None)
                try:
                    path = job.result()
                except (OSError, ValueError, http.client.HTTPException) as e:
                    log.warning("download failed: %s: %s", episode["url"], e)
                    self.failed(episode)
                    path = None
                if path and eid not in self._cancelled:
                    self.downloads[eid] = {"path": path, "show": show, "episode": episode}
                    self._save()
                self._cancelled.discard(eid)
                self.changed()
        finally:
            self._worker = None

    def _save_episode(self, show, episode):
        eid = episode["id"]
        when = datetime.date.fromtimestamp(episode["date"]).isoformat() + " " if episode["date"] else ""
        title = self.title(show) or show.get("title")
        rel = os.path.join(_name(title), _name(when + episode["title"]) + episode.get("ext", ".mp3"))
        path = os.path.join(self.folder, rel)
        if os.path.exists(path):
            base, ext = os.path.splitext(rel)
            rel = f"{base} {eid[:6]}{ext}"
            path = os.path.join(self.folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        request = urllib.request.Request(web(episode["url"]), headers={"User-Agent": USER_AGENT})
        part = path + ".part"
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(part, "wb") as out:
                total = _int(response.headers.get("Content-Length"))
                got = 0
                while chunk := response.read(CHUNK):
                    if eid in self._cancelled:
                        raise OSError("cancelled")
                    out.write(chunk)
                    got += len(chunk)
                    if total:
                        self.progress[eid] = min(got / total, 1.0)
            os.replace(part, path)
        except BaseException:
            for gone in (os.unlink, os.rmdir):
                try:
                    gone(part if gone is os.unlink else os.path.dirname(part))
                except OSError:
                    pass
            if eid in self._cancelled:
                return None
            raise
        return rel

    # artwork -----------------------------------------------------------------

    def _art_url(self, show):
        return show.get("image") or self._about(show, "image")

    def _art_base(self, show):
        url = self._art_url(show)
        return os.path.join(self.art_dir, hashlib.sha1(url.encode()).hexdigest()[:16]) if url else None

    def art(self, show):
        base = self._art_base(show)
        return base + ".png" if base and os.path.exists(base + ".png") else None

    def want_art(self, shows, arrived):
        """Covers the shows lack, fetched behind the list, each told of as it comes."""
        for show in shows:
            base = self._art_base(show)
            if (base and base not in self._art_fetching and not os.path.exists(base + ".png")
                    and _age(base + ".no") > TOP_LIFE):
                self._art_fetching.add(base)
                spawn(self._fetch_art(show, base, arrived))

    async def _fetch_art(self, show, base, arrived):
        try:
            had = await asyncio.get_running_loop().run_in_executor(
                self._covers, self._save_art, self._art_url(show), base)
        finally:
            self._art_fetching.discard(base)
        if had:
            arrived(show)

    def _save_art(self, url, base):
        os.makedirs(self.art_dir, exist_ok=True)
        data = self._download(url, ART_BYTES, ART_SECONDS)
        if data and self._shrink(data, base + ".png", ART_SIDE):
            return True
        open(base + ".no", "w").close()
        return False


def _age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return float("inf")
