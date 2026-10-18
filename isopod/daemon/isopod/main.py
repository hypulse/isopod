import argparse
import asyncio
import logging
import os
import signal

from .app import App


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="isopodd", description="isoPod system daemon")
    p.add_argument("--sim", action="store_true", help="simulate hardware (development)")
    p.add_argument("--socket", default="/run/isopod/isopodd.sock")
    p.add_argument("--config", default="/storage/.config/isopod")
    p.add_argument("--cache", default="/storage/.cache/isopod")
    p.add_argument("--music", default="/storage/music")
    p.add_argument("--videos", default="/storage/videos")
    p.add_argument("--books", default="/storage/books")
    p.add_argument("--podcasts", default="/storage/podcasts")
    p.add_argument("--audiobooks", default="/storage/audiobooks")
    p.add_argument("--data", default="/usr/share/isopod")
    p.add_argument("--mpd", default=os.environ.get("MPD_HOST", "/run/mpd/socket"))
    p.add_argument("--mpd-port", type=int, default=6600)
    p.add_argument("--apps", choices=("systemd", "direct", "sim"),
                   help="how apps run: systemd units (device), process groups (desktop), or not at all")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, loop.stop)
    app = App(args)
    task = loop.create_task(app.run())
    try:
        loop.run_forever()
    finally:
        app.close()
        task.cancel()
        loop.close()


if __name__ == "__main__":
    main()
