import asyncio
import copy


class State:
    """Shared snapshot the shell renders from; changes are coalesced into one broadcast."""

    DEBOUNCE = 0.03

    def __init__(self, on_change):
        self._on_change = on_change
        self._pending = None
        self.rev = 0
        self.adjusters = []
        self.data = {
            "player": {"state": "stop", "kind": "song", "title": "", "artist": "", "album": "",
                       "elapsed": 0.0, "duration": 0.0, "pos": 0, "count": 0, "art": None,
                       "shuffle": False, "repeat": False, "single": False, "buffering": False},
            "volume": {"level": 0, "muted": False, "output": "speaker", "name": ""},
            "battery": {"percent": 100, "charging": False},
            "bluetooth": {"enabled": False, "audio": None},
            "wifi": {"enabled": False, "ssid": None, "signal": 0},
            "screen": {"on": True},
            "apps": [],
        }

    def get(self, section):
        return self.data[section]

    def update(self, section, **fields):
        current = self.data[section]
        if all(current.get(k) == v for k, v in fields.items()):
            return
        current.update(fields)
        self._schedule()

    def replace(self, section, value):
        if self.data[section] == value:
            return
        self.data[section] = value
        self._schedule()

    def snapshot(self):
        snap = {"rev": self.rev, **copy.deepcopy(self.data)}
        for adjust in self.adjusters:
            adjust(snap)
        return snap

    def _schedule(self):
        if self._pending is None:
            self._pending = asyncio.get_running_loop().call_later(self.DEBOUNCE, self._flush)

    def _flush(self):
        self._pending = None
        self.rev += 1
        self._on_change(self.snapshot())
