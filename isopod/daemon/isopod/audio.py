import asyncio
import json
import logging
import time

from .util import run, spawn

log = logging.getLogger("isopod.audio")


class Pactl:
    """Sinks, their routes, volume and the default sink through pipewire-pulse, the path
    ROCKNIX's own volume script uses. WirePlumber moves the device's own sink between the
    speaker and the headphone routes as the jack changes, keeping volume and mute per route."""

    def __init__(self):
        self.listener = lambda: None
        self.sinks = []
        self.default = None

    async def start(self, listener):
        self.listener = listener
        await self.refresh()
        spawn(self._monitor())

    async def _monitor(self):
        while True:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pactl", "subscribe", stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL)
            except OSError:
                return
            pending = None
            while line := await proc.stdout.readline():
                if b"sink" in line or b"server" in line:
                    if pending is None or pending.done():
                        pending = spawn(self._debounced())
            await proc.wait()
            await asyncio.sleep(1)

    async def _debounced(self):
        await asyncio.sleep(0.05)
        await self.refresh()

    async def refresh(self):
        code, out, _ = await run("pactl", "-f", "json", "list", "sinks")
        try:
            sinks = json.loads(out) if code == 0 else []
        except ValueError:
            sinks = []
        _, default, _ = await run("pactl", "get-default-sink")
        self.default = default.strip() or None
        self.sinks = [_sink(s) for s in sinks]
        self.listener()

    def current(self):
        return next((s for s in self.sinks if s["name"] == self.default), None)

    async def set_volume(self, sink, percent):
        await run("pactl", "set-sink-volume", sink, f"{percent}%")
        await self.refresh()

    async def set_mute(self, sink, on):
        await run("pactl", "set-sink-mute", sink, "1" if on else "0")

    async def set_default(self, sink):
        await run("pactl", "set-default-sink", sink)

    async def set_port(self, sink, port):
        await run("pactl", "set-sink-port", sink, port)


def _sink(raw):
    props = raw.get("properties", {})
    levels = [int(str(ch.get("value_percent", "0")).rstrip("%") or 0)
              for ch in (raw.get("volume") or {}).values()]
    api = props.get("device.api", "")
    active = raw.get("active_port") or ""
    ports, available = {}, True
    for p in raw.get("ports") or []:
        label = f"{p.get('name', '')} {p.get('description', '')}".lower()
        if "headphone" in label:
            ports["headphones"] = p.get("name", "")
        elif "speaker" in label:
            ports["speaker"] = p.get("name", "")
        if p.get("name") == active:
            available = p.get("availability") != "not available"
    if api == "bluez5":
        kind = "bluetooth"
    elif "hdmi" in raw.get("name", "").lower():
        kind = "hdmi"
    elif "headphone" in active.lower():
        kind = "headphones"
    else:
        kind = "speaker"
    return {"name": raw.get("name", ""), "description": raw.get("description", ""),
            "level": max(levels) if levels else 0, "muted": bool(raw.get("mute")), "kind": kind,
            "ports": ports, "port": active, "available": available,
            "address": props.get("api.bluez5.address", "")}


class FakeAudio:
    """PipeWire and WirePlumber as far as isoPod can tell, for the simulator and the tests: the
    device's own sink with a speaker and a headphone route that keep their own volume and
    mute, and Bluetooth sinks that become the default when they arrive."""

    INTERNAL = "alsa_output.platform-audiocodec"
    PORTS = {"speaker": "[Out] Speaker", "headphones": "[Out] Headphones"}

    def __init__(self):
        self.listener = lambda: None
        self.routes = {"speaker": {"level": 40, "muted": False}, "headphones": {"level": 30, "muted": False}}
        self.route = "speaker"
        self.bluetooth = {}
        self.default = self.INTERNAL
        self.sinks = []
        self._build()

    def _build(self):
        own = self.routes[self.route]
        self.sinks = [{"name": self.INTERNAL, "description": "Built-in Audio", "level": own["level"],
                       "muted": own["muted"], "kind": self.route, "ports": dict(self.PORTS),
                       "port": self.PORTS[self.route], "available": True, "address": ""}]
        for name, bt in self.bluetooth.items():
            self.sinks.append({"name": name, "description": bt["description"], "level": bt["level"],
                               "muted": bt["muted"], "kind": "bluetooth", "ports": {}, "port": "",
                               "available": True, "address": bt["address"]})

    def _changed(self):
        self._build()
        self.listener()

    def _state(self, sink):
        return self.routes[self.route] if sink == self.INTERNAL else self.bluetooth.get(sink)

    async def start(self, listener):
        self.listener = listener
        listener()

    async def refresh(self):
        self._changed()

    def current(self):
        return next((s for s in self.sinks if s["name"] == self.default), None)

    async def set_volume(self, sink, percent):
        if self._state(sink):
            self._state(sink)["level"] = percent
        self._changed()

    async def set_mute(self, sink, on):
        if self._state(sink):
            self._state(sink)["muted"] = on
        self._changed()

    async def set_default(self, sink):
        if sink == self.INTERNAL or sink in self.bluetooth:
            self.default = sink
        self._changed()

    async def set_port(self, sink, port):
        self.route = next((r for r, p in self.PORTS.items() if p == port), self.route)
        self._changed()

    # the world outside

    def plug(self, inserted):
        """WirePlumber picks the route whose availability just changed."""
        self.route = "headphones" if inserted else "speaker"
        self._changed()

    def add_bluetooth(self, name, address="AA:BB:CC:DD:EE:01", level=50):
        sink = f"bluez_output.{address.replace(':', '_')}.1"
        self.bluetooth[sink] = {"description": name, "level": level, "muted": False, "address": address}
        self.default = sink  # module-switch-on-connect
        self._changed()

    def remove_bluetooth(self, address=None):
        for sink in [s for s, bt in self.bluetooth.items() if address in (None, bt["address"])]:
            del self.bluetooth[sink]
            if self.default == sink:
                self.default = self.INTERNAL
        self._changed()


