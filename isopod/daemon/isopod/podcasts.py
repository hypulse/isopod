import asyncio
import datetime
import email.utils
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
FEED_LIFE = 3600  # a show's episodes are asked for again once what is kept is an hour old
TOP_LIFE = 24 * 3600
RETRY = 600
KEEP = 300  # the newest episodes of a show; some have thousands
FEED_BYTES = 20 << 20
TIMEOUT = 20
ART_BYTES, ART_SIDE, ART_SECONDS = 8 << 20, 400, 15
NEW = 14 * 24 * 3600  # what came out up to two weeks before a show was subscribed to is new too
SAVE_EVERY = 15
DONE_LEFT = 30  # an episode is heard once no more than its last half minute is left, or tenth
CHUNK = 1 << 16
TYPES = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac",
         "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/wav": ".wav", "audio/x-wav": ".wav", "video/mp4": ".mp4"}
EXTS = (".mp3", ".m4a", ".m4b", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".m4v", ".mov")


class PodcastError(Exception):
    pass


def _id(*parts):
    return hashlib.sha1("\0".join(parts).encode()).hexdigest()[:16]


def show_id(url):
    return _id(url)


def fetch(url, limit=FEED_BYTES, timeout=TIMEOUT):
    """What is at an address, or PodcastError."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise PodcastError(str(e) or type(e).__name__) from None
    if len(data) > limit:
        raise PodcastError("too big")
    return data


# feeds -----------------------------------------------------------------------

def _split(tag):
    if not isinstance(tag, str):
        return None, ""
    if tag.startswith("{"):
        ns, _, name = tag[1:].partition("}")
        return ns.lower(), name
    return "", tag


def _child(el, name, itunes=False):
    """The first child of that name: one of iTunes', in whatever case a feed spells its
    namespace, or one of RSS's own."""
    if el is None:
        return None
    for child in el:
        ns, tag = _split(child.tag)
        if tag == name and (("itunes.com/dtds" in ns) if itunes else ns == ""):
            return child
    return None


def _text(el):
    return " ".join((el.text or "").split()) if el is not None else ""


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def parse_duration(text):
    """iTunes' duration: seconds, M:SS or H:MM:SS."""
    try:
        parts = [float(p) for p in (text or "").strip().split(":")]
    except ValueError:
        return 0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return int(seconds)


def parse_date(text):
    """An episode's day, in RSS's form or ISO's; one that names no zone is in UTC."""
    text = (text or "").strip()
    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        try:
            when = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return int(when.timestamp())


def parse_feed(data, url):
    """A show and its newest episodes, from its RSS feed. An episode is what has a file."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise PodcastError(f"not a feed: {e}") from None
    channel = _child(root, "channel")
    if channel is None:
        raise PodcastError("not a podcast feed")
    image = _child(channel, "image", itunes=True)
    art = (image.get("href") or "").strip() if image is not None else ""
    art = art or _text(_child(_child(channel, "image"), "url"))
    author = (_text(_child(channel, "author", itunes=True))
              or _text(_child(_child(channel, "owner", itunes=True), "name", itunes=True)))
    # Only a feed on the device itself may point at files on it.
    schemes = ("http://", "https://") + (("file://",) if url.startswith("file://") else ())
    episodes = []
    for item in (el for el in channel if _split(el.tag)[1] == "item"):
        enclosure = _child(item, "enclosure")
        address = (enclosure.get("url") or "").strip() if enclosure is not None else ""
        if not address.startswith(schemes):
            continue
        guid = _text(_child(item, "guid")) or address
        episodes.append({
            "id": _id(url, guid),
            "title": _text(_child(item, "title")) or _text(_child(item, "title", itunes=True)) or "Episode",
            "date": parse_date(_text(_child(item, "pubDate"))),
            "duration": parse_duration(_text(_child(item, "duration", itunes=True))),
            "url": address,
            "type": (enclosure.get("type") or "").strip().lower(),
            "size": _int(enclosure.get("length")),
        })
    episodes.sort(key=lambda e: -e["date"])
    return {"url": url, "title": _text(_child(channel, "title")) or url, "author": author, "image": art,
            "episodes": episodes[:KEEP]}


# subscriptions ---------------------------------------------------------------

def read_opml(path):
    """The shows an OPML file lists, as any podcast app writes one."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    shows, seen = [], set()
    for el in root.iter():
        attrs = {k.lower(): v for k, v in el.attrib.items()}
        url = (attrs.get("xmlurl") or "").strip()
        if _split(el.tag)[1] != "outline" or not url or url in seen:
            continue
        seen.add(url)
        shows.append({"id": show_id(url), "url": url, "title": (attrs.get("title") or attrs.get("text") or "").strip(),
                      "image": (attrs.get("imageurl") or "").strip()})
    return shows


