import hashlib
import logging
import os
import re
import struct
import time

from .mpd import MPDError
from .music import Spoken, local
from .util import load_json, save_json, sort_key

log = logging.getLogger("isopod.audiobooks")

AUDIO_EXTS = (".m4b", ".m4a", ".mp4", ".mp3", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".wma")
MP4_EXTS = (".m4b", ".m4a", ".mp4")
COVER_NAMES = ("cover", "folder", "front", "poster")
PICTURE_EXTS = (".jpg", ".jpeg", ".png")
# The folders one book is split into: CD 1, Disc 2, Part 3, 1부.
PARTS = re.compile(r"^\s*(cd|disc|disk|part|파트)?\s*\d+\s*(부)?\s*$", re.IGNORECASE)
SAVE_EVERY = 15
DONE_LEFT = 30


def natural_key(name):
    """Chapter 2 before Chapter 10."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, sort_key(p)) for p in re.split(r"(\d+)", name) if p]


def _strip_ext(name):
    return os.path.splitext(name)[0]


# MP4 chapters ------------------------------------------------------------------

def _boxes(f, start, end):
    """(type, offset of the body, end) of each box between two offsets."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        body = pos + 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            body += 8
        elif size == 0:
            size = end - pos
        if size < body - pos:
            return
        yield kind.decode("latin-1"), body, min(pos + size, end)
        pos += size


def _box(f, start, end, *path):
    for kind, body, stop in _boxes(f, start, end):
        if kind == path[0]:
            return (body, stop) if len(path) == 1 else _box(f, body, stop, *path[1:])
    return None


def _nero(f, at):
    """Nero's chapter list, which FFmpeg and most tools write: 100 ns starts and titles."""
    body, stop = at
    f.seek(body)
    data = f.read(min(stop - body, 1 << 20))
    if len(data) < 9:
        return []
    pos = 4 + (4 if data[0] else 0)
    count, pos = data[pos], pos + 1
    found = []
    for _ in range(count):
        if pos + 9 > len(data):
            break
        start, length = struct.unpack(">QB", data[pos:pos + 9])
        title = data[pos + 9:pos + 9 + length].decode("utf-8", "replace")
        found.append((start / 1e7, title))
        pos += 9 + length
    return found


def _table(f, at, fmt, skip=4):
    """The entries of a sample table box, after its version, flags and count."""
    body, stop = at
    f.seek(body + skip)
    count = struct.unpack(">I", f.read(4))[0]
    size = struct.calcsize(fmt)
    data = f.read(min(count * size, stop - body))
    return [struct.unpack(fmt, data[i:i + size]) for i in range(0, len(data) - size + 1, size)]


def _text_track(f, trak):
    """The titles of a QuickTime chapter track, which iTunes writes, and when each starts."""
    mdhd = _box(f, *trak, "mdia", "mdhd")
    stbl = _box(f, *trak, "mdia", "minf", "stbl")
    if not mdhd or not stbl:
        return []
    f.seek(mdhd[0])
    version = f.read(1)[0]
    f.seek(mdhd[0] + (20 if version else 12))
    scale = struct.unpack(">I", f.read(4))[0] or 1
    stts, stsz, stsc = (_box(f, *stbl, name) for name in ("stts", "stsz", "stsc"))
    stco, co64 = _box(f, *stbl, "stco"), _box(f, *stbl, "co64")
    if not (stts and stsz and stsc and (stco or co64)):
        return []
    starts, t = [], 0
    for count, delta in _table(f, stts, ">II"):
        for _ in range(min(count, 10000 - len(starts))):
            starts.append(t / scale)
            t += delta
    f.seek(stsz[0] + 4)
    fixed, count = struct.unpack(">II", f.read(8))
    sizes = [fixed] * count if fixed else [s for (s,) in _table(f, stsz, ">I", skip=8)]
    chunks = [c for (c,) in (_table(f, stco, ">I") if stco else _table(f, co64, ">Q"))]
    runs = _table(f, stsc, ">III")
    offsets = []
    for i, (first, per, _) in enumerate(runs):
        last = runs[i + 1][0] - 1 if i + 1 < len(runs) else len(chunks)
        for chunk in range(first - 1, min(last, len(chunks))):
            at = chunks[chunk]
            for _ in range(per):
                if len(offsets) >= len(sizes):
                    break
                offsets.append(at)
                at += sizes[len(offsets) - 1]
    found = []
    for start, at, size in zip(starts, offsets, sizes):
        f.seek(at)
        sample = f.read(min(size, 1024))
        length = struct.unpack(">H", sample[:2])[0] if len(sample) >= 2 else 0
        text = sample[2:2 + length]
        title = text.decode("utf-16") if text[:2] in (b"\xfe\xff", b"\xff\xfe") else text.decode("utf-8", "replace")
        found.append((start, title))
    return found


