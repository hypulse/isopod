import asyncio
import dataclasses
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from isopod.audio import Audio, FakeAudio, _sink  # noqa: E402
from isopod.battery import Battery  # noqa: E402
from isopod.bluetooth import A2DP_SINK, Backend, Bluetooth, Device, RavelBackend  # noqa: E402
from isopod.mpd import MPD, filter_expr, quote, records, values  # noqa: E402
from isopod.music import Music  # noqa: E402
from isopod.pages import BOOK_EXTS, folder_listing  # noqa: E402
from isopod.apps import AppSpec, Apps  # noqa: E402
from isopod import news  # noqa: E402
from isopod.ebook import (Glossary, annotate_epub, annotate_html, base_forms, follow_position,  # noqa: E402
                         settle_position, sidecar, write_epub)
from isopod.i18n import Translator  # noqa: E402
from isopod.lyrics import parse_lrc  # noqa: E402
from isopod.power import Power  # noqa: E402
from isopod.settings import Settings  # noqa: E402
from isopod.state import State  # noqa: E402
from isopod.radio import Radio, SongLog, station_from_api  # noqa: E402
from isopod.transfer import Transfer, safe_parts  # noqa: E402
from isopod.util import enc, fmt_duration, index_letter, matches, sort_key, spawn, split_path  # noqa: E402
from isopod.wifi import parse_terse  # noqa: E402


class UtilTest(unittest.TestCase):
    def test_sort_key_ignores_articles_and_case(self):
        names = ["the Weeknd", "Aurora", "a-ha", "The Beatles", "서울"]
        self.assertEqual(sorted(names, key=sort_key), ["a-ha", "Aurora", "The Beatles", "the Weeknd", "서울"])

    def test_index_letter(self):
        self.assertEqual(index_letter("The Beatles"), "B")
        self.assertEqual(index_letter("가을"), "ㄱ")
        self.assertEqual(index_letter("하늘"), "ㅎ")
        self.assertEqual(index_letter("99 Luftballons"), "#")

    def test_search_matches_initials(self):
        self.assertTrue(matches("han", "한강의 밤 (Han River)"))
        self.assertTrue(matches("ㅎㄱ", "한강의 밤"))
        self.assertTrue(matches("ㅎㄱㅇ ㅂ", "한강의 밤"))
        self.assertFalse(matches("ㅎㅂ", "한강의 밤"))
        self.assertFalse(matches("ㅎ가", "한강"))

    def test_path_roundtrip(self):
        path = "/music/album/" + enc("AC/DC", "Back in Black")
        self.assertEqual(split_path(path), ["music", "album", "AC/DC", "Back in Black"])

    def test_duration(self):
        self.assertEqual(fmt_duration(65), "1:05")
        self.assertEqual(fmt_duration(3725), "1:02:05")


class MpdParsingTest(unittest.TestCase):
    def test_quote_escapes(self):
        self.assertEqual(quote('say "hi" \\'), '"say \\"hi\\" \\\\"')

    def test_filter(self):
        self.assertEqual(filter_expr(albumartist='A "B"'), '(albumartist == "A \\"B\\"")')
        self.assertEqual(filter_expr(albumartist="A", album="B"),
                         '((albumartist == "A") AND (album == "B"))')

    def test_records_and_values(self):
        pairs = [("directory", "x"), ("file", "a.mp3"), ("Title", "A"), ("Artist", "one"),
                 ("Artist", "two"), ("file", "b.mp3"), ("Title", "B")]
        recs = records(pairs, start_keys=("file", "directory"))
        songs = [r for r in recs if "file" in r]
        self.assertEqual([s["title"] for s in songs], ["A", "B"])
        self.assertEqual(songs[0]["artist"], "one")
        self.assertEqual(values([("changed", "player"), ("changed", "mixer")], "changed"),
                         ["player", "mixer"])


class FakeMpdServer:
    def __init__(self):
        self.picture = bytes(range(256)) * 40

    async def handle(self, reader, writer):
        writer.write(b"OK MPD 0.24.0\n")
        while line := await reader.readline():
            cmd = line.decode().strip()
            if cmd.startswith("readpicture"):
                offset = int(cmd.rsplit(" ", 1)[1])
                chunk = self.picture[offset:offset + 4096]
                writer.write(f"size: {len(self.picture)}\ntype: image/png\nbinary: {len(chunk)}\n".encode()
                             + chunk + b"\nOK\n")
            elif cmd == "status":
                writer.write(b"state: play\nsong: 2\nelapsed: 12.5\nplaylistlength: 9\nOK\n")
            elif cmd.startswith("find"):
                writer.write(b"ACK [2@0] {find} bad filter\n")
            else:
                writer.write(b"OK\n")
            await writer.drain()


class MpdClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.sock = os.path.join(self.dir, "mpd.sock")
        self.fake = FakeMpdServer()
        self.server = await asyncio.start_unix_server(self.fake.handle, path=self.sock)

    async def asyncTearDown(self):
        self.server.close()

    async def test_status_and_errors(self):
        mpd = MPD(self.sock)
        self.addCleanup(mpd.close)
        status = dict(await mpd.call("status"))
        self.assertEqual(status["elapsed"], "12.5")
        with self.assertRaises(Exception):
            await mpd.call("find", "(bad)")
        self.assertEqual(dict(await mpd.call("status"))["song"], "2")

    async def test_binary_picture_in_chunks(self):
        mpd = MPD(self.sock)
        self.addCleanup(mpd.close)
        data, mime = await mpd.picture("x.flac")
        self.assertEqual(data, self.fake.picture)
        self.assertEqual(mime, "image/png")


