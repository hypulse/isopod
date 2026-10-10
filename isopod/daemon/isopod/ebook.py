"""E-book helpers: Word Wise hints written into a copy of an EPUB, and a small EPUB writer."""
import html
import os
import re
import uuid
import zipfile

SKIP_TAGS = {"head", "title", "script", "style", "ruby", "rt", "rp", "code", "pre", "h1", "h2", "h3",
             "h4", "h5", "h6", "a", "sup", "sub"}
TAG = re.compile(r"(<[^>]+>)")
WORD = re.compile(r"(?<![&#\w])[A-Za-z][a-z']*[a-z](?![\w;])")
TAG_NAME = re.compile(r"</?\s*([A-Za-z0-9]+)")
HEAD_END = re.compile(r"</head\s*>", re.I)
# readers draw ruby at under half size, too small to read at 640x480
HINT_STYLE = "<style>rt { font-size: 62%; line-height: 1.25; color: #5a5a5a; }</style>"


class Glossary:
    """word -> (hint, zipf x 10), loaded from a TSV built by tools/wordwise/build.py, with the
    common words beside it so that "brother" is never read as "broth" plus a suffix."""

    def __init__(self, path):
        self.hints = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3:
                    self.hints[parts[0]] = (parts[1], int(parts[2]))
        self.common = set()
        try:
            with open(os.path.join(os.path.dirname(path), "common-en.txt"), encoding="utf-8") as f:
                self.common = {line.strip() for line in f}
        except OSError:
            pass

    def lookup(self, word, rarer_than):
        """The hint for a word or its base form, if the word is rare enough."""
        word = word.lower()
        for i, form in enumerate(base_forms(word)):
            if i == 1 and word in self.common:
                return None
            hit = self.hints.get(form)
            if hit:
                return hit[0] if hit[1] < rarer_than else None
        return None


