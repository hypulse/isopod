import asyncio
import dataclasses
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from isopod.app import App  # noqa: E402
from isopod.audio import Audio, FakeAudio, _sink  # noqa: E402
from isopod import uevents  # noqa: E402
from isopod.battery import Battery  # noqa: E402
from isopod.bluetooth import A2DP_SINK, Backend, Bluetooth, Device, RavelBackend  # noqa: E402
from isopod.mpd import MPD, filter_expr, quote, records, values  # noqa: E402
from isopod.music import Music  # noqa: E402
from isopod.pages import VIDEO_EXTS, Pages, folder_listing  # noqa: E402
from isopod.apps import AppSpec, Apps  # noqa: E402
from isopod.i18n import Translator  # noqa: E402
from isopod.lyrics import decode, parse_lrc  # noqa: E402
from isopod.main import parse_args  # noqa: E402
from isopod.power import Power  # noqa: E402
from isopod.settings import Settings  # noqa: E402
from isopod.state import State  # noqa: E402
from isopod.system import System  # noqa: E402
from isopod.util import enc, fmt_duration, index_letter, matches, sort_key, spawn, split_path  # noqa: E402
from isopod.wifi import FakeWifi, Wifi, parse_terse  # noqa: E402


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


class CableTest(unittest.IsolatedAsyncioTestCase):
    async def test_the_cable_going_in_and_coming_out_is_told_once_each(self):
        root, cable = tempfile.mkdtemp(), []

        def supply(name, **values):
            os.makedirs(os.path.join(root, name), exist_ok=True)
            for key, value in values.items():
                with open(os.path.join(root, name, key), "w") as f:
                    f.write(value)

        supply("axp20x-battery", type="Battery", capacity="80", status="Charging", online="1")
        supply("axp20x-usb", type="USB", online="1")
        battery = Battery(State(lambda snap: None), cable=cable.append, root=root)
        battery.refresh()
        battery.refresh()
        self.assertEqual(cable, [True])
        supply("axp20x-usb", online="0")
        battery.refresh()
        battery.refresh()
        self.assertEqual(cable, [True, False], "the battery, always there, is not the cable")


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
