import asyncio
import json
import logging
import os
import re
import tempfile
import unicodedata
from urllib.parse import quote, unquote

log = logging.getLogger("isopod")


async def run(*args, timeout=10.0, env=None, stdin=None):
    """Run a command and return (returncode, stdout, stderr); never raises on failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as e:
        return 127, "", str(e)
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timeout"
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")


_tasks = set()


def spawn(coro):
    """Start a background task and log (instead of swallowing) its exceptions. The loop
    holds tasks only weakly, so a reference stays here until the task is done; without
    it a task parked on a socket can be collected mid-flight."""
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    task.add_done_callback(_log_task_error)
    return task


def _log_task_error(task):
    if not task.cancelled() and task.exception():
        log.error("background task failed", exc_info=task.exception())


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def sort_key(text):
    """iPod-style sort: case-insensitive, ignoring a leading English article."""
    text = unicodedata.normalize("NFC", text or "").strip()
    return _ARTICLE.sub("", text).casefold()


CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"


def _initial(ch):
    return CHOSEONG[(ord(ch) - 0xAC00) // 588] if "가" <= ch <= "힣" else ch


def index_letter(text):
    key = sort_key(text)
    if not key:
        return "#"
    ch = key[0]
    if "a" <= ch <= "z":
        return ch.upper()
    if "가" <= ch <= "힣":
        return _initial(ch)
    return "#"


def matches(query, text):
    """Substring match; a query of bare Hangul initials (ㅎㄱ) matches their syllables (한강)."""
    q = unicodedata.normalize("NFC", query).casefold().strip()
    t = unicodedata.normalize("NFC", text or "").casefold()
    if q in t:
        return True
    bare = q.replace(" ", "")
    return bool(bare) and all(c in CHOSEONG for c in bare) and bare in "".join(map(_initial, t.replace(" ", "")))


def enc(*parts):
    return "/".join(quote(str(p), safe="") for p in parts)


def split_path(path):
    return [unquote(p) for p in path.strip("/").split("/") if p]


def fmt_duration(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