class SpawnTest(unittest.IsolatedAsyncioTestCase):
    async def test_background_task_survives_garbage_collection(self):
        import gc

        async def parked():
            await asyncio.get_running_loop().create_future()
        task = spawn(parked())
        await asyncio.sleep(0)
        gc.collect()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        task.cancel()


class CoverFlowOrderTest(unittest.IsolatedAsyncioTestCase):
    async def test_albums_by_artist_then_album(self):
        music = Music(None, State(lambda snap: None), Translator("en"), tempfile.mkdtemp())
        rows = [("The Owls", "Quiet Hours"), ("Aurora Lane", "Paper Planes"), ("Aurora Lane", "Night Drive"),
                ("나무", "가을")]
        music.songs = [{"albumartist": a, "album": b, "genre": "", "file": f"{a}/{b}/1.mp3"} for a, b in rows]
        order = [key for key, _ in music.coverflow_albums()]
        self.assertEqual(order, [("Aurora Lane", "Night Drive"), ("Aurora Lane", "Paper Planes"),
                                 ("The Owls", "Quiet Hours"), ("나무", "가을")])


class FolderListingTest(unittest.TestCase):
    def test_books_hide_sidecars_and_sort_by_title(self):
        d = tempfile.mkdtemp()
        for sub in ("Classics", "데미안.sdr", ".hidden"):
            os.makedirs(os.path.join(d, sub))
        for name in ("The Road.epub", "notes.xyz", "데미안.epub", "Arc.fb2.zip"):
            open(os.path.join(d, name), "w").close()
        dirs, files = folder_listing(d, BOOK_EXTS)
        self.assertEqual(dirs, ["Classics"])
        self.assertEqual(files, ["Arc.fb2.zip", "The Road.epub", "데미안.epub"])


class BatteryWarningTest(unittest.TestCase):
    def test_warns_once_per_level_and_resets_on_charge(self):
        said = []
        b = Battery(None, notify=said.append)
        for percent in (40, 21, 20, 19, 12, 10, 9, 30, 8, 4):
            b._warn(percent, charging=False)
        self.assertEqual(said, [20, 10, 8, 4])
        b._warn(50, charging=True)
        b._warn(19, charging=False)
        self.assertEqual(said[-1], 19)


class FakeTransferApp:
    def __init__(self, root):
        self.t = Translator("en")
        self.paths = {k: os.path.join(root, k) for k in ("music", "videos", "books")}
        self.changed = []
        self.power = type("P", (), {"activity": lambda self: None})()
        self.music = type("M", (), {"update_library": staticmethod(lambda: asyncio.sleep(0))})()

    def page_changed(self, *paths):
        self.changed.extend(paths)


class TransferTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.mkdtemp()
        self.app = FakeTransferApp(self.root)
        self.tr = Transfer(self.app, (".mp4",), (".epub",), ports=(0,))
        self.assertTrue(await self.tr.start())

    async def asyncTearDown(self):
        self.tr.stop()

    async def request(self, method, path, body=b"", code=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.tr.port)
        head = f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n"
        if code is not None:
            head += f"X-Isopod-Code: {code}\r\n"
        writer.write(head.encode() + b"\r\n" + body)
        await writer.drain()
        data = await reader.read()
        writer.close()
        return int(data.split(b" ", 2)[1]), data.split(b"\r\n\r\n", 1)[1]

    async def test_page_and_uploads(self):
        status, body = await self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("Add Files to isoPod".encode(), body)
        self.assertEqual((await self.request("PUT", "/upload/a.mp3", b"x", code="nope"))[0], 403)
        status, _ = await self.request("PUT", "/upload/Album%20One/01.mp3", b"abc", code=self.tr.code)
        self.assertEqual(status, 201)
        with open(os.path.join(self.root, "music", "Album One", "01.mp3"), "rb") as f:
            self.assertEqual(f.read(), b"abc")
        self.assertEqual((await self.request("PUT", "/upload/b.epub", b"e", code=self.tr.code))[0], 201)
        self.assertTrue(os.path.exists(os.path.join(self.root, "books", "b.epub")))
        self.assertEqual((await self.request("PUT", "/upload/x.exe", b"e", code=self.tr.code))[0], 415)
        self.assertEqual((await self.request("PUT", "/upload/..%2Fescape.mp3", b"e", code=self.tr.code))[0], 400)
        self.assertEqual(self.tr.received, 2)

    def test_safe_parts(self):
        self.assertEqual(safe_parts("a/b%20c/d.mp3"), ["a", "b c", "d.mp3"])
        self.assertIsNone(safe_parts("../x.mp3"))
        self.assertIsNone(safe_parts(".hidden/x.mp3"))


class FakeEvents:
    def __init__(self):
        self.slept = 0
        self.menus = 0
        self.screens = []
        self.hold = None

    def screen_changed(self, on):
        self.screens.append(on)

    def screen_timeout(self, setting):
        return setting if self.hold is None else self.hold

    def before_sleep(self):
        self.slept += 1

    def power_menu(self):
        self.menus += 1

    def page_changed(self, *paths):
        pass

    def foreground_changed(self):
        pass

    def app_failed(self, title):
        pass


class PowerLidTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = State(lambda snap: None)
        self.events = FakeEvents()
        settings = {"brightness": 70, "screen_timeout": 60, "sleep_timeout": 600, "lid_keep_playing": True}
        self.power = Power(self.state, settings, self.events, sim=True)

    async def press(self):
        await self.power.power_key(True)
        await self.power.power_key(False)

    async def test_lid_sleeps_when_silent_and_wakes_on_open(self):
        await self.power.lid(True)
        self.assertEqual((self.power.screen_on, self.events.slept), (False, 1))
        await self.press()
        self.assertFalse(self.power.screen_on, "the power key does nothing while the lid is shut")
        await self.power.lid(False)
        self.assertTrue(self.power.screen_on)
        await self.press()
        self.assertTrue(self.power.screen_on, "a press right after opening the lid must not darken it")

    async def test_music_keeps_playing_with_lid_shut(self):
        self.state.update("player", state="play")
        await self.power.lid(True)
        self.assertEqual((self.power.screen_on, self.events.slept), (False, 0))

    async def test_wake_press_after_sleep_turns_screen_on(self):
        await self.power.sleep()
        self.assertFalse(self.power.screen_on)
        await self.press()
        self.assertTrue(self.power.screen_on)
        self.power._lit_at -= 5
        await self.press()
        self.assertFalse(self.power.screen_on, "a later press turns the screen off")

    async def test_front_app_can_hold_the_screen(self):
        self.assertIsNotNone(self.power._screen_deadline())
        self.events.hold = 0
        self.assertIsNone(self.power._screen_deadline())


class AppsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.apps = Apps(State(lambda snap: None), FakeEvents(), {}, sim=True)

    async def test_dark_freezes_front_and_old_apps_make_room(self):
        spec = lambda key: AppSpec(key, key, [key])  # noqa: E731
        for key in ("a", "b"):
            await self.apps.launch(spec(key), key)
            await self.apps.home()
        await self.apps.resume("a")
        await self.apps.set_dark(True)
        self.assertTrue(self.apps.running["a"]["frozen"])
        await self.apps.set_dark(False)
        self.assertFalse(self.apps.running["a"]["frozen"])
        await self.apps.home()
        await self.apps.launch(spec("c"), "c")
        self.assertEqual(sorted(self.apps.running), ["a", "b", "c"])
        await self.apps.home()
        await self.apps.launch(spec("d"), "d")
        self.assertNotIn("b", self.apps.running, "the app used longest ago closes first")


