import asyncio
import socket

NETLINK_KOBJECT_UEVENT = 15
FROM_KERNEL = 1


def parse(message):
    """ACTION@DEVPATH and then KEY=VALUE fields, each ended by a NUL."""
    fields = message.decode(errors="replace").split("\0")[1:]
    return dict(f.split("=", 1) for f in fields if "=" in f)


def watch(subsystem, callback):
    """Hand each of a subsystem's hotplug events to callback, read from the kernel itself:
    pyudev's ctypes does not find libudev on ROCKNIX."""
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK,
                         NETLINK_KOBJECT_UEVENT)
    sock.bind((0, FROM_KERNEL))

    def read():
        while True:
            try:
                event = parse(sock.recv(16384))
            except OSError:
                return
            if event.get("SUBSYSTEM") == subsystem:
                callback(event)

    asyncio.get_running_loop().add_reader(sock, read)
    return sock
