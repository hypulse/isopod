import asyncio
import dataclasses
import datetime
import email.utils
import json
import os
import re
import struct
import sys
import tempfile
import time
import types
import unicodedata
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from isopod.app import App  # noqa: E402
from isopod.audio import Audio, FakeAudio, _sink  # noqa: E402
from isopod import uevents  # noqa: E402
from isopod.battery import Battery  # noqa: E402
from isopod.bluetooth import A2DP_SINK, Backend, Bluetooth, Device, RavelBackend  # noqa: E402
from isopod.mpd import MPD, filter_expr, quote, records, values  # noqa: E402
from isopod.music import Music, Song  # noqa: E402
from isopod.pages import VIDEO_EXTS, Pages, folder_listing  # noqa: E402
from isopod.apps import AppSpec, Apps, prepare_koreader  # noqa: E402
from isopod.i18n import Translator  # noqa: E402
from isopod.lyrics import decode, parse_lrc  # noqa: E402
from isopod.main import parse_args  # noqa: E402
from isopod.power import Power  # noqa: E402
from isopod.radio import KEEP, LIFE, Radio, RadioError, station_from_api, unique  # noqa: E402
from isopod.settings import Settings  # noqa: E402
from isopod.state import State  # noqa: E402
from isopod.system import System  # noqa: E402
from isopod.util import enc, fmt_duration, index_letter, matches, sort_key, spawn, split_path  # noqa: E402
from isopod.wifi import FakeWifi, Nmcli, Wifi, parse_terse  # noqa: E402


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

    def test_a_blank_name_keeps_its_place_in_a_path(self):
        self.assertEqual(split_path("/music/album/" + enc("Blur", "")), ["music", "album", "Blur", ""])
        self.assertEqual(split_path("/music/album/" + enc("", "Parklife")), ["music", "album", "", "Parklife"])

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
        music = Music(None, State(lambda snap: None), tempfile.mkdtemp())
        rows = [("The Owls", "Quiet Hours"), ("Aurora Lane", "Paper Planes"), ("Aurora Lane", "Night Drive"),
                ("나무", "가을")]
        music.songs = [{"albumartist": a, "album": b, "genre": "", "file": f"{a}/{b}/1.mp3"} for a, b in rows]
        order = [key for key, _ in music.coverflow_albums()]
        self.assertEqual(order, [("Aurora Lane", "Night Drive"), ("Aurora Lane", "Paper Planes"),
                                 ("The Owls", "Quiet Hours"), ("나무", "가을")])


class ArtPanelTest(unittest.IsolatedAsyncioTestCase):
    """The covers beside the home and music menus: drawn from all the albums, the same while
    a menu is open, new each time it is come back to."""

    async def asyncSetUp(self):
        class NoApp:
            t = Translator("en")
            state = State(lambda snap: None)

            def page_changed(self, *paths):
                pass
        d = tempfile.mkdtemp()
        app = self.app = NoApp()
        app.music = Music(None, app.state, d, music_dir=d)
        app.music.songs = [Song(file=f"{n:03d}.mp3", title=str(n), artist="A", albumartist="A", album=f"Album {n:03d}",
                                genre="", year="", track=1, disc=0, duration=1.0) for n in range(120)]
        os.makedirs(app.music.art_dir)
        for song in app.music.songs:
            app.music._by_file[song["file"]] = song
            open(os.path.join(app.music.art_dir, app.music._art_key(song["file"]) + ".jpg"), "w").close()
        self.pages = Pages(app)

    async def art(self, path):
        return (await self.pages.build(path))["art"]

    async def test_a_menu_keeps_its_covers_while_open_and_draws_new_ones_when_come_back_to(self):
        first = await self.art("/music")
        self.assertEqual(len(first), 16)
        self.assertEqual(await self.art("/music"), first, "fetched again as the music changes")
        self.pages.leave("/music")
        self.assertNotEqual(await self.art("/music"), first, "left and opened again")
        names = {os.path.basename(a) for a in first}
        alphabetical = {os.path.basename(self.app.music._art_for(f)) for _, f in self.app.music.albums()[:32]}
        self.assertFalse(names <= alphabetical, "from all the albums, not the first ones by name")

    async def test_home_draws_new_covers_once_something_else_was_open(self):
        home = await self.art("/")
        self.assertEqual(await self.art("/"), home, "reloaded while it is in front")
        await self.pages.build("/music/albums")
        self.assertNotEqual(await self.art("/"), home)


class NoAlbumTest(unittest.IsolatedAsyncioTestCase):
    """Songs whose tags name no album make none, and an album without an artist still opens."""

    async def test_songs_without_an_album_and_albums_without_an_artist(self):
        class NoPictures:
            async def picture(self, uri):
                return None

        class NoApp:
            t = Translator("en")
            state = State(lambda snap: None)

            def page_changed(self, *paths):
                pass

        def song(file, title, artist, album):
            return {"file": file, "title": title, "artist": artist, "albumartist": artist, "album": album,
                    "genre": "", "disc": 0, "track": 0}

        app = NoApp()
        app.music = Music(NoPictures(), app.state, tempfile.mkdtemp())
        app.music.songs = [song("a.mp3", "Loose", "Blur", ""), song("b.mp3", "Tracy Jacks", "Blur", "Parklife"),
                           song("c.mp3", "Nameless", "", ""), song("d.mp3", "Track 1", "", "Mixtape")]
        pages = Pages(app)

        async def titles(path):
            return [i["title"] for i in (await pages.build(path))["items"]]

        async def opened(path, key):
            return await titles((await pages.activate(path, key))["push"])

        self.assertEqual(await titles("/music/coverflow"), ["Mixtape", "Parklife"])
        self.assertEqual(await titles("/music/albums"), ["Mixtape", "Parklife"])
        self.assertEqual(await opened("/music/albums", "al:0"), ["Track 1"], "no artist, and it opens")
        self.assertEqual(await opened("/music/albums", "al:1"), ["Tracy Jacks"])
        blur = (await pages.activate("/music/artists", "a:Blur"))["push"]
        self.assertEqual(await titles(blur), ["All Songs", "Parklife"])
        self.assertEqual(await opened(blur, "all"), ["Loose", "Tracy Jacks"], "the loose song is with its artist")
        nobody = (await pages.activate("/music/artists", "a:"))["push"]
        self.assertEqual(await opened(nobody, "all"), ["Nameless", "Track 1"])


class FavoritesTest(unittest.IsolatedAsyncioTestCase):
    """A favourite is a line of Favorites.m3u in the music folder, the first playlist listed."""

    async def test_a_song_is_marked_listed_and_taken_out_again(self):
        d = tempfile.mkdtemp()
        favorites = os.path.join(d, "Favorites.m3u")

        class Mpd:
            files = ["a.mp3", "b.mp3", "c.mp3"]

            async def call(self, name, *args):
                if name == "listallinfo":
                    lists = [("playlist", "Favorites.m3u")] if os.path.exists(favorites) else []
                    return [p for f in self.files for p in (("file", f), ("title", f[0].upper()))] + lists
                if name == "listplaylists":
                    return [("playlist", "Drive")]
                return []

        class NoApp:
            t = Translator("en")
            state = State(lambda snap: None)

            class power:
                sleep_timer_left = staticmethod(lambda: 0)

        app = NoApp()
        music = app.music = Music(Mpd(), app.state, tempfile.mkdtemp(), music_dir=d)
        await music.load_library()
        app.state.data["player"]["file"] = "b.mp3"
        pages = Pages(app)

        async def choose(sheet):
            await pages.resolve(sheet["sheet"]["token"], "favorite")

        async def rows(path, field="title"):
            return [i.get(field) for i in (await pages.build(path))["items"]]

        self.assertEqual(await rows("/music/playlists"), ["Favorites", "Drive"])
        self.assertEqual(await rows("/music/favorites"), [], "there is no such file yet")

        for key in ("s2", "s1"):
            sheet = await pages.alt("/music/songs", key)
            self.assertIn({"key": "favorite", "title": "Favorite", "value": None}, sheet["sheet"]["items"])
            await choose(sheet)
        self.assertEqual(await rows("/music/favorites"), ["C", "B"], "in the order they were added")
        self.assertEqual(await rows("/music/songs", "star"), [None, True, True])
        await music.load_library()
        self.assertEqual(await rows("/music/playlists"), ["Favorites", "Drive"], "and listed once")
        self.assertTrue(app.state.get("player")["favorite"], "the song playing is one of them")

        sheet = await pages.now_playing_options()
        self.assertEqual(sheet["sheet"]["items"][0], {"key": "favorite", "title": "Favorite", "value": "On"})
        await choose(sheet)
        self.assertEqual(await rows("/music/favorites"), ["C"])
        self.assertFalse(app.state.get("player")["favorite"])

        Mpd.files = ["a.mp3"]
        await music.load_library()
        self.assertEqual(await rows("/music/favorites"), [], "a song taken off the card is not listed")
        await music.set_favorite("a.mp3", True)
        with open(favorites, encoding="utf-8") as f:
            self.assertEqual(f.read(), "c.mp3\na.mp3\n", "and its line is kept")
        Mpd.files = ["a.mp3", "c.mp3"]
        await music.load_library()
        self.assertEqual(await rows("/music/favorites"), ["C", "A"], "for when it is back")


class PlaylistFilesTest(unittest.IsolatedAsyncioTestCase):
    """An .m3u anywhere in the music folder is a playlist, whichever computer wrote its paths."""

    async def test_playlists_in_the_music_folder(self):
        songs = ["Blur/Parklife/01 Girls & Boys.mp3", "Blur/Parklife/02 Tracy Jacks.mp3",
                 "Blur/Leisure/01 Intro.mp3", "Pulp/Different Class/01 Intro.mp3",
                 unicodedata.normalize("NFD", "한강/밤.mp3")]
        lists = {
            "Lists/Road.m3u": "\ufeff#EXTM3U\r\n#EXTINF:290,Blur - Girls & Boys\r\n"
                              "/Users/me/Music/Blur/Parklife/01 Girls & Boys.mp3\r\n"
                              "C:\\Music\\PULP\\Different Class\\01 Intro.mp3\r\n"
                              "Blur/Leisure/01 Intro.mp3\r\n01 Intro.mp3\r\nGone/Song.mp3\r\n한강/밤.mp3\r\n",
            "Blur/Parklife/Side A.M3U8": "02 Tracy Jacks.mp3\n../Leisure/01 Intro.mp3\n",
            "Blur/Parklife/Parklife.cue": "",
        }
        d = tempfile.mkdtemp()
        for name, text in lists.items():
            os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
            with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as f:
                f.write(text)

        class Mpd:
            async def call(self, name, *args):
                if name == "listallinfo":
                    return [("file", f) for f in songs] + [("playlist", p) for p in lists]
                if name == "listplaylists":
                    return [("playlist", "Drive")]
                return []

        class NoApp:
            t = Translator("en")
            state = State(lambda snap: None)

        app = NoApp()
        app.music = Music(Mpd(), app.state, tempfile.mkdtemp(), music_dir=d)
        await app.music.load_library()
        pages = Pages(app)

        async def opened(key):
            built = await pages.build((await pages.activate("/music/playlists", key))["push"])
            return built["title"], [i["title"] for i in built["items"]]

        listed = (await pages.build("/music/playlists"))["items"]
        self.assertEqual([i["title"] for i in listed], ["Favorites", "Drive", "Road", "Side A"])
        self.assertEqual(await opened("p:Blur/Parklife/Side A.M3U8"), ("Side A", ["02 Tracy Jacks", "01 Intro"]))
        road = await app.music.playlist_songs("Lists/Road.m3u")
        self.assertEqual([s["file"] for s in road], [songs[0], songs[3], songs[2], songs[4]],
                         "the bare 01 Intro.mp3 could be either album's, and Gone is not here")


class NowPlayingTest(unittest.IsolatedAsyncioTestCase):
    """What Now Playing shows beside the cover: a line about the song, and words that are timed."""

    async def now_playing(self, file, **tags):
        class NoApp:
            t = Translator("en")
            state = State(lambda snap: None)

        d = tempfile.mkdtemp()
        with open(os.path.join(d, "timed.lrc"), "w") as f:
            f.write("[00:01.00]One\n[00:05.00]Two\n")
        with open(os.path.join(d, "plain.txt"), "w") as f:
            f.write("Words without times\n")
        app = NoApp()
        app.music = Music(None, app.state, d, music_dir=d)
        app.music._by_file = {file: {"year": "", "genre": "", **tags}}
        app.state.data["player"]["file"] = file
        page = await Pages(app).build("/nowplaying")
        return page["note"], [i["title"] for i in page["items"]], page["times"]

    async def test_timed_words_and_a_line_about_the_song(self):
        self.assertEqual(await self.now_playing("timed.flac", year="2019", genre="Jazz"),
                         ("2019 · Jazz · FLAC", ["One", "Two"], [1.0, 5.0]))

    async def test_words_without_times_stay_on_their_own_screen(self):
        self.assertEqual(await self.now_playing("plain.mp3"), ("MP3", [], None))

    async def test_a_song_started_again_twice_sets_the_clock_twice(self):
        class Mpd:
            async def call(self, name, *args):
                return {"status": [("state", "play"), ("elapsed", "0.000"), ("song", "0")],
                        "currentsong": [("file", "a.mp3"), ("Title", "A")]}[name]

        sent = []
        music = Music(Mpd(), State(sent.append), tempfile.mkdtemp())
        music._no_art.add("a.mp3")
        for _ in range(2):
            await music.refresh()
            await asyncio.sleep(0.1)
            music._elapsed = (0.0, time.monotonic() - 2)
        self.assertEqual(len(sent), 2, "the shell's clock had run on two seconds")


class RescanTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_scan_follows_the_last_time_mpd_came_up(self):
        asked = []

        class Mpd:
            async def call(self, name, *args):
                asked.append(name)
                return []

        music = Music(Mpd(), State(lambda snap: None), tempfile.mkdtemp())
        music.RESCAN_AFTER = 0.1
        await music._changed({"reconnect"})
        await asyncio.sleep(0.05)
        await music._changed({"reconnect"})
        self.assertNotIn("update", asked, "not before MPD has been up a while")
        await asyncio.sleep(0.2)
        self.assertEqual(asked.count("update"), 1, "and once, though MPD came up twice")


