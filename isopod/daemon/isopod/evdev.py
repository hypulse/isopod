import fcntl
import glob
import os
import struct

EV_KEY = 0x01
EV_SW = 0x05
KEY_MAX = 0x2FF
SW_MAX = 0x10

EVENT = struct.Struct("llHHi")


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("E") << 8) | nr


def _eviocgname(size):
    return _ioc(2, 0x06, size)


def _eviocgbit(ev, size):
    return _ioc(2, 0x20 + ev, size)


def _eviocgsw(size):
    return _ioc(2, 0x1B, size)


EVIOCGRAB = _ioc(1, 0x90, 4)


def _bits(buf, maximum):
    return {i for i in range(maximum + 1) if buf[i // 8] >> (i % 8) & 1}


class Device:
    def __init__(self, path):
        self.path = path
        self.roles = set()
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            buf = bytearray(256)
            fcntl.ioctl(self.fd, _eviocgname(len(buf)), buf)
            self.name = buf.split(b"\0", 1)[0].decode(errors="replace")
            self.keys = self._capabilities(EV_KEY, KEY_MAX)
            self.switches = self._capabilities(EV_SW, SW_MAX)
        except OSError:
            os.close(self.fd)
            raise

    def _capabilities(self, ev, maximum):
        buf = bytearray(maximum // 8 + 1)
        try:
            fcntl.ioctl(self.fd, _eviocgbit(ev, len(buf)), buf)
        except OSError:
            return set()
        return _bits(buf, maximum)

    def switch_state(self, code):
        buf = bytearray(SW_MAX // 8 + 1)
        try:
            fcntl.ioctl(self.fd, _eviocgsw(len(buf)), buf)
        except OSError:
            return None
        return code in _bits(buf, SW_MAX)

    def grab(self, on):
        try:
            fcntl.ioctl(self.fd, EVIOCGRAB, 1 if on else 0)
            return True
        except OSError:
            return False

    def read(self):
        try:
            data = os.read(self.fd, EVENT.size * 64)
        except (BlockingIOError, InterruptedError):
            return []
        usable = len(data) - len(data) % EVENT.size
        return [EVENT.unpack_from(data, i)[2:] for i in range(0, usable, EVENT.size)]

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


def event_nodes():
    return sorted(glob.glob("/dev/input/event*"))