def write_opml(path, shows):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<opml version="2.0">', "  <head><title>isoPod</title></head>",
             "  <body>"]
    for s in shows:
        attrs = f'type="rss" text={quoteattr(s["title"])} title={quoteattr(s["title"])} xmlUrl={quoteattr(s["url"])}'
        if s.get("image"):
            attrs += f" imageUrl={quoteattr(s['image'])}"
        lines.append(f"    <outline {attrs}/>")
    lines += ["  </body>", "</opml>", ""]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(path + ".tmp", path)


# Apple's directory -----------------------------------------------------------

def _apple(get, path, **params):
    url = APPLE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        return json.loads(get(url, limit=4 << 20))
    except ValueError:
        raise PodcastError("not JSON") from None


def _show_from_apple(raw):
    url = (raw.get("feedUrl") or "").strip()
    if not url:
        return None
    return {"id": show_id(url), "url": url, "title": (raw.get("collectionName") or "").strip() or url,
            "author": (raw.get("artistName") or "").strip(),
            "image": raw.get("artworkUrl600") or raw.get("artworkUrl100") or ""}


def apple_search(term, store, get=fetch):
    raw = _apple(get, "/search", term=term, media="podcast", entity="podcast", limit=50, country=store)
    return [s for s in map(_show_from_apple, raw.get("results", [])) if s]


def apple_top(store, limit=100, get=fetch):
    """The store's chart, which names its shows; their feeds are looked up after."""
    entries = _apple(get, f"/{store}/rss/toppodcasts/limit={limit}/json").get("feed", {}).get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    ids = [i for i in (e.get("id", {}).get("attributes", {}).get("im:id") for e in entries) if i]
    if not ids:
        return []
    found = _apple(get, "/lookup", id=",".join(ids), entity="podcast", country=store)
    by_id = {str(r.get("collectionId")): r for r in found.get("results", [])}
    return [s for s in (_show_from_apple(by_id[i]) for i in ids if i in by_id) if s]


