import asyncio
import json
import logging
import os
import re
import shutil
import stat
import time

from . import uinput
from .apps import AppSpec

log = logging.getLogger("isopod.extras")

MANIFEST = "app.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
BUTTONS = ("up", "down", "left", "right", "a", "b", "x", "y", "start", "select", "l1", "r1", "l2", "r2")
KINDS = ("window", "list")
ASK_TIMEOUT = 60
IDLE_SECONDS = 300


class AppError(Exception):
    pass


class Refused(AppError):
    """The app's own answer that it could not do it, in words for the person."""


class Manifest:
    """One folder of /storage/apps: its app.json, read and checked."""

    def __init__(self, folder):
        self.id = os.path.basename(folder)
        self.dir = folder
        if not NAME.match(self.id):
            raise AppError("a folder name is lower-case letters, digits, - and _")
        try:
            with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise AppError(f"{MANIFEST}: {e}") from None
        if not isinstance(data, dict):
            raise AppError(f"{MANIFEST} is not an object")
        self.name = data.get("name")
        if not (isinstance(self.name, str) and self.name or isinstance(self.name, dict) and self.name):
            raise AppError("no name")
        self.kind = data.get("kind", "window")
        if self.kind not in KINDS:
            raise AppError(f"kind is one of {', '.join(KINDS)}")
        exe = data.get("exec")
        if not (isinstance(exe, list) and exe and all(isinstance(a, str) and a for a in exe)):
            raise AppError("exec is a list of words")
        self.argv = [self._program(exe[0])] + exe[1:]
        for field in ("keys", "env"):
            if not isinstance(data.get(field) or {}, dict):
                raise AppError(f"{field} is an object")
        self.keys = {}
        for button, keys in (data.get("keys") or {}).items():
            codes = uinput.combo(keys)
            if button not in BUTTONS or codes is None:
                log.warning("%s: key %s = %s left out", self.id, button, keys)
                continue
            self.keys[button] = codes
        self.pauses_music = bool(data.get("pauses_music", False))
        awake = data.get("awake")
        self.awake = awake if isinstance(awake, int) and awake >= 0 else None
        self.env = {str(k): str(v) for k, v in (data.get("env") or {}).items()}

    def _program(self, word):
        if "/" in word:
            return os.path.join(self.dir, word)
        own = os.path.join(self.dir, word)
        if os.path.isfile(own):
            return own
        return shutil.which(word, path=os.pathsep.join(
            (os.path.join(self.dir, "bin"), os.environ.get("PATH", "/usr/bin:/bin")))) or word

    def title(self, lang):
        if isinstance(self.name, str):
            return self.name
        return str(self.name.get(lang) or self.name.get("en") or next(iter(self.name.values())))

    def make_runnable(self):
        """A copy over the cable carries no permission to run: the program and bin/ get it."""
        files = [self.argv[0]] if self.argv[0].startswith(self.dir + os.sep) else []
        bin_dir = os.path.join(self.dir, "bin")
        if os.path.isdir(bin_dir):
            files += [os.path.join(bin_dir, n) for n in os.listdir(bin_dir)]
        for path in files:
            try:
                mode = os.stat(path).st_mode
                if stat.S_ISREG(mode) and mode & 0o111 != 0o111:
                    os.chmod(path, mode | 0o111)
            except OSError:
                pass


