import fcntl
import logging
import os
import struct

from .evdev import EV_KEY, EVENT

log = logging.getLogger("isopod.uinput")

NAME = "isoPod keys"
EV_SYN, SYN_REPORT = 0x00, 0

UI_SET_EVBIT = (1 << 30) | (4 << 16) | (ord("U") << 8) | 100
UI_SET_KEYBIT = (1 << 30) | (4 << 16) | (ord("U") << 8) | 101
UI_DEV_SETUP = (1 << 30) | (92 << 16) | (ord("U") << 8) | 3
UI_DEV_CREATE = (ord("U") << 8) | 1
UI_DEV_DESTROY = (ord("U") << 8) | 2
BUS_VIRTUAL = 0x06

KEYS = {
    "esc": 1, "minus": 12, "equal": 13, "backspace": 14, "tab": 15, "leftbrace": 26, "rightbrace": 27,
    "enter": 28, "ctrl": 29, "semicolon": 39, "apostrophe": 40, "grave": 41, "shift": 42, "backslash": 43,
    "comma": 51, "dot": 52, "slash": 53, "alt": 56, "space": 57, "f11": 87, "f12": 88, "home": 102,
    "up": 103, "pageup": 104, "left": 105, "right": 106, "end": 107, "down": 108, "pagedown": 109,
    "insert": 110, "delete": 111, "meta": 125,
}
KEYS.update({str(n): 2 + (n - 1) % 10 for n in range(10)})
KEYS.update({f"f{n}": 58 + n for n in range(1, 11)})
KEYS.update(zip("qwertyuiop", range(16, 26)))
KEYS.update(zip("asdfghjkl", range(30, 39)))
KEYS.update(zip("zxcvbnm", range(44, 51)))
# The power key, the volume and the media keys stay out: whatever watches for those would
# take a press of ours for the real one. Keys 1 to 31 all present make udev call it a keyboard.
DECLARED = sorted(set(range(1, 113)) | {125})


def combo(text):
    """'ctrl+l' as the codes to press, in order; None when a name is unknown."""
    names = [n.strip().lower() for n in str(text).split("+")]
    codes = [KEYS.get(n) for n in names]
    return codes if names and None not in codes else None


class Keyboard:
    """A keyboard of isopodd's own, for apps that know no gamepad. Made on first use."""

    def __init__(self, path="/dev/uinput"):
        self.path = path
        self.fd = None
        self.held = {}

    def _open(self):
        if self.fd is not None:
            return True
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except OSError as e:
            log.warning("no keyboard for apps: %s", e)
            return False
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
            for code in DECLARED:
                fcntl.ioctl(fd, UI_SET_KEYBIT, code)
            fcntl.ioctl(fd, UI_DEV_SETUP, struct.pack("HHHH80sI", BUS_VIRTUAL, 0, 0, 1,
                                                      NAME.encode(), 0))
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError as e:
            log.warning("no keyboard for apps: %s", e)
            os.close(fd)
            return False
        self.fd = fd
        return True

    def _emit(self, code, value):
        os.write(self.fd, EVENT.pack(0, 0, EV_KEY, code, value) + EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))

    def press(self, button, codes):
        if not self._open():
            return
        self.release(button)
        try:
            for code in codes:
                self._emit(code, 1)
        except OSError as e:
            log.warning("key press lost: %s", e)
            return
        self.held[button] = codes

    def release(self, button):
        codes = self.held.pop(button, None)
        if not codes or self.fd is None:
            return
        try:
            for code in reversed(codes):
                self._emit(code, 0)
        except OSError as e:
            log.warning("key release lost: %s", e)

    def release_all(self):
        for button in list(self.held):
            self.release(button)