def _name(text, limit=80):
    """A name a file can have on any card."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", text or "")
    return " ".join(text.split()).strip(" .")[:limit].strip() or "Episode"


def _ext(episode):
    ext = os.path.splitext(urllib.parse.urlsplit(episode["url"]).path)[1].lower()
    return ext if ext in EXTS else TYPES.get(episode.get("type", ""), ".mp3")


class Episode(Spoken):
    kind = "podcast"

    def __init__(self, podcasts, show, episode, uri):
        super().__init__(episode["id"], [uri], episode["title"], show.get("title", ""), podcasts.art(show))
        self.podcasts = podcasts
        self.show = show
        self.episode = episode

    def heard(self, index, elapsed, duration, force=False):
        self.podcasts.heard(self.episode["id"], elapsed, duration or self.episode["duration"], force)


class Podcasts:
    """Shows from their own feeds, found through Apple's directory. What is subscribed to is
    an OPML file in the podcasts folder, beside the episodes that were downloaded, so both
    come and go over the cable. A show's episodes are kept on the card once they are had,
    and asked for again behind what is shown when they are an hour old. Nothing is
    downloaded unless it is asked for."""

    def __init__(self, folder, config_dir, cache_dir, lang=lambda: "ko", fetch=fetch, download=download,
                 shrink=shrink):
        self.folder = folder
        self.opml = os.path.join(folder, SUBSCRIPTIONS)
        self.state_path = os.path.join(config_dir, "podcasts.json")
        self.feed_dir = os.path.join(cache_dir, "podcasts", "feeds")
        self.art_dir = os.path.join(cache_dir, "podcasts", "art")
        self.lang = lang
        self.changed = lambda: None
        self.failed = lambda episode: None
        self._fetch = fetch
        self._download = download
        self._shrink = shrink
        data = load_json(self.state_path, {})
        self.played = data.get("episodes", {})
        self.added = data.get("added", {})
        self.downloads = data.get("downloads", {})
        self.last = data.get("last")
        self._saved_at = 0.0
        self._subs, self._subs_mtime = [], None
        self._feeds = {}
        self._known = {}
        self._loading = {}
        self._tried = {}
        self._found = {}
        self._top = None
        self._art_fetching = set()
        self._renewing = asyncio.Semaphore(2)
        self.progress = {}
        self._queue = []
        self._cancelled = set()
        self._current = None
        self._worker = None

    def _save(self):
        self._saved_at = time.monotonic()
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
            fresh = [s for s in self._subs if s["id"] not in self.added]
            for s in fresh:
                self.added[s["id"]] = int(time.time())
            if fresh:
                self._save()
        for s in self._subs:
            self._known.setdefault(s["id"], s)
        return self._subs

    def is_subscribed(self, sid):
        return any(s["id"] == sid for s in self.subscriptions())

    def subscribe(self, show, on):
        shows = [s for s in self.subscriptions() if s["id"] != show["id"]]
        if on:
            feed = self._feeds.get(show["id"]) or {}
            shows.insert(0, {"id": show["id"], "url": show["url"], "title": show.get("title") or feed.get("title", ""),
                             "image": show.get("image") or feed.get("image", "")})
            self.added[show["id"]] = int(time.time())
            self._save()
        write_opml(self.opml, shows)
        self.subscriptions()

    def show(self, sid):
        """A show met in a list or a search, a subscribed one, or one whose episodes are kept."""
        self.subscriptions()
        if sid not in self._known:
            feed = self._cached(sid)
            if feed:
                self._known[sid] = {"id": sid, "url": feed["url"], "title": feed["title"], "image": feed["image"]}
        return self._known.get(sid)

    def _meet(self, shows):
        for s in shows:
            self._known.setdefault(s["id"], s)
        return shows

    def meet(self, show):
        """A show known from an episode of it, so its page can be opened."""
        self._known.setdefault(show["id"], show)

    def title(self, show):
        return (self._cached(show["id"]) or {}).get("title") or show.get("title") or ""

    def author(self, show):
        return show.get("author") or (self._cached(show["id"]) or {}).get("author", "")

    async def search(self, term):
        key = " ".join(term.casefold().split())
        if key not in self._found:
            found = await asyncio.to_thread(apple_search, term, STORES.get(self.lang(), "us"), get=self._fetch)
            if len(self._found) >= 20:
                del self._found[next(iter(self._found))]
            self._found[key] = found
        return self._meet(self._found[key])

    async def top(self):
        """The chart of the store for isoPod's language, kept a day and fetched again behind
        what is shown once it is older."""
        store = STORES.get(self.lang(), "us")
        path = os.path.join(self.feed_dir, f"top-{store}.json")
        if self._top is None or self._top[0] != store:
            self._top = (store, load_json(path, None))
        shows = self._top[1]
        if shows is None:
            shows = await self._chart(store, path)
        elif _age(path) > TOP_LIFE and self._due("top"):
            spawn(self._renew_chart(store, path))
        return self._meet(shows)

    async def _chart(self, store, path):
        shows = await asyncio.to_thread(apple_top, store, get=self._fetch)
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

    def _feed_file(self, sid):
        return os.path.join(self.feed_dir, sid + ".json")

    def _cached(self, sid):
        if sid not in self._feeds:
            self._feeds[sid] = load_json(self._feed_file(sid), None)
        return self._feeds[sid]

    async def _keep(self, show):
        data = await asyncio.to_thread(self._fetch, show["url"])
        feed = await asyncio.to_thread(parse_feed, data, show["url"])
        save_json(self._feed_file(show["id"]), feed)
        self._feeds[show["id"]] = feed
        return feed

    def _fetched(self, show):
        """One fetch of a feed serves all who wait for it."""
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

    async def feed(self, show):
        """A show's episodes: kept ones at once, fetched again behind them once they have aged."""
        feed = self._cached(show["id"])
        if feed is None:
            return await self._fetched(show)
        if _age(self._feed_file(show["id"])) > FEED_LIFE and self._due(show["id"]):
            spawn(self._renew(show))
        return feed

    async def _renew(self, show):
        async with self._renewing:
            try:
                await self._fetched(show)
            except PodcastError as e:
                log.info("%s kept as it was: %s", show["url"], e)
                return
        self.changed()

    def renew_subscriptions(self):
        """Opening the list of shows asks each one that has aged for what is new in it."""
        for show in self.subscriptions():
            if _age(self._feed_file(show["id"])) > FEED_LIFE and self._due(show["id"]):
                spawn(self._renew(show))

    def episodes(self, show):
        return (self._cached(show["id"]) or {}).get("episodes", [])

    def latest(self, show):
        episodes = self.episodes(show)
        return episodes[0]["date"] if episodes else 0

    def is_new(self, show, episode):
        """Not heard nor begun, and out since a little before the show was subscribed to."""
        added = self.added.get(show["id"])
        return (added is not None and episode["id"] not in self.played and episode["date"] >= added - NEW
                and self.is_subscribed(show["id"]))

    def has_new(self, show):
        return any(self.is_new(show, e) for e in self.episodes(show)[:20])

    def place(self, eid):
        """(seconds in, heard to the end)."""
        mark = self.played.get(eid) or {}
        return mark.get("pos", 0), bool(mark.get("done"))

    def set_heard(self, eid, done):
        if done:
            self.played[eid] = {"pos": 0, "done": True}
        else:
            self.played.pop(eid, None)
        self._save()

    def heard(self, eid, elapsed, duration, force=False):
        if duration > 0 and elapsed >= duration - min(DONE_LEFT, duration / 10):
            mark = {"pos": 0, "done": True}
        elif elapsed >= 5:
            mark = {"pos": int(elapsed), "done": False}
        else:
            return
        if self.played.get(eid) != mark:
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
        """One download at a time, in the order asked for, the page told how far each is."""
        try:
            while self._queue:
                show, episode = self._queue.pop(0)
                eid = self._current = episode["id"]
                job = asyncio.ensure_future(asyncio.to_thread(self._save_episode, show, episode))
                while not job.done():
                    await asyncio.wait([job], timeout=1)
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
        rel = os.path.join(_name(self.title(show) or show.get("title")), _name(when + episode["title"]) + _ext(episode))
        path = os.path.join(self.folder, rel)
        if os.path.exists(path):
            base, ext = os.path.splitext(rel)
            rel = f"{base} {eid[:6]}{ext}"
            path = os.path.join(self.folder, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        request = urllib.request.Request(episode["url"], headers={"User-Agent": USER_AGENT})
        part = path + ".part"
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(part, "wb") as out:
                total = _int(response.headers.get("Content-Length")) or episode.get("size") or 0
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
        return show.get("image") or (self._cached(show["id"]) or {}).get("image", "")

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
            had = await asyncio.to_thread(self._save_art, self._art_url(show), base)
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