def mp4_chapters(path):
    """[(start in seconds, title)] of an MP4 audiobook, [] if it names no chapters."""
    try:
        with open(path, "rb") as f:
            end = os.fstat(f.fileno()).st_size
            moov = _box(f, 0, end, "moov")
            if not moov:
                return []
            chpl = _box(f, *moov, "udta", "chpl")
            found = _nero(f, chpl) if chpl else []
            if not found:
                tracks = {}
                wanted = None
                for kind, body, stop in _boxes(f, *moov):
                    if kind != "trak":
                        continue
                    tkhd = _box(f, body, stop, "tkhd")
                    if tkhd:
                        f.seek(tkhd[0])
                        version = f.read(1)[0]
                        f.seek(tkhd[0] + (20 if version else 12))
                        tracks[struct.unpack(">I", f.read(4))[0]] = (body, stop)
                    chap = _box(f, body, stop, "tref", "chap")
                    if chap and chap[1] - chap[0] >= 4:
                        f.seek(chap[0])
                        wanted = struct.unpack(">I", f.read(4))[0]
                if wanted in tracks:
                    found = _text_track(f, tracks[wanted])
    except (OSError, struct.error, IndexError, ValueError) as e:
        log.info("no chapters read from %s: %s", path, e)
        return []
    found = sorted((max(0.0, start), title.strip()) for start, title in found)
    return found if len(found) > 1 else []


# the library ------------------------------------------------------------------

def _audio(names):
    return [n for n in names if n.lower().endswith(AUDIO_EXTS) and not n.startswith(".")]


def _listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def _is_dir(path):
    try:
        return os.path.isdir(path)
    except OSError:
        return False


class Book(Spoken):
    kind = "audiobook"

    def __init__(self, books, key, title, files, chapters, author="", art=None):
        super().__init__(key, ["file://" + f for f in files], title, author, art)
        self.books = books
        self.files = files
        self.chapters = chapters  # (file index, start in that file, title)

    def chapter(self, index, elapsed):
        """Which chapter that point is in."""
        found = 0
        for i, (file, start, _) in enumerate(self.chapters):
            if (file, start) <= (index, elapsed + 0.5):
                found = i
        return found

    def album(self, index, elapsed):
        return self.chapters[self.chapter(index or 0, elapsed)][2] if len(self.chapters) > 1 else ""

    def heard(self, index, elapsed, duration, force=False):
        self.books.heard(self, index or 0, elapsed, duration, force)