class CoverArtTest(unittest.IsolatedAsyncioTestCase):
    async def test_songs_sharing_a_folder_keep_their_own_album_covers(self):
        class Pictures:
            async def picture(self, uri):
                return uri.encode(), "image/jpeg"

        music = Music(Pictures(), State(lambda snap: None), tempfile.mkdtemp())
        music.songs = [{"file": "01 Girls & Boys.mp3", "albumartist": "Blur", "album": "Parklife"},
                       {"file": "04 Parklife.mp3", "albumartist": "Blur", "album": "Parklife"},
                       {"file": "06 ROBOTMAN.mp3", "albumartist": "Someone", "album": "Else"},
                       {"file": "untagged.mp3", "albumartist": "", "album": ""},
                       {"file": "untagged too.mp3", "albumartist": "", "album": ""}]
        music._by_file = {s["file"]: s for s in music.songs}
        covers = [await music.art_path(s["file"]) for s in music.songs]
        self.assertEqual(covers[0], covers[1], "one cover for an album")
        self.assertEqual(len(set(covers)), 4, "and never one cover for a whole folder")


def on_air(uuid, name, **more):
    """A station as Radio Browser gives it."""
    return {"stationuuid": uuid, "name": name, "url_resolved": f"http://{uuid}.example/stream", "hls": 0,
            "favicon": f"http://{uuid}.example/logo.png", "tags": "jazz,smooth jazz,lounge,late night",
            "countrycode": "FR", "codec": "MP3", "bitrate": 128, **more}


class Stations:
    """Radio Browser as far as isoPod asks it: it notes what was asked and can be out of reach."""

    def __init__(self, found):
        self.found, self.asked, self.down = found, [], ()

    async def get(self, path, **params):
        self.asked.append(params.get("tag") or params.get("countrycode") or params.get("name") or path)
        if self.asked[-1] in self.down:
            raise RadioError("out of reach")
        if path == "/json/countries":
            return [{"iso_3166_1": "FR", "stationcount": 3690}, {"iso_3166_1": "JP", "stationcount": 400},
                    {"iso_3166_1": "fr", "stationcount": 12}, {"iso_3166_1": "AD", "stationcount": 3}]
        if path == "/json/stations/byuuid":
            return [s for s in self.found if s["stationuuid"] in params["uuids"].split(",")]
        return self.found


def aged(*paths):
    """Makes files a little more than a week old."""
    for path in paths:
        os.utime(path, (time.time() - LIFE - 60,) * 2)


class RadioTest(unittest.IsolatedAsyncioTestCase):
    """Stations come from Radio Browser: a list is kept from the first time it is opened, and
    is fetched again only once it is a week old."""

    def radio(self, found, d=None, **more):
        self.dir = d or tempfile.mkdtemp()
        self.stations = Stations(found)
        return Radio(self.dir, self.dir, directory=self.stations, **more)

    def test_a_station_as_isopod_keeps_it(self):
        s = station_from_api(on_air("x", " Jazz ", codec="UNKNOWN"))
        self.assertEqual((s["name"], s["codec"], s["genres"], s["country"]),
                         ("Jazz", "", ["jazz", "lounge"], "FR"), "of its own words, the genres isoPod lists")
        self.assertEqual(station_from_api(on_air("r", "R", tags="rock,classic rock,2000s"))["genres"],
                         ["classicrock", "rock"], "the narrower genre first")
        self.assertEqual(station_from_api(on_air("n", "N", tags="1930,aac,france,Hip-Hop"))["genres"], ["hiphop"])
        hls = station_from_api(on_air("y", "Live", url_resolved="https://x/playlist.m3u8", hls=1))
        self.assertEqual(hls["url"], "hls+https://x/playlist.m3u8", "the scheme MPD plays HLS under")
        self.assertEqual(station_from_api(on_air("z", "Old", url_resolved="mms://x/stream"))["url"], "")
        self.assertEqual(station_from_api(on_air("f", "File", url_resolved="file:///etc/passwd"))["url"], "")

    def test_the_same_station_listed_twice_shows_once(self):
        rows = [station_from_api(r) for r in (
            on_air("1", "KBS Classic FM"), on_air("2", "kbs classic-fm"),
            on_air("3", "KBS Classic FM", countrycode="US"),
            on_air("4", "Another Name", url_resolved="http://1.example/stream"),
            on_air("5", "Unplayable", url_resolved="rtsp://x"))]
        self.assertEqual([s["uuid"] for s in unique(rows)], ["1", "3"],
                         "one name in one country, one address: the first of each stays")

    async def test_a_list_is_asked_for_once_and_then_kept(self):
        radio = self.radio([on_air(str(i), f"Station {i}") for i in range(KEEP + 30)])
        self.assertEqual(len(await radio.top()), KEEP)
        await radio.top()
        await radio.genre("jazz")
        await radio.country("FR")
        self.assertEqual(await radio.countries(), ["FR", "JP"], "countries with stations enough, by their code")
        self.assertEqual(await radio.genre("made up"), [], "only the genres isoPod lists")
        self.assertEqual(self.stations.asked, ["/json/stations/search", "jazz", "FR", "/json/countries"])

        again = self.radio([], self.dir)
        self.assertEqual(len(await again.top()), KEEP, "kept on the card: isoPod was off in between")
        self.assertEqual(self.stations.asked, [])

    async def test_a_search_is_asked_each_time_isopod_is_switched_on(self):
        radio = self.radio([on_air("1", "Jazz FM")])
        self.assertEqual([s["name"] for s in await radio.search("jazz")], ["Jazz FM"])
        await radio.search("Jazz ")
        self.assertEqual(self.stations.asked, ["jazz"])
        await self.radio([], self.dir).search("jazz")
        self.assertEqual(self.stations.asked, ["jazz"], "not kept on the card")

    async def test_out_of_reach_keeps_nothing_and_asks_again(self):
        radio = self.radio([on_air("1", "One")])
        self.stations.down = ("jazz",)
        with self.assertRaises(RadioError):
            await radio.genre("jazz")
        self.stations.down = ()
        self.assertEqual(len(await radio.genre("jazz")), 1)

    async def test_a_list_a_week_old_is_shown_as_it_was_and_fetched_again_behind_that(self):
        told = []
        radio = self.radio([on_air("1", "One"), on_air("2", "Two")])
        radio.changed = lambda: told.append("changed")
        await radio.top()
        await radio.genre("jazz")
        self.stations.found = [on_air("2", "Two, renamed"), on_air("9", "Nine")]
        self.stations.asked.clear()
        await radio.top()
        await asyncio.sleep(0.1)
        self.assertEqual(self.stations.asked, [], "not before it has aged")

        aged(radio._file("top"), radio._file("genre-jazz"))
        self.stations.down = ("jazz",)
        self.assertEqual([s["name"] for s in await radio.top()], ["One", "Two"], "at once, as it was")
        await radio.genre("jazz")
        await asyncio.sleep(0.3)
        self.assertEqual([s["name"] for s in await radio.top()], ["Two, renamed", "Nine"])
        self.assertEqual(told, ["changed"], "the screen is told to draw the new list")
        self.assertEqual([s["name"] for s in await radio.genre("jazz")], ["One", "Two"], "out of reach: it stays")

        self.stations.down = ()
        await radio.genre("jazz")
        await asyncio.sleep(0.1)
        self.assertEqual(self.stations.asked.count("jazz"), 1, "and it is not tried over and over")
        radio._tried.clear()
        await radio.genre("jazz")
        await asyncio.sleep(0.3)
        self.assertEqual([s["name"] for s in await radio.genre("jazz")], ["Two, renamed", "Nine"], "but later")

    async def test_a_favourite_follows_its_station(self):
        radio = self.radio([on_air("1", "One"), on_air("2", "Two")])
        radio.toggle_favorite((await radio.top())[1])
        self.stations.found = [on_air("2", "Two, renamed", url_resolved="http://moved.example/stream")]
        radio.check_favorites()
        await asyncio.sleep(0.3)
        self.assertEqual([(s["name"], s["url"]) for s in radio.favorites],
                         [("Two, renamed", "http://moved.example/stream")])
        self.stations.asked.clear()
        radio.check_favorites()
        await asyncio.sleep(0.1)
        self.assertEqual(self.stations.asked, [], "asked once a week, not each time they are opened")

        aged(radio._asked)
        radio._tried.clear()
        self.stations.found = []
        radio.check_favorites()
        await asyncio.sleep(0.3)
        self.assertEqual(self.stations.asked, ["/json/stations/byuuid"])
        self.assertEqual(len(radio.favorites), 1, "a favourite the directory dropped stays a favourite")

    async def test_a_logo_is_fetched_once_and_one_that_cannot_be_had_waits_a_week(self):
        asked, arrived = [], []

        def download(url):
            asked.append(url)
            return b"" if "dead" in url else b"picture"

        def shrink(data, path):
            with open(path, "wb") as f:
                f.write(data)
            return True

        radio = self.radio([on_air("1", "One"), on_air("2", "Two", favicon="http://dead.example/logo.png"),
                            on_air("3", "Three", favicon="")], download=download, shrink=shrink)
        top = await radio.top()
        self.assertEqual([radio.logo(s) for s in top], [None, None, None])
        for _ in range(2):
            radio.want_logos(top, lambda s: arrived.append(s["name"]))
            radio.want_logos(top, lambda s: arrived.append(s["name"]))
            await asyncio.sleep(0.2)
        self.assertEqual(sorted(asked), ["http://1.example/logo.png", "http://dead.example/logo.png"])
        self.assertEqual(arrived, ["One"])
        self.assertTrue(radio.logo(top[0]).endswith(".png"))
        self.assertIsNone(radio.logo(top[1]))

        aged(radio._logo_base(top[1]) + ".no")
        radio.want_logos(top, arrived.append)
        await asyncio.sleep(0.2)
        self.assertEqual(asked.count("http://dead.example/logo.png"), 2, "asked for once more a week later")

        self.stations.found = [on_air("3", "Three", favicon="")]
        aged(radio._file("top"))
        await radio.top()
        await asyncio.sleep(0.3)
        self.assertEqual(os.listdir(radio.logo_dir), [], "the logos and marks of stations no list shows are gone")


class RadioPagesTest(unittest.IsolatedAsyncioTestCase):
    """The radio's screens, and Now Playing while a station is on."""

    async def asyncSetUp(self):
        class Mpd:
            sent, broken, url = [], (), ""

            async def call(self, name, *args):
                if name == "status" and self.url in self.broken:
                    return [("state", "stop"), ("error", "Failed to decode")]
                return {"status": [("state", "play"), ("song", "0"), ("playlistlength", "1"), ("elapsed", "3.0"),
                                   ("audio", "44100:16:2")],
                        "currentsong": [("file", self.url), ("Title", "Aurora Lane - Neon Coast"),
                                        ("Name", "the name the stream gives itself")]}.get(name, [])

            async def call_list(self, commands):
                if commands[0] == ("clear",):
                    self.url = commands[1][1]
                    self.sent.append(self.url)

        class NoApp:
            t = Translator("en")
            changed, toasts = [], []

            class power:
                sleep_timer_left = staticmethod(lambda: 0)

            def page_changed(self, *paths):
                self.changed += paths

            def toast(self, text, icon=None):
                self.toasts.append(text)

        d = tempfile.mkdtemp()
        app = self.app = NoApp()
        app.state = State(lambda snap: None)
        self.mpd = Mpd()
        app.music = Music(self.mpd, app.state, d)
        self.stations = Stations([on_air("1", "Jazz FM"), on_air("2", "Radio Nova", countrycode="JP", bitrate=0),
                                  on_air("3", "FIP", tags="")])
        app.radio = Radio(d, d, directory=self.stations, download=lambda url: b"")
        app.radio.changed = lambda: app.page_changed("/radio")
        app.music.station_favorite = lambda station: app.radio.is_favorite(station.get("uuid"))
        self.pages = Pages(app)
        app.music.stream_failed = lambda: app.toast(self.pages.unreachable())
        app.state.data["wifi"]["ssid"] = "Home"
        app.music.step_station = self.pages.step_station
        self.addCleanup(lambda: app.music._stream_watch and app.music._stream_watch.cancel())

    async def rows(self, path, field="title"):
        return [i.get(field) for i in (await self.pages.build(path))["items"] if not i.get("header")]

    async def test_the_menu_and_what_a_row_says_of_a_station(self):
        self.assertEqual(await self.rows("/radio"), ["Favorites", "Popular", "Genres", "Countries", "Search"])
        genres = await self.rows("/radio/genres")
        self.assertEqual(genres[:3] + genres[-3:], ["Alternative", "Ambient", "Anime", "90s", "2000s", "2010s"],
                         "by name, and the decades after them in their own order")
        self.app.t = Translator("ko")
        self.assertEqual((await self.rows("/radio/genres"))[0], "Alternative", "in English in any language")
        self.assertEqual((await self.rows("/radio/top", "subtitle"))[0], "프랑스 · Jazz")
        self.assertEqual((await self.pages.build("/radio/genre/00s"))["title"], "2000s")
        self.app.t = Translator("en")
        self.assertEqual((await self.pages.activate("/radio/genres", "g:jazz"))["push"], "/radio/genre/jazz")
        self.assertEqual(await self.rows("/radio/countries"), ["France", "Japan"])
        self.assertEqual((await self.pages.activate("/radio/countries", "c:JP"))["push"], "/radio/country/JP")
        self.assertEqual(await self.rows("/radio/top", "subtitle"), ["France · Jazz", "Japan · Jazz", "France"])
        self.assertEqual(await self.rows("/radio/genre/jazz", "subtitle"), ["France", "Japan", "France"],
                         "the list says what they play")
        self.assertEqual(await self.rows("/radio/country/FR", "subtitle"), ["Jazz · Lounge"] * 2 + [None],
                         "the list says where they are")
        self.assertEqual((await self.pages.build("/radio/country/FR"))["title"], "France")
        search = (await self.pages.resolve((await self.pages.activate("/radio", "search"))["input"]["token"],
                                           " nova "))["push"]
        self.assertEqual((await self.pages.build(search))["title"], "Search for “nova”")

    async def test_a_list_that_is_not_kept_yet_says_so_when_out_of_reach(self):
        self.stations.down = ("/json/stations/search", "/json/countries")
        for path in ("/radio/top", "/radio/countries"):
            page = await self.pages.build(path)
            self.assertEqual((page["items"], page["empty"]["title"]), ([], "Couldn’t Connect"))
        self.app.state.data["wifi"]["ssid"] = None
        self.assertEqual((await self.pages.build("/radio/top"))["empty"]["title"], "Wi-Fi Needed",
                         "with no network joined it says what is missing")
        self.stations.down = ()
        self.assertEqual(len(await self.rows("/radio/top")), 3)
        self.stations.down = ("/json/stations/search",)
        self.assertEqual(len(await self.rows("/radio/top")), 3, "one that is kept opens all the same")

    async def test_a_station_is_tuned_in_marked_and_followed_by_its_neighbours(self):
        pages, state = self.pages, self.app.state
        self.assertEqual(await pages.activate("/radio/top", "st:2"), {"nowplaying": True})
        self.assertEqual(self.mpd.sent, ["http://2.example/stream"])
        player = state.get("player")
        self.assertEqual((player["kind"], player["title"], player["buffering"], player["favorite"]),
                         ("radio", "Radio Nova", True, False), "shown at once, before it is heard")
        self.assertNotIn("/json/url/2", self.stations.asked, "and nobody is told what was played")
        self.assertEqual(await self.rows("/radio/top", "accessory"), ["none", "playing", "none"])

        await self.app.music.refresh()
        player = state.get("player")
        self.assertEqual((player["title"], player["artist"], player["buffering"], player["duration"]),
                         ("Radio Nova", "Aurora Lane - Neon Coast", False, 0.0), "its name, and what it says is on")
        page = await pages.build("/nowplaying")
        self.assertEqual((page["note"], page["items"]), ("Japan · MP3", []))

        sheet = await pages.now_playing_options()
        self.assertEqual([o["key"] for o in sheet["sheet"]["items"]], ["favorite", "timer"],
                         "shuffle, repeat and a queue are a song's")
        await pages.resolve(sheet["sheet"]["token"], "favorite")
        self.assertTrue(state.get("player")["favorite"])
        self.assertEqual(await self.rows("/radio/favorites"), ["Radio Nova"])
        self.assertEqual(await self.rows("/radio/top", "star"), [None, True, None])
        self.assertIn("/radio/*", self.app.changed)

        sheet = await pages.alt("/radio/favorites", "st:2")
        self.assertEqual(sheet["sheet"]["items"], [{"key": "favorite", "title": "Favorite", "value": "On"}])
        await pages.resolve(sheet["sheet"]["token"], "favorite")
        self.assertEqual(await self.rows("/radio/favorites"), [])
        self.assertFalse(state.get("player")["favorite"])

        await self.app.music.next()
        self.assertEqual(state.get("player")["title"], "FIP", "the next station of the list it was picked from")
        await self.app.music.next()
        self.assertEqual(state.get("player")["title"], "Jazz FM", "and round to the first")
        await self.app.music.previous()
        self.assertEqual(state.get("player")["title"], "FIP")
        await self.app.music.seek(10)
        self.assertEqual(len(self.mpd.sent), 4, "a station cannot be wound")

    async def test_an_hls_station_is_tried_under_both_of_its_addresses(self):
        plain = "https://nhk.example/live.m3u8"
        self.stations.found = [on_air("h", "NHK", url_resolved=plain, hls=1)]
        self.mpd.broken = ("hls+" + plain,)
        await self.pages.activate("/radio/top", "st:h")
        await self.app.music._stream_watch
        self.assertEqual(self.mpd.sent, [plain], "as a plain address first")

        self.mpd.broken = (plain,)
        await self.pages.activate("/radio/top", "st:h")
        await self.app.music._stream_watch
        self.assertEqual(self.mpd.sent[1:], [plain, "hls+" + plain], "then the way FFmpeg alone reads it")
        player = self.app.state.get("player")
        self.assertEqual((player["title"], player["state"], player["buffering"]), ("NHK", "play", False))
        self.assertEqual(await self.rows("/radio/top", "accessory"), ["playing"])
        self.assertEqual(self.app.toasts, [])

        self.mpd.broken = (plain, "hls+" + plain)
        await self.pages.activate("/radio/top", "st:h")
        await self.app.music._stream_watch
        self.assertEqual(self.app.toasts, ["Couldn’t Connect"], "under neither: it says so, once")


