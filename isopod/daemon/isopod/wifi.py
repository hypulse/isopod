import asyncio
import logging
import re
import shutil
import time

from .util import run, spawn

log = logging.getLogger("isopod.wifi")

_FIELD = re.compile(r"((?:\\.|[^:\\])*)(?::|$)")


def parse_terse(line):
    """Split one line of `nmcli -t` output; colons inside values are escaped as \\:."""
    fields = [m.group(1) for m in _FIELD.finditer(line)][:-1] if line else []
    return [re.sub(r"\\(.)", r"\1", f) for f in fields]


class Nmcli:
    def __init__(self):
        self.enabled = False
        self.networks = []
        self.saved = []
        self.listener = lambda: None

    async def start(self, listener):
        self.listener = listener
        await self.refresh()
        spawn(self._monitor())

    async def _monitor(self):
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "nmcli", "monitor", stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
            except OSError:
                return
            pending = None
            while await proc.stdout.readline():
                if pending is None or pending.done():
                    pending = spawn(self._debounced_refresh())
            await proc.wait()
            await asyncio.sleep(2)

    async def _debounced_refresh(self):
        await asyncio.sleep(0.4)
        await self.refresh()

    async def refresh(self, rescan=False):
        code, out, _ = await run("nmcli", "-t", "-f", "WIFI", "general")
        self.enabled = code == 0 and out.strip() == "enabled"
        networks = []
        if self.enabled:
            _, out, _ = await run("nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                                  "device", "wifi", "list", "--rescan",
                                  "yes" if rescan else "no", timeout=20)
            best = {}
            for line in out.splitlines():
                in_use, ssid, signal, security = (parse_terse(line) + ["", "", "", ""])[:4]
                if not ssid:
                    continue
                net = {"ssid": ssid, "signal": int(signal or 0), "secure": bool(security.strip()),
                       "active": in_use == "*"}
                prev = best.get(ssid)
                if not prev or net["active"] or (not prev["active"] and net["signal"] > prev["signal"]):
                    best[ssid] = net
            networks = sorted(best.values(), key=lambda n: (not n["active"], -n["signal"]))
        _, out, _ = await run("nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show")
        saved = []
        for line in out.splitlines():
            name, uuid, kind = (parse_terse(line) + ["", "", ""])[:3]
            if kind == "802-11-wireless":
                saved.append({"name": name, "uuid": uuid})
        self.networks, self.saved = networks, saved
        self.listener()

    async def scan(self):
        await self.refresh(rescan=True)

    async def set_enabled(self, on):
        if shutil.which("wifictl"):
            await run("wifictl", "enable" if on else "disable", timeout=30)
        else:
            await run("nmcli", "radio", "wifi", "on" if on else "off")
        await self.refresh()

    async def connect(self, ssid, password=None):
        args = ["nmcli", "--wait", "30", "device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        code, _, err = await run(*args, timeout=40)
        await self.refresh()
        return code == 0

    async def connect_saved(self, uuid):
        code, _, _ = await run("nmcli", "--wait", "30", "connection", "up", "uuid", uuid, timeout=40)
        await self.refresh()
        return code == 0

    async def forget(self, uuid):
        await run("nmcli", "connection", "delete", "uuid", uuid)
        await self.refresh()


class FakeWifi:
    def __init__(self):
        self.enabled = True
        self.saved = [{"name": "Home", "uuid": "u-home"}, {"name": "Office 5G", "uuid": "u-office"}]
        self.networks = []
        self._air = [("Home", 82, True), ("Cafe isoPod", 64, True), ("Office 5G", 51, True),
                     ("Free Seoul WiFi", 38, False), ("DIRECT-printer", 22, True)]
        self._active = "Home"
        self.listener = lambda: None

    async def start(self, listener):
        self.listener = listener
        await self.refresh()

    async def refresh(self, rescan=False):
        self.networks = [] if not self.enabled else sorted(
            ({"ssid": s, "signal": sig, "secure": sec, "active": s == self._active}
             for s, sig, sec in self._air), key=lambda n: (not n["active"], -n["signal"]))
        self.listener()

    async def scan(self):
        await asyncio.sleep(0.8)
        await self.refresh()

    async def reach(self, ssid):
        """The network in use, by name; none when the last one went out of reach."""
        self._active = ssid or None
        await self.refresh()

    async def set_enabled(self, on):
        self.enabled = on
        if not on:
            self._active = None
        await self.refresh()

    async def connect(self, ssid, password=None):
        await asyncio.sleep(1.0)
        secure = next((sec for s, _, sec in self._air if s == ssid), True)
        if secure and password != "password":
            return False
        self._active = ssid
        if all(s["name"] != ssid for s in self.saved):
            self.saved.append({"name": ssid, "uuid": f"u-{len(self.saved)}"})
        await self.refresh()
        return True

    async def connect_saved(self, uuid):
        await asyncio.sleep(0.6)
        name = next((s["name"] for s in self.saved if s["uuid"] == uuid), None)
        if name not in (s for s, _, _ in self._air):
            return False
        self._active = name
        await self.refresh()
        return True

    async def forget(self, uuid):
        self.saved = [s for s in self.saved if s["uuid"] != uuid]
        await self.refresh()


class Wifi:
    SCAN_EVERY = 15
    SIGNAL_EVERY = 30

    def __init__(self, backend, state, t, events, remember=None):
        self.backend = backend
        self.remember = remember or (lambda on: None)
        self.state = state
        self.t = t
        self.events = events
        self.busy = None
        self._seen = None
        self._scanned_at = 0

    async def start(self):
        await self.backend.start(self._publish)
        spawn(self._follow_signal())

    async def _follow_signal(self):
        """The bars in the title bar follow the signal while there is someone to see them."""
        while True:
            await asyncio.sleep(self.SIGNAL_EVERY)
            if self.backend.enabled and self.events.watching():
                await self.backend.refresh()

    def _publish(self):
        active = next((n for n in self.backend.networks if n["active"]), None)
        self.state.update("wifi", enabled=self.backend.enabled,
                          ssid=active["ssid"] if active else None,
                          signal=active["signal"] if active else 0)
        seen = (self.backend.enabled,
                tuple((n["ssid"], n["active"], n["secure"], n["signal"] // 25) for n in self.backend.networks),
                tuple(sorted(s["uuid"] for s in self.backend.saved)))
        if seen != self._seen:
            self._seen = seen
            self.events.page_changed("/settings/wifi", "/settings/wifi/saved", "/settings", "/control")

    def scan_if_stale(self):
        if time.monotonic() - self._scanned_at > self.SCAN_EVERY:
            self._scanned_at = time.monotonic()
            spawn(self.scan())

    @property
    def enabled(self):
        return self.backend.enabled

    def networks(self):
        return self.backend.networks

    def saved(self):
        return sorted(self.backend.saved, key=lambda s: s["name"].casefold())

    def saved_for(self, ssid):
        return next((s for s in self.backend.saved if s["name"] == ssid), None)

    async def set_enabled(self, on):
        self.remember(bool(on))
        await self.backend.set_enabled(on)

    async def scan(self):
        await self.backend.scan()

    async def join(self, ssid, password=None):
        self.busy = ssid
        self.events.page_changed("/settings/wifi")
        try:
            saved = self.saved_for(ssid)
            ok = (await self.backend.connect_saved(saved["uuid"]) if saved and password is None
                  else await self.backend.connect(ssid, password))
        finally:
            self.busy = None
        key = "wifi_connected" if ok else "wifi_failed"
        self.events.toast(self.t(key, ssid=ssid), "wifi")
        self.events.page_changed("/settings/wifi")
        return ok

    async def forget(self, uuid):
        await self.backend.forget(uuid)
        self.events.toast(self.t("forgot_network"), "wifi")
