import asyncio
import json
import logging
import os

from .util import spawn

log = logging.getLogger("isopod.ipc")


class Client:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    def send(self, message):
        if self.writer.is_closing():
            return
        self.writer.write(json.dumps(message, ensure_ascii=False).encode() + b"\n")


class Server:
    """Newline-delimited JSON over a UNIX socket. Requests carry an id; events do not."""

    def __init__(self, path, handler, on_connect=None):
        self.path = path
        self.handler = handler
        self.on_connect = on_connect
        self.clients = set()
        self._server = None

    async def start(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._serve, path=self.path, limit=1 << 22)
        os.chmod(self.path, 0o666)

    def broadcast(self, event):
        for client in list(self.clients):
            client.send(event)

    async def _serve(self, reader, writer):
        client = Client(reader, writer)
        self.clients.add(client)
        if self.on_connect:
            self.on_connect(client)
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                except ValueError:
                    continue
                spawn(self._dispatch(client, request))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.clients.discard(client)
            writer.close()

    async def _dispatch(self, client, request):
        rid = request.get("id")
        try:
            result = await self.handler(client, request)
            reply = {"ok": True, **(result or {})}
        except Exception as e:
            log.exception("request failed: %s", request.get("op"))
            reply = {"ok": False, "error": str(e)}
        if rid is not None:
            reply["id"] = rid
            client.send(reply)