class FolderListingTest(unittest.TestCase):
    def test_videos_hide_dot_names_and_sort_by_title(self):
        d = tempfile.mkdtemp()
        for sub in ("Trips", ".hidden"):
            os.makedirs(os.path.join(d, sub))
        for name in ("The Road.mkv", "notes.xyz", "데미안.mp4", "Arc.webm"):
            open(os.path.join(d, name), "w").close()
        dirs, files = folder_listing(d, VIDEO_EXTS)
        self.assertEqual(dirs, ["Trips"])
        self.assertEqual(files, ["Arc.webm", "The Road.mkv", "데미안.mp4"])


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

    def close(self):
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

    async def test_a_computer_on_the_cable_keeps_it_awake_with_the_lid_shut(self):
        self.power.busy = lambda: True
        await self.power.lid(True)
        self.assertEqual((self.power.screen_on, self.events.slept), (False, 0))
        self.power.busy = lambda: False
        await self.power.lid(False)
        await self.power.lid(True)
        self.assertEqual(self.events.slept, 1, "and no longer once the computer has let go")

    async def test_a_computer_has_the_port_when_it_is_configured(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "class/udc/musb-hdrc.5.auto"))
        for state, connected in (("not attached", False), ("configured", True), ("suspended", False)):
            with open(os.path.join(root, "class/udc/musb-hdrc.5.auto/state"), "w") as f:
                f.write(state + "\n")
            self.assertEqual(System(sysfs=root).computer_connected(), connected, state)
        self.assertFalse(System(sysfs=tempfile.mkdtemp()).computer_connected(), "no port at all")

    async def test_music_keeps_playing_with_lid_shut(self):
        self.state.update("player", state="play")
        await self.power.lid(True)
        self.assertEqual((self.power.screen_on, self.events.slept), (False, 0))

    async def test_a_video_that_plays_keeps_playing_with_lid_shut_unless_it_always_sleeps(self):
        self.power.video = lambda: True
        await self.power.lid(True)
        self.assertEqual(self.events.slept, 0)
        await self.power.lid(False)
        self.power.settings["lid_keep_playing"] = False
        await self.power.lid(True)
        self.assertEqual(self.events.slept, 1)

    async def test_wake_press_after_sleep_turns_screen_on(self):
        await self.power.sleep()
        self.assertFalse(self.power.screen_on)
        await self.press()
        self.assertTrue(self.power.screen_on)
        self.power._lit_at -= 5
        await self.press()
        self.assertFalse(self.power.screen_on, "a later press turns the screen off")

    async def test_rocknix_is_told_the_brightness_so_its_wake_script_sets_the_same(self):
        told, settings = [], {"brightness": 35}
        power = Power(self.state, settings, self.events, sim=True, remember=told.append)
        power.apply_brightness()
        settings["brightness"] = 40
        power.apply_brightness()
        self.assertEqual(told, [], "not for every step of the slider")
        await asyncio.sleep(1.1)
        self.assertEqual(told, [40])

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

    async def test_on_the_device_a_waiting_app_is_stopped_and_closed_as_it_is(self):
        """`systemctl freeze` refuses on ROCKNIX's hybrid cgroups. A stopped unit needs no
        waking before `systemctl stop`: systemd sends SIGCONT after its SIGTERM."""
        module, ran = sys.modules[Apps.__module__], []

        async def run(*args, **kwargs):
            ran.append(" ".join(args))
            return 0, "active", ""
        self.addCleanup(setattr, module, "run", module.run)
        module.run = run
        apps = Apps(State(lambda snap: None), FakeEvents(), {}, runner="systemd")
        await apps.launch(AppSpec("a", "a", ["a"]), "a")
        await apps.home()
        await apps.resume("a")
        await apps.set_dark(True)
        await apps.close("a")
        self.assertEqual([c for c in ran if c.startswith("systemctl")], [
            "systemctl kill --signal=SIGSTOP isopod-a-2.service",
            "systemctl kill --signal=SIGCONT isopod-a-2.service",
            "systemctl kill --signal=SIGSTOP isopod-a-2.service",
            "systemctl stop isopod-a-2.service"])


    def _on_the_device(self, answers):
        """Apps as the device runs them, with systemd saying of each unit what `answers` holds."""
        module, events = sys.modules[Apps.__module__], FakeEvents()

        async def run(*args, **kwargs):
            return 0, answers.pop(0) if len(answers) > 1 else answers[0], ""
        self.addCleanup(setattr, module, "run", module.run)
        module.run = run
        events.failed = []
        events.app_failed = events.failed.append
        apps = Apps(State(lambda snap: None), events, {}, runner="systemd")
        for title, shown in (("book", True), ("film", False)):
            apps.running[title] = {"title": title, "unit": "isopod-" + title, "workspace": 2, "frozen": False,
                                   "shown": shown}
        return apps, events

    async def test_a_book_left_at_once_did_start_and_only_an_app_without_a_window_failed(self):
        apps, events = self._on_the_device(["inactive"])
        await asyncio.gather(apps._watch_start("book", "isopod-book"), apps._watch_start("film", "isopod-film"))
        self.assertEqual((events.failed, apps.running), (["film"], {}))

    async def test_an_app_whose_window_closed_is_looked_at_again_until_it_has_ended(self):
        apps, _ = self._on_the_device(["active", "active", "inactive"])
        await apps._reap("book", waits=(0, 0))
        self.assertIn("book", apps.running, "still ending at both looks")
        await apps._reap("book", waits=(0, 0))
        self.assertNotIn("book", apps.running)


class LyricsTest(unittest.TestCase):
    def test_synced_lrc(self):
        lines, times = parse_lrc("[ar:X]\n[offset:+500]\n[00:00.00]\n[00:12.50]Two\n[00:05.00][00:30.00]One and again\n")
        self.assertEqual(lines, ["One and again", "Two", "One and again"])
        self.assertEqual(times, [4.5, 12.0, 29.5])

    def test_plain_text(self):
        lines, times = parse_lrc("\nFirst line\n\nSecond verse\n\n")
        self.assertEqual((lines, times), (["First line", "", "Second verse"], None))
        self.assertIsNone(parse_lrc(""))

    def test_word_timing_is_left_out(self):
        lines, times = parse_lrc("[00:01.00]<00:01.00>Two <00:01.60>words\n")
        self.assertEqual((lines, times), (["Two words"], [1.0]))

    def test_utf16_from_windows_tools(self):
        text = "[00:01.00]가사\n"
        for raw in (text.encode("utf-16"), b"\xef\xbb\xbf" + text.encode(), text.encode()):
            self.assertEqual(parse_lrc(decode(raw)), (["가사"], [1.0]))

    async def lyrics_beside(self, song, *names):
        d = tempfile.mkdtemp()
        for name in names:
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write("[00:01.00]" + name + "\n")
        state = State(lambda snap: None)
        state.data["player"]["file"] = song
        found = await Music(None, state, d, music_dir=d).lyrics()
        return found and found[0]

    def test_the_file_named_like_the_song(self):
        import unicodedata
        run = asyncio.run
        self.assertEqual(run(self.lyrics_beside("a.mp3", "a.lrc", "a.txt", "b.lrc")), ["a.lrc"])
        self.assertEqual(run(self.lyrics_beside("Song.mp3", "song.LRC")), ["song.LRC"])
        typed_on_a_mac = unicodedata.normalize("NFD", "한강.lrc")
        self.assertEqual(run(self.lyrics_beside("한강.mp3", typed_on_a_mac)), [typed_on_a_mac])


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


class Mpv:
    """mpv as far as its JSON socket goes: it takes commands and reports when it is paused."""

    def __init__(self, path):
        self.path = path
        self.commands = []
        self.watchers = []

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, self.path)

    async def _client(self, reader, writer):
        while line := await reader.readline():
            command = json.loads(line)["command"]
            self.commands.append(command)
            if command[0] == "observe_property":
                self.watchers.append(writer)
            writer.write(b'{"error": "success"}\n')
            await writer.drain()
        writer.close()

    async def pause(self, paused):
        for writer in self.watchers:
            writer.write(json.dumps({"event": "property-change", "id": 1, "name": "pause",
                                     "data": paused}).encode() + b"\n")
            await writer.drain()
        await asyncio.sleep(0.05)


