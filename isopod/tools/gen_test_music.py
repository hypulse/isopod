#!/usr/bin/env python3
"""Create a small fictional music library (tagged MP3s with cover art) for the simulator."""
import math
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = os.environ.get("ISOPOD_BOLD_FONT", "/usr/share/fonts/pretendard/Pretendard-Bold.otf")

ALBUMS = [
    ("Aurora Lane", "Night Drive", 2024, "Synthpop", ((24, 18, 76), (214, 58, 140)), "sun",
     [("Neon Coast", 228), ("Afterglow", 242), ("Mile Marker", 211), ("Static Hearts", 255), ("Last Exit", 236)]),
    ("Aurora Lane", "Paper Planes", 2021, "Indie Pop", ((252, 228, 190), (146, 196, 232)), "plane",
     [("Paper Planes", 202), ("Weekend Light", 225), ("Tidal", 248)]),
    ("서울 야경", "한강의 밤", 2023, "K-Indie", ((12, 20, 48), (58, 72, 140)), "city",
     [("한강의 밤", 251), ("첫차", 219), ("골목길", 232), ("새벽 세 시", 267)]),
    ("The Midnight Owls", "Quiet Hours", 2019, "Jazz", ((10, 52, 58), (22, 110, 104)), "moon",
     [("Blue Room", 312), ("Late Train", 287), ("Candlelight", 330), ("Soft Rain", 298)]),
    ("Nova Kim", "Orbit", 2025, "K-Pop", ((8, 8, 12), (70, 40, 110)), "ring",
     [("Orbit", 197), ("Gravity", 185), ("Satellite", 208)]),
    ("Harbor Lights", "Salt & Stone", 2022, "Folk", ((236, 222, 196), (96, 150, 170)), "waves",
     [("Lighthouse", 238), ("Harbor Song", 260), ("Driftwood", 224)]),
    ("윤슬", "물결", 2024, "Ballad", ((180, 214, 240), (40, 96, 176)), "waves",
     [("물결", 245), ("너에게", 230)]),
]


def cover(path, colors, motif, title, artist):
    size = 600
    img = Image.new("RGB", (size, size))
    px = img.load()
    (r0, g0, b0), (r1, g1, b1) = colors
    for y in range(size):
        t = y / (size - 1)
        c = (int(r0 + (r1 - r0) * t), int(g0 + (g1 - g0) * t), int(b0 + (b1 - b0) * t))
        for x in range(size):
            px[x, y] = c
    d = ImageDraw.Draw(img, "RGBA")
    if motif == "sun":
        for i in range(9):
            d.rectangle([150, 250 + i * 22, 450, 258 + i * 22], fill=(255, 200, 80, 200 - i * 18))
        d.ellipse([170, 110, 430, 370], fill=(255, 190, 90, 230))
        for i in range(6):
            d.rectangle([160, 250 + i * 26, 440, 262 + i * 26], fill=(colors[1] + (255,)))
    elif motif == "plane":
        d.polygon([(120, 360), (470, 180), (260, 420)], fill=(255, 255, 255, 235))
        d.polygon([(260, 420), (470, 180), (300, 470)], fill=(210, 220, 235, 235))
    elif motif == "city":
        import random
        random.seed(7)
        x = 0
        while x < size:
            w = random.randint(30, 70)
            h = random.randint(120, 330)
            d.rectangle([x, size - h, x + w, size], fill=(8, 10, 24, 255))
            for wy in range(size - h + 12, size - 10, 22):
                for wx in range(x + 8, x + w - 8, 16):
                    if random.random() < 0.35:
                        d.rectangle([wx, wy, wx + 6, wy + 9], fill=(255, 214, 120, 220))
            x += w + 4
        d.ellipse([420, 60, 500, 140], fill=(250, 245, 220, 240))
    elif motif == "moon":
        d.ellipse([190, 120, 410, 340], fill=(236, 228, 200, 245))
        d.ellipse([240, 100, 460, 320], fill=colors[0] + (255,))
    elif motif == "ring":
        d.ellipse([200, 200, 400, 400], fill=(190, 150, 255, 255))
        d.arc([90, 250, 510, 350], 200, 340, fill=(255, 255, 255, 230), width=10)
        d.arc([90, 250, 510, 350], 20, 160, fill=(255, 255, 255, 230), width=10)
        for i in range(40):
            a = i * 2.39996
            r = 280 + (i * 37) % 20
            d.ellipse([300 + r * math.cos(a) - 2, 300 + r * math.sin(a) - 2,
                       300 + r * math.cos(a) + 2, 300 + r * math.sin(a) + 2], fill=(255, 255, 255, 160))
    elif motif == "waves":
        for i in range(7):
            y = 280 + i * 42
            pts = [(x, y + 18 * math.sin(x / 55 + i)) for x in range(0, size + 20, 20)]
            d.line(pts, fill=(255, 255, 255, 170 - i * 18), width=8)
    font = ImageFont.truetype(FONT, 46)
    small = ImageFont.truetype(FONT, 28)
    d.text((40, 36), title, font=font, fill=(255, 255, 255, 235))
    d.text((42, 92), artist, font=small, fill=(255, 255, 255, 200))
    img.save(path, quality=92)