class Audio:
    """Where sound goes, by the iPhone's rules. The output connected last plays: Bluetooth or
    wired headphones, else the speaker. Losing the one in use pauses what was playing before a
    note reaches the speaker. While Bluetooth plays, the device's own sink stays muted, so a
    Bluetooth dropout is silent too.

    Each output keeps its own volume, never above the limit, and isoPod is the one that keeps
    it: the speaker and the wired headphones stay where they were left across a restart
    whatever else sets the sink (ROCKNIX's boot script puts one saved number on whichever
    output plays). A headset's own volume buttons are the user's hand and are kept."""

    STEP = 5
    SETTLE = 0.4
    SAVE_AFTER = 0.5
    ECHO = 2.0  # what a headset reports for a moment after its level was set is not a button

    def __init__(self, backend, state, settings, remember=None, lost=None, first=None):
        self.backend = backend
        self.state = state
        self.settings = settings
        self.remember = remember
        self.lost = lost
        self.first = first        # the speaker's level on a device that has never set one
        self.wired = None         # headphones in the jack; unknown until the input layer says
        self.recent = []          # outputs in the order they arrived: "wired" or a Bluetooth sink
        self.output = None        # what plays now: "speaker", "wired", or a sink name
        self._seen = (None, None, None)
        self._levels = dict(settings["volumes"])
        self._leveled = None      # the output whose level was put back when it came into use
        self._setting = asyncio.Lock()
        self._writing = 0
        self._echo_until = 0.0
        self._put_back = []
        self._save = None
        self._save_levels = None
        self._timer = None
        self._running = False
        self._again = False
        self._gen = 0

    async def start(self):
        if "speaker" not in self._levels and self.first:
            level = await self.first()
            if level is not None:
                self._levels["speaker"] = level
        await self.backend.start(self._changed)
        self._schedule(0)

    # what the backend tells us

    def _changed(self):
        self._publish()
        b = self.backend
        bt = tuple(sorted(s["name"] for s in b.sinks if s["kind"] == "bluetooth"))
        internal = self._internal()
        seen = (bt, b.default, internal["port"] if internal else None)
        if seen == self._seen:
            self._watch_level()
            return
        before = self._seen[0] or ()
        self._seen = seen
        for name in bt:
            if name not in before:
                self._arrived(name)
        for name in before:
            if name not in bt:
                self._left(name)
        self._schedule()

    def _publish(self):
        sink = self.backend.current()
        if sink:
            self.state.update("volume", level=sink["level"], muted=sink["muted"],
                              output=sink["kind"], name=sink["description"])

    def _internal(self):
        return next((s for s in self.backend.sinks if s["kind"] in ("speaker", "headphones")), None)

    def _arrived(self, output):
        if output in self.recent:
            self.recent.remove(output)
        self.recent.append(output)

    def _left(self, output):
        if output in self.recent:
            self.recent.remove(output)
        if output == self.output:
            self._lose("bluetooth")

    # events from the rest of isoPod

    def jack(self, inserted, initial=False):
        self.wired = inserted
        if inserted:
            self._arrived("wired")
        else:
            if "wired" in self.recent:
                self.recent.remove("wired")
            if not initial and self.output == "wired":
                self._lose("wired")
        self._schedule()

    def bluetooth_lost(self, address):
        """BlueZ often says so before PipeWire drops the sink; the earlier, the quieter."""
        sink = next((s["name"] for s in self.backend.sinks
                     if s["kind"] == "bluetooth" and s["address"] == address), None)
        if sink and sink == self.output:
            self._lose("bluetooth")

    def _lose(self, kind):
        """The output in use is gone; whoever reports it first, it is handled once."""
        self.output = None
        self._gen += 1
        spawn(self._silence(kind))

    async def _silence(self, kind):
        """Sound falls back to the device's own sink: mute it at once (behind Bluetooth it
        mostly is already) while what was playing pauses; the settle that follows lets the
        speaker speak again."""
        internal = self._internal()
        steps = [self.lost(kind)] if self.lost else []
        if internal:
            steps.append(self.backend.set_mute(internal["name"], True))
        await asyncio.gather(*steps)
        self._schedule()

    # the policy, once things are quiet

    def _schedule(self, delay=None):
        self._gen += 1
        if self._timer:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(
            self.SETTLE if delay is None else delay, self._kick)

    def _kick(self):
        self._timer = None
        if self._running:
            self._again = True
        else:
            spawn(self._run())

    async def _run(self):
        self._running = True
        try:
            while True:
                self._again = False
                await self._apply()
                if not self._again:
                    break
        finally:
            self._running = False

    async def _apply(self):
        gen = self._gen
        b = self.backend
        bt = [s["name"] for s in b.sinks if s["kind"] == "bluetooth"]
        for name in bt:
            if name not in self.recent:
                self.recent.insert(0, name)  # connected before isoPod was listening
        self.recent = [o for o in self.recent if o in bt or (o == "wired" and self.wired)]
        internal = self._internal()
        current = b.current()
        if current and current["kind"] == "hdmi" and current["available"]:
            want = target = current["name"]  # ROCKNIX's hdmi_sense chose it
        else:
            want = self.recent[-1] if self.recent else ("wired" if self.wired else "speaker")
            target = want if want in bt else (internal["name"] if internal else None)

        async def step(action, *args):
            if self._gen != gen:
                self._again = True
                return False
            await action(*args)
            return True

        if target and b.default != target and not await step(b.set_default, target):
            return
        if internal:
            if target == internal["name"] and self.wired is not None:
                port = internal["ports"].get("headphones" if self.wired else "speaker")
                if port and internal["port"] != port and not await step(b.set_port, internal["name"], port):
                    return
            if not await step(b.set_mute, internal["name"], target != internal["name"]):
                return
        self.output = want
        await b.refresh()
        if self._gen != gen:
            self._again = True
            return
        await self._settle_level()

    # volume

    def _key(self):
        sink = self.backend.current()
        if not sink:
            return None
        if sink["kind"] == "bluetooth":
            return f"bt:{sink['address'] or sink['name']}"
        return {"headphones": "wired", "speaker": "speaker"}.get(sink["kind"])

    def _limit(self):
        return self.settings["volume_limit"]

    async def _write(self, key, level):
        """The level of an output: remembered at once, and set when the sink is free. Presses
        that pile up behind a slow sink end at the last level asked for."""
        self._leveled = key
        if self._levels.get(key) != level:
            self._levels[key] = level
            if self._save_levels:
                self._save_levels.cancel()
            self._save_levels = asyncio.get_running_loop().call_later(self.SAVE_AFTER, self.flush)
        async with self._setting:
            sink, level = self.backend.current(), self._levels[key]
            if not sink or self._key() != key or sink["level"] == level:
                return
            self._writing += 1
            try:
                await self.backend.set_volume(sink["name"], level)
            finally:
                self._writing -= 1
            if sink["kind"] == "bluetooth":
                self._echo_until = time.monotonic() + self.ECHO

    def flush(self):
        """Levels reach the disk shortly after the last change, and at once before power goes."""
        if self._save_levels:
            self._save_levels.cancel()
            self._save_levels = None
        self.settings.set("volumes", dict(self._levels))

    async def _settle_level(self):
        """An output comes back at the level it was left at, and never above the limit. One
        heard for the first time keeps the level it arrives with."""
        sink, key = self.backend.current(), self._key()
        if sink and key and key != self._leveled:
            await self._write(key, min(self._levels.get(key, sink["level"]), self._limit()))
        else:
            self._watch_level()

    def _watch_level(self):
        """Somebody else moved the volume of the output in use."""
        sink, key = self.backend.current(), self._key()
        if self._writing or not sink or key != self._leveled or key not in self._levels:
            return
        level = min(self._levels[key], self._limit())
        if abs(sink["level"] - level) <= 1:
            return
        now = time.monotonic()
        self._put_back = [t for t in self._put_back if now - t < 3]
        if sink["kind"] != "bluetooth" and len(self._put_back) < 3:
            self._put_back.append(now)
            spawn(self._write(key, level))
        elif now >= self._echo_until:
            # A headset's buttons, or a sink that will not take the level: what it says goes.
            spawn(self._write(key, min(sink["level"], self._limit())))

    async def change(self, delta):
        sink, key = self.backend.current(), self._key()
        if not sink:
            return None
        known = key in self._levels and key == self._leveled and sink["kind"] != "bluetooth"
        base = min(self._levels[key], self._limit()) if known else sink["level"]
        level = max(0, min(self._limit(), base + delta))
        if key:
            await self._write(key, level)
        elif level != sink["level"]:
            await self.backend.set_volume(sink["name"], level)
        if sink["kind"] == "speaker" and self.remember:
            # What ROCKNIX sets at boot, so the speaker starts where it was even before
            # isoPod is up.
            if self._save:
                self._save.cancel()
            self._save = asyncio.get_running_loop().call_later(1.5, self.remember, level)
        return level

    async def clamp_to_limit(self):
        sink, key = self.backend.current(), self._key()
        if not sink or sink["level"] <= self._limit():
            return
        if key:
            await self._write(key, self._limit())
        else:
            await self.backend.set_volume(sink["name"], self._limit())