class Audiobooks:
    """Books from the audiobooks folder: a folder of files, one for each part or chapter, or
    one file, whose chapters are the ones it names inside. Each book keeps its place."""

    def __init__(self, folder, config_dir, cache_dir, mpd=None):
        self.folder = folder
        self.mpd = mpd
        self.state_path = os.path.join(config_dir, "audiobooks.json")
        self.info_path = os.path.join(cache_dir, "audiobooks", "info.json")
        self.art_dir = os.path.join(cache_dir, "audiobooks", "art")
        self.places = load_json(self.state_path, {})
        self.info = load_json(self.info_path, {})
        self.changed = lambda: None
        self._saved_at = 0.0
        self._no_art = set()
        self._measuring = set()
        self._art_busy = False

    def _path(self, rel):
        root = os.path.normpath(self.folder)
        path = os.path.normpath(os.path.join(root, rel))
        return path if path == root or path.startswith(root + os.sep) else root

    def _rel(self, path):
        return os.path.relpath(path, self.folder)

    def _book_folder(self, path):
        """A folder of a book's files, or of the parts it is split into. An M4B is a whole
        book, so a folder of them is a shelf: an author's, say."""
        names = _listdir(path)
        audio = _audio(names)
        if audio:
            return not all(n.lower().endswith(".m4b") for n in audio)
        parts = [n for n in names if _is_dir(os.path.join(path, n)) and not n.startswith(".")]
        return bool(parts) and all(PARTS.match(n) and _audio(_listdir(os.path.join(path, n))) for n in parts)

    def listing(self, rel=""):
        """(folders, books) in a folder of the library: a book by its path from the library."""
        path = self._path(rel)
        rel = os.path.relpath(path, self._path(""))
        rel = "" if rel == "." else rel
        folders, books = [], []
        for name in _listdir(path):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if _is_dir(full):
                (books if self._book_folder(full) else folders).append(name)
            elif name.lower().endswith(AUDIO_EXTS):
                books.append(name)
        folders.sort(key=natural_key)
        books.sort(key=lambda n: natural_key(_strip_ext(n) if not _is_dir(os.path.join(path, n)) else n))
        return folders, [os.path.normpath(os.path.join(rel, b)) for b in books]

    def folder_art(self, rel):
        """A folder of books shows the cover of the first of them that has one."""
        return next(filter(None, map(self.art, self.listing(rel)[1])), None)

    def files(self, key):
        path = self._path(key)
        if not _is_dir(path):
            return [path] if os.path.exists(path) else []
        names = _listdir(path)
        found = [os.path.join(path, n) for n in sorted(_audio(names), key=lambda n: natural_key(_strip_ext(n)))]
        for part in sorted((n for n in names if _is_dir(os.path.join(path, n)) and PARTS.match(n)), key=natural_key):
            folder = os.path.join(path, part)
            found += [os.path.join(folder, n) for n in sorted(_audio(_listdir(folder)),
                                                               key=lambda n: natural_key(_strip_ext(n)))]
        return found

    def title(self, key):
        name = os.path.basename(key)
        return name if _is_dir(self._path(key)) else _strip_ext(name)

    def chapters(self, key, files=None):
        """(file index, start in it, title): a file's own chapters, else one for each file."""
        files = files if files is not None else self.files(key)
        found = []
        for i, file in enumerate(files):
            inside = self._file_info(file).get("chapters") or []
            if inside:
                found += [(i, start, title or f"{len(found) + 1}") for start, title in inside]
            else:
                found.append((i, 0.0, self._file_info(file).get("title") or _strip_ext(os.path.basename(file))))
        return found

    def book(self, key):
        files = self.files(key)
        if not files:
            return None
        info = self._file_info(files[0])
        return Book(self, key, self.title(key), files, self.chapters(key, files), info.get("artist", ""),
                    self.art(key))

    def identify(self, uri):
        """A book MPD is found playing after a restart."""
        path = local(uri)
        if not path.startswith(os.path.normpath(self.folder) + os.sep):
            return None
        rel = self._rel(path)
        parts = rel.split(os.sep)
        for depth in range(1, len(parts) + 1):
            key = os.path.join(*parts[:depth])
            if (depth == len(parts) or self._book_folder(self._path(key))) and path in self.files(key):
                return self.book(key)
        return None

    # what is kept of each file ------------------------------------------------

    def _file_info(self, file):
        """Its length, its tags and its chapters, kept until the file changes."""
        try:
            st = os.stat(file)
        except OSError:
            return {}
        stamp = [st.st_mtime_ns, st.st_size]
        info = self.info.get(file)
        if not info or info.get("stamp") != stamp:
            info = {"stamp": stamp, "chapters": mp4_chapters(file) if file.lower().endswith(MP4_EXTS) else []}
            self.info[file] = info
        return info

    def measured(self, key):
        """Every file of the book has its length."""
        return all("duration" in self._file_info(f) for f in self.files(key))

    async def measure(self, keys):
        """The lengths and tags MPD reads from files outside the music folder, for those of
        the books that lack them. Tells when it has some."""
        todo = [f for k in keys for f in self.files(k) if "duration" not in self._file_info(f)
                and f not in self._measuring]
        if not todo or not self.mpd:
            return
        self._measuring.update(todo)
        try:
            for file in todo:
                info = self._file_info(file)
                try:
                    pairs = await self.mpd.call("lsinfo", "file://" + file)
                except MPDError as e:
                    log.info("no tags from %s: %s", file, e)
                    continue
                tags = {k.lower(): v for k, v in pairs}
                info.update(duration=_float(tags.get("duration") or tags.get("time")),
                            title=tags.get("title", ""), artist=tags.get("albumartist") or tags.get("artist", ""))
            save_json(self.info_path, self.info)
        finally:
            self._measuring.difference_update(todo)
        self.changed()

    def durations(self, key):
        return [self._file_info(f).get("duration", 0.0) for f in self.files(key)]

    def left(self, key):
        """Seconds of the book still to hear, None while its files are not all measured."""
        if not self.measured(key):
            return None
        lengths = self.durations(key)
        place = self.places.get(key) or {}
        if place.get("done"):
            return 0.0
        index = min(place.get("file", 0), len(lengths) - 1)
        return max(0.0, sum(lengths[index:]) - place.get("pos", 0))

    def total(self, key):
        return sum(self.durations(key)) if self.measured(key) else None

    # where each book is -------------------------------------------------------

    def place(self, key):
        """(file index, seconds in, heard to the end)."""
        place = self.places.get(key) or {}
        return place.get("file", 0), place.get("pos", 0), bool(place.get("done"))

    def started(self, key):
        return key in self.places

    def set_heard(self, key, done):
        if done:
            self.places[key] = {"file": 0, "pos": 0, "done": True}
        else:
            self.places.pop(key, None)
        self._save()

    def heard(self, book, index, elapsed, duration, force=False):
        last = index == len(book.files) - 1
        if last and duration > 0 and elapsed >= duration - min(DONE_LEFT, duration / 10):
            mark = {"file": 0, "pos": 0, "done": True}
        elif index or elapsed >= 5:
            mark = {"file": index, "pos": int(elapsed), "done": False}
        else:
            return
        if self.places.get(book.key) != mark:
            self.places[book.key] = mark
            if force or mark["done"] or time.monotonic() - self._saved_at > SAVE_EVERY:
                self._save()

    def _save(self):
        self._saved_at = time.monotonic()
        save_json(self.state_path, self.places)

    def resume(self, key):
        """Where to start: a little before the place it was left, the beginning once heard."""
        index, pos, done = self.place(key)
        return (0, 0.0) if done else (index, max(0.0, pos - 2))

    # covers -------------------------------------------------------------------

    def _art_file(self, key):
        return os.path.join(self.art_dir, hashlib.sha1(key.encode()).hexdigest()[:16])

    def art(self, key):
        """A cover beside the files, else the picture inside the first one."""
        path = self._path(key)
        folder = path if _is_dir(path) else os.path.dirname(path)
        names = _listdir(folder)
        pictures = {n.lower(): n for n in names if n.lower().endswith(PICTURE_EXTS) and not n.startswith(".")}
        if _is_dir(path):
            wanted = [n + e for n in COVER_NAMES for e in PICTURE_EXTS] + sorted(pictures)
        else:
            wanted = [_strip_ext(os.path.basename(path)).lower() + e for e in PICTURE_EXTS]
        for name in wanted:
            if name in pictures:
                return os.path.join(folder, pictures[name])
        base = self._art_file(key)
        for ext in (".jpg", ".png"):
            if os.path.exists(base + ext):
                return base + ext
        return None

    async def fetch_art(self, keys):
        """The pictures inside the books that have no cover beside them."""
        if self._art_busy or not self.mpd:
            return
        self._art_busy = True
        found = False
        try:
            for key in keys:
                if self.art(key) or key in self._no_art:
                    continue
                files = self.files(key)
                picture = await self.mpd.picture("file://" + files[0]) if files else None
                if not picture:
                    self._no_art.add(key)
                    continue
                data, mime = picture
                path = self._art_file(key) + (".png" if "png" in mime else ".jpg")
                os.makedirs(self.art_dir, exist_ok=True)
                with open(path + ".part", "wb") as f:
                    f.write(data)
                os.replace(path + ".part", path)
                found = True
        finally:
            self._art_busy = False
        if found:
            self.changed()


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
