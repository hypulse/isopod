import asyncio
import logging

log = logging.getLogger("isopod.mpd")


class MPDError(Exception):
    pass


def quote(arg):
    arg = str(arg)
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def filter_expr(**tags):
    """Build an MPD filter such as ((albumartist == "A") AND (album == "B"))."""
    parts = [f'({tag} == {quote(value)})' for tag, value in tags.items()]
    return parts[0] if len(parts) == 1 else "(" + " AND ".join(parts) + ")"


def records(pairs, start_keys=("file",)):
    """Group key/value pairs into dicts, starting a new record at any start key."""
    out, cur = [], None
    for key, value in pairs:
        k = key.lower()
        if k in start_keys:
            cur = {k: value}
            out.append(cur)
        elif cur is not None and k not in cur:
            cur[k] = value
    return out


def values(pairs, key):
    key = key.lower()
    return [v for k, v in pairs if k.lower() == key]


class Connection:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None

    @property
    def connected(self):
        return self.writer is not None and not self.writer.is_closing()

    async def connect(self):
        if self.host.startswith("/"):
            self.reader, self.writer = await asyncio.open_unix_connection(self.host, limit=1 << 22)
        else:
            self.reader, self.writer = await asyncio.open_connection(self.host, self.port, limit=1 << 22)
        greeting = await self.reader.readline()
        if not greeting.startswith(b"OK MPD"):
            self.close()
            raise MPDError("unexpected greeting")

    def close(self):
        if self.writer is not None:
            self.writer.close()
        self.reader = self.writer = None

    async def _send(self, line):
        self.writer.write(line.encode() + b"\n")
        await self.writer.drain()

    async def _read_pairs(self):
        pairs = []
        while True:
            line = await self.reader.readline()
            if not line:
                raise ConnectionError("mpd closed the connection")
            line = line.decode(errors="replace").rstrip("\n")
            if line == "OK" or line == "list_OK":
                if line == "OK":
                    return pairs
                continue
            if line.startswith("ACK "):
                raise MPDError(line)
            key, _, value = line.partition(": ")
            pairs.append((key, value))

    async def command(self, name, *args):
        await self._send(" ".join([name, *map(quote, args)]))
        return await self._read_pairs()

    async def command_list(self, commands):
        lines = ["command_list_begin"]
        lines += [" ".join([c[0], *map(quote, c[1:])]) for c in commands]
        lines.append("command_list_end")
        await self._send("\n".join(lines))
        return await self._read_pairs()

    async def binary(self, name, uri, offset):
        await self._send(" ".join([name, quote(uri), str(offset)]))
        meta, data = {}, b""
        while True:
            line = await self.reader.readline()
            if not line:
                raise ConnectionError("mpd closed the connection")
            text = line.decode(errors="replace").rstrip("\n")
            if text == "OK":
                return meta, data
            if text.startswith("ACK "):
                raise MPDError(text)
            key, _, value = text.partition(": ")
            if key == "binary":
                size = int(value)
                data = await self.reader.readexactly(size)
                await self.reader.readexactly(1)
            else:
                meta[key] = value


class MPD:
    """One connection for commands (serialized) and one blocked in `idle` for change events."""

    def __init__(self, host="127.0.0.1", port=6600):
        self.host = host
        self.port = port
        self._conn = Connection(host, port)
        self._lock = asyncio.Lock()

    async def _with_connection(self, fn):
        async with self._lock:
            for attempt in range(2):
                try:
                    if not self._conn.connected:
                        await self._conn.connect()
                    return await fn(self._conn)
                except (OSError, ConnectionError, asyncio.IncompleteReadError) as e:
                    self._conn.close()
                    if attempt:
                        raise MPDError(str(e)) from e

    def close(self):
        self._conn.close()

    async def call(self, name, *args):
        return await self._with_connection(lambda c: c.command(name, *args))

    async def call_list(self, commands):
        if not commands:
            return []
        return await self._with_connection(lambda c: c.command_list(commands))

    async def picture(self, uri, limit=4 << 20):
        """Embedded picture first, then cover.* next to the file. Returns (bytes, mime) or None."""
        for cmd in ("readpicture", "albumart"):
            try:
                data, mime, offset = b"", None, 0
                while True:
                    meta, chunk = await self._with_connection(
                        lambda c, o=offset: c.binary(cmd, uri, o))
                    if not chunk:
                        break
                    mime = mime or meta.get("type")
                    data += chunk
                    offset += len(chunk)
                    if offset >= int(meta.get("size", 0)) or offset > limit:
                        break
                if data:
                    return data, mime or "image/jpeg"
            except MPDError:
                continue
        return None

    async def idle_forever(self, on_change):
        conn = Connection(self.host, self.port)
        delay = 0.5
        while True:
            try:
                if not conn.connected:
                    await conn.connect()
                    delay = 0.5
                    await on_change({"reconnect"})
                pairs = await conn.command("idle", "database", "update", "stored_playlist",
                                           "playlist", "player", "mixer", "options")
                await on_change(set(values(pairs, "changed")))
            except (OSError, ConnectionError, MPDError, asyncio.IncompleteReadError) as e:
                log.warning("mpd idle connection lost: %s", e)
                conn.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5)