class Provider:
    """A list app: a program that answers isoPod's questions, one JSON line each way. It
    starts at the first question and is stopped after a while without one."""

    def __init__(self, manifest, env):
        self.manifest = manifest
        self.env = env
        self.proc = None
        self.asked = 0
        self.used_at = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.returncode is None

    async def _start(self):
        self.manifest.make_runnable()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.manifest.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                cwd=self.manifest.dir, env=self.env, limit=4 << 20)
        except OSError as e:
            raise AppError(str(e)) from None
        try:
            with open(f"/proc/{self.proc.pid}/oom_score_adj", "w") as f:
                f.write("500")
        except OSError:
            pass

    async def ask(self, question, timeout=ASK_TIMEOUT):
        async with self._lock:
            self.used_at = time.monotonic()
            if not self.running:
                await self._start()
            self.asked += 1
            question = {**question, "id": self.asked}
            try:
                self.proc.stdin.write((json.dumps(question, ensure_ascii=False) + "\n").encode())
                await self.proc.stdin.drain()
                deadline = time.monotonic() + timeout
                while True:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), deadline - time.monotonic())
                    if not line:
                        raise AppError("the app ended")
                    try:
                        answer = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(answer, dict) and answer.get("id") == self.asked:
                        self.used_at = time.monotonic()
                        return answer
            except (asyncio.TimeoutError, OSError, ValueError, AppError) as e:
                await self.stop()
                raise AppError(str(e) or "no answer") from None

    async def stop(self):
        proc, self.proc = self.proc, None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), 2)
        except ProcessLookupError:
            pass
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


class Extras:
    """The apps in /storage/apps, read again whenever the folder or an app.json changes."""

    def __init__(self, folder, data_dir, lang=lambda: "en"):
        self.folder = folder
        self.data_dir = data_dir
        self.lang = lang
        self.changed = None
        self._stamp = None
        self._apps = {}
        self._providers = {}

    def _look(self):
        try:
            entries = sorted(e.name for e in os.scandir(self.folder) if not e.name.startswith("."))
        except OSError:
            entries = []
        stamp = []
        for name in entries:
            try:
                stamp.append((name, os.stat(os.path.join(self.folder, name, MANIFEST)).st_mtime_ns))
            except OSError:
                pass
        return tuple(stamp)

    def apps(self):
        stamp = self._look()
        if stamp != self._stamp:
            self._stamp = stamp
            found = {}
            for name, _ in stamp:
                try:
                    found[name] = Manifest(os.path.join(self.folder, name))
                except AppError as e:
                    log.warning("app %s left out: %s", name, e)
            self._apps = found
        return self._apps

    def sorted(self):
        lang = self.lang()
        return sorted(self.apps().values(), key=lambda m: m.title(lang).casefold())

    def get(self, app_id):
        return self.apps().get(app_id)

    def env(self, m, base):
        home = os.path.join(self.data_dir, m.id)
        os.makedirs(home, exist_ok=True)
        env = dict(base)
        lib = os.path.join(m.dir, "lib")
        if os.path.isdir(lib):
            env["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, (lib, base.get("LD_LIBRARY_PATH"))))
        env["PATH"] = os.pathsep.join((os.path.join(m.dir, "bin"), base.get("PATH", "/usr/bin:/bin")))
        env.update({"HOME": home, "ISOPOD_APP_DIR": m.dir, "ISOPOD_APP_DATA": home, "ISOPOD_LANG": self.lang()})
        env.update(m.env)
        return env

    def spec(self, m):
        """A window app, for the same runner that starts videos and books."""
        m.make_runnable()
        env = self.env(m, {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        return AppSpec("app-" + m.id, None, m.argv, pauses_music=m.pauses_music, env=env,
                       awake=m.awake, cwd=m.dir, keys=m.keys or None)

    async def ask(self, m, question):
        provider = self._providers.get(m.id)
        if provider is None or provider.manifest is not m:
            if provider:
                await provider.stop()
            provider = self._providers[m.id] = Provider(m, self.env(m, os.environ))
        answer = await provider.ask({**question, "lang": self.lang()})
        if answer.get("error"):
            raise Refused(str(answer["error"]))
        return answer

    async def watch(self, every=5):
        """Notice apps copied in or taken out, and stop list apps nobody has asked for a while."""
        self.apps()
        while True:
            await asyncio.sleep(every)
            before = self._stamp
            current = self.apps()
            if self._stamp != before and self.changed:
                self.changed()
            now = time.monotonic()
            for app_id, provider in list(self._providers.items()):
                if current.get(app_id) is not provider.manifest or now - provider.used_at > IDLE_SECONDS:
                    await provider.stop()
                    del self._providers[app_id]

    async def close(self):
        for provider in self._providers.values():
            await provider.stop()
        self._providers.clear()
