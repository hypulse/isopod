import asyncio
import dataclasses
import logging

from .util import spawn

log = logging.getLogger("isopod.bluetooth")

AUDIO_UUIDS = ("0000110b", "0000111e", "00001108", "0000110d")
A2DP_SINK = "0000110b-0000-1000-8000-00805f9b34fb"


@dataclasses.dataclass
class Device:
    path: str
    address: str
    name: str
    icon: str = ""
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    uuids: tuple = ()
    rssi: int = None
    transport: bool = False

    @property
    def is_audio(self):
        return self.icon.startswith("audio") or any(
            u.lower().startswith(AUDIO_UUIDS) for u in self.uuids)

    @property
    def named(self):
        """BlueZ puts the address, dashed, where a name it has not learnt would be."""
        return bool(self.name) and self.name.replace("-", ":").upper() != self.address.upper()

    @property
    def ready(self):
        """Connected as the user means it: headphones count once an A2DP stream has been set
        up, not while only a link is up, which pairing and a stray LE connection also bring."""
        return self.connected and (self.transport or not self.is_audio)

    @property
    def kind(self):
        if self.is_audio:
            return "audio"
        if self.icon.startswith("input"):
            return "input"
        return "other"


class Backend:
    def __init__(self):
        self.devices = {}
        self.streams = set()
        self.powered = False
        self.discovering = False
        self.address = ""
        self.listener = lambda kind, device, old: None

    def _emit(self, kind, device=None, old=None):
        self.listener(kind, device, old)


