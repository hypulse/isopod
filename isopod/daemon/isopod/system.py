import os
import shutil
import socket

from . import __version__
from .util import run

FUNCTIONS = "/etc/profile.d/001-functions"


class System:
    """Keeps ROCKNIX's own settings in step so its suspend hooks do the right thing."""

    def __init__(self, sim=False):
        self.sim = sim

    async def rocknix_setting(self, key, value):
        if self.sim or not os.path.exists(FUNCTIONS):
            return
        await run("sh", "-c", f'. {FUNCTIONS}; set_setting "$0" "$1"', key, str(value))

    async def set_bluetooth(self, on):
        if self.sim:
            return
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
