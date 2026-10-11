import re

STAMP = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
TAG = re.compile(r"^\[[a-z]+:.*\]$", re.I)
OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\]$", re.I)
WORD_STAMP = re.compile(r"<\d+:\d+(?:[.:]\d+)?>")


def decode(data):
    """Lyrics files are UTF-8 or, from Windows tools, UTF-16 with a byte order mark."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def parse_lrc(text):
    """LRC or plain text -> (lines, times). times is None for lyrics without timing."""
    timed, plain, offset = [], [], 0.0
    for raw in (text or "").splitlines():
        line = raw.strip()
        m = OFFSET.match(line)
        if m:
            offset = int(m.group(1)) / 1000.0
            continue
        stamps = STAMP.findall(line)
        if stamps:
            words = WORD_STAMP.sub("", STAMP.sub("", line)).strip()
            for minutes, seconds in stamps:
                timed.append((int(minutes) * 60 + float(seconds.replace(":", ".")), words))
        elif not TAG.match(line):
            plain.append(line)
    if timed:
        timed.sort(key=lambda t: t[0])
        while timed and not timed[0][1]:
            timed.pop(0)
        return [w for _, w in timed], [max(0.0, t - offset) for t, _ in timed]
    while plain and not plain[0]:
        plain.pop(0)
    while plain and not plain[-1]:
        plain.pop()
    return (plain, None) if plain else None