# Lyrics written for these fictional songs: a synced .lrc and a plain .txt beside the files.
LYRICS = {
    ("Aurora Lane", "Night Drive", 1, "Neon Coast"): [
        (0, ""), (6, "City lights are folding into the sea"), (12, "Radio low, just the road and me"),
        (18, "Every mile marker counting down"), (24, "The neon coast is the only town"),
        (31, "Hold the wheel, hold the night"), (37, "We run on borrowed starlight"),
        (44, "Neon coast, neon coast"), (50, "Take me where the signal's lost"),
    ],
    ("서울 야경", "한강의 밤", 1, "한강의 밤"): [
        (0, ""), (5, "강물 위로 번지는 불빛"), (11, "다리 아래 머무는 바람"), (17, "오늘 하루도 여기까지"),
        (23, "천천히 걸어도 괜찮아"), (30, "한강의 밤 너와 나"), (36, "말없이 건너는 다리"),
    ],
}
PLAIN_LYRICS = {
    ("The Midnight Owls", "Quiet Hours", 2, "Late Train"): (
        "The last train leaves at a quarter past one\n"
        "The station clock forgot the sun\n\n"
        "A folded ticket, a borrowed coat\n"
        "A melody I never wrote\n\n"
        "Ride the late train home\n"
        "Ride it on your own\n"
    ),
}


def write_lyrics(folder, artist, album, n, title):
    base = os.path.join(folder, f"{n:02d} {title}")
    synced = LYRICS.get((artist, album, n, title))
    if synced:
        with open(base + ".lrc", "w", encoding="utf-8") as f:
            f.write(f"[ar:{artist}]\n[ti:{title}]\n")
            for t, line in synced:
                f.write(f"[{t // 60:02d}:{t % 60:02d}.00]{line}\n")
    plain = PLAIN_LYRICS.get((artist, album, n, title))
    if plain:
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(plain)


def main(root):
    music = os.path.join(root, "music")
    os.makedirs(music, exist_ok=True)
    for artist, album, year, genre, colors, motif, tracks in ALBUMS:
        folder = os.path.join(music, artist, album)
        os.makedirs(folder, exist_ok=True)
        art = os.path.join(folder, "cover.jpg")
        cover(art, colors, motif, album, artist)
        for n, (title, seconds) in enumerate(tracks, 1):
            write_lyrics(folder, artist, album, n, title)
            out = os.path.join(folder, f"{n:02d} {title}.mp3")
            if os.path.exists(out):
                continue
            subprocess.run([
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "lavfi", "-t", str(seconds), "-i", "anullsrc=r=22050:cl=mono",
                "-i", art, "-map", "0:a", "-map", "1:v",
                "-c:a", "libmp3lame", "-b:a", "16k", "-c:v", "copy", "-disposition:v", "attached_pic",
                "-id3v2_version", "3",
                "-metadata", f"title={title}", "-metadata", f"artist={artist}",
                "-metadata", f"album_artist={artist}", "-metadata", f"album={album}",
                "-metadata", f"date={year}", "-metadata", f"genre={genre}",
                "-metadata", f"track={n}/{len(tracks)}", out,
            ], check=True)
    playlists = os.path.join(root, "playlists")
    os.makedirs(playlists, exist_ok=True)
    with open(os.path.join(playlists, "드라이브.m3u"), "w") as f:
        f.write("Aurora Lane/Night Drive/01 Neon Coast.mp3\n")
        f.write("Nova Kim/Orbit/01 Orbit.mp3\n")
        f.write("서울 야경/한강의 밤/02 첫차.mp3\n")
    with open(os.path.join(playlists, "Late Night Jazz.m3u"), "w") as f:
        f.write("The Midnight Owls/Quiet Hours/01 Blue Room.mp3\n")
        f.write("The Midnight Owls/Quiet Hours/04 Soft Rain.mp3\n")
    videos = os.path.join(root, "videos")
    os.makedirs(os.path.join(videos, "Concerts"), exist_ok=True)
    for name in ("Big Buck Bunny.mp4", "Sintel.mkv", "Tears of Steel.mp4", "Concerts/Live at Han River.mp4"):
        open(os.path.join(videos, name), "a").close()
    books = os.path.join(root, "books")
    for d in ("Classics", "데미안.sdr"):
        os.makedirs(os.path.join(books, d), exist_ok=True)
    for name in ("데미안.epub", "The Little Prince.pdf", "어린 왕자.epub", "Field Notes.cbz",
                 "Classics/Pride and Prejudice.epub", "Classics/Moby-Dick.fb2.zip"):
        open(os.path.join(books, name), "a").close()
    ports = os.path.join(root, "ports")
    os.makedirs(ports, exist_ok=True)
    for name in ("Cave Story.sh", "Celeste Classic.sh", "PortMaster.sh"):
        open(os.path.join(ports, name), "a").close()


if __name__ == "__main__":
    main(sys.argv[1])