class WordWiseTest(unittest.TestCase):
    def glossary(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "g.tsv"), "w") as f:
            f.write("ubiquitous\tpresent everywhere\t34\nscrutinize\tlook at closely\t28\n"
                    "reluctant\tunwilling\t38\nwhisper\t<speak> softly\t30\n")
        return Glossary(os.path.join(d, "g.tsv"))

    def test_base_forms_find_the_headword(self):
        self.assertIn("scrutinize", base_forms("scrutinized"))
        self.assertIn("scrutinize", base_forms("scrutinizing"))
        self.assertIn("study", base_forms("studies"))

    def test_hints_once_outside_skipped_tags(self):
        g = self.glossary()
        doc = ("<html><head><title>ubiquitous</title></head><body><h1>ubiquitous</h1>"
               "<p>Phones are ubiquitous; so ubiquitous that we scrutinized them. "
               "A reluctant Reader. <a href='#'>whisper</a> whispering &nbsp;</p></body></html>")
        out = annotate_html(doc, g, 35, set())
        self.assertIn("<title>ubiquitous</title>", out)
        self.assertIn("<h1>ubiquitous</h1>", out)
        self.assertEqual(out.count("<rt>present everywhere</rt>"), 1)
        self.assertIn("<ruby>scrutinized<rt>look at closely</rt></ruby>", out)
        self.assertNotIn("unwilling", out, "common enough words stay bare")
        self.assertIn("<a href='#'>whisper</a>", out)
        self.assertIn("<ruby>whispering<rt>&lt;speak&gt; softly</rt></ruby>", out)
        self.assertIn("&nbsp;", out)

    def test_compounds_and_sentence_starts(self):
        out = annotate_html("<p>Scrutinize it. A torn ubiquitous-looking pocket.</p>", self.glossary(), 35, set())
        self.assertIn("<ruby>Scrutinize<rt>look at closely</rt></ruby> it", out)
        self.assertIn("<ruby>ubiquitous<rt>present everywhere</rt></ruby>-looking", out)

    def test_epub_round_trip(self):
        d = tempfile.mkdtemp()
        src, dst = os.path.join(d, "a.epub"), os.path.join(d, "ww", "a.epub")
        write_epub(src, "Test", [("One", "<p>It is ubiquitous.</p>")], lang="en")
        self.assertEqual(annotate_epub(src, dst, self.glossary()), 1)
        import zipfile
        with zipfile.ZipFile(dst) as z:
            self.assertEqual(z.namelist()[0], "mimetype")
            self.assertEqual(z.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
            page = z.read("OEBPS/c0.xhtml").decode()
            self.assertIn("<ruby>ubiquitous", page)
            self.assertIn("<style>rt {", page)

    def test_position_follows_the_version_read_last(self):
        d = tempfile.mkdtemp()
        book, copy = os.path.join(d, "Book.epub"), os.path.join(d, "ww", "Book.epub")
        os.makedirs(os.path.dirname(sidecar(book)))
        with open(sidecar(book), "w") as f:
            f.write('return {\n    ["copt_font_size"] = 26,\n    ["copt_h_page_margins"] = {\n        [1] = 15,\n'
                    '    },\n    ["cre_dom_version"] = 20240114,\n'
                    '    ["last_xpointer"] = "/body/DocFragment[3]/body/div/p[4]/text().117",\n'
                    '    ["percent_finished"] = 0.25,\n}\n')
        self.assertTrue(follow_position([book, copy], copy))
        with open(sidecar(copy)) as f:
            state = f.read()
        self.assertIn('"/body/DocFragment[3]/body/div/p[4]"', state)
        self.assertIn("20240114", state)
        self.assertIn('["copt_block_rendering_mode"] = 3', state, "legacy layout would drop the ruby")
        self.assertIn('["copt_font_size"] = 26', state)
        self.assertFalse(follow_position([book, copy], copy), "already where it was read last")

        with open(sidecar(copy), "w") as f:
            f.write('return {\n    ["bookmarks"] = {},\n    ["cre_dom_version"] = 20240114,\n'
                    '    ["last_xpointer"] = "/body/DocFragment[9]/body/p[2]/ruby[3]/text().1",\n'
                    '    ["percent_finished"] = 0.5,\n}\n')
        os.utime(sidecar(book), (1, 1))
        self.assertTrue(follow_position([book, copy], book))
        with open(sidecar(book)) as f:
            state = f.read()
        self.assertIn('"/body/DocFragment[9]/body/p[2]"', state)
        self.assertIn("0.5", state)

        with open(sidecar(copy), "a") as f:
            f.write("-- rebuilt\n")
        settle_position(copy)
        with open(sidecar(copy)) as f:
            self.assertIn('"/body/DocFragment[9]/body/p[2]"', f.read())


RSS =b"""<?xml version="1.0"?><rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel><title>T</title>
<item><title>Long story</title><link>http://x/1</link><content:encoded><![CDATA[<p>%s</p><p>Second.</p>]]>
</content:encoded></item>
<item><title>Short one</title><link>http://x/2</link><description>Just a teaser.</description></item>
</channel></rss>""" % (b"Full text. " * 60)

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>A</title>
<entry><title>Atom item</title><link rel="alternate" href="http://y/1"/><summary>Short.</summary></entry></feed>"""

PAGE = ("<html><body><nav><p>Menu menu menu menu menu menu menu</p></nav><div class='a'>"
        + "".join(f"<p>Paragraph {i} of the article body, long enough to count as text.</p>" for i in range(8))
        + "</div><footer><p>Copyright footer text that is long enough</p></footer></body></html>").encode()


class NewsTest(unittest.TestCase):
    def test_parse_rss_and_atom(self):
        rss = news.parse_feed(RSS)
        self.assertEqual([i["title"] for i in rss], ["Long story", "Short one"])
        atom = news.parse_feed(ATOM)
        self.assertEqual((atom[0]["title"], atom[0]["link"]), ("Atom item", "http://y/1"))

    def test_article_page_extraction(self):
        paragraphs = news.extract_article(PAGE.decode())
        self.assertEqual(len(paragraphs), 8)
        self.assertNotIn("Menu", " ".join(paragraphs))

    def test_edition(self):
        pages = {"http://feed": RSS, "http://x/2": PAGE}

        def fetcher(url, timeout=10):
            if url not in pages:
                raise OSError("offline")
            return pages[url]
        d = tempfile.mkdtemp()
        path = os.path.join(d, news.edition_name())
        self.assertEqual(news.build_edition(path, "News", [("Feed", "http://feed")], fetcher=fetcher), 2)
        import zipfile
        with zipfile.ZipFile(path) as z:
            second = z.read("OEBPS/c1.xhtml").decode()
        self.assertIn("Paragraph 3 of the article body", second)
        self.assertEqual(news.editions(d), [news.edition_name()])


class LyricsTest(unittest.TestCase):
    def test_synced_lrc(self):
        lines, times = parse_lrc("[ar:X]\n[offset:+500]\n[00:00.00]\n[00:12.50]Two\n[00:05.00][00:30.00]One and again\n")
        self.assertEqual(lines, ["One and again", "Two", "One and again"])
        self.assertEqual(times, [4.5, 12.0, 29.5])

    def test_plain_text(self):
        lines, times = parse_lrc("\nFirst line\n\nSecond verse\n\n")
        self.assertEqual((lines, times), (["First line", "", "Second verse"], None))
        self.assertIsNone(parse_lrc(""))


class WifiParsingTest(unittest.TestCase):
    def test_terse_with_escaped_colons(self):
        self.assertEqual(parse_terse("*:Cafe\\:isoPod:72:WPA2"), ["*", "Cafe:isoPod", "72", "WPA2"])
        fields = parse_terse(":Open Net:40:")
        self.assertEqual(fields[:3], ["", "Open Net", "40"])


class AudioParsingTest(unittest.TestCase):
    def test_bluetooth_sink(self):
        raw = {"name": "bluez_output.AA.1", "description": "AirPods Pro", "mute": False,
               "volume": {"front-left": {"value_percent": "45%"}, "front-right": {"value_percent": "47%"}},
               "properties": {"device.api": "bluez5"}}
        self.assertEqual(_sink(raw)["kind"], "bluetooth")
        self.assertEqual(_sink(raw)["level"], 47)


class Amp(FakeAudio):
    """The speaker amplifier needs 100 ms from its route going live before it makes a sound."""

    live_since = None

    def _build(self):
        super()._build()
        live = self.default == self.INTERNAL and self.route == "speaker" and not self.routes["speaker"]["muted"]
        self.live_since = (self.live_since or time.monotonic()) if live else None

    def audible(self):
        return self.live_since is not None and time.monotonic() - self.live_since >= 0.1


class RoutesTest(unittest.IsolatedAsyncioTestCase):
    """Every way headphones come and go, checked against a model of PipeWire and WirePlumber."""

    async def asyncSetUp(self):
        self.fake = Amp()
        self.settings = Settings(tempfile.mkdtemp())
        self.lost = []
        self.playing, self.on_speaker, self.leaks = False, False, 0

        async def lost(kind):
            self.lost.append(kind)
            self.playing = False
        self.audio = Audio(self.fake, State(lambda snapshot: None), self.settings, lost=lost)
        self.audio.SETTLE = 0.02
        self.watcher = asyncio.get_running_loop().create_task(self.watch())

    async def asyncTearDown(self):
        self.watcher.cancel()

    async def watch(self):
        while True:
            if self.playing and not self.on_speaker and self.fake.audible():
                self.leaks += 1
            await asyncio.sleep(0.005)

    async def start(self, jack=False):
        self.fake.plug(jack)
        await self.audio.start()
        self.audio.jack(jack, initial=True)
        await self.quiet()

    async def quiet(self):
        await asyncio.sleep(0.15)

    def play(self, on_speaker=False):
        self.playing, self.on_speaker = True, on_speaker

    def plug(self, inserted):
        self.fake.plug(inserted)  # WirePlumber is as quick as isoPod, or quicker
        self.audio.jack(inserted)

    def internal_muted(self):
        return self.fake.routes[self.fake.route]["muted"]

    async def test_headphones_in_keeps_playing_there(self):
        await self.start()
        self.play(on_speaker=True)
        self.plug(True)
        await self.quiet()
        self.on_speaker = False
        self.assertEqual((self.audio.output, self.fake.route, self.lost), ("wired", "headphones", []))
        self.assertFalse(self.internal_muted())

    async def test_headphones_out_pause_before_the_speaker_sounds(self):
        await self.start(jack=True)
        self.play()
        self.plug(False)
        await self.quiet()
        self.assertEqual(self.lost, ["wired"])
        self.assertEqual(self.leaks, 0)
        self.assertEqual((self.audio.output, self.fake.route), ("speaker", "speaker"))
        self.assertFalse(self.internal_muted(), "the speaker speaks again once paused")

    async def test_bluetooth_takes_over_and_parks_the_speaker(self):
        await self.start()
        self.play(on_speaker=True)
        self.fake.add_bluetooth("AirPods", "AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.assertEqual(self.audio.output, self.fake.default)
        self.assertTrue(self.fake.routes["speaker"]["muted"])
        self.assertEqual(self.lost, [])

    async def test_bluetooth_dropout_pauses_in_silence(self):
        await self.start()
        self.fake.add_bluetooth("AirPods", "AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.play()
        self.audio.bluetooth_lost("AA:BB:CC:DD:EE:01")
        self.fake.remove_bluetooth("AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.assertEqual((self.lost, self.leaks), (["bluetooth"], 0))
        self.assertEqual((self.audio.output, self.fake.default), ("speaker", FakeAudio.INTERNAL))
        self.assertFalse(self.internal_muted())

    async def test_bluetooth_gone_before_it_settled_is_silent_too(self):
        await self.start()
        self.play(on_speaker=True)
        self.fake.add_bluetooth("AirPods", "AA:BB:CC:DD:EE:01")
        await asyncio.sleep(0.005)
        self.audio.output = self.fake.default  # it already played there
        self.on_speaker = False
        self.fake.remove_bluetooth("AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.assertEqual((self.lost, self.leaks), (["bluetooth"], 0))
        self.assertFalse(self.internal_muted())

    async def test_a_sink_gone_without_word_from_bluez_still_pauses(self):
        await self.start()
        self.fake.add_bluetooth("JBL Flip 6", "AA:BB:CC:DD:EE:02")
        await self.quiet()
        self.play()
        self.fake.remove_bluetooth()
        await self.quiet()
        self.assertEqual((self.lost, self.leaks), (["bluetooth"], 0))

    async def test_the_output_connected_last_plays(self):
        await self.start()
        self.fake.add_bluetooth("AirPods", "AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.play()
        self.plug(True)
        await self.quiet()
        self.assertEqual((self.audio.output, self.fake.default, self.fake.route),
                         ("wired", FakeAudio.INTERNAL, "headphones"))
        self.assertFalse(self.internal_muted())
        self.plug(False)
        await self.quiet()
        self.assertEqual(self.lost, ["wired"])
        self.assertEqual(self.audio.output, self.fake.default, "back to the Bluetooth still connected")
        self.assertTrue(self.fake.routes["speaker"]["muted"], "parked again behind Bluetooth")
        self.assertEqual(self.leaks, 0)

    async def test_a_dropout_elsewhere_changes_nothing(self):
        await self.start()
        self.fake.add_bluetooth("AirPods", "AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.plug(True)
        await self.quiet()
        self.play()
        self.audio.bluetooth_lost("AA:BB:CC:DD:EE:01")
        self.fake.remove_bluetooth("AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.assertEqual((self.lost, self.audio.output), ([], "wired"))

    async def test_losing_both_at_once_pauses_once(self):
        await self.start()
        self.fake.add_bluetooth("AirPods", "AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.plug(True)
        await self.quiet()
        self.play()
        self.plug(False)
        self.audio.bluetooth_lost("AA:BB:CC:DD:EE:01")
        self.fake.remove_bluetooth("AA:BB:CC:DD:EE:01")
        await self.quiet()
        self.assertEqual((self.lost, self.leaks), (["wired"], 0))
        self.assertEqual((self.audio.output, self.fake.route), ("speaker", "speaker"))
        self.assertFalse(self.internal_muted())

    async def test_a_bouncing_plug_settles_where_it_ends(self):
        await self.start(jack=True)
        self.play()
        for inserted in (False, True, False, True, False):
            self.plug(inserted)
            await asyncio.sleep(0.01)
        await self.quiet()
        self.assertIn("wired", self.lost)
        self.assertEqual(self.leaks, 0)
        self.assertEqual((self.audio.output, self.fake.route), ("speaker", "speaker"))
        self.assertFalse(self.internal_muted())
        self.plug(True)
        await self.quiet()
        self.assertFalse(self.internal_muted(), "headphones never come back muted")

    async def test_start_lifts_a_mute_left_behind(self):
        self.fake.routes["speaker"]["muted"] = True
        await self.start()
        self.assertFalse(self.internal_muted())

    async def test_each_output_keeps_its_level_within_the_limit(self):
        self.settings.set("volumes", {"speaker": 30, "wired": 90})
        self.settings.set("volume_limit", 60)
        self.audio = Audio(self.fake, State(lambda snapshot: None), self.settings)
        self.audio.SETTLE = 0.02
        await self.start()
        self.plug(True)
        await self.quiet()
        self.assertEqual(self.fake.routes["headphones"]["level"], 60)
        await self.audio.change(-5)
        self.plug(False)
        await self.quiet()
        self.assertEqual(self.fake.routes["speaker"]["level"], 30)
        self.plug(True)
        await self.quiet()
        self.assertEqual(self.fake.routes["headphones"]["level"], 55)


class RadioTest(unittest.IsolatedAsyncioTestCase):
    async def test_parse_dedupe_and_favorites(self):
        payload = json.dumps([
            {"stationuuid": "1", "name": "KBS Cool FM", "url_resolved": "http://a", "tags": "pop,korean,kbs",
             "codec": "AAC", "bitrate": 128, "countrycode": "KR"},
            {"stationuuid": "1", "name": "dup", "url_resolved": "http://a"},
            {"stationuuid": "2", "name": "", "url": "http://b"},
        ]).encode()
        calls = []

        def fetch(url):
            calls.append(url)
            return payload
        d = tempfile.mkdtemp()
        radio = Radio(d, d, fetch=fetch)
        stations = await radio.top()
        self.assertEqual([s["name"] for s in stations], ["KBS Cool FM", "Radio"])
        self.assertEqual(stations[0]["tags"], ["pop", "korean", "kbs"])
        await radio.top()
        self.assertEqual(len(calls), 1, "second call should come from cache")
        self.assertTrue(radio.toggle_favorite(stations[0]))
        self.assertTrue(Radio(d, d, fetch=fetch).is_favorite("1"))
        self.assertFalse(radio.toggle_favorite(stations[0]))

    def test_station_shape(self):
        s = station_from_api({"stationuuid": "x", "name": " Jazz ", "url": "http://j", "codec": "UNKNOWN"})
        self.assertEqual((s["name"], s["url"], s["bitrate"], s["codec"]), ("Jazz", "http://j", 0, ""))
        hls = station_from_api({"stationuuid": "y", "url_resolved": "https://x/playlist.m3u8", "hls": 1})
        self.assertEqual(hls["url"], "hls+https://x/playlist.m3u8")


class OneSong:
    """MPD with one thing in its queue."""

    def __init__(self, state, **song):
        self.status = [("state", state), ("audio", "44100:16:2"), ("song", "0"), ("playlistlength", "1")]
        self.song = list(song.items())

    async def call(self, command, *args):
        return self.status if command == "status" else self.song

    async def picture(self, uri):
        return None


class SongLogTest(unittest.IsolatedAsyncioTestCase):
    """The radio's song history: what counts as heard, and what stays."""

    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.now = 1000

    def log(self, recent=3):
        log = SongLog(self.dir, clock=lambda: self.now)
        log.DWELL, log.RECENT = 0.02, recent
        return log

    async def hear(self, log, title, station="Night FM"):
        self.now += 60
        log.playing(title, station)
        await asyncio.sleep(0.05)

    async def test_a_song_counts_once_it_has_played_a_while(self):
        log = self.log()
        log.playing("Aurora Lane -  Neon Coast ", "Night FM")
        self.assertEqual(log.entries, [], "not at once: stations get passed through")
        await asyncio.sleep(0.05)
        self.assertEqual(log.entries, [{"title": "Aurora Lane - Neon Coast", "station": "Night FM",
                                        "time": 1000, "saved": False}])

    async def test_a_station_passed_through_leaves_nothing(self):
        log = self.log()
        log.playing("Song A", "One")
        log.playing("Song B", "Two")
        log.playing("", "Two")
        await asyncio.sleep(0.05)
        self.assertEqual(log.entries, [])

    async def test_a_station_naming_itself_is_not_a_song(self):
        log = self.log()
        await self.hear(log, "NIGHT FM")
        await self.hear(log, "   ")
        self.assertEqual(log.entries, [])
        self.assertIsNone(log.on_air)

    async def test_a_song_heard_again_moves_to_the_top(self):
        log = self.log()
        for title in ("Song A", "Song B", "song a"):
            await self.hear(log, title, "Two" if title == "song a" else "One")
        self.assertEqual([(e["title"], e["station"], e["time"]) for e in log.entries],
                         [("Song A", "Two", 1180), ("Song B", "One", 1120)])

    async def test_saved_songs_stay_when_the_rest_roll_off(self):
        log = self.log(recent=2)
        await self.hear(log, "Keeper")
        log.set_saved("Keeper", True)
        for title in ("One", "Two", "Three"):
            await self.hear(log, title)
        self.assertEqual([e["title"] for e in log.saved()], ["Keeper"])
        self.assertEqual([e["title"] for e in log.recent()], ["Three", "Two"])
        await self.hear(log, "Keeper")
        self.assertTrue(log.find("keeper")["saved"], "hearing it again does not unsave it")
        log.set_saved("Keeper", False)
        self.assertEqual([e["title"] for e in log.recent()], ["Keeper", "Three"])

    async def test_saving_what_is_on_air_does_not_wait(self):
        log = self.log()
        log.DWELL = 60
        log.playing("Just Started", "Night FM")
        log.set_saved(log.on_air[0], True, log.on_air[1])
        self.assertEqual([(e["title"], e["station"], e["saved"]) for e in log.entries],
                         [("Just Started", "Night FM", True)])
        log.playing("", "")

    async def test_it_outlives_a_restart_and_can_be_cleared(self):
        log = self.log()
        await self.hear(log, "Song A")
        log.set_saved("Song A", True)
        again = self.log()
        self.assertEqual([(e["title"], e["saved"]) for e in again.entries], [("Song A", True)])
        again.remove("song a")
        self.assertEqual(self.log().entries, [])

    async def test_only_a_playing_station_is_on_air(self):
        said = []
        stream = {"file": "http://night.fm/live", "Name": "Night FM", "Title": "Aurora Lane - Neon Coast"}
        for state, song in (("play", stream), ("pause", stream),
                            ("play", {"file": "a/1.mp3", "Title": "Local", "Artist": "Someone"})):
            music = Music(OneSong(state, **song), State(lambda snapshot: None), None, self.dir)
            music.on_air = lambda title, station: said.append((title, station))
            await music.refresh()
        self.assertEqual(said, [("Aurora Lane - Neon Coast", "Night FM"), ("", "Night FM"), ("", "Local")])


class Bluez(Backend):
    """BlueZ as far as isoPod can tell. A link comes up before anything else does, a first
    connection pairs on the way, and for headphones the stream shows last."""

    def __init__(self, *devices):
        super().__init__()
        self.powered = True
        self.calls = []
        self.errors = {}
        self.already = set()
        self.drops = set()
        self.devices = {d.path: d for d in devices}

    async def start(self, listener):
        self.listener = listener
        for dev in self.devices.values():
            self._emit("changed", dev, None)

    def set(self, path, **fields):
        old = self.devices[path]
        self.devices[path] = dataclasses.replace(old, **fields)
        self._emit("changed", self.devices[path], old)

    def _fail(self, method):
        if self.errors.get(method):
            raise RuntimeError(self.errors[method].pop(0))

    async def discover(self, on):
        self.calls.append(("discover", on))
        self.discovering = on
        self._emit("adapter")

    async def pair(self, path):
        self.calls.append(("pair", path))
        self.set(path, connected=True)
        try:
            self._fail("pair")
        except RuntimeError:
            self.set(path, connected=False)
            raise
        self.set(path, paired=True)

    async def trust(self, path):
        self.calls.append(("trust", path))
        self.set(path, trusted=True)

    async def connect(self, path, profile=None):
        self.calls.append(("connect", path, profile))
        if path in self.already:
            self.set(path, connected=True, paired=True, transport=True)
            raise RuntimeError("org.bluez.Error.NotAvailable -- br-connection-profile-unavailable")
        self.set(path, connected=True)
        try:
            self._fail("connect")
        except RuntimeError:
            self.set(path, connected=False)
            raise
        self.set(path, paired=True, transport=bool(profile))
        if path in self.drops:
            self.set(path, connected=False, transport=False)

    async def disconnect(self, path):
        self.calls.append(("disconnect", path))
        self.set(path, connected=False, transport=False)

    async def remove(self, path):
        self.calls.append(("remove", path))
        self._emit("removed", None, self.devices.pop(path))


class BluetoothEvents:
    def __init__(self):
        self.toasts, self.found, self.lost = [], [], []

    def toast(self, text, icon=None):
        self.toasts.append(text)

    def page_changed(self, *paths):
        pass

    def audio_device_found(self, device):
        self.found.append(device.name)

    def audio_device_lost(self, device):
        self.lost.append(device.name)


PODS, PAD = "/org/bluez/hci0/dev_AC", "/org/bluez/hci0/dev_E4"


class BluetoothTest(unittest.IsolatedAsyncioTestCase):
    """Pairing and connecting as BlueZ plays them out, AirPods first."""

    async def asyncSetUp(self):
        self.bluez = Bluez(
            Device(PODS, "AC:12:2F:00:00:01", "AirPods Pro", "audio-headphones", rssi=-48),
            Device(PAD, "E4:17:D8:00:00:02", "8BitDo Pro 2", "input-gaming", rssi=-60),
            Device("/org/bluez/hci0/dev_F0", "F0:99:B6:00:00:03", "iPhone", "phone", rssi=-40),
            Device("/org/bluez/hci0/dev_5C", "5C:F3:70:00:00:04", "5C-F3-70-00-00-04", "audio-card", rssi=-30))
        self.events = BluetoothEvents()
        self.settings = Settings(tempfile.mkdtemp())
        self.bt = Bluetooth(self.bluez, State(lambda snapshot: None), self.settings, Translator("ko"),
                            self.events, None)
        self.bt.RETRY_DELAY, self.bt.STREAM_WAIT = 0, 0.05
        await self.bt.start()

    def connects(self):
        return [c for c in self.bluez.calls if c[0] == "connect"]

    async def test_only_named_headphones_and_controllers_are_offered(self):
        self.assertEqual([d.name for d in self.bt.other_devices()], ["AirPods Pro", "8BitDo Pro 2"])
        self.assertEqual(self.bt.my_devices(), [])

    async def test_headphones_connect_by_their_audio_service_and_say_so_once(self):
        await self.bt.activate(PODS)
        self.assertEqual(self.bluez.calls, [("trust", PODS), ("connect", PODS, A2DP_SINK)])
        self.assertEqual(self.events.toasts, ["AirPods Pro 연결됨"])
        self.assertEqual(self.events.found, ["AirPods Pro"])
        self.assertEqual([d.name for d in self.bt.my_devices()], ["AirPods Pro"])
        self.assertEqual(self.settings["recent_bluetooth"][0]["address"], "AC:12:2F:00:00:01")

    async def test_a_link_without_sound_is_not_a_connection(self):
        self.bluez.set(PODS, connected=True, paired=True)
        self.assertEqual((self.events.toasts, self.events.found), ([], []))
        self.assertFalse(self.bt.find(PODS).ready)
        await self.bt.activate(PODS)
        self.assertEqual(self.connects(), [("connect", PODS, A2DP_SINK)], "a press connects; it does not hang up")
        self.assertTrue(self.bt.find(PODS).ready)

    async def test_a_failed_first_attempt_says_so_once_and_leaves_nothing_behind(self):
        self.bluez.errors["connect"] = ["org.bluez.Error.Failed -- br-connection-refused"] * 3
        await self.bt.activate(PODS)
        self.assertEqual(self.events.toasts, ["AirPods Pro에 연결할 수 없어요"])
        self.assertEqual(len(self.connects()), 3)
        self.assertEqual(self.bluez.calls[-1], ("remove", PODS))
        self.assertEqual(self.events.found, [])

    async def test_headphones_that_do_not_answer_are_not_asked_again(self):
        self.bluez.set(PODS, paired=True, trusted=True)
        self.bluez.errors["connect"] = ["org.bluez.Error.Failed -- br-connection-page-timeout"]
        await self.bt.activate(PODS)
        self.assertEqual(len(self.connects()), 1)
        self.assertEqual(self.events.toasts, ["AirPods Pro에 연결할 수 없어요"])
        self.assertIn(PODS, self.bluez.devices, "a device that was paired stays")

    async def test_a_link_that_drops_at_once_is_a_failure_said_once(self):
        self.bluez.drops.add(PODS)
        await self.bt.activate(PODS)
        self.assertEqual(len(self.connects()), 3)
        self.assertEqual(self.events.toasts, ["AirPods Pro에 연결할 수 없어요"])
        self.assertIn(PODS, self.bluez.devices, "it did pair, so it stays to be tried again")

    async def test_headphones_that_connected_by_themselves_count(self):
        self.bluez.set(PODS, paired=True, trusted=True)
        self.bluez.already.add(PODS)
        await self.bt.activate(PODS)
        self.assertEqual(len(self.connects()), 1)
        self.assertEqual(self.events.toasts, ["AirPods Pro 연결됨"])

    async def test_controllers_pair_first_and_connect_whole(self):
        await self.bt.activate(PAD)
        self.assertEqual(self.bluez.calls, [("pair", PAD), ("trust", PAD), ("connect", PAD, None)])
        self.assertEqual(self.events.toasts, ["8BitDo Pro 2 연결됨"])

    async def test_a_press_on_connected_headphones_hangs_up(self):
        await self.bt.activate(PODS)
        await self.bt.activate(PODS)
        self.assertEqual(self.bluez.calls[-1], ("disconnect", PODS))
        self.assertEqual(self.events.toasts, ["AirPods Pro 연결됨", "AirPods Pro 연결 끊김"])
        self.assertEqual(self.events.lost, ["AirPods Pro"])

    async def test_headphones_that_go_away_are_announced_and_reported(self):
        await self.bt.activate(PODS)
        self.bluez.set(PODS, connected=False, transport=False)
        self.assertEqual(self.events.toasts, ["AirPods Pro 연결됨", "AirPods Pro 연결 끊김"])
        self.assertEqual(self.events.lost, ["AirPods Pro"])

    async def test_no_inquiry_while_headphones_play(self):
        self.bt.begin_discovery()
        await asyncio.sleep(0.01)
        self.assertTrue(self.bt.discovering)
        await self.bt.activate(PODS)
        self.assertEqual(self.bluez.calls[:2], [("discover", True), ("discover", False)])
        self.bt.begin_discovery()
        await asyncio.sleep(0.01)
        self.assertFalse(self.bt.discovering)
        self.assertEqual(self.bluez.calls.count(("discover", True)), 1)

    async def test_no_inquiry_while_a_device_is_being_connected(self):
        self.events.page_changed = lambda *paths: self.bt.begin_discovery()  # the open page, redrawn
        self.bt.begin_discovery()
        await asyncio.sleep(0.01)
        await self.bt.activate(PAD)
        self.assertEqual(self.bluez.calls[:5], [("discover", True), ("discover", False), ("pair", PAD),
                                                ("trust", PAD), ("connect", PAD, None)])
        await asyncio.sleep(0.01)
        self.assertEqual(self.bluez.calls[5:], [("discover", True)], "it looks again once that is done")


class BluezSignalsTest(unittest.TestCase):
    def test_headphones_are_ready_once_a_stream_is_set_up(self):
        bluez, dev = RavelBackend(), "/org/bluez/hci0/dev_AC_12_2F_00_00_01"
        bluez._apply(dev, {"org.bluez.Device1": {"Address": "AC:12:2F:00:00:01", "Alias": "AirPods Pro",
                                                 "Icon": "audio-headphones", "Connected": True}})
        self.assertFalse(bluez.devices[dev].ready, "a link alone, as pairing or LE makes one")
        bluez._apply(dev + "/sep1/fd0", {"org.bluez.MediaTransport1": {"Device": dev}})
        self.assertTrue(bluez.devices[dev].ready)
        bluez._apply(dev, {"org.bluez.Device1": {"RSSI": -50}})
        self.assertTrue(bluez.devices[dev].ready, "it lasts while the link does")
        bluez._apply(dev, {"org.bluez.Device1": {"Connected": False}})
        bluez._apply(dev, {"org.bluez.Device1": {"Connected": True}})
        self.assertFalse(bluez.devices[dev].ready, "a new link starts without a stream")

    def test_a_device_without_a_name_shows_its_address(self):
        bluez, dev = RavelBackend(), "/org/bluez/hci0/dev_5C_F3_70_00_00_04"
        bluez._apply(dev, {"org.bluez.Device1": {"Address": "5C:F3:70:00:00:04", "Alias": "5C-F3-70-00-00-04"}})
        self.assertFalse(bluez.devices[dev].named)
        bluez._apply(dev, {"org.bluez.Device1": {"Alias": "JBL Flip 6"}})
        self.assertTrue(bluez.devices[dev].named)


if __name__ == "__main__":
    unittest.main()
