# isoPod

A music-first operating system for the Anbernic RG35XX SP, in the spirit of the iPod video.
isoPod keeps ROCKNIX's kernel, drivers, bootloader and build system as they are, and
replaces the front end with a small shell built around your music.

![isoPod screens](docs/screens.png)

## What you get

- **Music** from `/storage/music`: Cover Flow, Playlists, Artists, Albums, Songs, Genres,
  Up Next and Search. Playback carries on in the background, whatever else is open, and a
  sleep timer in Now Playing stops it for you.
- **Lyrics** in Now Playing: timed lyrics follow the line being sung, plain ones scroll.
  They come from an `.lrc` or `.txt` file beside the song, or from the song's own tags.
- **Search** with an on-screen keyboard that types Hangul as well as Latin; bare initials
  such as ㅎㄱ find 한강.
- **Radio**: stations from [Radio Browser](https://www.radio-browser.info), with
  Favorites, Top Stations, Korea, By Country and Search.
- **Videos** from `/storage/videos`, played by mpv: MP4, MKV, AVI, WebM, MOV and the rest.
  Subtitles beside the file (SRT, SMI, ASS, VTT) or inside it turn on by themselves, Korean
  first, and older Korean subtitle files read correctly. It remembers where you stopped.
- **Books** from `/storage/books`, opened in KOReader. **Word Wise** writes a short meaning
  above harder English words, in Korean or in plain English: press X on a book to choose.
  The page you are on follows you when you turn it on or off.
- **News**: over Wi-Fi, the day's articles from a few news feeds become one book, and the
  last week stays. Change the feeds in `/storage/.config/isopod/news-feeds.txt`, one
  `Name | URL` per line.
- **Games**: PortMaster and the ports it installs.
- **Bluetooth** headphones connect from one list, in Settings or the Control Center.
- **Headphones**, wired or Bluetooth, take the sound when they arrive; with both, the one
  connected last plays. Unplugging or losing the one in use pauses music and videos before
  a note reaches the speaker, and each output keeps its own volume, never above the limit.
- **Wi-Fi** remembers every network you join.
- **Multitasking**: each app keeps its place. Home pauses it where it is, and the home
  screen lists it so you can pick it up again. Up to three apps stay open; when memory
  runs low, the one used longest ago closes first and gets the chance to save.
- **Lid and power**: closing the lid turns the screen off and keeps the music playing,
  and the app in front pauses until the lid opens. With nothing playing the device
  sleeps, and opening the lid wakes it. Videos keep the screen on while they play.

## The look

The last iPod classic, brought forward. Menus split in two: the list on the left, and on
the right a panel where album art drifts slowly across, or the song playing, or a colour and
a glyph for the section you are on. Cover Flow turns the albums in 3D over a black floor
with their reflections, each side turned to face the middle; press A and the album flips
over to show its songs. Artwork stays square, as the covers are, and type is set in
Pretendard.

## The rules

Every screen is a list, and every list works the same way.

| Button | What it does |
| --- | --- |
| D-pad | Move. In long lists ← and → jump to the previous or next letter; in Cover Flow they turn the albums |
| A | Open or choose |
| B | Back |
| X | Options for the selected item |
| Y | Now Playing |
| Start | Play or pause |
| Select | Control Center |
| L1 / R1 | Previous or next track |
| L2 / R2 | Page up or down |
| Home | Home screen; from an app, pause the app and come home |
| Volume | Volume, always |
| Power | Turn the screen off or on; hold for Sleep, Restart and Shut Down |

In Now Playing, ← and → skip 10 seconds, ↑ and ↓ change the volume, and Y shows the lyrics.

Apps get the buttons while they are in front, except Home and Volume. They follow the
same idea:

- **KOReader**: A selects, B leaves the book (the page is saved), X or Start opens the
  menu, and ← and →, the shoulders and the triggers turn pages.
- **mpv**: A pauses, B leaves and remembers the spot, ← and → skip 10 seconds, ↑ and ↓
  skip a minute, and L1 and R1 move between chapters.

## How it fits together

| Part | Where | Role |
| --- | --- | --- |
| isopodd | `isopod/daemon` | Python asyncio daemon. Owns the buttons, the lid and the power key. Runs audio routing, Bluetooth, Wi-Fi, the MPD client and apps, and describes every screen |
| isopod-shell | `isopod/shell` | C and SDL2 renderer at 640×480. Draws what isopodd describes and nothing else |
| MPD | `projects/ROCKNIX/packages/isopod/mpd` | Plays music and radio through PipeWire |
| sway | ROCKNIX | Shows the shell on workspace 1 and each app on a workspace of its own |
| systemd | ROCKNIX | Runs each app as a transient unit, frozen while it waits in the background |

The recipes that put these in the image are in `projects/ROCKNIX/packages/isopod`.

## Install on an RG35XX SP

1. Download `isopod-H700-images` from the latest
   [workflow run](https://github.com/hypulse/isopod/actions/workflows/isopod-h700.yml).
   Pick the `DDR4` image for boards with LPDDR4 memory and the `DDR3` image for the rest.
2. Write the `.img.gz` to a microSD card with balenaEtcher or Raspberry Pi Imager.
3. On the card's `ISOPOD` partition, copy `device_trees/sun50i-h700-anbernic-rg35xx-sp.dtb`
   out of that folder to the top level, next to `KERNEL` and `SYSTEM`, and name the copy
   `dtb.img`; the original stays where it is. On a Mac, in Terminal:

   ```sh
   cp /Volumes/ISOPOD/device_trees/sun50i-h700-anbernic-rg35xx-sp.dtb /Volumes/ISOPOD/dtb.img
   ```

   Without it the bootloader has nothing to start Linux with, and the device shows only its
   red light. An SP with the newer panel takes `sun50i-h700-anbernic-rg35xx-sp-v2-panel.dtb`.
4. Add music, videos and books either way:
   - **Over Wi-Fi**: open Settings > Add Files over Wi-Fi, then open the address it shows
     in a browser on the same network, enter the code and drop files or folders. Each file
     goes to Music, Videos or Books by its type.
   - **On a second microSD card** (exFAT works with any computer): put them in `music`,
     `videos` and `books` folders. isoPod creates the folders on first boot and shows the
     card as an `SD Card` folder in each library.

## Build

The image builds the same way ROCKNIX does, in its build container. isoPod ships no 32-bit
userland, so only the aarch64 pass is needed:

```sh
docker run --rm -it --init -v "$PWD":"$PWD" -w "$PWD" --user "$(id -u):$(id -g)" \
    ghcr.io/rocknix/rocknix-build:latest \
    bash -c "PROJECT=ROCKNIX DEVICE=H700 ARCH=aarch64 ./scripts/build_distro"
```

Pushes to the `isopod` branch are built by `.github/workflows/isopod-h700.yml`.

## Try it without the device

The simulator runs MPD, isopodd with simulated hardware and the shell offscreen, and saves
screenshots along a scripted walk. Set `ISOPOD_RECORD=/out/demo.mp4` and run
`isopod/tools/sim/demo.txt` to record the whole system as a captioned video.

```sh
docker build -t isopod-dev isopod/tools/dev
docker run --rm -v "$PWD":/src -v "$PWD/shots":/out -w /src isopod-dev \
    isopod/tools/sim/run.sh /src/isopod/tools/sim/tour.txt /out
```

The desk runs the same system with the real apps under a headless sway, mpv and KOReader
included, and records it: the storyboard in `isopod/tools/desk/demo.py` plays music with
lyrics, videos in four formats with their subtitles, books with Word Wise and the news,
switching between them.

```sh
docker build -t isopod-desk isopod/tools/desk
docker run --rm -v "$PWD":/src -v "$PWD/books":/books -v "$PWD/out":/out -w /src isopod-desk \
    isopod/tools/desk/run.sh /books /out
```

Daemon tests: `python3 -m unittest discover -s isopod/daemon/tests`.

## Status

This is a demo. The shell, pages and the MPD client are exercised in the simulator, and
mpv, KOReader and multitasking on the desk. The parts that talk to the hardware, BlueZ,
NetworkManager, PipeWire, evdev, systemd freezing and suspend, still need a pass on the
device.

## Credits

isoPod is built on [ROCKNIX](https://github.com/ROCKNIX/distribution) and uses
[MPD](https://www.musicpd.org), [KOReader](https://koreader.rocks),
[PortMaster](https://portmaster.games), [mpv](https://mpv.io),
[cJSON](https://github.com/DaveGamble/cJSON) and the
[Pretendard](https://github.com/orioncactus/pretendard) typeface. Word Wise hints come from
the Korean Wiktionary, Princeton WordNet and wordfreq; see `data/wordwise`.
