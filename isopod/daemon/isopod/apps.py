import asyncio
import json
import logging
import os
import shutil
import signal
import time

from .util import run, spawn

log = logging.getLogger("isopod.apps")

SHELL_WORKSPACE = 1
APP_WORKSPACES = range(2, 7)
MAX_OPEN = 3
MIN_AVAILABLE_MB = 256


def _first_existing(*paths):
    return next((p for p in paths if p and (os.path.exists(p) or shutil.which(p))), None)


def _group_alive(pgid):
    """Any process of the group still running (zombies waiting for a reaper do not count)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] != "Z" and int(fields[2]) == pgid:
            return True
    return False


async def _mpv(path, command):
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), 1)
        writer.write((json.dumps({"command": command}) + "\n").encode())
        await writer.drain()
        await asyncio.wait_for(reader.readline(), 1)
        writer.close()
    except (OSError, asyncio.TimeoutError):
        pass


def _available_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class AppSpec:
    """`awake` holds the screen: 0 keeps it lit while the app is in front, N stretches the
    timeout to at least N seconds, None leaves it to the setting."""

    def __init__(self, key, title_key, argv, needs=None, pauses_music=False, env=None, setup=None,
                 awake=None, control=None):
        self.key = key
        self.title_key = title_key
        self.argv = argv
        self.needs = needs
        self.pauses_music = pauses_music
        self.env = env or {}
        self.setup = setup
        self.awake = awake
        self.control = control  # mpv's JSON IPC socket, to pause it when headphones go

    def available(self):
        return self.needs is None or _first_existing(*self.needs) is not None


KOREADER_HOME = "/storage/.config/koreader"
KOREADER_DEFAULTS = """-- Written once by isoPod; KOReader owns this file from now on.
return {
    ["home_dir"] = "/storage/books",
    ["lastdir"] = "/storage/books",
    ["language"] = "%s",
    ["back_in_reader"] = "default",
    ["back_to_exit"] = "always",
    ["quickstart_shown_version"] = 999999999,
    ["color_rendering"] = true,
}
"""


def prepare_koreader(data_dir, lang):
    """Install isoPod's button layout patch and first-run settings into KOReader's home."""
    try:
        patches = os.path.join(KOREADER_HOME, "patches")
        os.makedirs(patches, exist_ok=True)
        src = os.path.join(data_dir, "koreader", "patches")
        for name in os.listdir(src):
            shutil.copyfile(os.path.join(src, name), os.path.join(patches, name))
        settings = os.path.join(KOREADER_HOME, "settings.reader.lua")
        if not os.path.exists(settings):
            with open(settings, "w") as f:
                f.write(KOREADER_DEFAULTS % ("ko_KR" if lang == "ko" else "en"))
    except OSError as e:
        log.warning("could not prepare KOReader: %s", e)


def catalog(data_dir, lang=lambda: "ko", sim=False, run_dir="/run/isopod"):
    koreader = _first_existing("/usr/bin/koreader", "/storage/koreader/koreader.sh")
    portmaster = _first_existing("/usr/bin/start_portmaster.sh")
    if sim:
        koreader, portmaster = "koreader", "start_portmaster.sh"
    specs = {
        "koreader": AppSpec("koreader", "books", [koreader] if koreader else None, needs=[koreader],
                            env={"KO_HOME": KOREADER_HOME, "SDL_FULLSCREEN": "1",
                                 "EMULATE_READER_W": "640", "EMULATE_READER_H": "480"},
                            setup=lambda: prepare_koreader(data_dir, lang()), awake=600),
        "portmaster": AppSpec("portmaster", "PortMaster", [portmaster] if portmaster else None,
                              needs=[portmaster]),
        "retroarch": AppSpec("retroarch", "RetroArch", ["retroarch"], needs=["retroarch"],
                             pauses_music=True),
        "video": AppSpec("video", "videos", ["mpv", f"--config-dir={data_dir}/mpv",
                                             f"--input-ipc-server={run_dir}/mpv.sock"],
                         needs=["mpv"], pauses_music=True, awake=0, control=f"{run_dir}/mpv.sock"),
    }
    if sim:
        for spec in specs.values():
            spec.needs = None
    return specs


class Apps:
    """External programs, one sway workspace each. On the device each is a transient systemd
    unit frozen through its cgroup; a desktop session runs them as process groups stopped with
    SIGSTOP; the simulator only records the launch."""

    def __init__(self, state, events, env, sim=False, runner=None):
        self.state = state
        self.events = events
        self.env = env
        self.runner = runner or ("sim" if sim else "systemd")
        self.sim = self.runner == "sim"
        self.running = {}
        self.front = None
        self._lock = asyncio.Lock()

    async def start(self):
        if not self.sim:
            spawn(self._watch_sway())

    async def _alive(self, app):
        if self.runner == "direct":
            return app["proc"].returncode is None
        if self.sim:
            return True
        _, out, _ = await run("systemctl", "is-active", f"{app['unit']}.service")
        return out.strip() in ("active", "activating", "deactivating", "reloading")

    def _group(self, app, sig):
        try:
            os.killpg(app["proc"].pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    @property
    def front_plays_media(self):
        app = self.running.get(self.front)
        return bool(app and app["media"])

    def opened_file(self, key):
        app = self.running.get(key)
        return app["args"][0] if app and app["args"] else None

    def is_media(self, key):
        app = self.running.get(key)
        return bool(app and app["media"])

    def front_awake(self):
        app = self.running.get(self.front)
        return app["awake"] if app else None

    async def pause_media(self):
        """Pause a playing video. One frozen in the background is silent and stays as it is."""
        for app in list(self.running.values()):
            if app["control"] and not app["frozen"] and not self.sim:
                await _mpv(app["control"], ["set_property", "pause", True])

    async def _signal(self, app, verb):
        if self.runner == "direct":
            self._group(app, signal.SIGSTOP if verb == "freeze" else signal.SIGCONT)
        elif not self.sim:
            await run("systemctl", verb, f"{app['unit']}.service")

    async def set_dark(self, dark):
        """A dark screen pauses the app in front; light brings it back where it was."""
        async with self._lock:
            app = self.running.get(self.front)
            if app and app["frozen"] != dark:
                await self._signal(app, "freeze" if dark else "thaw")
                app["frozen"] = dark

    async def _make_room(self):
        """Like a phone: when too many apps wait in the background or memory runs low,
        the one used longest ago is closed (it gets SIGTERM, so it can save)."""
        while self.running:
            available = None if self.sim else _available_mb()
            if len(self.running) < MAX_OPEN and (available is None or available >= MIN_AVAILABLE_MB):
                return
            oldest = min(self.running, key=lambda k: self.running[k]["used_at"])
            log.info("closing %s to make room (%s MB free)", oldest, available)
            await self.close(oldest)

    def _publish(self):
        self.state.replace("apps", [
            {"key": key, "title": app["title"], "frozen": app["frozen"], "front": key == self.front}
            for key, app in self.running.items()])
        self.events.page_changed("/", "/books/*", "/games")

    def _free_workspace(self):
        used = {app["workspace"] for app in self.running.values()}
        return next((ws for ws in APP_WORKSPACES if ws not in used), None)

    async def launch(self, spec, title, extra_args=()):
        if spec.key in self.running:
            await self.close(spec.key)
        await self._make_room()
        workspace = self._free_workspace()
        if workspace is None or not spec.argv:
            return False
        app_id = f"isopod-app-{workspace}"
        unit = f"isopod-{spec.key}-{workspace}"
        argv = list(spec.argv) + list(extra_args)
        if argv[0] == "mpv":
            argv.insert(1, f"--wayland-app-id={app_id}")
        env = {**self.env, **spec.env, "SDL_VIDEO_WAYLAND_WMCLASS": app_id, "SDL_VIDEO_X11_WMCLASS": app_id,
               "SDL_APP_ID": app_id}
        self.running[spec.key] = {"title": title, "unit": unit, "workspace": workspace, "frozen": False,
                                  "app_id": app_id, "media": spec.pauses_music, "args": list(extra_args),
                                  "awake": spec.awake, "control": spec.control, "used_at": time.monotonic()}
        if self.sim:
            log.info("sim: launch %s", argv)
        elif self.runner == "direct":
            if spec.setup:
                spec.setup()
            try:
                proc = await asyncio.create_subprocess_exec(*argv, env={**os.environ, **env}, start_new_session=True)
            except OSError as e:
                log.warning("launch failed: %s", e)
                self.running.pop(spec.key, None)
                return False
            self.running[spec.key]["proc"] = proc
            await run("swaymsg", "workspace", "number", str(workspace))
            spawn(self._watch_start(spec.key, unit))
        else:
            if spec.setup:
                spec.setup()
            # Every process gets SIGTERM so readers and games can save before they go.
            cmd = ["systemd-run", f"--unit={unit}", "--collect", "--quiet",
                   "--property=TimeoutStopSec=5", "--property=OOMScoreAdjust=300"]
            cmd += [f"--setenv={k}={v}" for k, v in env.items()]
            code, _, err = await run(*cmd, "--", *argv)
            if code != 0:
                log.warning("launch failed: %s", err.strip())
                self.running.pop(spec.key, None)
                return False
            await run("swaymsg", "workspace", "number", str(workspace))
            spawn(self._watch_start(spec.key, unit))
        self.front = spec.key
        self.events.foreground_changed()
        self._publish()
        return True

    async def _watch_start(self, key, unit):
        """An app that dies before it shows a window would leave an empty workspace in front."""
        for _ in range(10):
            await asyncio.sleep(1)
            app = self.running.get(key)
            if not app or app["unit"] != unit:
                return
            if not await self._alive(app):
                log.warning("%s exited during start", unit)
                self.events.app_failed(app["title"])
                await self._reap(key, delay=0)
                return

    async def home(self):
        """Freeze the front app and bring the shell forward."""
        key = self.front
        if key is None:
            return
        app = self.running.get(key)
        if app:
            async with self._lock:
                if not app["frozen"]:
                    await self._signal(app, "freeze")
                app["frozen"] = True
                app["used_at"] = time.monotonic()
        self.front = None
        if not self.sim:
            await run("swaymsg", "workspace", "number", str(SHELL_WORKSPACE))
        self.events.foreground_changed()
        self._publish()

    async def resume(self, key):
        app = self.running.get(key)
        if not app:
            return
        async with self._lock:
            await self._signal(app, "thaw")
            app["frozen"] = False
            app["used_at"] = time.monotonic()
        if not self.sim:
            await run("swaymsg", "workspace", "number", str(app["workspace"]))
        self.front = key
        self.events.foreground_changed()
        self._publish()

    async def close(self, key):
        app = self.running.pop(key, None)
        if not app:
            return
        if self.runner == "direct":
            # Launchers are often shell scripts that die at once; the program under them
            # is still saving, so wait for the whole group like systemd waits for the unit.
            self._group(app, signal.SIGCONT)
            self._group(app, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while _group_alive(app["proc"].pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.1)
            self._group(app, signal.SIGKILL)
            await app["proc"].wait()
        elif not self.sim:
            await run("systemctl", "thaw", f"{app['unit']}.service")
            await run("systemctl", "stop", f"{app['unit']}.service", timeout=6)
        if self.front == key:
            self.front = None
            if not self.sim:
                await run("swaymsg", "workspace", "number", str(SHELL_WORKSPACE))
            self.events.foreground_changed()
        self._publish()

    async def _watch_sway(self):
        """Notice apps that exit on their own and return to the shell."""
        while True:
            proc = await asyncio.create_subprocess_exec(
                "swaymsg", "-t", "subscribe", "-m", '["window"]',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            while line := await proc.stdout.readline():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                change = event.get("change")
                if change == "new":
                    spawn(self._adopt(event.get("container") or {}))
                elif change == "close":
                    # Ports and PortMaster name their own windows, so any close is a
                    # reason to look at every app.
                    for key in list(self.running):
                        spawn(self._reap(key))
            await proc.wait()
            await asyncio.sleep(2)

    async def _adopt(self, con):
        """A window isoPod did not name joins the app launched last, never the shell."""
        app_id = con.get("app_id") or ""
        if app_id.startswith("isopod-") or "id" not in con or not self.running:
            return
        latest = max(self.running.values(), key=lambda a: a["used_at"])
        await run("swaymsg", f"[con_id={con['id']}]", "move", "container", "to", "workspace",
                  "number", str(latest["workspace"]))

    async def _reap(self, key, delay=0.5):
        await asyncio.sleep(delay)
        app = self.running.get(key)
        if not app or await self._alive(app):
            return
        self.running.pop(key, None)
        if self.front == key:
            self.front = None
            await run("swaymsg", "workspace", "number", str(SHELL_WORKSPACE))
            self.events.foreground_changed()
        self._publish()


def installed_ports(ports_dir):
    """Launch scripts PortMaster installed, as (title, path)."""
    try:
        names = sorted(os.listdir(ports_dir), key=str.casefold)
    except OSError:
        return []
    return [(os.path.splitext(n)[0], os.path.join(ports_dir, n)) for n in names
            if n.endswith(".sh") and n.lower() != "portmaster.sh"]


def port_spec(title, path):
    return AppSpec("port", title, ["bash", path], pauses_music=True)
