import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import shutil
from urllib.parse import unquote, urlsplit

from .system import _local_ip
from .util import spawn

log = logging.getLogger("isopod.transfer")

AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".aif", ".aiff",
              ".wma", ".ape", ".wv", ".mpc", ".dsf", ".dff", ".jpg", ".jpeg", ".png")
SUBTITLE_EXTS = (".srt", ".smi", ".sami", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup")
MAX_HEADER = 16 << 10
CHUNK = 1 << 20
MAX_FAILURES = 10


def safe_parts(path):
    """Path components a client may write to: no hidden names, no climbing out."""
    parts = [p for p in unquote(path).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") or "\0" in p for p in parts):
        return None
    return parts


class Transfer:
    """While Settings > Add Files over Wi-Fi is open, a browser on the same network can
    drop files onto the device. Each file lands in music, videos or books by its type."""

    def __init__(self, app, video_exts, book_exts, ports=(80, 8080)):
        self.app = app
        self.ports = ports
        self.kinds = [("videos", tuple(video_exts) + SUBTITLE_EXTS), ("books", tuple(book_exts)),
                      ("music", AUDIO_EXTS)]
        self.server = None
        self.port = None
        self.code = ""
        self.received = 0
        self.failures = 0
        self._rescan = None

    @property
    def running(self):
        return self.server is not None

    def url(self):
        ip = _local_ip()
        if not ip:
            return None
        return f"http://{ip}" + ("" if self.port == 80 else f":{self.port}")

    async def start(self):
        if self.server:
            return True
        self.code = f"{secrets.randbelow(10000):04d}"
        self.received = self.failures = 0
        for port in self.ports:
            try:
                self.server = await asyncio.start_server(self._client, host="0.0.0.0", port=port,
                                                         limit=MAX_HEADER)
                self.port = self.server.sockets[0].getsockname()[1]
                log.info("transfer open on port %d", port)
                return True
            except OSError as e:
                log.info("transfer: port %d unavailable: %s", port, e)
        return False

    def stop(self):
        if self.server:
            self.server.close()
            self.server = None
            log.info("transfer closed")

    def _kind(self, name):
        lower = name.lower()
        return next((kind for kind, exts in self.kinds if lower.endswith(exts)), None)

    async def _client(self, reader, writer):
        try:
            await self._serve(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        except Exception:
            log.exception("transfer request failed")
        finally:
            writer.close()

    async def _serve(self, reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        headers = {k.strip().lower(): v.strip() for k, v in
                   (line.split(":", 1) for line in lines[1:] if ":" in line)}
        path = urlsplit(target).path
        if method == "GET" and path == "/":
            await self._send(writer, 200, self._page(), "text/html; charset=utf-8")
        elif method == "PUT" and path.startswith("/upload/"):
            status, body = await self._upload(reader, headers, path[len("/upload/"):])
            await self._send(writer, status, json.dumps(body).encode(), "application/json")
        else:
            await self._send(writer, 404, b"{}", "application/json")

    async def _send(self, writer, status, body, ctype):
        reason = {200: "OK", 201: "Created", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                  411: "Length Required", 415: "Unsupported Media Type", 507: "Insufficient Storage"}
        writer.write(f"HTTP/1.1 {status} {reason.get(status, '')}\r\nContent-Type: {ctype}\r\n"
                     f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                     "Connection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _upload(self, reader, headers, rel):
        if not hmac.compare_digest(headers.get("x-isopod-code", ""), self.code):
            self.failures += 1
            if self.failures >= MAX_FAILURES:
                self.code = f"{secrets.randbelow(10000):04d}"
                self.failures = 0
                self.app.page_changed("/settings/transfer")
            return 403, {"error": "code"}
        parts = safe_parts(rel)
        if not parts:
            return 400, {"error": "name"}
        kind = self._kind(parts[-1])
        if not kind:
            return 415, {"error": "type"}
        try:
            size = int(headers["content-length"])
        except (KeyError, ValueError):
            return 411, {"error": "length"}
        root = os.path.realpath(self.app.paths[kind])
        dest = os.path.join(root, *parts)
        if os.path.commonpath([root, os.path.realpath(os.path.dirname(dest))]) != root:
            return 400, {"error": "name"}
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if shutil.disk_usage(root).free < size + (64 << 20):
            return 507, {"error": "space"}
        part = dest + ".part"
        try:
            with open(part, "wb") as f:
                left = size
                while left:
                    chunk = await reader.read(min(CHUNK, left))
                    if not chunk:
                        raise ConnectionError("client went away")
                    f.write(chunk)
                    left -= len(chunk)
                    self.app.power.activity()
            os.replace(part, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(part)
            raise
        self.received += 1
        self._done(kind)
        return 201, {"kind": kind, "path": "/".join(parts)}

    def _done(self, kind):
        self.app.page_changed("/settings/transfer", f"/{kind}/*")
        if kind == "music":
            if self._rescan:
                self._rescan.cancel()
            self._rescan = asyncio.get_running_loop().call_later(
                2, lambda: spawn(self.app.music.update_library()))

    def _page(self):
        t = self.app.t
        strings = {k: t("web_" + k) for k in ("title", "code", "drop", "choose", "done", "wrong_code",
                                              "unsupported", "no_space", "failed", "hint")}
        with open(os.path.join(os.path.dirname(__file__), "transfer.html"), encoding="utf-8") as f:
            html = f.read()
        return html.replace("__STRINGS__", json.dumps(strings, ensure_ascii=False)) \
                   .replace("__LANG__", t.lang).encode()
