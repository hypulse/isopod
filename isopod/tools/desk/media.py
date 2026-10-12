#!/usr/bin/env python3
"""Sample videos in several containers with Korean subtitles, and a book, for the desktop
recording."""
import html
import os
import subprocess
import sys
import zipfile

FONT = "/usr/share/fonts/pretendard/Pretendard-Bold.otf"

LINES = [
    (1, 5, "안녕하세요, isoPod 동영상 재생이에요"),
    (6, 11, "자막은 파일 옆에 두면 자동으로 켜져요"),
    (12, 17, "B를 누르면 이 자리를 기억하고 닫혀요"),
    (18, 24, "Home을 누르면 멈춰 두고 홈으로 가요"),
]


def stamp(t, sep=","):
    return f"00:00:{t:02d}{sep}000"


def smi(lines):
    body = "".join(f"<SYNC Start={a * 1000}><P Class=KRCC>{text}\n<SYNC Start={b * 1000}><P Class=KRCC>&nbsp;\n"
                   for a, b, text in lines)
    return ("<SAMI>\n<HEAD>\n<STYLE TYPE=\"text/css\">\n<!--\nP { font-size:20pt; }\n"
            ".KRCC { Name:Korean; lang:ko-KR; SAMIType:CC; }\n-->\n</STYLE>\n</HEAD>\n<BODY>\n" + body + "</BODY>\n</SAMI>\n")


def srt(lines):
    return "".join(f"{i}\n{stamp(a)} --> {stamp(b)}\n{text}\n\n" for i, (a, b, text) in enumerate(lines, 1))


def ass(lines):
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Alignment, MarginV\n"
            "Style: Default,Pretendard,26,&H00FFFFFF,&H00000000,1,2,2,24\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Text\n")
    return head + "".join(f"Dialogue: 0,0:00:{a:02d}.00,0:00:{b:02d}.00,Default,{text}\n" for a, b, text in lines)


def video(path, label, colors, vcodec, acodec, extra=()):
    c0, c1 = colors
    draw = (f"drawtext=fontfile={FONT}:text='{label}':fontcolor=white:fontsize=40:x=(w-text_w)/2:y=h/2-40,"
            f"drawtext=fontfile={FONT}:text='%{{pts\\:hms}}':fontcolor=white@0.7:fontsize=22:x=(w-text_w)/2:y=h/2+20")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"gradients=s=640x360:c0={c0}:c1={c1}:speed=0.015:d=30:r=25",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=30",
                    *extra, "-vf", draw, "-pix_fmt", "yuv420p", "-c:v", vcodec, "-c:a", acodec, "-shortest", path],
                   check=True)


PAGE = ("쥐며느리는 건드리면 몸을 동그랗게 만다. 접으면 작아지고 펴면 다시 걷는다. "
        "주머니 속 기계도 그렇게 접혔다 펴진다. ")


def epub(path, title, chapters):
    """A small EPUB 3: chapters are (title, body as XHTML)."""
    def page(name, body):
        return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ko" lang="ko">'
                f"<head><title>{html.escape(name)}</title></head><body>{body}</body></html>")
    items = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>'
                    for i in range(len(chapters)))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    toc = "".join(f'<li><a href="c{i}.xhtml">{html.escape(name)}</a></li>' for i, (name, _) in enumerate(chapters))
    files = {
        "META-INF/container.xml":
            '<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
            "</container>",
        "content.opf":
            '<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:identifier id="id">urn:isopod:{html.escape(title)}</dc:identifier>'
            f"<dc:title>{html.escape(title)}</dc:title><dc:creator>isoPod</dc:creator><dc:language>ko</dc:language>"
            '<meta property="dcterms:modified">2000-01-01T00:00:00Z</meta></metadata><manifest>'
            f'<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>{items}'
            f"</manifest><spine>{spine}</spine></package>",
        "nav.xhtml": page(title, '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
                                 f"<ol>{toc}</ol></nav>"),
    }
    files.update({f"c{i}.xhtml": page(name, body) for i, (name, body) in enumerate(chapters)})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        for name, text in files.items():
            z.writestr(name, text)


def main(folder, books=None):
    if books:
        os.makedirs(books, exist_ok=True)
        epub(os.path.join(books, "쥐며느리 이야기.epub"), "쥐며느리 이야기",
             [(f"{n}장", f"<h2>{n}장</h2>" + f"<p>{PAGE * 4}</p>" * 14) for n in range(1, 7)])
    os.makedirs(folder, exist_ok=True)
    video(os.path.join(folder, "밤의 도시.mp4"), "MP4 - H.264 / AAC", ("0x1e3c72", "0x2a5298"), "libx264", "aac")
    with open(os.path.join(folder, "밤의 도시.smi"), "w", encoding="cp949") as f:
        f.write(smi(LINES))
    video(os.path.join(folder, "Retro Tape.avi"), "AVI - MPEG-4 / MP3", ("0x42275a", "0x734b6d"), "mpeg4",
          "libmp3lame")
    with open(os.path.join(folder, "Retro Tape.srt"), "w", encoding="utf-8") as f:
        f.write(srt([(a, b, t.replace("isoPod 동영상", "SRT 자막 동영상")) for a, b, t in LINES]))
    ass_path = os.path.join(folder, ".ocean.ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass([(a, b, t.replace("isoPod 동영상", "MKV 속 ASS 자막")) for a, b, t in LINES]))
    video(os.path.join(folder, "Ocean Waves.mkv"), "MKV - H.264 - ASS", ("0x134e5e", "0x71b280"), "libx264",
          "libopus", extra=("-i", ass_path, "-map", "0:v", "-map", "1:a", "-map", "2:s", "-c:s", "ass",
                            "-metadata:s:s:0", "language=kor", "-disposition:s:0", "default"))
    os.remove(ass_path)
    video(os.path.join(folder, "Loop.webm"), "WebM - VP8 / Vorbis", ("0xff5f6d", "0xffc371"), "libvpx",
          "libvorbis", extra=("-b:v", "600k"))


if __name__ == "__main__":
    main(*sys.argv[1:3])