class VideoTest(unittest.IsolatedAsyncioTestCase):
    """A video in front: the gamepad is mpv's own, and the rest reaches it through isopodd."""

    async def asyncSetUp(self):
        d = tempfile.mkdtemp()
        self.mpv = Mpv(os.path.join(d, "mpv.sock"))
        await self.mpv.start()
        self.app = App(parse_args(["--sim", "--socket", os.path.join(d, "s"), "--config", d, "--cache", d,
                                   "--data", d, "--mpd", os.path.join(d, "mpd")]))
        self.app.audio.SETTLE = 0.02
        await self.app.audio.start()
        await asyncio.sleep(0.1)
        self.toggled = []

        async def toggle():
            self.toggled.append("music")
        self.app.music.toggle = toggle

    async def asyncTearDown(self):
        self.mpv.server.close()

    def watch(self):
        """What launching does on a device, where the simulator only notes the launch."""
        apps = self.app.apps
        apps.sim = False
        film = {"title": "Film", "unit": "isopod-video-2", "workspace": 2, "frozen": False, "app_id": "a",
                "media": True, "args": [], "awake": 0, "control": self.mpv.path, "playing": True,
                "used_at": 0.0}
        apps.running["video"], apps.front = film, "video"
        return film

    async def test_up_and_down_are_the_volume_and_it_shows_over_the_film(self):
        self.watch()
        before = self.app.state.get("volume")["level"]
        await self.app.key("up")
        await self.app.key("up", repeat=True)
        await self.app.key("left")
        self.assertEqual(self.app.state.get("volume")["level"], before + 10)
        shown = [c for c in self.mpv.commands if "show-text" in c]
        self.assertEqual(len(shown), 2)
        self.assertIn("음량", shown[0][2])

    async def test_a_headsets_buttons_go_to_the_film_in_front_else_to_the_music(self):
        await self.app.media("toggle")
        self.assertEqual((self.toggled, self.mpv.commands), (["music"], []))
        film = self.watch()
        for action in ("toggle", "pause", "forward"):
            await self.app.media(action)
        self.assertEqual(self.mpv.commands, [["cycle", "pause"], ["set_property", "pause", True], ["seek", 15]])
        film["frozen"] = True
        await self.app.media("play")
        self.assertEqual((len(self.mpv.commands), self.toggled), (3, ["music"]), "a frozen film hears nothing")

    async def test_a_headset_is_told_of_the_film_in_front_and_else_of_the_music(self):
        told = []
        self.app.bluetooth.set_playback = told.append
        for state, status in (("play", "Playing"), ("pause", "Paused")):
            self.app.state.update("player", state=state)
            await asyncio.sleep(State.DEBOUNCE * 2)
            self.assertEqual(told[-1], status)
        film = self.watch()
        self.app.playback_changed()
        self.assertEqual(told[-1], "Playing", "the music is paused under a film that plays")
        film["playing"] = False
        self.app.playback_changed()
        self.assertEqual(told[-1], "Paused")
        self.app.apps.front = None
        self.app.state.update("player", state="stop")
        await asyncio.sleep(State.DEBOUNCE * 2)
        self.assertEqual(told[-1], "Stopped")

    async def test_a_film_sent_to_the_back_is_paused_and_goes_on_when_it_returns(self):
        film = self.watch()
        await self.app.apps.home()
        self.assertEqual((film["frozen"], self.mpv.commands), (True, [["set_property", "pause", True]]))
        await self.app.resume_app("video")
        self.assertEqual((film["frozen"], self.mpv.commands[-1]), (False, ["set_property", "pause", False]))

    async def test_a_film_paused_by_hand_stays_paused(self):
        film = self.watch()
        film["playing"] = False
        await self.app.apps.home()
        await self.app.resume_app("video")
        self.assertEqual(self.mpv.commands, [])
        await self.app.apps.set_dark(True)
        await self.app.apps.set_dark(False)
        self.assertEqual(self.mpv.commands, [])

    async def test_with_the_lid_shut_a_playing_film_is_heard_without_its_picture(self):
        film = self.watch()
        task = asyncio.ensure_future(self.app.apps._watch_playback("video", film))
        await asyncio.sleep(0.1)
        await self.app.lid(True)
        await asyncio.sleep(0.1)
        self.assertEqual((film["frozen"], self.app.power._suspended), (False, False))
        self.assertEqual(self.mpv.commands[-1], ["set_property", "vid", "no"])
        self.assertIsNone(self.app.power._sleep_deadline(), "while it plays isoPod stays up")
        await self.mpv.pause(True)
        self.assertIsNotNone(self.app.power._sleep_deadline(), "paused, it sleeps as the music would")
        await self.app.lid(False)
        await asyncio.sleep(0.1)
        self.assertEqual((film["frozen"], self.mpv.commands[-1]), (False, ["set_property", "vid", "auto"]))
        task.cancel()

    async def test_with_the_lid_shut_a_paused_film_waits_and_isopod_sleeps(self):
        film = self.watch()
        film["playing"] = False
        await self.app.lid(True)
        await asyncio.sleep(0.1)
        self.assertEqual((film["frozen"], self.app.power._suspended, self.mpv.commands), (True, True, []))

    async def test_sleep_pauses_a_film_heard_with_the_lid_shut(self):
        self.watch()
        await self.app.lid(True)
        await asyncio.sleep(0.1)
        await self.app.power._sleep_timer_done()
        await asyncio.sleep(0.1)
        self.assertEqual(self.mpv.commands[-1], ["set_property", "pause", True])

    async def test_a_paused_film_lets_the_screen_go(self):
        film = self.watch()
        task = asyncio.ensure_future(self.app.apps._watch_playback("video", film))
        await asyncio.sleep(0.1)
        self.assertEqual(self.app.screen_timeout(60), 0, "playing, it holds the screen")
        await self.mpv.pause(True)
        self.assertEqual(self.app.screen_timeout(60), 60)
        await self.mpv.pause(False)
        self.assertEqual(self.app.screen_timeout(60), 0)
        task.cancel()


class BooksTest(unittest.IsolatedAsyncioTestCase):
    """A book is picked in isoPod's list and read in KOReader, which gets its buttons from isoPod."""

    SYSTEM = os.path.join(os.path.dirname(__file__), "..", "..", "system")

    async def asyncSetUp(self):
        d = tempfile.mkdtemp()
        self.books = os.path.join(d, "books")
        for folder in ("Classics", "데미안.sdr"):
            os.makedirs(os.path.join(self.books, folder))
        for name in ("데미안.epub", "notes.xyz", "A Tale.fb2.zip", "Classics/Emma.epub"):
            open(os.path.join(self.books, name), "w").close()
        self.app = App(parse_args(["--sim", "--socket", os.path.join(d, "s"), "--config", d, "--cache", d,
                                   "--data", d, "--books", self.books, "--mpd", os.path.join(d, "mpd")]))
        self.toggled = 0
        self.skipped = []

        async def toggle():
            self.toggled += 1

        async def step(way):
            self.skipped.append(way)
        self.app.music.toggle = toggle
        self.app.music.next = lambda: step("next")
        self.app.music.previous = lambda: step("previous")

    async def test_the_list_is_books_and_folders_without_koreaders_notes(self):
        page = await self.app.pages.build("/ebooks")
        self.assertEqual([(i["key"], i["title"]) for i in page["items"]],
                         [("d:Classics", "Classics"), ("f:A Tale.fb2.zip", "A Tale"), ("f:데미안.epub", "데미안")])
        inside = await self.app.pages.build("/ebooks/Classics")
        self.assertEqual((inside["title"], [i["key"] for i in inside["items"]]), ("Classics", ["f:Emma.epub"]))

    async def test_a_book_opens_in_koreader_and_the_one_open_is_picked_up_again(self):
        apps = self.app.apps
        await self.app.pages.activate("/ebooks", "f:데미안.epub")
        first = apps.running["koreader"]
        self.assertEqual((apps.front, first["title"], first["args"]),
                         ("koreader", "데미안", [os.path.join(self.books, "데미안.epub")]))
        await apps.home()
        home = await self.app.pages.build("/")
        self.assertEqual([(i["title"], i["preview"]) for i in home["items"] if i.get("key") == "app:koreader"],
                         [("데미안", "book")])
        await self.app.pages.activate("/ebooks", "f:데미안.epub")
        self.assertIs(apps.running["koreader"], first)
        self.assertEqual(apps.front, "koreader")
        await apps.home()
        await self.app.pages.activate("/ebooks/Classics", "f:Emma.epub")
        self.assertEqual(apps.running["koreader"]["args"], [os.path.join(self.books, "Classics", "Emma.epub")])

    async def test_home_on_the_home_screen_goes_back_to_the_app_it_was_last_pressed_in(self):
        apps, sent = self.app.apps, []
        self.app.server.broadcast = lambda msg: msg["event"] in ("key", "present") and sent.append(msg)
        await self.app.pages.activate("/", "home")
        self.assertIsNone(apps.front, "Home was pressed in no app yet")
        await self.app.launch(self.app.catalog["video"], "film", ["film.mp4"])
        await self.app.key("home")
        await self.app.pages.activate("/ebooks", "f:데미안.epub")
        await self.app.key("home")
        self.assertEqual((apps.front, sent[-1]), (None, {"event": "present", "do": {"home": True}}))
        await self.app.key("home")
        self.assertEqual(sent[-1], {"event": "key", "key": "home"}, "the shell knows where it is")
        await self.app.pages.activate("/", "home")
        self.assertEqual(apps.front, "koreader")
        await self.app.key("home")
        await apps.close("koreader")
        await self.app.pages.activate("/", "home")
        self.assertEqual((apps.front, sorted(apps.running)), (None, ["video"]), "that app is closed")

    async def test_start_is_the_music_behind_a_book_and_the_rest_is_koreaders(self):
        await self.app.pages.activate("/ebooks", "f:데미안.epub")
        before = self.app.state.get("volume")["level"]
        for name in ("up", "down", "confirm", "pageup", "pagedown"):
            await self.app.key(name)
        self.assertEqual((self.toggled, self.app.state.get("volume")["level"]), (0, before))
        await self.app.key("play")
        self.assertEqual(self.toggled, 1)

    async def test_l2_and_r2_change_the_song_in_a_list_and_behind_a_book_but_not_under_a_film_or_a_shut_lid(self):
        sent = []
        self.app.server.broadcast = lambda msg: msg["event"] == "key" and sent.append(msg)
        await self.app.key("next")
        await self.app.pages.activate("/ebooks", "f:데미안.epub")
        await self.app.key("previous")
        self.assertEqual((self.skipped, sent), (["next", "previous"], []), "the shell is not asked")
        await self.app.key("home")
        await self.app.launch(self.app.catalog["video"], "film", ["film.mp4"])
        await self.app.key("next")
        await self.app.key("home")
        self.app.power.lid_closed = True
        await self.app.key("next")
        self.assertEqual(self.skipped, ["next", "previous"])

    def test_koreader_gets_its_buttons_and_menu_at_every_start_and_its_settings_once(self):
        home = os.path.join(tempfile.mkdtemp(), "koreader")
        prepare_koreader(self.SYSTEM, "/storage/books", "ko_KR", home)
        settings = os.path.join(home, "settings.reader.lua")
        ours = [os.path.join(home, "patches", "2-isopod-keys.lua"),
                os.path.join(home, "settings", "reader_menu_order.lua")]
        with open(settings) as f:
            first = f.read()
        for line in ('["home_dir"] = "/storage/books"', '["end_document_action"] = "nothing"',
                     '["language"] = "ko_KR"', '["newsdownloader"] = true', '["hotkeys"] = true'):
            self.assertIn(line, first)
        for kept in ("statistics", "kosync"):
            self.assertNotIn(f'["{kept}"]', first, "the book's status page, and the page kept with another reader")
        kept = 'return {\n    ["font_size"] = 24,\n}\n'
        for path in ours:
            with open(path, "w") as f:
                f.write("-- changed")
        with open(settings, "w") as f:
            f.write(kept)
        prepare_koreader(self.SYSTEM, "/storage/books", "ko_KR", home)
        with open(settings) as f:
            self.assertEqual(f.read(), 'return {\n    ["language"] = "ko_KR",\n    ["font_size"] = 24,\n}\n',
                             "KOReader's own from then on, but for the language")
        for path in ours:
            with open(path) as f:
                self.assertNotEqual(f.read(), "-- changed")
        prepare_koreader(self.SYSTEM, "/storage/books", "en", home)
        with open(settings) as f:
            self.assertEqual(f.read(), 'return {\n    ["language"] = "en",\n    ["font_size"] = 24,\n}\n')

    def test_the_shoulders_start_and_home_are_no_buttons_of_koreaders(self):
        with open(os.path.join(self.SYSTEM, "koreader", "patches", "2-isopod-keys.lua")) as f:
            patch = f.read()
        table = patch.split("Gamepad.button_key_names = {")[1].split("}")[0]
        keys = {int(n): name for n, name in re.findall(r'\[(\d+)\] = "(\w+)"', table)}
        # SDL: 0 south (B), 1 east (A), 2 X, 3 Y, 4 Select, 5 Home, 6 Start, 9 L1, 10 R1, 11-14 the D-pad
        self.assertEqual(keys, {0: "Back", 1: "Press", 2: "Menu", 3: "AA", 4: "Select",
                                11: "Up", 12: "Down", 13: "Left", 14: "Right"})
        self.assertIn('keys.ToggleHighlightIndicator = { { "Select" } }', patch)
        self.assertIn("Gamepad.axis_key_names = {}", patch, "L2 and R2 are axes to SDL")


class Late(FakeAudio):
    """PipeWire that has no sink to show yet when isopodd first asks."""

    ready = False

    def _build(self):
        super()._build()
        if not self.ready:
            self.sinks = []


