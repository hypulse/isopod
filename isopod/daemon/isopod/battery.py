import asyncio
import glob
import logging
import os

from . import uevents
from .util import spawn

log = logging.getLogger("isopod.battery")

SMOOTHED = "/tmp/battery.percent"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


class Battery:
    """Percent from batteryplus when present (the PMIC gauge is uncalibrated on mainline)."""

    INTERVAL = 30
    WARN_AT = (20, 10, 5)

    def __init__(self, state, notify=None, sim=False, root="/sys/class/power_supply"):
        self.state = state
        self.notify = notify or (lambda percent: None)
        self.sim = sim
        self.root = root
        self._warned = 101

    async def start(self):
        if self.sim:
            self.state.update("battery", percent=78, charging=False)
            return
        self.refresh()
        uevents.watch("power_supply", lambda event: self.refresh())
        spawn(self._poll())

    async def _poll(self):
        while True:
            await asyncio.sleep(self.INTERVAL)
            self.refresh()

    def refresh(self):
        battery = next((d for d in sorted(glob.glob(os.path.join(self.root, "*")))
                        if _read(os.path.join(d, "type")) == "Battery"), None)
        if not battery:
            return
        percent = _read(SMOOTHED) or _read(os.path.join(battery, "capacity")) or "0"
        status = _read(os.path.join(battery, "status")) or ""
        try:
            value = max(0, min(100, int(float(percent))))
        except ValueError:
            value = 0
        charging = status in ("Charging", "Full")
        self.state.update("battery", percent=value, charging=charging)
        self._warn(value, charging)

    def simulate(self, percent, charging=False):
        self.state.update("battery", percent=percent, charging=charging)
        self._warn(percent, charging)

    def _warn(self, percent, charging):
        """Say so once as the charge crosses each warning level on the way down."""
        if charging or percent > self._warned + 3:
            self._warned = 101
            return
        level = min((w for w in self.WARN_AT if percent <= w), default=None)
        if level is not None and level < self._warned:
            self._warned = level
            self.notify(percent)
