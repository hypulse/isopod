import asyncio
import logging
import time

from . import evdev
from .util import spawn

log = logging.getLogger("isopod.input")

BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_SELECT, BTN_START, BTN_MODE = 0x13A, 0x13B, 0x13C
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
KEY_ESC, KEY_BACKSPACE, KEY_ENTER, KEY_SPACE = 1, 14, 28, 57
KEY_UP, KEY_LEFT, KEY_RIGHT, KEY_DOWN = 103, 105, 106, 108
KEY_VOLUMEDOWN, KEY_VOLUMEUP, KEY_POWER = 114, 115, 116
KEY_NEXTSONG, KEY_PLAYPAUSE, KEY_PREVIOUSSONG, KEY_STOPCD = 163, 164, 165, 166
KEY_REWIND, KEY_PLAYCD, KEY_PAUSECD, KEY_FASTFORWARD = 168, 200, 201, 208
SW_LID, SW_HEADPHONE_INSERT = 0x00, 0x02

# Nintendo-style face buttons on the RG35XX: A is east (confirm), B is south (back).
# The four shoulder buttons do nothing.
BUTTONS = {
    BTN_DPAD_UP: "up", BTN_DPAD_DOWN: "down", BTN_DPAD_LEFT: "left", BTN_DPAD_RIGHT: "right",
    BTN_EAST: "confirm", BTN_SOUTH: "back", BTN_NORTH: "more", BTN_WEST: "now",
    BTN_START: "play", BTN_SELECT: "control", BTN_MODE: "home",
    KEY_UP: "up", KEY_DOWN: "down", KEY_LEFT: "left", KEY_RIGHT: "right",
    KEY_ENTER: "confirm", KEY_ESC: "back", KEY_BACKSPACE: "back", KEY_SPACE: "play",
}
MEDIA = {
    KEY_PLAYPAUSE: "toggle", KEY_PLAYCD: "play", KEY_PAUSECD: "pause", KEY_STOPCD: "pause",
    KEY_NEXTSONG: "next", KEY_PREVIOUSSONG: "prev", KEY_FASTFORWARD: "forward", KEY_REWIND: "rewind",
}
VOLUME = {KEY_VOLUMEUP: 1, KEY_VOLUMEDOWN: -1}
REPEATING = {"up", "down", "left", "right"}


def classify(dev):
    roles = set()
    if dev.keys & set(BUTTONS):
        roles.add("buttons")
    if dev.keys & set(VOLUME):
        roles.add("volume")
    if KEY_POWER in dev.keys:
        roles.add("power")
    if dev.keys & set(MEDIA):
        roles.add("media")
    if SW_LID in dev.switches:
        roles.add("lid")
    if SW_HEADPHONE_INSERT in dev.switches:
        roles.add("jack")
    return roles


class Input:
    """Reads every relevant input node; the router decides who gets each event."""

    def __init__(self, router):
        self.router = router
        self.devices = {}
        self._repeats = {}
        self.exclusive = True

    def set_exclusive(self, on):
        """While the shell is in front nobody else sees the buttons, so a frozen
        app does not wake up to a backlog of presses meant for the shell."""
        self.exclusive = on
        for dev in self.devices.values():
            if "buttons" in dev.roles:
                dev.grab(on)

    async def start(self):
        self.scan()
        try:
            import pyudev
        except ImportError:
            log.info("pyudev missing; input hotplug disabled")
            return
        monitor = pyudev.Monitor.from_netlink(pyudev.Context())
        monitor.filter_by("input")
        monitor.start()
        asyncio.get_running_loop().add_reader(monitor.fileno(), self._udev, monitor)

    def _udev(self, monitor):
        while (device := monitor.poll(timeout=0)) is not None:
            node = device.device_node or ""
            if not node.startswith("/dev/input/event"):
                continue
            if device.action == "remove":
                self._drop(node)
            elif device.action == "add":
                asyncio.get_running_loop().call_later(0.3, self.scan)

    def scan(self):
        for node in evdev.event_nodes():
            if node in self.devices:
                continue
            try:
                dev = evdev.Device(node)
            except OSError:
                continue
            roles = classify(dev)
            if not roles:
                dev.close()
                continue
            log.info("input %s: %s %s", node, dev.name, sorted(roles))
            dev.roles = roles
            if "buttons" in roles and self.exclusive:
                dev.grab(True)
            self.devices[node] = dev
            asyncio.get_running_loop().add_reader(dev.fd, self._read, dev)
            if "lid" in roles and dev.switch_state(SW_LID):
                spawn(self.router.lid(True))
            if "jack" in roles:
                self.router.jack(bool(dev.switch_state(SW_HEADPHONE_INSERT)), initial=True)

    def _drop(self, node):
        dev = self.devices.pop(node, None)
        if dev:
            asyncio.get_running_loop().remove_reader(dev.fd)
            dev.close()

    def _read(self, dev):
        try:
            events = dev.read()
        except OSError:
            self._drop(dev.path)
            return
        for etype, code, value in events:
            if etype == evdev.EV_KEY:
                self._key(code, value)
            elif etype == evdev.EV_SW:
                if code == SW_LID:
                    spawn(self.router.lid(bool(value)))
                elif code == SW_HEADPHONE_INSERT:
                    self.router.jack(bool(value))

    def _key(self, code, value):
        if value == 2:
            return
        pressed = value == 1
        if code in BUTTONS:
            name = BUTTONS[code]
            self._stop_repeat(name)
            if pressed:
                spawn(self.router.key(name, False))
                if name in REPEATING:
                    self._repeats[name] = spawn(self._repeat(name, lambda n=name: self.router.key(n, True)))
        elif code in VOLUME:
            name = f"volume{code}"
            self._stop_repeat(name)
            if pressed:
                spawn(self.router.volume(VOLUME[code]))
                self._repeats[name] = spawn(self._repeat(name, lambda d=VOLUME[code]: self.router.volume(d),
                                                         first=0.45, rate=0.12))
        elif code == KEY_POWER:
            spawn(self.router.power_key(pressed))
        elif code in MEDIA and pressed:
            spawn(self.router.media(MEDIA[code]))

    def _stop_repeat(self, name):
        task = self._repeats.pop(name, None)
        if task:
            task.cancel()

    async def _repeat(self, name, fire, first=0.36, rate=0.085):
        await asyncio.sleep(first)
        started = time.monotonic()
        while True:
            await fire()
            held = time.monotonic() - started
            await asyncio.sleep(rate / 2 if held > 1.5 else rate)