class LevelsTest(unittest.IsolatedAsyncioTestCase):
    """Each output's volume across a restart, with ROCKNIX's boot script and a headset's own
    buttons in the way."""

    PODS = "AA:BB:CC:DD:EE:01"

    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.first = None

    async def boot(self, jack=False, backend=FakeAudio):
        """A fresh PipeWire (speaker at 40, headphones at 30, Bluetooth at 50) and a fresh
        isopodd over the settings on disk."""
        async def first():
            return self.first
        self.fake = backend()
        self.fake.plug(jack)
        self.settings = Settings(self.dir)
        self.audio = Audio(self.fake, State(lambda snapshot: None), self.settings, first=first)
        self.audio.SETTLE = self.audio.SAVE_AFTER = 0.02
        self.audio.ECHO = 0.1
        await self.audio.start()
        self.audio.jack(jack, initial=True)
        await self.quiet()

    async def quiet(self):
        await asyncio.sleep(0.15)

    async def plug(self, inserted):
        self.fake.plug(inserted)
        self.audio.jack(inserted)
        await self.quiet()

    async def others_set(self, level):
        """Like ROCKNIX's 050-audio: `pactl set-sink-volume @DEFAULT_SINK@`, from outside."""
        self.fake._state(self.fake.default)["level"] = level
        self.fake._changed()
        await self.quiet()

    def levels(self):
        bluetooth = [bt["level"] for bt in self.fake.bluetooth.values()]
        return self.fake.routes["speaker"]["level"], self.fake.routes["headphones"]["level"], *bluetooth

    async def test_each_output_comes_back_where_it_was_left(self):
        await self.boot()
        await self.audio.change(+25)
        await self.plug(True)
        await self.audio.change(-10)
        self.fake.add_bluetooth("AirPods", self.PODS)
        await self.quiet()
        await self.audio.change(+5)
        self.assertEqual(self.levels(), (65, 20, 55))
        await self.quiet()

        await self.boot()
        self.assertEqual(self.levels(), (65, 30), "the speaker is back; nothing else is in use yet")
        await self.others_set(60)
        self.assertEqual(self.levels(), (65, 30), "and ROCKNIX's boot script does not move it")
        await self.plug(True)
        self.assertEqual(self.levels(), (65, 20))
        self.fake.add_bluetooth("AirPods", self.PODS)
        await self.quiet()
        self.assertEqual(self.levels(), (65, 20, 55))

    async def test_headphones_in_at_boot_do_not_get_the_speakers_number(self):
        Settings(self.dir).set("volumes", {"speaker": 80, "wired": 25})
        await self.boot(jack=True)
        self.assertEqual(self.fake.routes["headphones"]["level"], 25)
        await self.others_set(80)
        self.assertEqual(self.fake.routes["headphones"]["level"], 25)

    async def test_a_sink_that_shows_up_late_still_gets_its_level(self):
        Settings(self.dir).set("volumes", {"speaker": 70})
        await self.boot(backend=Late)
        self.fake.ready = True
        self.fake._changed()
        await self.quiet()
        self.assertEqual(self.fake.routes["speaker"]["level"], 70)

    async def test_a_device_that_never_set_a_level_starts_where_rocknix_would(self):
        self.first = 60
        await self.boot()
        self.assertEqual(self.fake.routes["speaker"]["level"], 60)
        await self.plug(True)
        self.assertEqual(self.fake.routes["headphones"]["level"], 30, "headphones keep what they arrive with")
        await self.others_set(60)
        self.assertEqual(self.fake.routes["headphones"]["level"], 30)

    async def test_a_headsets_own_buttons_are_kept(self):
        await self.boot()
        self.fake.add_bluetooth("AirPods", self.PODS)
        await self.quiet()
        await self.others_set(70)
        self.assertEqual(self.levels(), (40, 30, 70))
        self.assertEqual(Settings(self.dir)["volumes"]["bt:" + self.PODS], 70)
        self.settings.set("volume_limit", 60)
        await self.others_set(90)
        self.assertEqual(self.levels(), (40, 30, 60), "never above the limit")

    async def test_what_a_headset_says_right_after_its_level_was_set_is_an_echo(self):
        Settings(self.dir).set("volumes", {"bt:" + self.PODS: 35})
        await self.boot()
        self.audio.ECHO = 5
        self.fake.add_bluetooth("AirPods", self.PODS)
        await self.quiet()
        await self.others_set(50)
        self.assertEqual(self.audio._levels["bt:" + self.PODS], 35)
        await self.audio.change(+5)
        self.assertEqual(self.levels(), (40, 30, 55), "the keys go on from what is heard")

    async def test_the_keys_go_on_from_the_level_kept_not_from_one_set_outside(self):
        Settings(self.dir).set("volumes", {"speaker": 30})
        await self.boot()
        self.fake._state(self.fake.default)["level"] = 60
        self.fake.sinks[0]["level"] = 60
        self.assertEqual(await self.audio.change(+5), 35)

    async def test_presses_faster_than_the_sink_all_count(self):
        class Slow(FakeAudio):
            async def set_volume(self, sink, percent):
                await asyncio.sleep(0.03)
                await super().set_volume(sink, percent)
        await self.boot(backend=Slow)
        await asyncio.gather(*(self.audio.change(+5) for _ in range(4)))
        await self.quiet()
        self.assertEqual(self.levels(), (60, 30))

    async def test_a_level_is_on_disk_before_power_goes(self):
        await self.boot()
        self.audio.SAVE_AFTER = 60
        await self.audio.change(+5)
        self.assertNotEqual(Settings(self.dir)["volumes"].get("speaker"), 45)
        self.audio.flush()
        self.assertEqual(Settings(self.dir)["volumes"]["speaker"], 45)

    async def test_a_sink_that_will_not_take_a_level_is_not_fought(self):
        class Stubborn(FakeAudio):
            asked = 0

            async def set_volume(self, sink, percent):
                self.asked += 1
                await super().set_volume(sink, 50)
        Settings(self.dir).set("volumes", {"speaker": 30})
        await self.boot(backend=Stubborn)
        for _ in range(10):
            self.fake._changed()
            await asyncio.sleep(0.02)
        self.assertEqual(self.audio._levels["speaker"], 50, "what the sink says goes in the end")
        self.assertLessEqual(self.fake.asked, 5)


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

    async def set_powered(self, on):
        self.calls.append(("power", on))
        self.powered = on
        self._emit("adapter")

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
                            self.events, System(sim=True))
        self.bt.RETRY_DELAY, self.bt.STREAM_WAIT = 0, 0.05
        await self.bt.start()

    def connects(self):
        return [c for c in self.bluez.calls if c[0] == "connect"]

    async def test_what_a_headsets_buttons_ask_goes_where_its_keys_go(self):
        asked = []

        async def media(action):
            asked.append(action)
        self.events.media = media
        self.bluez.remote("next")
        await asyncio.sleep(0.01)
        self.bt.set_playback("Playing")
        self.assertEqual((asked, self.bluez.playback), (["next"], "Playing"))

    async def test_only_named_headphones_and_controllers_are_offered(self):
        self.assertEqual([d.name for d in self.bt.other_devices()], ["AirPods Pro", "8BitDo Pro 2"])
        self.assertEqual(self.bt.my_devices(), [])

    async def test_headphones_connect_by_their_audio_service_and_say_so_once(self):
        await self.bt.activate(PODS)
        self.assertEqual(self.bluez.calls, [("trust", PODS), ("connect", PODS, A2DP_SINK)])
        self.assertEqual(self.events.toasts, ["AirPods Pro 연결됨"])
        self.assertEqual(self.events.found, ["AirPods Pro"])
        self.assertEqual([d.name for d in self.bt.my_devices()], ["AirPods Pro"])

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
        self.assertEqual(self.events.toasts, ["AirPods Pro에 연결할 수 없음"])
        self.assertEqual(len(self.connects()), 3)
        self.assertEqual(self.bluez.calls[-1], ("remove", PODS))
        self.assertEqual(self.events.found, [])

    async def test_headphones_that_do_not_answer_are_not_asked_again(self):
        self.bluez.set(PODS, paired=True, trusted=True)
        self.bluez.errors["connect"] = ["org.bluez.Error.Failed -- br-connection-page-timeout"]
        await self.bt.activate(PODS)
        self.assertEqual(len(self.connects()), 1)
        self.assertEqual(self.events.toasts, ["AirPods Pro에 연결할 수 없음"])
        self.assertIn(PODS, self.bluez.devices, "a device that was paired stays")

    async def test_a_link_that_drops_at_once_is_a_failure_said_once(self):
        self.bluez.drops.add(PODS)
        await self.bt.activate(PODS)
        self.assertEqual(len(self.connects()), 3)
        self.assertEqual(self.events.toasts, ["AirPods Pro에 연결할 수 없음"])
        self.assertIn(PODS, self.bluez.devices, "it did pair, so it stays to be tried again")

    async def test_headphones_that_connected_by_themselves_count(self):
        self.bluez.set(PODS, paired=True, trusted=True)
        self.bluez.already.add(PODS)
        await self.bt.activate(PODS)
        self.assertEqual(len(self.connects()), 1)
        self.assertEqual(self.events.toasts, ["AirPods Pro 연결됨"])

    async def test_bluetooth_left_on_comes_back_after_a_boot(self):
        await self.bt.set_enabled(True)
        self.assertTrue(Settings(os.path.dirname(self.settings.path))["bluetooth"])
        self.bluez.powered = False
        self.bluez.calls.clear()
        await self.bt._recover(settle=0)
        self.assertEqual(self.bluez.calls, [("power", True)], "though ROCKNIX's setting is off again")

    async def test_switching_off_powers_the_adapter_down_while_its_service_still_runs(self):
        class Service:
            async def set_bluetooth(_, on):
                self.bluez.calls.append(("service", on))

        self.bt.system = Service()
        await self.bt.set_enabled(False)
        await self.bt.set_enabled(True)
        self.assertEqual(self.bluez.calls, [("power", False), ("service", False),
                                            ("service", True), ("power", True)])

    async def test_bluetooth_left_off_stays_off(self):
        await self.bt.set_enabled(False)
        self.bluez.calls.clear()
        await self.bt._recover(settle=0)
        self.assertEqual(self.bluez.calls, [])

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


class WifiSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_rocknix_which_sets_wifi_at_boot_and_on_wake_hears_of_the_switch(self):
        told = []
        wifi = Wifi(FakeWifi(), State(lambda snap: None), Translator("en"), BluetoothEvents(),
                    remember=told.append)
        await wifi.set_enabled(False)
        await wifi.set_enabled(True)
        self.assertEqual(told, [False, True])

    async def test_networkmanager_hears_of_the_switch_as_well_as_the_radio(self):
        from isopod import wifi as module
        calls = []

        async def run(*args, **kwargs):
            calls.append(args)
            return 0, "", ""

        saved = module.run, module.shutil.which
        module.run, module.shutil.which = run, lambda name: "/usr/bin/" + name
        try:
            await Nmcli().set_enabled(True)
            await Nmcli().set_enabled(False)
        finally:
            module.run, module.shutil.which = saved
        switches = [c for c in calls if c[0] == "wifictl" or c[1] == "radio"]
        self.assertEqual(switches, [("wifictl", "enable"), ("nmcli", "radio", "wifi", "on"),
                                    ("wifictl", "disable"), ("nmcli", "radio", "wifi", "off")])


class SavedNetworkTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_saved_network_is_joined_or_forgotten_from_the_same_sheet_as_a_device(self):
        class NoApp:
            t = Translator("en")
            state = State(lambda snap: None)

        app = NoApp()
        app.wifi = Wifi(FakeWifi(), app.state, app.t, BluetoothEvents())
        await app.wifi.backend.refresh()
        pages = Pages(app)

        async def sheet(uuid):
            return (await pages.alt("/settings/wifi/saved", "u:" + uuid))["sheet"]

        self.assertEqual([i["key"] for i in (await sheet("u-home"))["items"]], ["forget"], "it is joined")
        office = await sheet("u-office")
        self.assertEqual((office["title"], [i["key"] for i in office["items"]]), ("Office 5G", ["join", "forget"]))
        await pages.resolve(office["token"], "forget")
        self.assertEqual([s["name"] for s in app.wifi.saved()], ["Home"])


class HotplugTest(unittest.TestCase):
    def test_a_kernel_message_becomes_its_fields(self):
        raw = (b"add@/devices/virtual/input/input7/event5\0ACTION=add\0SUBSYSTEM=input\0"
               b"MAJOR=13\0MINOR=69\0DEVNAME=input/event5\0SEQNUM=2903\0")
        self.assertEqual(uevents.parse(raw), {"ACTION": "add", "SUBSYSTEM": "input", "MAJOR": "13",
                                              "MINOR": "69", "DEVNAME": "input/event5", "SEQNUM": "2903"})


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

    def test_bluez_is_told_what_plays_once_a_change_and_calls_with_what_a_headset_asks(self):
        player, sent, asked = "org.mpris.MediaPlayer2.Player", [], []

        class Signal:
            @classmethod
            def new_signal(cls, *header):
                signal = cls()
                signal.header = header
                return signal

            def append_objects(self, signature, *body):
                self.body = body

        class Call:
            type, no_reply = "call", False

            def __init__(self, member, path="/isopod/player", interface=player):
                self.member, self.path, self.interface = member, path, interface

            def new_method_return(self):
                return "answer to " + self.member

        connection = types.SimpleNamespace(send=sent.append)
        bluez = RavelBackend()
        bluez._dbussy = types.SimpleNamespace(Message=Signal, DBUS=types.SimpleNamespace(
            MESSAGE_TYPE_METHOD_CALL="call", HANDLER_RESULT_HANDLED="taken", HANDLER_RESULT_NOT_YET_HANDLED="left"))
        bluez.bus = types.SimpleNamespace(connection=connection)
        bluez.remote = asked.append
        for status in ("Playing", "Playing", "Paused"):
            bluez.set_playback(status)
        self.assertEqual([(s.header, s.body) for s in sent], [
            (("/isopod/player", "org.freedesktop.DBus.Properties", "PropertiesChanged"),
             (player, {"PlaybackStatus": ("s", status)}, [])) for status in ("Playing", "Paused")])
        del sent[:]
        for member in ("Pause", "Next", "Previous", "Stop", "Play", "Seek"):
            self.assertEqual(bluez._called(connection, Call(member), None), "taken")
        self.assertEqual(asked, ["pause", "next", "prev", "pause", "play"])
        self.assertEqual(len(sent), 6, "every call is answered, the ones not acted on too")
        self.assertEqual(bluez._called(connection, Call("Play", path="/org/bluez/agent"), None), "left")
        self.assertEqual(len(asked), 5)

    def test_a_device_without_a_name_shows_its_address(self):
        bluez, dev = RavelBackend(), "/org/bluez/hci0/dev_5C_F3_70_00_00_04"
        bluez._apply(dev, {"org.bluez.Device1": {"Address": "5C:F3:70:00:00:04", "Alias": "5C-F3-70-00-00-04"}})
        self.assertFalse(bluez.devices[dev].named)
        bluez._apply(dev, {"org.bluez.Device1": {"Alias": "JBL Flip 6"}})
        self.assertTrue(bluez.devices[dev].named)


if __name__ == "__main__":
    unittest.main()


class SleepingRadioTest(unittest.IsolatedAsyncioTestCase):
    """The RTL8821CS that did not answer at boot: no adapter until its UART driver is bound again."""

    def sysfs(self, adapter):
        root = tempfile.mkdtemp()
        driver = os.path.join(root, "bus/serial/drivers/hci_uart_h5")
        os.makedirs(driver)
        os.makedirs(os.path.join(root, "class/bluetooth", "hci0" if adapter else ""))
        os.symlink("/dev/null", os.path.join(driver, "serial0-0"))
        os.symlink("/dev/null", os.path.join(driver, "module"))
        return root, driver

    async def test_no_adapter_binds_the_driver_again(self):
        root, driver = self.sysfs(adapter=False)
        await System(sysfs=root).wake_bluetooth(wait=0)
        for step in ("unbind", "bind"):
            with open(os.path.join(driver, step)) as f:
                self.assertEqual(f.read(), "serial0-0")

    async def test_an_adapter_that_is_there_is_left_alone(self):
        root, driver = self.sysfs(adapter=True)
        await System(sysfs=root).wake_bluetooth(wait=0)
        self.assertFalse(os.path.exists(os.path.join(driver, "unbind")))


# podcasts and audiobooks -----------------------------------------------------------

import gzip  # noqa: E402
import http.server  # noqa: E402
import threading  # noqa: E402

