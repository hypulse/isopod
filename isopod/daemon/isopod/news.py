"""The daily paper: RSS and Atom feeds fetched over Wi-Fi into one EPUB per day."""
import concurrent.futures
import datetime
import html
import logging
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

from .ebook import write_epub

log = logging.getLogger("isopod.news")

DEFAULT_FEEDS = [
    ("연합뉴스", "https://www.yna.co.kr/rss/news.xml"),
    ("BBC 코리아", "https://feeds.bbci.co.uk/korean/rss.xml"),
    ("한겨레", "https://www.hani.co.kr/rss/"),
    ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
]
PER_FEED = 8
KEEP = 7
USER_AGENT = "isopod/0.1 (+https://github.com/hypulse/isopod)"
NS = {"atom": "http://www.w3.org/2005/Atom", "content": "http://purl.org/rss/1.0/modules/content/"}


def fetch(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_feeds(path):
    """One feed per line, "Name | URL"; the defaults until the file exists."""
    try:
        with open(path, encoding="utf-8") as f:
            feeds = []
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "|" in line:
                    name, url = (p.strip() for p in line.split("|", 1))
                    feeds.append((name, url))
            return feeds or DEFAULT_FEEDS
    except OSError:
        return DEFAULT_FEEDS


def parse_feed(data):
    """RSS 2.0 or Atom -> [{title, link, body}] with body as HTML (may be empty)."""
    root = ET.fromstring(data)
    items = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        for it in root.iter("item"):
            items.append({
                "title": (it.findtext("title") or "").strip(),
                "link": (it.findtext("link") or "").strip(),
                "body": it.findtext("content:encoded", namespaces=NS) or it.findtext("description") or "",
            })
    else:
        for it in root.iter(f"{{{NS['atom']}}}entry"):
            link = it.find("atom:link[@rel='alternate']", NS)
            if link is None:
                link = it.find("atom:link", NS)
            items.append({
                "title": (it.findtext("atom:title", namespaces=NS) or "").strip(),
                "link": link.get("href", "") if link is not None else "",
                "body": it.findtext("atom:content", namespaces=NS) or it.findtext("atom:summary", namespaces=NS) or "",
            })
    return [i for i in items if i["title"]]


class _Paragraphs(HTMLParser):
    """Collects paragraph text and remembers which container each paragraph sits in."""

    BLOCK_SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "figure", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.skip, self.buf, self.in_p = [], 0, [], False
        self.paragraphs, self.containers = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_SKIP:
            self.skip += 1
        if tag in ("div", "article", "section", "main", "td", "body"):
            self.containers += 1
            self.stack.append(self.containers)
        if tag == "p" and not self.skip:
            self.in_p, self.buf = True, []
        if tag == "br" and self.in_p:
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_SKIP and self.skip:
            self.skip -= 1
        if tag == "p" and self.in_p:
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if len(text) > 30:
                self.paragraphs.append((self.stack[-1] if self.stack else 0, text))
            self.in_p = False
        if tag in ("div", "article", "section", "main", "td", "body") and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.buf.append(data)


def extract_article(page):
    """The paragraphs of the container holding the most paragraph text."""
    parser = _Paragraphs()
    try:
        parser.feed(page)
    except Exception:
        return []
    groups = {}
    for key, text in parser.paragraphs:
        groups.setdefault(key, []).append(text)
    if not groups:
        return []
    best = max(groups.values(), key=lambda ps: sum(len(p) for p in ps))
    return best if sum(len(p) for p in best) > 300 else []


def text_of(fragment):
    return [p for p in (re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", chunk))).strip()
                        for chunk in re.split(r"</p>|<br\s*/?>|\n\s*\n", fragment or "")) if p]


def article(item, fetcher=fetch):
    """Paragraphs for one item: the feed's own text when it is substantial, else the page."""
    paragraphs = text_of(item["body"])
    if sum(len(p) for p in paragraphs) < 400 and item["link"]:
        try:
            page = fetcher(item["link"], timeout=8).decode("utf-8", errors="replace")
            paragraphs = extract_article(page) or paragraphs
        except Exception as e:
            log.info("article %s: %s", item["link"], e)
    return paragraphs


def build_edition(path, title, feeds, lang="ko", fetcher=fetch):
    """Fetch every feed and write the day's EPUB. Returns how many articles it holds."""
    chapters = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for name, url in feeds:
            try:
                items = parse_feed(fetcher(url))[:PER_FEED]
            except Exception as e:
                log.warning("feed %s: %s", url, e)
                continue
            for item, paragraphs in zip(items, pool.map(lambda it: article(it, fetcher), items)):
                if not paragraphs:
                    continue
                body = (f"<h1>{html.escape(item['title'])}</h1><p><small>{html.escape(name)}</small></p>"
                        + "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs))
                chapters.append((f"{name} · {item['title']}", body))
    if chapters:
        write_epub(path, title, chapters, lang=lang, author="isoPod")
    return len(chapters)


def editions(folder):
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".epub")]
    except OSError:
        return []
    return sorted(names, reverse=True)


def prune(folder, keep=KEEP):
    for name in editions(folder)[keep:]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def edition_name(day=None):
    return f"{(day or datetime.date.today()).isoformat()}.epub"
