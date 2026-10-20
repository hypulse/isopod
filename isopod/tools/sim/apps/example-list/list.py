"""A list app: isoPod asks for a page or for what to play, one JSON line each way."""
import json
import sys

CHANNELS = {
    "walks": ("Morning Walks", ["Han River at Dawn", "Seoul Forest", "Namsan Steps"]),
    "cooking": ("Desk Cooking", ["Kimchi Fried Rice", "Egg Drop Soup"]),
}


def video(channel, n):
    return {"title": CHANNELS[channel][1][n], "subtitle": CHANNELS[channel][0], "play": f"{channel}/{n}"}


def page(path, query, lang):
    if path == "/":
        rows = [{"title": "Search" if lang == "en" else "검색", "search": "/search",
                 "prompt": "Title" if lang == "en" else "제목"},
                {"title": "Channels" if lang == "en" else "채널", "header": True}]
        rows += [{"title": name, "subtitle": f"{len(videos)} videos", "path": "/channel/" + key}
                 for key, (name, videos) in CHANNELS.items()]
        return {"items": rows}
    if path.startswith("/channel/"):
        key = path.split("/")[2]
        if key not in CHANNELS:
            return {"error": "No such channel"}
        return {"title": CHANNELS[key][0], "items": [video(key, n) for n in range(len(CHANNELS[key][1]))]}
    if path == "/search":
        found = [video(key, n) for key, (_, videos) in CHANNELS.items() for n, title in enumerate(videos)
                 if query.casefold() in title.casefold()]
        return {"items": found, "empty": "No results" if lang == "en" else "결과 없음"}
    return {"error": "No such page"}


for line in sys.stdin:
    ask = json.loads(line)
    if ask["op"] == "page":
        answer = page(ask["path"], ask.get("query", ""), ask["lang"])
    elif ask["op"] == "play":
        channel, n = ask["play"].split("/")
        answer = {"title": CHANNELS[channel][1][int(n)], "url": f"https://example.com/{channel}-{n}.mp4"}
    else:
        answer = {"error": "Unknown question"}
    print(json.dumps({"id": ask["id"], **answer}, ensure_ascii=False), flush=True)
