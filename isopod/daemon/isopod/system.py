import asyncio
import logging
import os
import shutil
import socket
import time

from . import __version__
from .util import run

log = logging.getLogger("isopod.system")

FUNCTIONS = "/etc/profile.d/001-functions"


class System:
    """Keeps ROCKNIX's own settings in step so its suspend hooks do the right thing."""

    def __init__(self, sim=False, sysfs="/sys"):
        self.sim = sim
        self.sysfs = sysfs

    async def rocknix_setting(self, key, value):
        if self.sim or not os.path.exists(FUNCTIONS):
            return
        await run("sh", "-c", f'. {FUNCTIONS}; set_setting "$0" "$1"', key, str(value))

    async def _rocknix_get(self, key):
        if self.sim or not os.path.exists(FUNCTIONS):
            return ""
        _, out, _ = await run("sh", "-c", f'. {FUNCTIONS}; get_setting "$0"', key)
        return out.strip()

    def computer_connected(self):
        """A computer has the USB port in use, as it has while it reads or copies files."""
        udc = os.path.join(self.sysfs, "class/udc")
        try:
            for name in os.listdir(udc):
                with open(os.path.join(udc, name, "state")) as f:
                    if f.read().strip() == "configured":
                        return True
        except OSError:
            pass
        return False

    async def bluetooth_wanted(self):
        """Whether Bluetooth was on when the device last went down."""
        return await self._rocknix_get("controllers.bluetooth.enabled") == "1"

    async def rocknix_volume(self):
        """The one volume ROCKNIX keeps, which its boot script sets on whatever plays."""
        try:
            return max(0, min(100, int(await self._rocknix_get("audio.volume"))))
        except ValueError:
            return None

    def has_bluetooth_adapter(self):
        try:
            return any(n.startswith("hci") for n in os.listdir(os.path.join(self.sysfs, "class/bluetooth")))
        except OSError:
            return False

    def _rebind_bluetooth_uart(self):
        drivers = os.path.join(self.sysfs, "bus/serial/drivers")
        try:
            names = [d for d in os.listdir(drivers) if d.startswith("hci_uart")]
        except OSError:
            return
        for driver in (os.path.join(drivers, n) for n in names):
            for device in os.listdir(driver):
                if device == "module" or not os.path.islink(os.path.join(driver, device)):
                    continue
                log.info("no Bluetooth adapter: binding %s to %s again", device, os.path.basename(driver))
                for step in ("unbind", "bind"):
                    try:
                        with open(os.path.join(driver, step), "w") as f:
                            f.write(device)
                    except OSError as e:
                        log.warning("%s %s: %s", step, device, e)

    async def wake_bluetooth(self, wait=4.0):
        """The RG35XX SP's RTL8821CS often does not answer when the kernel first calls it
        at boot, and then there is no adapter until its UART driver is bound again."""
        if self.sim or self.has_bluetooth_adapter():
            return
        await asyncio.get_running_loop().run_in_executor(None, self._rebind_bluetooth_uart)
        deadline = time.monotonic() + wait
        while not self.has_bluetooth_adapter() and time.monotonic() < deadline:
            await asyncio.sleep(0.2)

    async def set_bluetooth(self, on):
        if self.sim:
            return
        if on:
            await self.wake_bluetooth()
        if shutil.which("rocknix-bluetooth"):
            await run("rocknix-bluetooth", "enable" if on else "disable", timeout=20)
        else:
            await self.rocknix_setting("controllers.bluetooth.enabled", 1 if on else 0)

    def uptime(self):
        return _uptime()

    def about(self, music_dir):
        info = {"version": __version__, "model": "Anbernic RG35XX SP", "software": ""}
        try:
            with open("/sys/firmware/devicetree/base/model", "rb") as f:
                info["model"] = f.read().rstrip(b"\0").decode()
        except OSError:
            pass
        try:
            with open("/etc/os-release") as f:
                release = dict(line.rstrip().split("=", 1) for line in f if "=" in line)
            info["software"] = release.get("OS_VERSION", "").strip('"')
        except OSError:
            pass
        target = music_dir if os.path.exists(music_dir) else "/"
        usage = shutil.disk_usage(target)
        info["capacity"], info["available"] = usage.total, usage.free
        info["ip"] = _local_ip()
        return info


def _uptime():
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1000