from isopod.audiobooks import Audiobooks, mp4_chapters, natural_key  # noqa: E402
from isopod.extras import Extras  # noqa: E402
from isopod import input as input_module, uinput  # noqa: E402
from isopod.music import Spoken  # noqa: E402
from isopod.podcasts import (Apple, NotListed, Podcasts, PodcastError, fetch, final_address,  # noqa: E402
                             read_opml, write_opml)


def apple_show(sid=100, title="Show", feed="https://x/feed"):
    return {"wrapperType": "track", "kind": "podcast", "collectionId": sid, "trackId": sid, "collectionName": title,
            "artistName": "Host", "feedUrl": feed, "artworkUrl600": f"https://x/{sid}.jpg"}


def apple_episode(tid, title, days_ago, seconds, url="", sid=100):
    when = datetime.datetime.fromtimestamp(time.time() - days_ago * 86400, datetime.timezone.utc)
    return {"wrapperType": "podcastEpisode", "kind": "podcast-episode", "trackId": tid, "collectionId": sid,
            "trackName": title, "releaseDate": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "trackTimeMillis": seconds * 1000,
            "episodeUrl": url or f"https://x/{tid}.mp3", "episodeFileExtension": "mp3", "episodeContentType": "audio"}


def lookup(*results):
    return json.dumps({"resultCount": len(results), "results": list(results)}).encode()


class Web:
    """Addresses and what is at them; the count of each fetch."""

    def __init__(self, pages):
        self.pages = pages
        self.asked = []

    def __call__(self, url, limit=0, timeout=0):
        self.asked.append(url)
        for start, body in self.pages.items():
            if url.startswith(start):
                return body() if callable(body) else body
        raise PodcastError("unreachable")


class AppleTest(unittest.TestCase):
    def test_a_show_and_its_newest_episodes(self):
        web = Web({"https://itunes.apple.com/lookup?id=100": lookup(
            apple_show(), apple_episode(2, "Older", 3, 750), apple_episode(1, "Newer", 1, 3723),
            dict(apple_episode(3, "No file", 0, 1), episodeUrl=""),
            apple_episode(4, "공백 있는 파일", 2, 60, url="https://x/a b/한강.mp3"))})
        show, episodes = Apple(web).episodes("100", "kr")
        self.assertEqual((show["id"], show["title"], show["author"]), ("100", "Show", "Host"))
        self.assertEqual([e["title"] for e in episodes], ["Newer", "공백 있는 파일", "Older"], "newest first, files only")
        self.assertEqual([e["duration"] for e in episodes], [3723, 60, 750])
        self.assertEqual(episodes[1]["url"], "https://x/a%20b/%ED%95%9C%EA%B0%95.mp3", "sent as a web client sends it")
        self.assertIn("entity=podcastEpisode", web.asked[0])
        self.assertIn("country=kr", web.asked[0])

    def test_apple_s_times_are_utc(self):
        web = Web({"https://itunes.apple.com/lookup": lookup(apple_show(), dict(
            apple_episode(1, "One", 0, 60), releaseDate="2026-10-17T20:00:00Z"))})
        _, (episode,) = Apple(web).episodes("100", "kr")
        self.assertEqual(episode["date"], int(datetime.datetime(2026, 10, 17, 20, tzinfo=datetime.timezone.utc)
                                              .timestamp()))

    def test_a_show_apple_does_not_have(self):
        with self.assertRaises(NotListed):
            Apple(Web({"https://itunes.apple.com/lookup": lookup()})).episodes("9", "kr")

    def test_the_chart_in_its_order_with_shows_without_a_feed_too(self):
        web = Web({"https://itunes.apple.com/kr/rss/toppodcasts": json.dumps({"feed": {"entry": [
                   {"id": {"attributes": {"im:id": "2"}}}, {"id": {"attributes": {"im:id": "1"}}}]}}).encode(),
                   "https://itunes.apple.com/lookup": lookup(apple_show(1, "One"), apple_show(2, "Two", feed=""))})
        self.assertEqual([(s["id"], s["title"]) for s in Apple(web).top("kr")], [("2", "Two"), ("1", "One")])

    def test_opml_both_ways_and_another_app_s(self):
        path = os.path.join(tempfile.mkdtemp(), "Subscriptions.opml")
        shows = [{"id": "100", "url": "https://x/a?b=1&c=2", "title": 'Tom & "Jerry"', "image": "https://x/a.jpg"}]
        write_opml(path, shows)
        self.assertEqual(read_opml(path), shows)
        with open(path, "w") as f:  # as another app writes one: folders, a feed and no Apple id
            f.write('<opml version="1.0"><body><outline text="Feeds"><outline type="rss" text="Nested" '
                    'xmlUrl="https://y/feed"/></outline></body></opml>')
        (show,) = read_opml(path)
        self.assertEqual((show["title"], show["url"], show["id"][:5]), ("Nested", "https://y/feed", "feed-"))


class LocalWeb(http.server.BaseHTTPRequestHandler):
    """Redirects as many times as the path says, then a file; gzip when it is asked for."""

    def do_GET(self):
        hops = int(self.path.strip("/").split("/")[0] or 0)
        if hops:
            self.send_response(302)
            self.send_header("Location", f"/{hops - 1}/" + "/".join(self.path.strip("/").split("/")[1:]))
            self.end_headers()
            return
        body = b'{"resultCount": 0, "results": []}'
        self.send_response(200)
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AddressesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalWeb)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_an_episode_is_given_to_mpd_past_the_counters_redirects(self):
        self.assertEqual(final_address(f"{self.base}/7/episode.mp3"), f"{self.base}/0/episode.mp3",
                         "seven redirects, where MPD stops at five")

    def test_an_address_that_leads_nowhere_is_kept(self):
        self.assertEqual(final_address("http://127.0.0.1:9/x.mp3", timeout=2), "http://127.0.0.1:9/x.mp3")

    def test_apple_s_answers_come_compressed(self):
        self.assertEqual(fetch(f"{self.base}/0/lookup"), b'{"resultCount": 0, "results": []}')


class PodcastsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.mkdtemp()
        self.episodes = [apple_episode(1, "New one", 1, 600), apple_episode(2, "Old one", 100, 600)]
        self.web = Web({"https://itunes.apple.com/lookup?id=100&": lambda: lookup(apple_show(), *self.episodes),
                        "https://itunes.apple.com/search": lookup(apple_show(), {"collectionName": "No id"})})
        self.p = Podcasts(os.path.join(self.root, "podcasts"), self.root, self.root, directory=Apple(self.web),
                          download=lambda url, limit=0, timeout=0: b"")
        self.show = {"id": "100", "url": "https://x/feed", "title": "Show"}

    async def test_found_subscribed_to_and_kept_in_the_opml_file(self):
        found = await self.p.search("show")
        self.assertEqual([(s["id"], s["title"], s["author"]) for s in found], [("100", "Show", "Host")])
        self.p.subscribe(found[0], True)
        self.assertEqual([(s["id"], s["url"]) for s in read_opml(self.p.opml)], [("100", "https://x/feed")])
        self.assertEqual(self.p.subscriptions()[0]["image"], "https://x/100.jpg", "the small cover is remembered")
        self.p.subscribe(found[0], False)
        self.assertEqual(self.p.subscriptions(), [])

    async def test_another_app_s_opml_is_matched_to_apple_by_its_feed(self):
        write_opml(self.p.opml, [{"id": "feed-1", "url": "https://x/feed", "title": "Show"}])
        (show,) = self.p.subscriptions()
        self.assertEqual([e["title"] for e in await self.p.load(show)], ["New one", "Old one"])
        self.assertEqual([s["id"] for s in read_opml(self.p.opml)], ["100"], "named by Apple's id from then on")
        write_opml(self.p.opml, [{"id": "feed-2", "url": "https://elsewhere/feed", "title": "Gone"}])
        os.utime(self.p.opml, (time.time() + 5, time.time() + 5))
        with self.assertRaises(NotListed):
            await self.p.load(self.p.subscriptions()[0])

    async def test_episodes_are_kept_and_asked_for_again_once_they_have_aged(self):
        self.assertEqual(len(await self.p.load(self.show)), 2)
        await self.p.load(self.show)
        self.assertEqual(len(self.web.asked), 1, "kept")
        self.episodes = [apple_episode(3, "Newest", 0, 60)]
        aged(self.p._show_file("100"))
        self.assertEqual(len(await self.p.load(self.show)), 2, "shown as it was")
        await asyncio.sleep(0.05)
        self.assertEqual([e["title"] for e in await self.p.load(self.show)], ["Newest"], "fetched behind")

    async def test_what_is_new_is_what_came_out_lately_and_was_not_heard(self):
        self.p.subscribe(self.show, True)
        new, old = await self.p.load(self.show)
        self.assertEqual((self.p.is_new(self.show, new), self.p.is_new(self.show, old)), (True, False))
        self.assertTrue(self.p.has_new(self.show))
        self.p.heard(new["id"], 100, 600)
        self.assertFalse(self.p.is_new(self.show, new), "begun")

    async def test_a_place_is_kept_and_the_end_is_heard(self):
        self.p.heard("e", 3, 600)
        self.assertEqual(self.p.place("e"), (0, False), "a few seconds is no place")
        self.p.heard("e", 200, 600, force=True)
        self.assertEqual(load(self.p.state_path)["episodes"]["e"], {"pos": 200, "done": False})
        self.p.heard("e", 580, 600)
        self.assertEqual(self.p.place("e"), (0, True), "the last half minute is the credits")
        self.p.set_heard("e", False)
        self.assertEqual(self.p.place("e"), (0, False))

    async def test_places_are_written_now_and_then_and_only_the_latest_kept(self):
        self.p.heard("a", 100, 600, force=True)
        self.p.heard("a", 110, 600)
        self.assertEqual(load(self.p.state_path)["episodes"]["a"]["pos"], 100, "not every few seconds")
        for n in range(2100):
            self.p.played[f"x{n}"] = {"pos": 10, "done": False}
        self.p.heard("a", 120, 600, force=True)
        kept = load(self.p.state_path)["episodes"]
        self.assertEqual((len(kept), "x0" in kept, "a" in kept), (2000, False, True))

    async def test_a_download_is_played_from_the_card_where_it_was_left(self):
        source = os.path.join(self.root, "source.mp3")
        with open(source, "wb") as f:
            f.write(b"ID3" + b"\0" * 5000)
        episode = {"id": "e1", "title": "One/Two", "date": 1791000000, "duration": 600, "url": "file://" + source,
                   "ext": ".mp3"}
        show = dict(self.show, title="Show: Live")
        spoken, start = self.p.play(show, episode)
        self.assertEqual((spoken.uris, start), (["file://" + source], 0), "from the web while it is not here")
        self.p.download(show, episode)
        self.assertTrue(self.p.downloading())
        await self.p._worker
        file = self.p.file("e1")
        self.assertEqual(os.path.relpath(file, self.p.folder), "Show Live/2026-10-03 One Two.mp3")
        self.assertEqual(os.path.getsize(file), 5003)
        self.assertFalse(self.p.downloading())
        self.p.heard("e1", 120, 600, force=True)
        spoken, start = self.p.play(show, episode)
        self.assertEqual((spoken.uris, start), (["file://" + file], 118))
        again = Podcasts(self.p.folder, self.root, self.root, directory=Apple(self.web))
        self.assertEqual(again.identify(file).title, "One/Two", "known again after a restart")
        self.assertEqual([e["id"] for _, e in again.downloaded()], ["e1"])
        again.remove("e1")
        self.assertFalse(os.path.exists(os.path.dirname(file)))

    async def test_a_download_that_cannot_be_had_is_said_so(self):
        failed = []
        self.p.failed = failed.append
        episode = {"id": "e2", "title": "Gone", "date": 0, "duration": 0, "url": "file:///nowhere/at/all.mp3"}
        self.p.download({"id": "s", "url": "u", "title": "S"}, episode)
        await self.p._worker
        self.assertEqual((failed, self.p.file("e2"), os.listdir(self.p.folder)), ([episode], None, []))

    async def test_covers_wait_for_their_own_threads_not_the_common_pool(self):
        started = threading.Event()
        release = threading.Event()

        def slow(url, limit=0, timeout=0):
            started.set()
            release.wait(5)
            return b""
        self.p._download = slow
        self.p.want_art([{"id": str(n), "image": f"https://x/{n}.jpg"} for n in range(20)], lambda show: None)
        await asyncio.sleep(0.1)
        self.assertTrue(started.is_set())
        t = time.monotonic()
        self.assertEqual(await asyncio.to_thread(lambda: "free"), "free")
        self.assertLess(time.monotonic() - t, 1, "the common pool is not taken by twenty covers")
        release.set()


def load(path):
    with open(path) as f:
        return json.load(f)


class Mpd:
    """Records commands; plays what it was given, at the time it was asked for."""

    def __init__(self):
        self.sent = []
        self.queue = []
        self.at = (0, 0.0)
        self.duration = 600.0

    async def call(self, name, *args):
        self.sent.append((name, *args))
        if name == "seek":
            self.at = (int(args[0]), float(args[1]))
        if name == "seekcur":
            self.at = (self.at[0], float(args[0]))
        if name == "status":
            return [("state", "play"), ("song", str(self.at[0])), ("elapsed", str(self.at[1])),
                    ("duration", str(self.duration)), ("playlistlength", str(len(self.queue))), ("audio", "x")]
        if name == "currentsong":
            return [("file", self.queue[self.at[0]])] if self.queue else []
        return []

    async def call_list(self, commands):
        for c in commands:
            if c[0] == "clear":
                self.queue = []
            elif c[0] == "add":
                self.queue.append(c[1])
            elif c[0] == "play":
                self.at = (int(c[1]), 0.0)
        self.sent += commands


class SpokenPlaybackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mpd = Mpd()
        self.state = State(lambda snap: None)
        self.music = Music(self.mpd, self.state, tempfile.mkdtemp())
        self.heard = []

        test = self

        class Talk(Spoken):
            def heard(self, index, elapsed, duration, force=False):
                test.heard.append((index, elapsed, force))

        self.talk = Talk("k", ["file:///a.mp3", "file:///b.mp3"], "A Talk", "Someone")
        self.talk.kind = "audiobook"

    async def test_it_starts_where_it_was_left_with_repeat_and_shuffle_off(self):
        await self.music.play_spoken(self.talk, 1, 42.0)
        self.assertEqual(self.mpd.sent[:6], [("clear",), ("random", "0"), ("repeat", "0"), ("single", "0"),
                                             ("add", "file:///a.mp3"), ("add", "file:///b.mp3")])
        self.assertEqual(self.mpd.sent[-1], ("seek", "1", "42.0"))
        await self.music.refresh()
        player = self.state.get("player")
        self.assertEqual((player["kind"], player["title"], player["artist"]), ("audiobook", "A Talk", "Someone"))
        self.assertEqual(self.heard, [(1, 42.0, False)])

    async def test_back_and_ahead_are_seconds_not_files(self):
        await self.music.play_spoken(self.talk, 0, 100.0)
        await self.music.refresh()
        await self.music.previous()
        self.assertEqual(self.mpd.sent[-1][0], "seekcur")
        self.assertAlmostEqual(float(self.mpd.sent[-1][1]), 85.0, delta=0.5)
        self.mpd.at = (0, 590.0)
        await self.music.refresh()
        await self.music.next()
        self.assertEqual(self.mpd.sent[-1], ("seekcur", "599.0"), "not past the end")

    async def test_what_mpd_cannot_play_is_told_apart_from_what_cannot_be_reached(self):
        said = []
        self.music.cannot_play = lambda: said.append("cannot play")
        self.music.stream_failed = lambda: said.append("unreachable")
        web = Spoken("w", ["https://x/broken.mp3"], "Broken")
        status = self.mpd.call

        async def failing(name, *args):
            if name == "status":
                return [("state", "stop"), ("error", "Failed to decode")]
            return await status(name, *args)
        self.mpd.call = failing
        await self.music.play_spoken(web)
        await self.music._stream_watch
        self.assertEqual(said, ["cannot play"])

    async def test_mpd_found_playing_one_after_a_restart_is_known_again(self):
        self.mpd.queue = ["/b.mp3"]
        self.music.identify = lambda uri: self.talk if self.talk.index(uri) is not None else None
        await self.music.refresh()
        self.assertEqual(self.state.get("player")["kind"], "audiobook", "with or without file://")


def box(kind, body):
    return struct.pack(">I4s", 8 + len(body), kind.encode()) + body


def full(kind, body, version=0):
    return box(kind, bytes([version, 0, 0, 0]) + body)


class AudiobooksTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.lib = os.path.join(self.root, "audiobooks")
        for path in ("Saint-Exupéry/어린 왕자/1장.mp3", "Saint-Exupéry/어린 왕자/10장.mp3",
                     "Saint-Exupéry/어린 왕자/2장.mp3", "Saint-Exupéry/어린 왕자/cover.jpg",
                     "Hound/CD 2/01.mp3", "Hound/CD 1/02.mp3", "Hound/CD 1/01.mp3", "Single.m4b",
                     "Saint-Exupéry/Notes/readme.txt", "Austen/Emma.m4b", "Austen/Persuasion.m4b"):
            os.makedirs(os.path.dirname(os.path.join(self.lib, path)), exist_ok=True)
            open(os.path.join(self.lib, path), "w").close()
        self.b = Audiobooks(self.lib, self.root, self.root)

    def test_folders_of_books_and_books(self):
        self.assertEqual(self.b.listing(), (["Austen", "Saint-Exupéry"], ["Hound", "Single.m4b"]))
        self.assertEqual(self.b.listing("Austen"), ([], ["Austen/Emma.m4b", "Austen/Persuasion.m4b"]),
                         "a folder of M4Bs is a shelf of books")
        self.assertEqual(self.b.listing("Saint-Exupéry"), (["Notes"], ["Saint-Exupéry/어린 왕자"]))
        self.assertEqual([os.path.relpath(f, self.lib) for f in self.b.files("Saint-Exupéry/어린 왕자")],
                         ["Saint-Exupéry/어린 왕자/1장.mp3", "Saint-Exupéry/어린 왕자/2장.mp3",
                          "Saint-Exupéry/어린 왕자/10장.mp3"])
        self.assertEqual([os.path.relpath(f, self.lib) for f in self.b.files("Hound")],
                         ["Hound/CD 1/01.mp3", "Hound/CD 1/02.mp3", "Hound/CD 2/01.mp3"], "disc by disc")
        self.assertEqual(self.b.art("Saint-Exupéry/어린 왕자"), os.path.join(self.lib, "Saint-Exupéry/어린 왕자/cover.jpg"))
        self.assertEqual(self.b.listing("../.."), self.b.listing(), "nothing above the library")

    def test_a_place_in_a_book_is_kept_until_its_end_is_heard(self):
        book = self.b.book("Hound")
        self.assertEqual(self.b.resume("Hound"), (0, 0.0))
        book.heard(1, 65.0, 300.0, force=True)
        self.assertEqual(Audiobooks(self.lib, self.root, self.root).resume("Hound"), (1, 63.0))
        book.heard(2, 290.0, 300.0)
        self.assertEqual(self.b.place("Hound"), (0, 0, True))
        self.assertEqual(self.b.resume("Hound"), (0, 0.0), "heard: from the beginning again")

    def test_a_book_mpd_is_found_playing_is_known_again(self):
        book = self.b.identify("file://" + os.path.join(self.lib, "Hound/CD 2/01.mp3"))
        self.assertEqual((book.key, book.title, book.index(os.path.join(self.lib, "Hound/CD 2/01.mp3"))),
                         ("Hound", "Hound", 2))
        self.assertIsNone(self.b.identify("file:///elsewhere/x.mp3"))

    def test_nero_chapters(self):
        entries = b"".join(struct.pack(">QB", int(start * 1e7), len(t.encode())) + t.encode()
                           for start, t in ((0, "Opening"), (95.5, "둘째 장"), (300, "Last")))
        chpl = full("chpl", b"\0\0\0\0" + bytes([3]) + entries, version=1)
        path = os.path.join(self.root, "nero.m4b")
        with open(path, "wb") as f:
            f.write(box("ftyp", b"M4B \0\0\0\0") + box("moov", box("udta", chpl)))
        self.assertEqual(mp4_chapters(path), [(0.0, "Opening"), (95.5, "둘째 장"), (300.0, "Last")])

    def test_a_quicktime_chapter_track(self):
        titles = [b"Chapter One", "둘".encode()]
        samples = b"".join(struct.pack(">H", len(t)) + t for t in titles)
        head = box("ftyp", b"M4A \0\0\0\0")

        def moov(offset):
            def tkhd(track):
                return full("tkhd", b"\0" * 8 + struct.pack(">I", track) + b"\0" * 68)
            audio = box("trak", tkhd(1) + box("tref", box("chap", struct.pack(">I", 2))))
            stbl = box("stbl", full("stts", struct.pack(">III", 2, 1, 60000))
                       + full("stsz", struct.pack(">II", 0, 2) + struct.pack(">II", 2 + len(titles[0]), 2 + len(titles[1])))
                       + full("stsc", struct.pack(">IIII", 1, 1, 2, 1))
                       + full("stco", struct.pack(">II", 1, offset)))
            mdhd = full("mdhd", b"\0" * 8 + struct.pack(">II", 1000, 120000) + b"\0" * 4)
            text = box("trak", tkhd(2) + box("mdia", mdhd + box("minf", stbl)))
            return box("moov", audio + text)
        offset = len(head) + len(moov(0)) + 8
        path = os.path.join(self.root, "itunes.m4a")
        with open(path, "wb") as f:
            f.write(head + moov(offset) + box("mdat", samples))
        self.assertEqual(mp4_chapters(path), [(0.0, "Chapter One"), (60.0, "둘")])

    def test_no_chapters_from_what_is_not_an_mp4(self):
        path = os.path.join(self.root, "x.m4b")
        with open(path, "wb") as f:
            f.write(b"\0\0\0\x08junk" * 3)
        self.assertEqual(mp4_chapters(path), [])
        self.assertLess(natural_key("Part 2"), natural_key("Part 10"))

    async def test_a_book_of_chapters_in_one_file(self):
        file = os.path.join(self.lib, "Single.m4b")
        self.b._file_info(file)["chapters"] = [(0.0, "One"), (60.0, "Two"), (120.0, "Three")]
        book = self.b.book("Single.m4b")
        self.assertEqual((book.album(0, 59), book.album(0, 61), book.album(0, 500)), ("One", "Two", "Three"))
        self.assertEqual(book.chapter(0, 125), 2)


class SpokenPagesTest(unittest.IsolatedAsyncioTestCase):
    """Podcasts and audiobooks on the screen."""

    async def asyncSetUp(self):
        root = tempfile.mkdtemp()

        class NoApp:
            t = Translator("en")
            changed, toasts = [], []

            class power:
                sleep_timer_left = staticmethod(lambda: 0)

            def page_changed(self, *paths):
                self.changed += paths

            def toast(self, text, icon=None):
                self.toasts.append(text)

        app = self.app = NoApp()
        app.state = State(lambda snap: None)
        app.state.data["wifi"]["ssid"] = "Home"
        self.mpd = Mpd()
        app.music = Music(self.mpd, app.state, root)
        self.web = Web({"https://itunes.apple.com/lookup?id=100&": lookup(
            apple_show(), apple_episode(1, "New one", 1, 1500), apple_episode(2, "Old one", 400, 600)),
            "https://itunes.apple.com/lookup?id=gone": lookup()})
        app.podcasts = Podcasts(os.path.join(root, "podcasts"), root, root, lang=lambda: app.t.lang,
                                directory=Apple(self.web), download=lambda url, limit=0, timeout=0: b"")
        lib = os.path.join(root, "audiobooks")
        os.makedirs(os.path.join(lib, "Book"))
        for name in ("1.mp3", "2.mp3"):
            open(os.path.join(lib, "Book", name), "w").close()
        app.audiobooks = Audiobooks(lib, root, root)
        app.music.identify = lambda uri: app.podcasts.identify(uri) or app.audiobooks.identify(uri)
        app.extras = Extras(os.path.join(root, "apps"), root)
        self.pages = Pages(app)
        self.show = {"id": "100", "url": "https://x/feed", "title": "Show"}

    async def rows(self, path, *fields):
        items = [i for i in (await self.pages.build(path))["items"] if not i.get("header")]
        return [tuple(i.get(f) for f in fields) if len(fields) > 1 else i.get(fields[0]) for i in items]

    async def test_the_menu_and_a_show_subscribed_to(self):
        self.assertEqual(await self.rows("/podcasts", "title"), ["Subscriptions", "Downloads", "Popular", "Search"])
        self.assertEqual((await self.pages.build("/podcasts/shows"))["empty"]["title"], "No Subscriptions")
        self.app.podcasts.subscribe(self.show, True)
        await self.app.podcasts.load(self.show)
        self.assertEqual(await self.rows("/podcasts/shows", "title", "dot"), [("Show", True)])
        rows = await self.rows("/podcasts/show/" + self.show["id"], "key", "accessory", "on", "subtitle", "dot")
        self.assertEqual(rows[0], ("subscribe", "switch", True, None, None))
        self.assertEqual(rows[1][3:], (datetime.date.fromtimestamp(time.time() - 86400).strftime("%b %-d") + " · 25 min",
                                       True))
        self.assertIsNone(rows[2][4], "too old to be new")

    async def test_an_episode_plays_and_says_what_is_left(self):
        self.app.podcasts.meet(self.show)
        path = "/podcasts/show/" + self.show["id"]
        key = (await self.rows(path, "key"))[1]
        with unittest.mock.patch("isopod.podcasts.final_address", lambda url: url.replace("x/", "cdn/")):
            self.assertEqual(await self.pages.activate(path, key), {"nowplaying": True})
            player = self.app.state.get("player")
            self.assertEqual((player["kind"], player["title"], player["buffering"]), ("podcast", "New one", True),
                             "shown at once, while its address is followed")
            for _ in range(50):
                await asyncio.sleep(0.01)
        self.assertEqual(self.mpd.queue, ["https://cdn/1.mp3"], "MPD is given where the redirects lead")
        self.mpd.at = (0, 600.0)
        self.mpd.duration = 1500.0
        await self.app.music.refresh()
        player = self.app.state.get("player")
        self.assertEqual((player["kind"], player["title"], player["artist"]), ("podcast", "New one", "Show"))
        self.assertEqual((await self.pages.build("/nowplaying"))["note"],
                         datetime.date.fromtimestamp(time.time() - 86400).strftime("%b %-d"))
        self.app.podcasts.heard(key[2:], 600, 1500, force=True)
        self.assertEqual((await self.rows(path, "subtitle", "accessory"))[1][1], "playing")
        self.assertTrue((await self.rows(path, "subtitle"))[1].endswith("15 min left"))
        options = await self.pages.now_playing_options()
        self.assertEqual([o["key"] for o in options["sheet"]["items"]], ["show", "timer"])

    async def test_an_episode_s_options(self):
        self.app.podcasts.meet(self.show)
        path = "/podcasts/show/" + self.show["id"]
        key = (await self.rows(path, "key"))[1]
        sheet = (await self.pages.alt(path, key))["sheet"]
        self.assertEqual([o["key"] for o in sheet["items"]], ["download", "heard"])
        await self.pages.resolve(sheet["token"], "heard")
        self.assertEqual(self.app.podcasts.place(key[2:]), (0, True))
        self.assertTrue((await self.rows(path, "subtitle"))[1].endswith("Played"))

    async def test_no_network_and_a_show_apple_does_not_have(self):
        self.app.podcasts.meet({"id": "gone", "url": "https://gone/feed", "title": "Gone"})
        page = await self.pages.build("/podcasts/show/gone")
        self.assertEqual([i["title"] for i in page["items"]], ["Subscribe", "Not on Apple Podcasts"])
        self.app.podcasts.meet({"id": "200", "url": "", "title": "Far"})
        page = await self.pages.build("/podcasts/show/200")
        self.assertEqual([i["title"] for i in page["items"]], ["Subscribe", "Couldn’t Connect"])
        self.assertEqual((await self.pages.build("/podcasts/top"))["empty"]["title"], "Couldn’t Connect")

    async def test_audiobooks_under_books_and_the_radio_on_the_home_screen(self):
        self.assertEqual(await self.rows("/", "title"), ["Music", "Radio", "Podcasts", "Videos", "Books", "Apps",
                                                         "Settings", "Shuffle Songs"])
        self.assertEqual(await self.rows("/books", "title"), ["eBooks", "Audiobooks"])
        self.assertEqual(await self.pages.activate("/books", "audiobooks"), {"push": "/audiobooks"})
        self.assertNotIn("Radio", await self.rows("/music", "title"))
        self.assertEqual(await self.pages.activate("/", "radio"), {"push": "/radio"})
        self.assertEqual(await self.rows("/audiobooks", "key", "title"), [("b:Book", "Book")])
        self.assertEqual(await self.pages.activate("/audiobooks", "b:Book"), {"nowplaying": True})
        self.assertEqual(self.mpd.queue, ["file://" + os.path.join(self.app.audiobooks.folder, "Book", n)
                                          for n in ("1.mp3", "2.mp3")])
        await self.app.music.refresh()
        self.assertEqual(self.app.state.get("player")["kind"], "audiobook")
        self.assertEqual((await self.pages.build("/nowplaying"))["note"], "1 / 2")
        self.assertEqual(await self.rows("/audiobooks/book/Book", "title", "accessory"),
                         [("1", "playing"), ("2", "none")])
        sheet = (await self.pages.alt("/audiobooks", "b:Book"))["sheet"]
        self.assertEqual([o["key"] for o in sheet["items"]], ["chapters", "heard"])