def _unwrap(value):
    if isinstance(value, (tuple, list)) and len(value) == 2 and type(value[0]).__name__ == "Signature":
        return _unwrap(value[1])
    if isinstance(value, dict):
        return {k: _unwrap(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_unwrap(v) for v in value)
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class RavelBackend(Backend):
    """BlueZ over the system bus via dbussy/ravel, the binding ROCKNIX already ships."""

    async def start(self, listener):
        import dbussy
        import ravel
        self.listener = listener
        self._dbussy, self._ravel = dbussy, ravel
        self.bus = await ravel.system_bus_async(loop=asyncio.get_running_loop())
        self.adapter_path = None

        @ravel.signal(in_signature="oa{sa{sv}}")
        def added(path, interfaces, bus=None):
            self._apply(path, _unwrap(interfaces))

        @ravel.signal(in_signature="oas")
        def removed(path, interfaces, bus=None):
            if "org.bluez.Device1" in interfaces and path in self.devices:
                old = self.devices.pop(path)
                self.streams.discard(path)
                self._emit("removed", None, old)
            if "org.bluez.Adapter1" in interfaces and path == self.adapter_path:
                self.adapter_path = None
                self.powered = False
                self._emit("adapter")

        @ravel.signal(in_signature="sa{sv}as", message_keyword="message")
        def changed(interface, props, invalidated, message=None):
            self._apply(message.path, {interface: _unwrap(props)})

        self._handlers = (added, removed, changed)
        self.bus.listen_objects_added(added)
        self.bus.listen_objects_removed(removed)
        for iface in ("org.bluez.Device1", "org.bluez.Adapter1"):
            self.bus.listen_propchanged(path="/", fallback=True, interface=iface, func=changed)
        await self.refresh()

    async def refresh(self):
        try:
            om = await self.bus.get_proxy_object("org.bluez", "/").get_async_interface(
                self._dbussy.DBUSX.INTERFACE_OBJECT_MANAGER)
            objects = _unwrap((await om.GetManagedObjects())[0])
        except Exception as e:
            log.info("bluez not available: %s", e)
            self.powered = False
            self._emit("adapter")
            return
        for path, interfaces in objects.items():
            self._apply(path, interfaces)

    def _apply(self, path, interfaces):
        if "org.bluez.Adapter1" in interfaces:
            props = interfaces["org.bluez.Adapter1"]
            self.adapter_path = path
            self.powered = bool(props.get("Powered", self.powered))
            self.discovering = bool(props.get("Discovering", self.discovering))
            self.address = props.get("Address", self.address)
            self._emit("adapter")
        if "org.bluez.Device1" in interfaces:
            props = interfaces["org.bluez.Device1"]
            old = self.devices.get(path)
            dev = dataclasses.replace(old) if old else Device(path=path, address="", name="")
            for attr, key in (("address", "Address"), ("icon", "Icon"), ("paired", "Paired"),
                              ("trusted", "Trusted"), ("connected", "Connected"), ("rssi", "RSSI")):
                if key in props:
                    setattr(dev, attr, props[key])
            if "Alias" in props or "Name" in props:
                dev.name = props.get("Alias") or props.get("Name") or dev.address
            if "UUIDs" in props:
                dev.uuids = tuple(props["UUIDs"])
            if not dev.name:
                dev.name = dev.address
            if not dev.connected:
                self.streams.discard(path)
            dev.transport = path in self.streams
            self.devices[path] = dev
            self._emit("changed", dev, old)
        if "org.bluez.MediaTransport1" in interfaces:
            self._streaming(interfaces["org.bluez.MediaTransport1"].get("Device"))

    def _streaming(self, path):
        """An A2DP stream to this device was set up. That holds for as long as the link does:
        a stream that is closed to be configured anew is not the headphones going away."""
        old = self.devices.get(path)
        if not path or (old and not old.connected):
            return
        self.streams.add(path)
        if old and not old.transport:
            dev = dataclasses.replace(old, transport=True)
            self.devices[path] = dev
            self._emit("changed", dev, old)

    async def _iface(self, path, name):
        return await self.bus.get_proxy_object("org.bluez", path).get_async_interface(name)

    async def set_powered(self, on):
        if not self.adapter_path:
            await self.refresh()
        if self.adapter_path:
            adapter = await self._iface(self.adapter_path, "org.bluez.Adapter1")
            adapter.Powered = bool(on)
            await self._ravel.set_prop_flush(adapter)

    async def discover(self, on):
        if not self.adapter_path:
            return
        adapter = await self._iface(self.adapter_path, "org.bluez.Adapter1")
        try:
            if on:
                await self._classic_only(adapter)
                await adapter.StartDiscovery()
            else:
                await adapter.StopDiscovery()
        except Exception as e:
            log.info("discovery %s: %s", on, e)

    async def _classic_only(self, adapter):
        """An LE scan lists every beacon in the building, and it leads BlueZ to reach dual-mode
        headphones such as AirPods over LE, where they pair and then carry no sound."""
        try:
            await adapter.SetDiscoveryFilter({"Transport": ("s", "bredr")})
        except Exception as e:
            log.warning("discovery filter: %s", e)

    async def pair(self, path):
        dev = await self._iface(path, "org.bluez.Device1")
        try:
            await dev.Pair()
        except Exception as e:
            if "AlreadyExists" not in str(e):
                raise

    async def trust(self, path):
        dev = await self._iface(path, "org.bluez.Device1")
        dev.Trusted = True
        await self._ravel.set_prop_flush(dev)

    async def connect(self, path, profile=None):
        dev = await self._iface(path, "org.bluez.Device1")
        await (dev.ConnectProfile(profile) if profile else dev.Connect())
        if profile == A2DP_SINK:
            self._streaming(path)

    async def disconnect(self, path):
        await (await self._iface(path, "org.bluez.Device1")).Disconnect()

    async def remove(self, path):
        adapter = await self._iface(self.adapter_path, "org.bluez.Adapter1")
        await adapter.RemoveDevice(path)


class FakeBackend(Backend):
    """Simulated radio for the development container."""

    def __init__(self):
        super().__init__()
        seeds = [
            ("AirPods Pro", "audio-headphones", True),
            ("8BitDo Pro 2", "input-gaming", True),
            ("Galaxy Buds2", "audio-headset", False),
            ("JBL Flip 6", "audio-card", False),
            ("iPhone", "phone", False),
            ("AA-BB-CC-DD-EE-05", "", False),
        ]
        for i, (name, icon, paired) in enumerate(seeds):
            path = f"/org/bluez/hci0/dev_{i:02X}"
            self.devices[path] = Device(path, f"AA:BB:CC:DD:EE:{i:02X}", name, icon,
                                        paired=paired, trusted=paired, rssi=-40 - 9 * i)
        self.address = "AA:BB:CC:00:00:01"

    async def start(self, listener):
        self.listener = listener
        self.powered = True
        self._emit("adapter")

    async def _change(self, path, delay, **fields):
        await asyncio.sleep(delay)
        old = self.devices[path]
        self.devices[path] = dataclasses.replace(old, **fields)
        self._emit("changed", self.devices[path], old)

    async def set_powered(self, on):
        await asyncio.sleep(0.2)
        self.powered = bool(on)
        if not on:
            for path, dev in list(self.devices.items()):
                if dev.connected:
                    await self._change(path, 0, connected=False, transport=False)
        self._emit("adapter")

    async def discover(self, on):
        discovering = bool(on) and self.powered
        if discovering != self.discovering:
            self.discovering = discovering
            self._emit("adapter")

    async def pair(self, path):
        await self._change(path, 1.2, paired=True)

    async def trust(self, path):
        await self._change(path, 0, trusted=True)

    async def connect(self, path, profile=None):
        await self._change(path, 0.8, connected=True, paired=True, transport=bool(profile))

    async def disconnect(self, path):
        await self._change(path, 0.3, connected=False, transport=False)

    async def remove(self, path):
        old = self.devices[path]
        self.devices[path] = dataclasses.replace(old, paired=False, trusted=False, connected=False,
                                                 transport=False)
        self._emit("changed", self.devices[path], old)


class Bluetooth:
    DISCOVERY_SECONDS = 45
    ATTEMPTS = 3
    RETRY_DELAY = 1.0
    STREAM_WAIT = 4.0

    def __init__(self, backend, state, settings, t, events, system):
        self.backend = backend
        self.state = state
        self.settings = settings
        self.t = t
        self.events = events
        self.system = system
        self.busy = set()
        self._said = {}
        self._discovery_stop = None
        self._adapter_seen = None

    async def start(self):
        await self.backend.start(self._on_backend)
        self._publish()

    def _relevant(self, kind, device, old):
        if kind == "adapter":
            now = (self.backend.powered, self.backend.discovering)
            changed = now != self._adapter_seen
            self._adapter_seen = now
            return changed
        if kind == "changed" and device and old:
            return any(getattr(device, f) != getattr(old, f)
                       for f in ("name", "paired", "trusted", "connected", "icon", "uuids", "transport"))
        return True

    def _on_backend(self, kind, device, old):
        if not self._relevant(kind, device, old):
            return
        if kind == "removed" and old:
            self._said.pop(old.path, None)
        elif kind == "changed" and device and old is None:
            self._said[device.path] = device.ready
        elif kind == "changed" and device and old.ready != device.ready:
            log.info("%s (%s) %s", device.name, device.address, "is ready" if device.ready else "is gone")
            if device.path not in self.busy:
                self._announce(device)
            if device.is_audio:
                (self.events.audio_device_found if device.ready else self.events.audio_device_lost)(device)
                if device.ready and self.discovering:
                    self.end_discovery()
        self._publish()
        self.events.page_changed("/settings/bluetooth", "/control", "/settings")

    def _announce(self, device):
        """Say that a device connected or went away, once per change. What a link does while
        isoPod is still pairing or connecting is not news; the outcome is."""
        if self._said.get(device.path, False) == device.ready:
            return
        self._said[device.path] = device.ready
        if device.ready:
            self.settings.remember_bluetooth(device.address, device.name)
        self.events.toast(self.t("bt_connected" if device.ready else "bt_disconnected", name=device.name),
                          "bluetooth")

    def _publish(self):
        audio = next((d.name for d in self.backend.devices.values() if d.ready and d.is_audio), None)
        self.state.update("bluetooth", enabled=self.backend.powered, audio=audio,
                          discovering=self.backend.discovering)

    @property
    def enabled(self):
        return self.backend.powered

    @property
    def discovering(self):
        return self.backend.discovering or self._discovery_stop is not None

    def _mine(self, dev):
        return dev.paired or dev.ready

    def my_devices(self):
        devs = [d for d in self.backend.devices.values() if self._mine(d)]
        return sorted(devs, key=lambda d: (not d.ready, not d.is_audio, d.name.casefold()))

    def other_devices(self):
        """What is worth pairing nearby: headphones, speakers and controllers that gave a name."""
        devs = [d for d in self.backend.devices.values()
                if not self._mine(d) and d.named and d.kind != "other"]
        return sorted(devs, key=lambda d: -(d.rssi if d.rssi is not None else -200))

    def _listening(self):
        return any(d.ready and d.is_audio for d in self.backend.devices.values())

    def recent_audio(self):
        known = {d.address: d for d in self.backend.devices.values() if d.paired}
        out = []
        for entry in self.settings["recent_bluetooth"]:
            dev = known.get(entry["address"])
            if dev and dev.is_audio:
                out.append(dev)
        for dev in self.my_devices():
            if dev.is_audio and dev not in out:
                out.append(dev)
        return out[:3]

    def find(self, path):
        return self.backend.devices.get(path)

    async def set_enabled(self, on):
        await self.system.set_bluetooth(on)
        if on:
            await self._wait_for_adapter()
        try:
            await self.backend.set_powered(on)
        except Exception as e:
            log.warning("set powered failed: %s", e)
        self._publish()

    async def _wait_for_adapter(self):
        refresh = getattr(self.backend, "refresh", None)
        for _ in range(10):
            if refresh:
                await refresh()
            if getattr(self.backend, "adapter_path", True):
                return
            await asyncio.sleep(0.5)

    def begin_discovery(self):
        """Look for new devices while the Bluetooth page is open, though not while a device is
        being connected or headphones are in use: an inquiry takes the radio away from both."""
        if not self.backend.powered or self.busy or self._listening():
            return
        if not self.backend.discovering:
            spawn(self.backend.discover(True))
        if self._discovery_stop:
            self._discovery_stop.cancel()
        self._discovery_stop = asyncio.get_running_loop().call_later(self.DISCOVERY_SECONDS, self.end_discovery)

    def end_discovery(self):
        spawn(self._stop_discovery())

    async def _stop_discovery(self):
        if self._discovery_stop:
            self._discovery_stop.cancel()
            self._discovery_stop = None
        await self.backend.discover(False)

    async def activate(self, path):
        """One press: connect what is not connected, disconnect what is."""
        dev = self.find(path)
        if not dev or path in self.busy:
            return
        self.busy.add(path)
        self.events.page_changed("/settings/bluetooth", "/control")
        failed = False
        try:
            if dev.ready:
                await self.backend.disconnect(path)
            else:
                await self._connect(dev)
        except Exception as e:
            log.warning("%s (%s): %s", dev.name, dev.address, e)
            failed = True
        finally:
            self.busy.discard(path)
        now = self.find(path)
        if failed:
            self.events.toast(self.t("bt_failed", name=dev.name), "bluetooth")
            if now and not dev.paired and not self._mine(now):
                await self._discard(path)
        elif now:
            self._announce(now)
        self.events.page_changed("/settings/bluetooth", "/control")

    async def _discard(self, path):
        """A first attempt that failed leaves BlueZ a half-made device, with keys the other side
        may not hold. Without it the next attempt starts clean, from a new inquiry."""
        try:
            await self.backend.remove(path)
        except Exception as e:
            log.info("discarding %s: %s", path, e)

    async def _connect(self, dev):
        path = dev.path
        log.info("connecting %s (%s), %s", dev.name, dev.address, "known" if dev.paired else "new")
        if self.discovering:
            await self._stop_discovery()
        if dev.is_audio:
            if not dev.trusted:
                await self.backend.trust(path)
            await self._retry(lambda: self._connect_audio(path))
        else:
            if not dev.paired:
                await self.backend.pair(path)
            now = self.find(path)
            if now and not now.trusted:
                await self.backend.trust(path)
            await self._retry(lambda: self.backend.connect(path))

    async def _connect_audio(self, path):
        """Headphones are reached by their A2DP service: that connection is always classic
        Bluetooth, whatever BlueZ learnt of the device over LE, and it pairs new ones on the way."""
        try:
            await self.backend.connect(path, A2DP_SINK)
        except Exception as e:
            # Headphones that connected by themselves leave BlueZ nothing to connect.
            if not any(word in str(e) for word in ("unavailable", "NotAvailable", "InProgress", "busy")):
                raise
            if not await self._stream(path, self.STREAM_WAIT):
                raise
            return
        if not await self._stream(path, 0):
            raise ConnectionError("the link went away as soon as it was up")

    async def _stream(self, path, wait):
        deadline = asyncio.get_running_loop().time() + wait
        while True:
            dev = self.find(path)
            if dev and dev.ready:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.1)

    async def _retry(self, connect):
        for attempt in range(self.ATTEMPTS):
            try:
                await connect()
                return
            except Exception as e:
                # A device that does not answer is not there; asking again only keeps the user waiting.
                gone = any(word in str(e) for word in ("page-timeout", "Host is down"))
                if gone or attempt == self.ATTEMPTS - 1:
                    raise
                log.info("retrying after: %s", e)
                await asyncio.sleep(self.RETRY_DELAY)

    async def forget(self, path):
        dev = self.find(path)
        if not dev:
            return
        await self.backend.remove(path)
        self._said.pop(path, None)
        self.settings.forget_bluetooth(dev.address)
        self.events.toast(self.t("forgot_device"), "bluetooth")
