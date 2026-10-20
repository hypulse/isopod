# Apps

An app is a folder in `/storage/apps`, the `Apps` storage over the cable, with an
`app.json` in it. Apps on the home screen lists it within seconds of its being copied in;
nothing needs restarting.

Examples: [`tools/sim/apps`](../tools/sim/apps), which the simulator tour `12-apps` walks
through.

## app.json

```json
{
  "name": {"en": "Lagrange", "ko": "라그랑주"},
  "exec": ["bin/lagrange"],
  "keys": {"a": "enter", "b": "alt+left", "l1": "pageup", "r1": "pagedown", "x": "ctrl+l"}
}
```

| Field | | |
| --- | --- | --- |
| `name` | required | A string, or one for each language (`en`, `ko`) |
| `kind` | | `window` (the default) or `list` |
| `exec` | required | The program and its arguments. A first word with a `/` in it is in the folder; a bare name is looked for in the folder, its `bin/`, then the system |
| `keys` | window | Buttons pressed as keys, for a program that knows no gamepad |
| `pauses_music` | window | Music pauses when it opens, as it does for a video |
| `awake` | window | The screen stays lit at least this many seconds without a press; `0` while it is in front |
| `env` | | More environment |

The folder's name is lower-case letters, digits, `-` and `_`. A folder whose `app.json`
cannot be read is left out, and isopodd's log says why.

## What an app gets

- It runs in its folder, as root, as everything on ROCKNIX does: copy in only what you trust.
- `HOME` is `/storage/.config/isopod/apps/<folder>`, kept when the folder is replaced.
  `ISOPOD_APP_DIR`, `ISOPOD_APP_DATA` and `ISOPOD_LANG` (`ko` or `en`) are set too.
- `bin/` is first on `PATH` and `lib/` is on `LD_LIBRARY_PATH`: bring the libraries the
  system lacks.
- A copy over the cable cannot be run as it comes, so before each start isoPod makes the
  program and everything in `bin/` runnable.
- Programs are built for aarch64 against glibc 2.44 or older.

## Window apps

A window app is shown full screen on a sway workspace of its own, as a video or a book is.
Home stops it where it is (SIGSTOP) and lists it under Open Apps; Close in its sheet ends it
with SIGTERM, and SIGKILL 5 s later. At most three apps are open: a fourth, or less than
256 MB of memory free, closes the one used longest ago.

An app that reads the gamepad, through SDL's game controller say, gets it while it is in
front. Home, the volume and the power key stay isoPod's; Start plays or pauses the music
and L2 and R2 skip songs.

`keys` maps buttons to keys for a program that knows only a keyboard. The gamepad then
stays isoPod's and the app sees a keyboard named `isoPod keys`. A button not in `keys`
does what it does for any app.

- Buttons: `up` `down` `left` `right` `a` `b` `x` `y` `start` `select` `l1` `r1` `l2` `r2`
- Keys: `a`–`z`, `0`–`9`, `f1`–`f12`, `enter` `esc` `tab` `space` `backspace` `delete`
  `insert` `home` `end` `pageup` `pagedown` `up` `down` `left` `right` `minus` `equal`
  `leftbrace` `rightbrace` `semicolon` `apostrophe` `grave` `backslash` `comma` `dot`
  `slash`, and `ctrl` `shift` `alt` `meta` before them with `+`, as in `ctrl+l`

## List apps

A list app has no window. isoPod shows its pages as its own lists and plays what it names
in its own video player, so the volume, the lid, a headset's buttons and Home work as they
do for a video from the card.

The program is started at the first question and talks over stdin and stdout, one JSON
object a line. It is stopped after five minutes without a question or when its folder
changes, and should end when stdin closes. A line on stdout that is not an answer is
skipped; stderr goes to isopodd's log. An answer comes within 60 s, with the question's
`id`.

```json
{"id": 1, "op": "page", "path": "/", "lang": "ko"}
{"id": 2, "op": "page", "path": "/search", "query": "lo-fi", "lang": "ko"}
{"id": 3, "op": "play", "play": "abc", "lang": "ko"}
```

A page:

```json
{"id": 1, "title": "Tube", "empty": "Nothing yet", "items": [
  {"title": "Channels", "header": true},
  {"title": "Lo-fi Girl", "subtitle": "12 new", "path": "/channel/1"},
  {"title": "Search", "search": "/search", "prompt": "Title"},
  {"title": "A video", "subtitle": "3:12", "play": "abc"}
]}
```

`path` opens another page. `search` brings up the keyboard and then asks for that page with
the words as `query`. `play` asks what to play:

```json
{"id": 3, "url": "https://example.com/v.mp4", "audio": "https://example.com/a.m4a",
 "title": "A video", "headers": {"Referer": "https://example.com/"}}
```

`audio`, a separate sound track, `title` and `headers` are optional. `{"id": 3, "error":
"Not in your country"}` shows those words instead.
