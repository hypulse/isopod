#!/usr/bin/env python3
"""Drive a recording of isoPod with real apps. Buttons go to isopodd the way the gamepad's
would; keys for mpv and KOReader go through the compositor to the app in front. Captions
are burnt into a strip under the screen at the end.

usage: demo.py WORK_DIR OUT_DIR   (run by run.sh inside isopod-desk)
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

W, OUT = sys.argv[1], sys.argv[2]
FONTS = "/usr/share/fonts/pretendard"


class Isopodd:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(path)
        self.n = 0
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while self.sock.recv(65536):
            pass

    def send(self, **req):
        self.n += 1
        req["id"] = self.n
        self.sock.sendall((json.dumps(req) + "\n").encode())


isopodd = Isopodd(os.path.join(W, "isopodd.sock"))
captions = []
started = 0.0


def caption(text):
    captions.append((time.time() - started, text))


def key(name, wait=0.6):
    isopodd.send(op="key", key=name)
    time.sleep(wait)


def keys(name, count, wait=0.6):
    for _ in range(count):
        isopodd.send(op="key", key=name)
        time.sleep(0.12)
    time.sleep(wait)


def app_key(name, wait=1.0):
    # each wtype is a new virtual keyboard; SDL drops keys that beat its keymap
    subprocess.run(["wtype", "-s", "300", "-k", name], check=False)
    time.sleep(wait)


def home_to(index, wait=1.2):
    """Home, then open the index-th item of the home menu."""
    key("home", 1.0)
    keys("up", 10, 0.3)
    keys("down", index, 0.4)
    key("confirm", wait)


def storyboard():
    caption("isoPod · 실제 mpv, KOReader와 함께 돌아가는 모습")
    time.sleep(4)

    caption("음악 › 커버 플로우 · 앨범을 넘기고 뒤집어 재생")
    key("confirm", 1.3)
    key("confirm", 2.2)
    key("right", 1.1)
    key("left", 1.2)
    key("confirm", 2.4)
    key("confirm", 2.8)
    caption("Y · 가사, 지금 부르는 줄을 따라가요")
    key("now", 12)

    caption("동영상 · MP4와 SMI 한국어 자막, 음악은 알아서 멈춰요")
    home_to(2)
    keys("down", 3, 0.4)
    key("confirm", 8)

    caption("Home · 동영상은 그 자리에서 멈춰 기다려요")
    key("home", 1.2)
    keys("down", 14, 3)

    caption("책 · X로 워드 와이즈를 켜요")
    home_to(3, 1.5)
    key("down", 0.8)
    key("more", 1.6)
    key("confirm", 1.2)
    key("confirm", 7)
    caption("워드 와이즈 · 어려운 단어 위에 짧은 뜻")
    time.sleep(3)
    app_key("Right", 3.5)
    app_key("Right", 3.5)

    caption("영영으로 바꿔도 읽던 자리에서 이어져요")
    home_to(3, 1.5)
    key("down", 0.8)
    key("more", 1.6)
    key("down", 0.6)
    key("confirm", 1.2)
    key("confirm", 8)

    caption("Home · 책도 멈춰 두고, 두 앱이 함께 기다려요")
    key("home", 1.2)
    keys("down", 14, 3)
    caption("열린 앱을 고르면 멈춘 자리에서 그대로 이어져요")
    key("up", 0.8)
    key("confirm", 7)

    caption("뉴스 · Wi-Fi로 오늘 신문을 받아 KOReader로")
    home_to(3, 1.5)
    key("confirm", 10)
    key("down", 0.8)
    key("confirm", 9)
    app_key("Right", 1.5)
    app_key("Right", 3)

    caption("MKV 속 ASS 자막")
    home_to(2)
    keys("down", 1, 0.4)
    key("confirm", 6.5)
    caption("AVI와 SRT 자막")
    home_to(2)
    keys("down", 2, 0.4)
    key("confirm", 7)
    caption("WebM")
    home_to(2)
    key("confirm", 4.5)

    caption("isoPod")
    key("home", 3.5)


def srt(entries, end):
    def ts(t):
        ms = int(t * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"
    out = []
    for i, (t, text) in enumerate(entries):
        stop = entries[i + 1][0] if i + 1 < len(entries) else end
        out.append(f"{i + 1}\n{ts(t)} --> {ts(stop)}\n{text}\n")
    return "\n".join(out)


def main():
    global started
    raw = os.path.join(OUT, "raw.mkv")
    rec = subprocess.Popen(["wf-recorder", "-o", "HEADLESS-1", "-f", raw, "-c", "libx264",
                            "-p", "preset=veryfast", "-p", "crf=16"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    launched = time.time()
    time.sleep(6)  # the recorder takes a few seconds to deliver its first frame
    started = time.time()
    try:
        storyboard()
    finally:
        stopped = time.time()
        end = stopped - started
        subs = os.path.join(OUT, "captions.srt")
        with open(subs, "w", encoding="utf-8") as f:
            f.write(srt(captions, end))
        rec.send_signal(signal.SIGINT)
        try:
            rec.wait(timeout=600)  # software x264 can trail the screen by minutes
        except subprocess.TimeoutExpired:
            rec.terminate()
            rec.wait()
    # The recording ends when it is stopped, so its length tells how late it began.
    length = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                   raw], capture_output=True, text=True).stdout.strip() or 0)
    lag = (stopped - launched) - length
    trim = max(0.0, started - launched - lag)
    print(f"recorder lag {lag:.2f} s, trimming {trim:.2f} s")
    style = ("FontName=Pretendard,FontSize=14,PrimaryColour=&H00F7F2F2,Alignment=2,MarginV=9,"
             "BorderStyle=1,Outline=0,Shadow=0")
    vf = (f"fps=30,pad=640:544:0:0:color=0x1c1c1e,"
          f"subtitles={subs}:fontsdir={FONTS}:force_style='{style}',scale=1280:1088:flags=lanczos")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{trim:.3f}", "-i", raw, "-vf", vf,
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart", os.path.join(OUT, "isopod-apps.mp4")], check=True)


if __name__ == "__main__":
    main()