def base_forms(w):
    """The word itself, then likely bases of an inflection, most specific first."""
    forms = [w]
    if w.endswith("ies") and len(w) > 4:
        forms.append(w[:-3] + "y")
    if w.endswith("ied") and len(w) > 4:
        forms.append(w[:-3] + "y")
    if w.endswith("ily") and len(w) > 4:
        forms.append(w[:-3] + "y")
    for suffix in ("ing", "ed", "es", "er", "est", "ly", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            stem = w[:-len(suffix)]
            if suffix in ("ing", "ed", "er", "est"):
                forms.append(stem + "e")  # soothed is soothe before it is sooth
            forms.append(stem)
            if suffix in ("ing", "ed", "er", "est") and len(stem) > 3 and stem[-1] == stem[-2]:
                forms.append(stem[:-1])
    return forms


def annotate_html(text, glossary, rarer_than, seen):
    """Wrap the first use of each hard word in <ruby> with its hint above it."""
    out, skipping = [], []
    for part in TAG.split(text):
        if part.startswith("<"):
            out.append(part)
            m = TAG_NAME.match(part)
            if m and not part.endswith("/>") and not part.startswith("<!") and not part.startswith("<?"):
                name = m.group(1).lower()
                if name in SKIP_TAGS:
                    if part.startswith("</"):
                        if name in skipping:
                            skipping.remove(name)
                    else:
                        skipping.append(name)
            continue
        if skipping or not part.strip():
            out.append(part)
            continue

        def replace(m):
            # names never reach the glossaries, so a capital is only the start of a sentence
            word = m.group(0)
            key = word.lower()
            if key in seen:
                return word
            hint = glossary.lookup(key, rarer_than)
            if not hint:
                return word
            seen.add(key)
            return f"<ruby>{word}<rt>{html.escape(hint, quote=False)}</rt></ruby>"
        out.append(WORD.sub(replace, part))
    return "".join(out)


def annotate_epub(src, dst, glossary, rarer_than=35):
    """Copy an EPUB, adding Word Wise hints to its text documents. Returns the hint count."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    count = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename.lower()
            if name.endswith((".xhtml", ".html", ".htm")) and not name.endswith(("nav.xhtml", "toc.xhtml")):
                text = data.decode("utf-8", errors="replace")
                seen = set()
                text = annotate_html(text, glossary, rarer_than, seen)
                text = HEAD_END.sub(lambda m: HINT_STYLE + m.group(0), text, count=1)
                count += len(seen)
                data = text.encode("utf-8")
            zout.writestr(info, data, compress_type=info.compress_type)
    os.replace(tmp, dst)
    return count


# the reading position, shared by a book and its Word Wise copies -----------------

XPOINTER = re.compile(r'(\["last_xpointer"\] = ")((?:[^"\\]|\\.)*)(")')
PERCENT = re.compile(r'(\["percent_finished"\] = )([-+.0-9eE]+)')
DOM_VERSION = re.compile(r'\["cre_dom_version"\] = (\d+)')
LAYOUT = re.compile(r'^    \["copt_[a-z_]+"\] = [^{\n]*,$', re.M)


def sidecar(path):
    """Where KOReader keeps a book's reading state."""
    base, ext = os.path.splitext(path)
    return os.path.join(f"{base}.sdr", f"metadata{ext}.lua")


def _element(xpointer):
    """Hints split text nodes, so a position is kept to its enclosing element."""
    return re.sub(r"/(ruby|rt|text\(\)).*$", "", xpointer)


def settle_position(book):
    """A rebuilt copy has new text nodes: keep its reader's page, not the character."""
    path = sidecar(book)
    try:
        with open(path, encoding="utf-8") as f:
            state = f.read()
    except OSError:
        return
    with open(path + ".part", "w", encoding="utf-8") as f:
        f.write(XPOINTER.sub(lambda m: m.group(1) + _element(m.group(2)) + m.group(3), state, count=1))
    os.replace(path + ".part", path)


def _mtime(path):
    try:
        return os.path.getmtime(sidecar(path))
    except OSError:
        return None


def follow_position(versions, target):
    """Open `target` where any version of the same book was read last."""
    read = [v for v in versions if _mtime(v) is not None]
    newest = max(read, key=_mtime, default=None)
    if newest is None or newest == target or (_mtime(target) or 0) >= _mtime(newest):
        return False
    try:
        with open(sidecar(newest), encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return False
    xp, dom = XPOINTER.search(source), DOM_VERSION.search(source)
    if not xp or not dom:
        return False
    point = _element(xp.group(2))
    percent = PERCENT.search(source)
    path = sidecar(target)
    try:
        with open(path, encoding="utf-8") as f:
            state = f.read()
    except OSError:
        state = None
    if state and XPOINTER.search(state):
        state = XPOINTER.sub(lambda m: m.group(1) + point + m.group(3), state, count=1)
        if percent:
            state = PERCENT.sub(lambda m: m.group(1) + percent.group(2), state, count=1)
    else:
        # The book's layout comes along; without a rendering mode KOReader treats a book read
        # before as legacy, and legacy layout cannot draw ruby.
        layout = LAYOUT.findall(source)
        if not any('"copt_block_rendering_mode"' in line for line in layout):
            layout.append('    ["copt_block_rendering_mode"] = 3,')
        state = "\n".join(["return {", f'    ["cre_dom_version"] = {dom.group(1)},', *layout,
                           f'    ["last_xpointer"] = "{point}",',
                           *([f'    ["percent_finished"] = {percent.group(2)},'] if percent else []), "}\n"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "w", encoding="utf-8") as f:
        f.write(state)
    os.replace(path + ".part", path)
    return True


# a small EPUB 3 writer ---------------------------------------------------------

def _xhtml(title, body, lang):
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{lang}" lang="{lang}">'
            f"<head><title>{html.escape(title)}</title></head><body>{body}</body></html>")


def write_epub(path, title, chapters, lang="ko", author="isoPod"):
    """chapters: [(title, body_html)] in reading order. Written atomically."""
    book_id = f"urn:uuid:{uuid.uuid4()}"
    items, spine, nav = [], [], []
    for i, (ctitle, _) in enumerate(chapters):
        items.append(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')
        nav.append(f'<li><a href="c{i}.xhtml">{html.escape(ctitle)}</a></li>')
    opf = (f'<?xml version="1.0" encoding="utf-8"?>\n'
           f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id" xml:lang="{lang}">'
           f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="id">{book_id}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
           f'<dc:creator>{html.escape(author)}</dc:creator><dc:language>{lang}</dc:language>'
           f'<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta></metadata>'
           f'<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           f'{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>')
    nav_doc = _xhtml(title, f'<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
                            f'<h1>{html.escape(title)}</h1><ol>{"".join(nav)}</ol></nav>', lang)
    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav_doc, compress_type=zipfile.ZIP_DEFLATED)
        for i, (ctitle, body) in enumerate(chapters):
            z.writestr(f"OEBPS/c{i}.xhtml", _xhtml(ctitle, body, lang), compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, path)
