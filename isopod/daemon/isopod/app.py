import asyncio
import logging
import os

from . import __version__
from .apps import Apps, catalog, volume_osd
from .audio import Audio, FakeAudio, Pactl
from .battery import Battery
from .bluetooth import Bluetooth, FakeBackend, RavelBackend
from .i18n import Translator
from .input import Input
from .ipc import Server
from .mpd import MPD
from .music import Music
from .pages import Pages
from .power import Power
from .radio import Radio
from .settings import Settings
from .state import State
from .system import System
from .util import spawn
from .wifi import FakeWifi, Nmcli, Wifi

log = logging.getLogger("isopod")

APP_ENV_KEYS = ("WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "SWAYSOCK", "SDL_VIDEODRIVER", "SDL_AUDIODRIVER",
                "XKB_CONFIG_ROOT", "HOME", "PATH", "LANG", "PIPEWIRE_RUNTIME_DIR",
                "SDL_GAMECONTROLLERCONFIG", "SDL_GAMECONTROLLERCONFIG_FILE")


class App:
    def __init__(self, args):
        self.sim = args.sim
        self.paths = {"socket": args.socket, "config": args.config, "cache": args.cache,
                      "music": args.music, "videos": args.videos, "books": args.books,
                      "data": args.data}
        self.settings = Settings(args.config)
        self.t = Translator(self.settings["lang"])
        self.state = State(self._state_changed)
        self.server = Server(args.socket, self._handle)
        self.system = System(sim=self.sim)
        self.music = Music(MPD(args.mpd, args.mpd_port), self.state, args.cache, music_dir=args.music)
        self.music.song_changed = lambda: self.page_changed("/nowplaying", "/nowplaying/lyrics")
        self.music.favorites_changed = lambda: self.page_changed("/music/*")
        self.music.stream_failed = lambda: self.toast(self.t("radio_unreachable"), "wifi")
        self.radio = Radio(args.config, args.cache)
        self.radio.changed = lambda: self.page_changed("/radio/*")
        self.music.station_favorite = lambda station: self.radio.is_favorite(station.get("uuid"))
        self.bluetooth = Bluetooth(FakeBackend() if self.sim else RavelBackend(),
                                   self.state, self.settings, self.t, self, self.system)
        # ROCKNIX switches Wi-Fi by its own setting at boot and on every wake.
        self.wifi = Wifi(FakeWifi() if self.sim else Nmcli(), self.state, self.t, self,
                         remember=lambda on: spawn(self.system.rocknix_setting("wifi.enabled", int(on))))
        self.audio = Audio(FakeAudio() if self.sim else Pactl(), self.state, self.settings,
                           remember=lambda level: spawn(self.system.rocknix_setting("audio.volume", level)),
                           lost=self._route_lost, first=self.system.rocknix_volume)
        self.power = Power(self.state, self.settings, self, sim=self.sim, busy=self.system.computer_connected,
                           remember=lambda percent: spawn(self.system.rocknix_setting("display.brightness", percent)))
        self.battery = Battery(self.state, notify=self._battery_low, sim=self.sim)
        runner = getattr(args, "apps", None) or ("sim" if self.sim else "systemd")
        self.apps = Apps(self.state, self, {k: v for k, v in os.environ.items() if k in APP_ENV_KEYS},
                         runner=runner)
        self.catalog = catalog(args.data, args.books, lambda: self.t.lang, sim=runner == "sim",
                               run_dir=os.path.dirname(args.socket))
        self.pages = Pages(self)
        self.music.step_station = self.pages.step_station
        self.input = None if self.sim else Input(self)
        self._last_player_state = "stop"
        self.ui_ready_at = None
        self._page_paths = set()
        self._page_flush = None

    async def run(self):
        await self.server.start()
        self.music.on_library_change(lambda: self.page_changed("/music", "/"))
        for name, service in (("music", self.music), ("bluetooth", self.bluetooth),
                              ("wifi", self.wifi), ("audio", self.audio), ("power", self.power),
                              ("battery", self.battery), ("apps", self.apps), ("input", self.input)):
            try:
                if service:
                    await service.start()
            except Exception:
                log.exception("%s failed to start", name)
        log.info("isoPod %s ready", __version__)
        await asyncio.Event().wait()

    # state and events --------------------------------------------------------

    def _state_changed(self, snapshot):
        self.server.broadcast({"event": "state", "state": snapshot})
        playing = snapshot["player"]["state"]
        if playing != self._last_player_state:
            self._last_player_state = playing
            spawn(self.power.playback_changed())
            self.page_changed("/")

    def toast(self, text, icon=None):
        self.server.broadcast({"event": "toast", "text": text, "icon": icon})

    def _battery_low(self, percent):
        self.toast(self.t("battery_low", n=percent), "battery")

    def page_changed(self, *paths):
        self._page_paths.update(paths)
        if self._page_flush is None:
            self._page_flush = asyncio.get_running_loop().call_later(0.1, self._flush_pages)

    def _flush_pages(self):
        self._page_flush = None
        paths, self._page_paths = sorted(self._page_paths), set()
        self.server.broadcast({"event": "page", "paths": paths})

    def audio_device_found(self, device):
        if self.sim:
            self.audio.backend.add_bluetooth(device.name, device.address)

    def audio_device_lost(self, device):
        self.audio.bluetooth_lost(device.address)
        if self.sim:
            self.audio.backend.remove_bluetooth(device.address)

    async def _route_lost(self, kind):
        """Headphones gone, wired or Bluetooth: music and a video pause, as on an iPhone."""
        if not self.settings["pause_on_unplug" if kind == "wired" else "pause_on_disconnect"]:
            return
        await asyncio.gather(self.music.pause(), self.apps.pause_media())
        if kind == "bluetooth":
            await self.music.reset_output()

    def _update_exclusive(self):
        """The gamepad belongs to the app in front only while the screen is lit."""
        if self.input:
            self.input.set_exclusive(self.apps.front is None or not self.power.screen_on)

    def foreground_changed(self):
        self._update_exclusive()
        self.page_changed("/")

    def screen_changed(self, on):
        self._update_exclusive()
        spawn(self.apps.set_dark(not on))

    def screen_timeout(self, setting):
        """Seconds of quiet before the screen goes dark; 0 means never."""
        hold = self.apps.front_awake()
        if hold is None or not setting:
            return setting
        return 0 if hold == 0 else max(setting, hold)

    def watching(self):
        return self.power.screen_on

    def playback_changed(self):
        """The video in front was paused or went on: the screen's hold changes with it."""
        self.power.poke()

    def app_failed(self, title):
        self.toast(self.t("app_failed", name=title))

    def power_menu(self):
        t = self.t
        options = [{"key": "sleep", "title": t("sleep_now")},
                   {"key": "restart", "title": t("restart")},
                   {"key": "shutdown", "title": t("shut_down"), "destructive": True}]

        async def choose(choice):
            action = {"sleep": self.power.sleep, "restart": self.power.restart,
                      "shutdown": self.power.shut_down}.get(choice)
            if action:
                spawn(action())
            return {"none": True}
        if self.apps.front:
            spawn(self.apps.home())
        self.server.broadcast({"event": "present", "do": {"sheet": {
            "title": "", "items": options, "token": self.pages._token(choose)}}})

    def before_sleep(self):
        self.audio.flush()
        spawn(self.music.pause())

    def close(self):
        """Power is about to go, or isopodd is being stopped."""
        self.audio.flush()

    def set_language(self, lang):
        self.t.lang = lang

    async def launch(self, spec, title, args=()):
        if spec.pauses_music:
            await self.music.pause()
        await self.apps.launch(spec, title, args)

    async def resume_app(self, key):
        if self.apps.is_media(key):
            await self.music.pause()
        await self.apps.resume(key)

    # input routing -----------------------------------------------------------

    async def key(self, name, repeat=False):
        if self.power.lid_closed:
            return
        if not repeat and await self.power.wake():
            return
        self.power.activity()
        if name == "home":
            # Home is always the home screen: the app in front waits, the shell goes to its root.
            if self.apps.front:
                await self.apps.home()
            self.server.broadcast({"event": "key", "key": "home"})
            return
        if self.apps.front:
            if self.apps.front_plays_media:
                # A video has the gamepad to itself, but up and down are the volume there as
                # they are in Now Playing, and the volume is isoPod's.
                if name in ("up", "down"):
                    await self.volume(1 if name == "up" else -1)
            elif name == "play":
                # A book is read to music, and Start plays or pauses it as it does in the shell.
                await self.music.toggle()
            return
        self.server.broadcast({"event": "key", "key": name, "repeat": repeat})

    def key_up(self, name):
        """Now Playing tells a tap of left or right from a hold by when the button comes up."""
        if not (self.power.lid_closed or self.apps.front):
            self.server.broadcast({"event": "key", "key": name, "up": True})

    async def volume(self, delta):
        self.power.activity()
        level = await self.audio.change(delta * Audio.STEP)
        if level is None:
            return
        if self.apps.front:
            await self.apps.say(volume_osd(self.t("volume"), level))
        else:
            self.server.broadcast({"event": "hud", "kind": "volume", "value": level,
                                   "output": self.state.get("volume")["output"]})

    async def power_key(self, pressed):
        await self.power.power_key(pressed)

    async def media(self, action):
        """A headset's buttons go to what is in front: a video, else the music."""
        if self.apps.front_plays_media:
            await self.apps.media(action)
            return
        m = self.music
        if action == "toggle":
            await m.toggle()
        elif action == "play":
            if self.state.get("player")["state"] != "play":
                await m.toggle()
        elif action == "pause":
            await m.pause()
        elif action == "next":
            await m.next()
        elif action == "prev":
            await m.previous()
        elif action == "forward":
            await m.seek(15)
        elif action == "rewind":
            await m.seek(-15)

    async def lid(self, closed):
        await self.power.lid(closed)

    def jack(self, inserted, initial=False):
        if self.sim:
            self.audio.backend.plug(inserted)
        self.audio.jack(inserted, initial)

    # requests from the shell -------------------------------------------------

    async def _handle(self, client, req):
        op = req.get("op")
        path = req.get("path", "/")
        if op == "hello":
            if self.ui_ready_at is None:
                uptime = self.system.uptime()
                log.info("shell connected %.1f s after the kernel started", uptime or 0)
                # Only a hello during boot measures boot; a restart of isopodd later does not.
                self.ui_ready_at = uptime if uptime and uptime < 180 else 0
                # ROCKNIX's 006-display has run by now and set its own brightness.
                self.power.apply_brightness()
            return {"state": self.state.snapshot(), "strings": self.t.shell_strings(),
                    "lang": self.t.lang, "version": __version__}
        if op == "page":
            return {"page": await self.pages.build(path)}
        if op == "leave":
            self.pages.leave(path)
            return {}
        if op == "activate":
            return {"do": await self.pages.activate(path, req.get("key", ""))}
        if op == "alt":
            if path == "/nowplaying":
                return {"do": await self.pages.now_playing_options()}
            return {"do": await self.pages.alt(path, req.get("key", ""))}
        if op == "resolve":
            return {"do": await self.pages.resolve(req.get("token", ""), req.get("value"))}
        if op == "slider":
            await self.pages.slider(path, req.get("value", 0))
            return {}
        if op == "player":
            await self._player(req.get("cmd"), req.get("value"))
            return {}
        if op == "volume":
            await self.volume(int(req.get("delta", 0)))
            return {}
        if op == "key":
            name = req.get("key")
            if name == "power":
                await self.power.power_key(True)
                await self.power.power_key(False)
            else:
                await self.key(name)
            return {}
        if op == "activity":
            self.power.activity()
            return {}
        if op == "sim" and self.sim:
            # Hardware the simulator lacks: the lid, a held power key, the battery, the jack,
            # a Wi-Fi network going out of reach.
            if "lid" in req:
                await self.lid(bool(req["lid"]))
            if "power" in req:
                await self.power.power_key(bool(req["power"]))
            if "battery" in req:
                self.battery.simulate(int(req["battery"]), bool(req.get("charging")))
            if "jack" in req:
                self.jack(bool(req["jack"]))
            if "wifi" in req:
                await self.wifi.backend.reach(req["wifi"])
            return {}
        raise ValueError(f"unknown op {op}")

    async def _player(self, cmd, value):
        m = self.music
        if cmd == "toggle":
            await m.toggle()
        elif cmd == "next":
            await m.next()
        elif cmd == "prev":
            await m.previous()
        elif cmd == "seek":
            await m.seek(int(value or 0))