PROVIDER = r"""
import json, sys
print("starting up")
sys.stdout.flush()
for line in sys.stdin:
    q = json.loads(line)
    if q["op"] == "page" and q["path"] == "/":
        a = {"title": "Tube" if q["lang"] == "en" else "튜브", "items": [
            {"title": "Channels", "header": True},
            {"title": "Lo-fi Girl", "subtitle": "12 new", "path": "/channel/1"},
            {"title": "Search", "search": "/search", "prompt": "Videos"},
            {"title": "A video", "play": "abc"},
            {"title": "Blocked", "play": "gone"},
            {"title": "Nothing to press"}]}
    elif q["op"] == "page":
        a = {"items": [{"title": q["path"] + "?" + q.get("query", ""), "play": "abc"}]}
    elif q["play"] == "gone":
        a = {"error": "Not in your country"}
    else:
        a = {"title": "A video, played", "url": "https://v/abc.mp4", "audio": "https://a/abc.m4a",
             "headers": {"Referer": "https://x/"}}
    print(json.dumps({"id": q["id"], **a}))
    sys.stdout.flush()
"""


def make_app(root, name, manifest, files=()):
    folder = os.path.join(root, name)
    os.makedirs(folder)
    with open(os.path.join(folder, "app.json"), "w") as f:
        f.write(manifest if isinstance(manifest, str) else json.dumps(manifest))
    for rel, text in files:
        os.makedirs(os.path.dirname(os.path.join(folder, rel)), exist_ok=True)
        with open(os.path.join(folder, rel), "w") as f:
            f.write(text)
    return folder


class InstalledAppsTest(unittest.IsolatedAsyncioTestCase):
    """Apps copied into /storage/apps: read, listed under Apps, opened, and asked."""

    async def asyncSetUp(self):
        d = self.dir = tempfile.mkdtemp()
        self.root = os.path.join(d, "apps")
        os.makedirs(self.root)
        self.app = App(parse_args(["--sim", "--socket", os.path.join(d, "s"), "--config", d, "--cache", d,
                                   "--data", d, "--mpd", os.path.join(d, "mpd"), "--apps-dir", self.root]))
        self.app.t.lang = "en"
        self.toasts = []
        self.app.toast = lambda text, icon=None: self.toasts.append(text)
        self.addAsyncCleanup(self.app.extras.close)

    def lagrange(self):
        return make_app(self.root, "lagrange", {
            "name": "Lagrange", "exec": ["bin/lagrange", "--sw"],
            "keys": {"a": "enter", "b": "alt+left", "l1": "PageUp", "home": "esc", "x": "hyper"}},
            [("bin/lagrange", "#!/bin/sh\n"), ("bin/helper", ""), ("lib/libx.so", "")])

    def tube(self):
        return make_app(self.root, "tube", {"name": {"en": "Tube", "ko": "튜브"}, "kind": "list",
                                            "exec": [sys.executable, "provider.py"]}, [("provider.py", PROVIDER)])

    async def rows(self, path, *fields):
        return [tuple(i.get(f) for f in fields) for i in (await self.app.pages.build(path))["items"]]

    def test_an_app_folder_read_and_what_is_left_out(self):
        self.lagrange()
        make_app(self.root, "Bad Name", {"name": "x", "exec": ["x"]})
        make_app(self.root, "broken", "{not json")
        make_app(self.root, "noexec", {"name": "x"})
        make_app(self.root, "film", {"name": "x", "exec": ["x"], "kind": "theatre"})
        make_app(self.root, "listed", {"name": "x", "exec": ["x"], "keys": ["a", "enter"]})
        apps = self.app.extras.apps()
        self.assertEqual(sorted(apps), ["lagrange"])
        m = apps["lagrange"]
        self.assertEqual((m.kind, m.argv), ("window", [os.path.join(self.root, "lagrange", "bin/lagrange"), "--sw"]))
        self.assertEqual(m.keys, {"a": [28], "b": [56, 105], "l1": [104]}, "Home and unknown keys are no app's")

    def test_a_copy_over_the_cable_is_made_runnable(self):
        folder = self.lagrange()
        spec = self.app.extras.spec(self.app.extras.get("lagrange"))
        for name in ("lagrange", "helper"):
            self.assertTrue(os.access(os.path.join(folder, "bin", name), os.X_OK), name)
        self.assertFalse(os.access(os.path.join(folder, "lib", "libx.so"), os.X_OK))
        home = os.path.join(self.dir, "apps", "lagrange")
        self.assertEqual((spec.key, spec.cwd, spec.keys, spec.env["HOME"], spec.env["LD_LIBRARY_PATH"]),
                         ("app-lagrange", folder, {"a": [28], "b": [56, 105], "l1": [104]}, home,
                          os.path.join(folder, "lib")))
        self.assertTrue(os.path.isdir(home))
        self.assertTrue(spec.env["PATH"].startswith(os.path.join(folder, "bin") + os.pathsep))

    async def test_apps_is_always_on_the_home_screen_before_settings(self):
        keys = [k for k, in await self.rows("/", "key")]
        self.assertEqual(keys[keys.index("books") + 1:keys.index("settings")], ["apps"])
        empty = await self.app.pages.build("/apps")
        self.assertEqual((empty["title"], empty["items"], empty["empty"]["title"]), ("Apps", [], "No Apps"))
        self.lagrange()
        self.tube()
        self.assertEqual(await self.rows("/apps", "key", "title", "accessory"),
                         [("a:lagrange", "Lagrange", "none"), ("a:tube", "Tube", "chevron")])
        self.app.t.lang = "ko"
        self.assertEqual([t for _, t in await self.rows("/apps", "key", "title")], ["Lagrange", "튜브"])

    async def test_a_window_app_opens_once_and_waits_on_the_home_screen(self):
        self.lagrange()
        await self.app.pages.activate("/apps", "a:lagrange")
        apps = self.app.apps
        self.assertEqual((apps.front, apps.front_keys), ("app-lagrange", {"a": [28], "b": [56, 105], "l1": [104]}))
        await apps.home()
        self.assertIsNone(apps.front_keys)
        self.assertEqual([(i["title"], i["preview"]) for i in (await self.app.pages.build("/"))["items"]
                          if i.get("key") == "app:app-lagrange"], [("Lagrange", "apps")])
        first = apps.running["app-lagrange"]
        await self.app.pages.activate("/apps", "a:lagrange")
        self.assertIs(apps.running["app-lagrange"], first, "picked up again, not started anew")
        self.assertEqual(apps.front, "app-lagrange")

    async def test_the_gamepad_stays_isopods_while_an_app_takes_keys(self):
        class Pad:
            keys, exclusive = "unset", None

            def set_keys(self, keys):
                self.keys = keys

            def set_exclusive(self, on):
                self.exclusive = on
        self.lagrange()
        pad = self.app.input = Pad()
        await self.app.pages.activate("/apps", "a:lagrange")
        self.assertEqual((pad.keys["a"], pad.exclusive), ([28], True))
        self.app.state.data["screen"]["on"] = False
        self.app._update_exclusive()
        self.assertEqual((pad.keys, pad.exclusive), (None, True))
        self.app.state.data["screen"]["on"] = True
        await self.app.apps.home()
        self.assertEqual((pad.keys, pad.exclusive), (None, True))

    async def test_a_list_app_s_pages_searches_and_videos(self):
        self.tube()
        pages = self.app.pages
        self.assertEqual(await pages.activate("/apps", "a:tube"), {"push": "/apps/tube"})
        page = await pages.build("/apps/tube")
        self.assertEqual((page["title"], page["rows"]), ("Tube", "tall"))
        self.assertEqual([(i.get("key"), i["title"], i.get("subtitle")) for i in page["items"]], [
            (None, "Channels", None), ("p:/channel/1", "Lo-fi Girl", "12 new"), ("s:/search", "Search", None),
            ("v:abc", "A video", None), ("v:gone", "Blocked", None)])
        push = (await pages.activate("/apps/tube", "p:/channel/1"))["push"]
        self.assertEqual([t for t, in await self.rows(push, "title")], ["/channel/1?"])
        ask = (await pages.activate("/apps/tube", "s:/search"))["input"]
        self.assertEqual((ask["title"], ask["placeholder"]), ("Search", "Videos"))
        push = (await pages.resolve(ask["token"], " lo/fi "))["push"]
        found = await pages.build(push)
        self.assertEqual((found["title"], [i["title"] for i in found["items"]]),
                         ("Search for “lo/fi”", ["/search?lo/fi"]))
        await pages.activate("/apps/tube", "v:abc")
        film = self.app.apps.running["video"]
        self.assertEqual((film["title"], film["args"]), ("A video, played", [
            "https://v/abc.mp4", "--ytdl=no", "--force-media-title=A video, played",
            "--audio-files-append=https://a/abc.m4a", "--http-header-fields-append=Referer: https://x/"]))
        await pages.activate("/apps/tube", "v:gone")
        self.assertEqual(self.toasts, ["Not in your country"])

    async def test_a_list_app_that_cannot_answer(self):
        make_app(self.root, "mute", {"name": "Mute", "kind": "list", "exec": [sys.executable, "-c", "pass"]})
        page = await self.app.pages.build("/apps/mute")
        self.assertEqual((page["items"], page["empty"]["title"]), ([], "Mute Didn’t Answer"))
        self.assertEqual(await self.app.pages.activate("/apps/mute", "v:abc"), {"reload": True})

    async def test_apps_copied_in_are_noticed_and_a_quiet_list_app_stopped(self):
        changed = []
        x = self.app.extras
        x.changed = lambda: changed.append(True)
        watch = spawn(x.watch(every=0.02))
        self.addCleanup(watch.cancel)
        await asyncio.sleep(0.05)
        self.assertEqual(changed, [])
        self.tube()
        await asyncio.sleep(0.1)
        self.assertEqual(changed, [True])
        await self.app.pages.build("/apps/tube")
        provider = x._providers["tube"]
        self.assertTrue(provider.running)
        provider.used_at -= 3600
        await asyncio.sleep(0.1)
        self.assertNotIn("tube", x._providers)
        self.assertFalse(provider.running)


class AppKeysTest(unittest.TestCase):
    """Buttons pressed as keys for an app in front that knows no gamepad."""

    class Keyboard:
        def __init__(self):
            self.log = []

        def press(self, button, codes):
            self.log.append(("press", button, codes))

        def release(self, button):
            self.log.append(("release", button))

        def release_all(self):
            self.log.append("release all")

    class Router:
        def __init__(self):
            self.log = []

        async def key(self, name, repeat):
            self.log.append(name)

        def key_up(self, name):
            self.log.append(name + " up")

        def typed(self):
            self.log.append("typed")

    def test_mapped_buttons_become_keys_and_the_rest_stay_isopods(self):
        async def go():
            router, keyboard = self.Router(), self.Keyboard()
            pad = input_module.Input(router, keyboard=keyboard)
            pad.set_keys({"a": [28], "x": [29, 38]})
            pad._key(input_module.BTN_EAST, 1)
            pad._key(input_module.BTN_EAST, 2)
            pad._key(input_module.BTN_EAST, 0)
            pad._key(input_module.BTN_WEST, 1)
            pad._key(input_module.BTN_MODE, 1)
            await asyncio.sleep(0)
            pad.set_keys(None)
            pad._key(input_module.BTN_WEST, 0)
            await asyncio.sleep(0)
            return router.log, keyboard.log
        router, keyboard = asyncio.run(go())
        self.assertEqual(keyboard, ["release all", ("press", "a", [28]), ("release", "a"), ("press", "x", [29, 38]),
                                    "release all"])
        self.assertEqual(router, ["typed", "typed", "home", "now up"])

    def test_a_combination_is_pressed_in_order_and_let_go_backwards(self):
        self.assertEqual((uinput.combo("Ctrl+L"), uinput.combo("f12"), uinput.combo("0"), uinput.combo("ctrl+")),
                         ([29, 38], [88], [11], None))
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        keyboard = uinput.Keyboard()
        keyboard.fd = w
        keyboard.press("b", [56, 105])
        keyboard.release_all()
        os.close(w)
        data = os.read(r, 4096)
        size = struct.calcsize("llHHi")
        events = [struct.unpack("llHHi", data[i:i + size])[2:] for i in range(0, len(data), size)]
        self.assertEqual([e for e in events if e[0] == 1], [(1, 56, 1), (1, 105, 1), (1, 105, 0), (1, 56, 0)])
        self.assertEqual(len(events), 8, "each key followed by a report")


class AppsWorkingFolderTest(unittest.IsolatedAsyncioTestCase):
    async def test_an_app_runs_in_its_own_folder_on_the_device(self):
        module, ran = sys.modules[Apps.__module__], []

        async def run(*args, **kwargs):
            ran.append(args)
            return 0, "active", ""
        self.addCleanup(setattr, module, "run", module.run)
        module.run = run
        apps = Apps(State(lambda snap: None), FakeEvents(), {}, runner="systemd")
        await apps.launch(AppSpec("app-x", None, ["/apps/x/run"], cwd="/apps/x"), "X")
        cmd = next(a for a in ran if a[0] == "systemd-run")
        self.assertIn("--working-directory=/apps/x", cmd)
        self.assertEqual(cmd[-2:], ("--", "/apps/x/run"))
