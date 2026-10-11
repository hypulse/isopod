import os

from .util import load_json, save_json

DEFAULTS = {
    "lang": "ko",
    "brightness": 70,
    "screen_timeout": 60,
    "sleep_timeout": 600,
    "lid_keep_playing": True,
    "volume_limit": 100,
    "volumes": {},
    "pause_on_disconnect": True,
    "pause_on_unplug": True,
    "bluetooth": False,
}


class Settings:
    def __init__(self, config_dir):
        self.path = os.path.join(config_dir, "settings.json")
        stored = load_json(self.path, {})
        self.data = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}

    def __getitem__(self, key):
        return self.data[key]

    def set(self, key, value):
        if self.data.get(key) == value:
            return
        self.data[key] = value
        save_json(self.path, self.data)
