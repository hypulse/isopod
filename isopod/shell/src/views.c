#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

#include "shell.h"

#define CONTENT_Y BAR_H
#define CONTENT_H (SCREEN_H - BAR_H)

static bool wants_frame, wants_slow_frame;

/* layout ------------------------------------------------------------------ */

int row_height(Page *p, int i)
{
    Item *it = &p->items[i];
    if (it->header)
        return it->title && *it->title ? HEADER_H : 14;
    return p->art_rows || (p->tall && it->subtitle) ? ROW_TALL_H : ROW_H;
}

int item_y(Page *p, int i)
{
    int y = 0;
    for (int k = 0; k < i && k < p->count; k++)
        y += row_height(p, k);
    return y;
}

int list_height(Page *p)
{
    return item_y(p, p->count);
}

static float page_scroll(Page *p)
{
    float t = ease_out((SDL_GetTicks() - p->scroll_at) / (float)ANIM_SCROLL_MS);
    if (t < 1)
        wants_frame = true;
    return p->scroll_from + (p->scroll_to - p->scroll_from) * t;
}

/* pieces ------------------------------------------------------------------ */

static void draw_switch(int x, int y, bool on, bool selected)
{
    Rgba track = on ? C_GREEN : (selected ? RGBA(255, 255, 255, 90) : C_SWITCH_OFF);
    fill_round(x, y, 52, 32, 16, track);
    int kx = on ? x + 22 : x + 2;
    fill_round(kx + 1, y + 3, 28, 28, 14, RGBA(0, 0, 0, 30));
    fill_round(kx, y + 2, 28, 28, 14, C_WHITE);
}

static void draw_selection(int x, int y, int w, int h)
{
    fill_gradient(x, y, w, h, C_SEL_TOP, C_SEL_BOTTOM);
}

static const char *device_icon(const char *kind)
{
    if (!kind)
        return NULL;
    if (!strcmp(kind, "audio"))
        return "dev-headphones";
    if (!strcmp(kind, "input"))
        return "dev-gamepad";
    return "dev-bluetooth";
}

static void draw_row(Page *p, int i, int x, int y, int w, bool selected)
{
    Item *it = &p->items[i];
    int h = row_height(p, i);
    if (it->header) {
        if (!it->title || !*it->title)
            return;
        int tw = draw_text(FONT_SEMIBOLD, FONT_HEADER, it->title, x + PAD_X, y + 9, C_TEXT3, w - 2 * PAD_X);
        if (it->spinner) {
            draw_spinner(x + PAD_X + tw + 20, y + h / 2, C_TEXT2);
            wants_frame = true;
        }
        return;
    }
    if (selected)
        draw_selection(x, y, w, h);
    Rgba text = selected ? C_WHITE : C_TEXT;
    Rgba text2 = selected ? RGBA(255, 255, 255, 215) : C_TEXT2;
    int left = x + PAD_X, right = x + w - PAD_X;

    if (p->art_rows) {
        draw_image(it->art, "art-default", left, y + (h - ART_THUMB) / 2, ART_THUMB, 0);
        left += ART_THUMB + 14;
    } else if (it->icon) {
        const char *name = device_icon(it->icon);
        draw_icon(name, left, y + (h - 28) / 2, selected ? C_WHITE : C_ACCENT);
        left += 40;
    }

    switch (it->accessory) {
    case ACC_CHEVRON:
        draw_icon("chevron", right - 11, y + (h - 20) / 2, selected ? C_WHITE : C_TEXT3);
        right -= 26;
        break;
    case ACC_CHECK:
        draw_icon("check", right - 20, y + (h - 18) / 2, selected ? C_WHITE : C_ACCENT);
        right -= 34;
        break;
    case ACC_SWITCH:
        draw_switch(right - 52, y + (h - 32) / 2, it->on, selected);
        right -= 64;
        break;
    case ACC_SPINNER:
        draw_spinner(right - 12, y + h / 2, selected ? C_WHITE : C_TEXT2);
        wants_frame = true;
        right -= 34;
        break;
    case ACC_PLAYING:
        draw_icon("speaker", right - 22, y + (h - 20) / 2, selected ? C_WHITE : C_ACCENT);
        right -= 36;
        break;
        right -= 34;
        break;
    }
    if (it->signal >= 0) {
        char name[16];
        snprintf(name, sizeof(name), "wifi-%d", it->signal);
        draw_icon(name, right - 26, y + (h - 20) / 2, selected ? C_WHITE : C_TEXT);
        right -= 36;
    }
    if (it->lock) {
        draw_icon("lock", right - 14, y + (h - 20) / 2, selected ? C_WHITE : C_TEXT);
        right -= 26;
    }
    if (it->value) {
        int vw = draw_text_right(FONT_REGULAR, FONT_VALUE, it->value, right, y + (h - 28) / 2, text2);
        right -= vw + 16;
    }

    int title_w = right - left;
    int ty = p->tall && it->subtitle ? y + 10 : y + (h - 31) / 2;
    if (selected && text_overflows(FONT_SEMIBOLD, FONT_ROW, it->title, title_w)) {
        draw_marquee(FONT_SEMIBOLD, FONT_ROW, it->title, left, ty, title_w, text, p->marquee_at);
        wants_frame = true;
    } else {
        draw_text(FONT_SEMIBOLD, FONT_ROW, it->title, left, ty, text, title_w);
    }
    if (p->tall && it->subtitle)
        draw_text(FONT_REGULAR, FONT_SUB, it->subtitle, left, y + 41, text2, title_w);
}

static void draw_empty(Page *p, int x, int y, int w, int h)
{
    int cy = y + h / 2;
    if (p->empty_icon) {
        char name[48];
        snprintf(name, sizeof(name), "empty-%s", p->empty_icon);
        draw_icon(name, x + w / 2 - 36, cy - 110, C_TEXT3);
    } else {
        cy -= 40;
    }
    draw_text_center(FONT_SEMIBOLD, FONT_BIG - 6, p->empty_title ? p->empty_title : S("empty_list", ""),
                     x + w / 2, cy - 18, C_TEXT, w - 60);
    if (p->empty_text)
        draw_text_wrap_center(FONT_REGULAR, FONT_SUB, p->empty_text, x + w / 2, cy + 24, w - 80, 30, 3, C_TEXT2);
}

static void draw_list(Page *p, int x, int y, int w, int h)
{
    SDL_RenderSetClipRect(app.renderer, &(SDL_Rect){x < 0 ? 0 : x, y, w, h});
    if (!p->loaded) {
        if (SDL_GetTicks() - p->opened_at > 180)
            draw_spinner(x + w / 2, y + h / 2, C_TEXT2);
        wants_frame = true;
    } else if (p->count == 0) {
        draw_empty(p, x, y, w, h);
    } else {
        float scroll = page_scroll(p);
        int total = list_height(p);
        int yy = 0;
        for (int i = 0; i < p->count; i++) {
            int rh = row_height(p, i);
            if (yy + rh > scroll && yy < scroll + h)
                draw_row(p, i, x, y + yy - (int)scroll, w, i == p->sel && p->style != STYLE_ABOUT);
            yy += rh;
            if (yy > scroll + h)
                break;
        }
        if (total > h) {
            int track_h = h - 8;
            int thumb = track_h * h / total;
            if (thumb < 28)
                thumb = 28;
            int ty = y + 4 + (int)((track_h - thumb) * (scroll / (total - h)));
            fill_round(x + w - 7, ty, 4, thumb, 2, RGBA(0, 0, 0, 70));
        }
    }
    SDL_RenderSetClipRect(app.renderer, NULL);
}

/* now playing ------------------------------------------------------------- */

static double live_elapsed(Player *pl)
{
    double e = pl->elapsed;
    if (!strcmp(pl->state, "play"))
        e += (SDL_GetTicks() - pl->elapsed_at) / 1000.0;
    if (pl->duration > 0 && e > pl->duration)
        e = pl->duration;
    return e;
}

static void draw_progress_row(Player *pl, int y)
{
    int bx = 110, bw = 420;
    bool hud = app.hud_at && SDL_GetTicks() - app.hud_at < HUD_MS && !strcmp(app.hud_kind, "volume");
    if (hud) {
        draw_icon("speaker-low", 58, y - 11, C_TEXT2);
        fill_round(bx, y - 5, bw, 10, 5, C_TRACK);
        fill_round(bx, y - 5, bw * app.hud_value / 100, 10, 5, C_ACCENT);
        draw_icon("speaker-high", 548, y - 11, C_TEXT2);
        wants_frame = true;
        return;
    }
    double e = live_elapsed(pl);
    char a[16], b[16];
    fmt_time(e, a, sizeof(a));
    b[0] = '-';
    fmt_time(pl->duration - e, b + 1, sizeof(b) - 1);
    draw_text_right(FONT_REGULAR, FONT_SMALL, a, bx - 12, y - 13, C_TEXT2);
    draw_text(FONT_REGULAR, FONT_SMALL, b, bx + bw + 12, y - 13, C_TEXT2, 0);
    fill_round(bx, y - 5, bw, 10, 5, C_TRACK);
    int fw = pl->duration > 0 ? (int)(bw * (e / pl->duration)) : 0;
    if (fw > 0) {
        fill_round(bx, y - 5, fw < 10 ? 10 : fw, 10, 5, C_ACCENT);
        fill_rect(bx + 3, y - 4, (fw < 10 ? 10 : fw) - 6, 3, RGBA(255, 255, 255, 60));
    }
}

/* Copies a string the daemon sent, with value where it names a slot such as {time}. */
static void fill_slot(char *out, size_t n, const char *words, const char *slot, const char *value)
{
    const char *at = strstr(words, slot);
    if (at)
        snprintf(out, n, "%.*s%s%s", (int)(at - words), words, value, at + strlen(slot));
    else
        snprintf(out, n, "%s", words);
}

/* The line being sung: the last one whose time has come, -1 before the first. */
static int lyric_now(Page *p, double elapsed)
{
    int cur = -1;
    for (int i = 0; i < p->count && i < p->time_count; i++)
        if (p->times[i] <= elapsed + 0.25)
            cur = i;
    return cur;
}

/* Under a cover that has the room, its reflection, fading out the way it did on the iPod. */
static void draw_reflection(const char *path, int x, int y, int size, int depth)
{
    SDL_Texture *t = image_or(path, "art-default", size, 0);
    if (!t)
        return;
    SDL_FPoint top[2] = {{(float)x, (float)y}, {(float)(x + size), (float)y}};
    SDL_FPoint bottom[2] = {{(float)x, (float)(y + depth)}, {(float)(x + size), (float)(y + depth)}};
    float u[2] = {0, 1};
    draw_strips(t, top, bottom, u, 2, 1, 1 - depth / (float)size, (SDL_Color){255, 255, 255, 72},
                (SDL_Color){255, 255, 255, 0});
}

/* Now Playing: where the song is in the queue, the cover, what the song is, and under
 * them either the line being sung with the one after it or, when the song has no timed
 * words, the cover's reflection. */
static void draw_nowplaying(Page *p, int x)
{
    Player *pl = &app.status.player;
    if (!pl->count && !strcmp(pl->state, "stop")) {
        draw_text_center(FONT_SEMIBOLD, FONT_NP_LINE, S("nothing_playing", ""), x + SCREEN_W / 2,
                         CONTENT_Y + CONTENT_H / 2 - 16, C_TEXT2, SCREEN_W - 80);
        return;
    }
    if (pl->pos > 0 && pl->count > 0) {
        char pos[16], count[16], some[64], place[64];
        snprintf(pos, sizeof(pos), "%d", pl->pos);
        snprintf(count, sizeof(count), "%d", pl->count);
        fill_slot(some, sizeof(some), S("of", "{pos} / {count}"), "{pos}", pos);
        fill_slot(place, sizeof(place), some, "{count}", count);
        draw_text(FONT_REGULAR, FONT_SMALL, place, x + 36, CONTENT_Y + 12, C_TEXT2, 240);
    }
    int ix = x + SCREEN_W - 36;
    if (pl->repeat) {
        draw_icon(pl->single ? "repeat-one" : "repeat", ix - 24, CONTENT_Y + 14, C_ACCENT);
        ix -= 34;
    }
    if (pl->shuffle)
        draw_icon("shuffle", ix - 24, CONTENT_Y + 14, C_ACCENT);

    bool words = p->count > 0 && p->time_count > 0;
    const int art = words ? 220 : 272, ax = x + 36, ay = CONTENT_Y + 44;
    draw_image(pl->art, "art-default", ax, ay, art, 0);
    Rgba edge = RGBA(0, 0, 0, 26);
    fill_rect(ax, ay, art, 1, edge);
    fill_rect(ax, ay + art - 1, art, 1, edge);
    fill_rect(ax, ay + 1, 1, art - 2, edge);
    fill_rect(ax + art - 1, ay + 1, 1, art - 2, edge);

    int tx = ax + art + 28, tw = x + SCREEN_W - 30 - tx;
    int title_lines = text_width(FONT_BOLD, FONT_NP_TITLE, pl->title) > tw ? 2 : 1;
    bool has_artist = pl->artist && *pl->artist, has_album = pl->album && *pl->album;
    bool has_note = p->note && *p->note;
    int block = title_lines * 38 + 8 + (has_artist ? 32 : 0) + (has_album ? 32 : 0) + (has_note ? 34 : 0);
    int ty = ay + art / 2 - block / 2;
    ty += draw_text_wrap(FONT_BOLD, FONT_NP_TITLE, pl->title, tx, ty, tw, 38, 2, C_TEXT) * 38 + 8;
    if (has_artist) {
        draw_text(FONT_REGULAR, FONT_NP_LINE, pl->artist, tx, ty, C_TEXT, tw);
        ty += 32;
    }
    if (has_album) {
        draw_text(FONT_REGULAR, FONT_NP_LINE, pl->album, tx, ty, C_TEXT2, tw);
        ty += 32;
    }
    if (has_note)
        draw_text(FONT_REGULAR, FONT_SMALL, p->note, tx, ty + 6, C_TEXT3, tw);

    if (words) {
        int cur = lyric_now(p, live_elapsed(pl)), next = cur + 1;
        while (next < p->count && !(p->items[next].title && *p->items[next].title))
            next++;
        const char *sung = cur >= 0 ? p->items[cur].title : NULL;
        const char *coming = next < p->count ? p->items[next].title : NULL;
        int cx = x + SCREEN_W / 2, w = SCREEN_W - 72, ly = ay + art + 18;
        if (sung && *sung) {
            int rows = draw_text_wrap_center(FONT_BOLD, 24, sung, cx, ly, w, 34, 2, C_TEXT);
            if (rows < 2 && coming)
                draw_text_center(FONT_SEMIBOLD, 21, coming, cx, ly + 38, C_TEXT3, w);
        } else if (coming) {
            draw_text_center(FONT_SEMIBOLD, 21, coming, cx, ly + 19, C_TEXT3, w);
        }
    } else {
        draw_reflection(pl->art, ax, ay + art + 2, art, 52);
    }

    draw_progress_row(pl, SCREEN_H - 42);
}

/* lyrics ------------------------------------------------------------------- */

#define LY_PAD 40
#define LY_SIZE 25
#define LY_LINE 34
#define LY_GAP 16

static int lyric_height(Page *p, int i, int w)
{
    const char *s = p->items[i].title;
    int n = s && *s ? wrap_count(FONT_SEMIBOLD, LY_SIZE, s, w, 4) : 1;
    return (n ? n : 1) * LY_LINE + LY_GAP;
}

static void draw_lyrics(Page *p, int x)
{
    if (!p->loaded) {
        draw_spinner(x + SCREEN_W / 2, CONTENT_Y + CONTENT_H / 2, C_TEXT2);
        wants_frame = true;
        return;
    }
    if (!p->count) {
        draw_empty(p, x, CONTENT_Y, SCREEN_W, CONTENT_H);
        return;
    }
    Player *pl = &app.status.player;
    int w = SCREEN_W - 2 * LY_PAD;
    double e = live_elapsed(pl);
    bool synced = p->time_count > 0;
    int cur = lyric_now(p, e);
    int total = 0, cur_y = 0;
    for (int i = 0; i < p->count; i++) {
        if (i == cur)
            cur_y = total;
        total += lyric_height(p, i, w);
    }
    float room = total - CONTENT_H + 60 > 0 ? total - CONTENT_H + 60 : 0;
    float target = synced ? (cur < 0 ? 0 : cur_y - CONTENT_H * 0.34f)
                          : (pl->duration > 0 ? (float)(e / pl->duration) * room : 0);
    target = target < 0 ? 0 : target > room ? room : target;
    Uint32 now = SDL_GetTicks();
    float dt = p->ly_at ? (now - p->ly_at) / 1000.0f : 1;
    p->ly_at = now;
    p->ly_scroll += (target - p->ly_scroll) * (1 - expf(-dt * 6));
    if (fabsf(target - p->ly_scroll) > 0.5f)
        wants_frame = true;

    SDL_RenderSetClipRect(app.renderer, &(SDL_Rect){x < 0 ? 0 : x, CONTENT_Y, SCREEN_W, CONTENT_H});
    int y = CONTENT_Y + 26 - (int)p->ly_scroll;
    for (int i = 0; i < p->count; i++) {
        int h = lyric_height(p, i, w);
        if (y + h > CONTENT_Y && y < SCREEN_H && p->items[i].title && *p->items[i].title) {
            Rgba c = !synced ? C_TEXT : i == cur ? C_TEXT : i < cur ? C_TEXT3 : C_TEXT2;
            draw_text_wrap(synced && i == cur ? FONT_BOLD : FONT_SEMIBOLD, LY_SIZE, p->items[i].title,
                           x + LY_PAD, y, w, LY_LINE, 4, c);
        }
        y += h;
    }
    SDL_RenderSetClipRect(app.renderer, NULL);
    fill_gradient(x, CONTENT_Y, SCREEN_W, 24, RGBA(255, 255, 255, 255), RGBA(255, 255, 255, 0));
    fill_gradient(x, SCREEN_H - 48, SCREEN_W, 48, RGBA(255, 255, 255, 0), RGBA(255, 255, 255, 255));
}

/* slider ------------------------------------------------------------------ */

static void draw_slider(Page *p, int x)
{
    int value = p->value, lo = p->min, hi = p->max > p->min ? p->max : 100;
    char text[16];
    snprintf(text, sizeof(text), "%d%%", value);
    draw_icon("sun-large", x + SCREEN_W / 2 - 28, CONTENT_Y + 70, C_TEXT2);
    draw_text_center(FONT_BOLD, FONT_BIG, text, x + SCREEN_W / 2, CONTENT_Y + 150, C_TEXT, 0);
    int bx = x + 110, bw = 420, by = CONTENT_Y + 250;
    draw_icon("sun-small", x + 62, by - 12, C_TEXT2);
    draw_icon("sun-large-outline", x + 548, by - 16, C_TEXT2);
    fill_round(bx, by - 6, bw, 12, 6, C_TRACK);
    int fw = bw * (value - lo) / (hi - lo);
    fill_round(bx, by - 6, fw < 12 ? 12 : fw, 12, 6, C_ACCENT);
    int kx = bx + fw - 16;
    fill_round(kx + 1, by - 14, 32, 32, 16, RGBA(0, 0, 0, 40));
    fill_round(kx, by - 16, 32, 32, 16, C_WHITE);
}

/* chrome ------------------------------------------------------------------- */

/* The title bar turns dark over Cover Flow, as it did on the iPod. */
static void draw_bar(const char *title, Uint8 alpha, const char *old_title, Uint8 old_alpha, bool dark)
{
    Rgba ink = dark ? RGB(244, 244, 246) : RGB(17, 17, 17);
    Rgba glyph = dark ? RGB(222, 222, 228) : RGB(60, 60, 67);
    if (dark) {
        fill_gradient(0, 0, SCREEN_W, BAR_H, RGB(52, 52, 56), RGB(22, 22, 24));
        fill_rect(0, BAR_H - 1, SCREEN_W, 1, RGB(0, 0, 0));
    } else {
        fill_gradient(0, 0, SCREEN_W, BAR_H, C_BAR_TOP, C_BAR_BOTTOM);
        fill_rect(0, BAR_H - 1, SCREEN_W, 1, C_BAR_LINE);
    }
    if (old_title && old_alpha)
        draw_text_center(FONT_SEMIBOLD, FONT_BAR, old_title, SCREEN_W / 2, 8,
                         RGBA(ink.r, ink.g, ink.b, old_alpha), 340);
    if (title)
        draw_text_center(FONT_SEMIBOLD, FONT_BAR, title, SCREEN_W / 2, 8, RGBA(ink.r, ink.g, ink.b, alpha), 340);

    Status *st = &app.status;
    if (!strcmp(st->player.state, "play"))
        draw_icon("play", 18, 13, glyph);
    else if (!strcmp(st->player.state, "pause"))
        draw_icon("pause", 18, 13, glyph);

    /* From the right: the battery, Wi-Fi while it is on (its bars once it has joined a
     * network, a faint fan until then) and Bluetooth while it is on (headphones once a
     * pair is connected). What is switched off is not drawn. */
    int x = SCREEN_W - 18;
    x -= draw_battery(x, 13, st->battery, st->charging, glyph) + 9;
    if (st->wifi_on) {
        char name[16];
        int bars = st->wifi_signal / 25;
        snprintf(name, sizeof(name), "wifi-%d", st->wifi_joined ? (bars > 3 ? 3 : bars) : 3);
        draw_icon(name, x - 26, 11, st->wifi_joined ? glyph : RGBA(glyph.r, glyph.g, glyph.b, 70));
        x -= 26 + 8;
    }
    if (st->bt_audio)
        draw_icon("bt-headphones", x - 26, 11, dark ? RGB(90, 170, 255) : C_ACCENT);
    else if (st->bt_on)
        draw_icon("bar-bluetooth", x - 14, 11, glyph);
}

/* The home screen's title is the time, where the iPod wrote its own name. */
static const char *bar_title(Page *p)
{
    static char clock[48];
    if (!p)
        return NULL;
    if (strcmp(p->path, "/"))
        return p->title;
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    char hm[16];
    snprintf(hm, sizeof(hm), "%d:%02d", tm.tm_hour % 12 ? tm.tm_hour % 12 : 12, tm.tm_min);
    fill_slot(clock, sizeof(clock), S(tm.tm_hour < 12 ? "clock_am" : "clock_pm", "{time}"), "{time}", hm);
    return clock;
}

/* split menus: the artwork panel ------------------------------------------ */

typedef struct {
    const char *name;
    Rgba top, bottom;
} Tone;

static const Tone TONES[] = {
    {"music", RGB(255, 94, 108), RGB(214, 36, 82)},      {"video", RGB(72, 170, 255), RGB(28, 98, 226)},
    {"settings", RGB(156, 162, 176), RGB(96, 102, 116)}, {"playlist", RGB(255, 122, 172), RGB(226, 60, 124)},
    {"genre", RGB(90, 206, 226), RGB(34, 140, 184)},     {"search", RGB(126, 136, 158), RGB(70, 80, 104)},
};

#define KB_MS 7000
#define KB_FADE 0.14f

static Uint8 scale_a(int a, Uint8 alpha)
{
    return (Uint8)(a * alpha / 255);
}

/* One cover drifting across the panel: a slow zoom and a pan that differs per slot. */
static void ken_burns(const char *path, const char *fallback, int px, int py, int pw, int ph, float u, int seed,
                      Uint8 alpha)
{
    SDL_Texture *t = image_or(path, fallback, 512, 0);
    if (!t)
        return;
    u = u < 0 ? 0 : u > 1 ? 1 : u;
    float zoom = 1.10f + 0.05f * (float)(seed % 3) + 0.14f * u;
    float side = ph * zoom;
    float ax = (seed & 1) ? 0.1f : 0.9f, ay = (seed & 2) ? 0.25f : 0.75f;
    float fx = ax + (1 - 2 * ax) * u, fy = ay + (1 - 2 * ay) * u;
    SDL_Rect dst = {px + (int)((pw - side) * fx), py + (int)((ph - side) * fy), (int)side, (int)side};
    SDL_SetTextureColorMod(t, 255, 255, 255);
    SDL_SetTextureAlphaMod(t, alpha);
    SDL_RenderCopy(app.renderer, t, NULL, &dst);
    SDL_SetTextureAlphaMod(t, 255);
    wants_slow_frame = true;
}

static void draw_panel(Page *p, const char *kind, int px, int py, int pw, int ph, Uint8 alpha)
{
    Uint32 el = SDL_GetTicks() - p->opened_at;
    if (!strcmp(kind, "art") && p->art_count) {
        int slot = (int)(el / KB_MS), n = p->art_count;
        float u = (el % KB_MS) / (float)KB_MS;
        ken_burns(p->art[slot % n], "art-default", px, py, pw, ph, u, slot, alpha);
        if (n > 1 && u > 1 - KB_FADE)
            ken_burns(p->art[(slot + 1) % n], "art-default", px, py, pw, ph, 0, slot + 1,
                      scale_a((int)(255 * (u - (1 - KB_FADE)) / KB_FADE), alpha));
        return;
    }
    Player *pl = &app.status.player;
    if (!strcmp(kind, "now") && (pl->count || strcmp(pl->state, "stop"))) {
        float u = (el % 24000) / 24000.0f;
        ken_burns(pl->art, "art-default", px, py, pw, ph, u < 0.5f ? u * 2 : 2 - u * 2, 2, alpha);
        return;
    }
    if (!strcmp(kind, "art") || !strcmp(kind, "now"))
        kind = "music";
    const Tone *tone = &TONES[0];
    for (size_t i = 0; i < SDL_arraysize(TONES); i++)
        if (!strcmp(TONES[i].name, kind))
            tone = &TONES[i];
    fill_gradient(px, py, pw, ph, RGBA(tone->top.r, tone->top.g, tone->top.b, alpha),
                  RGBA(tone->bottom.r, tone->bottom.g, tone->bottom.b, alpha));
    char name[48];
    snprintf(name, sizeof(name), "glyph-%s", tone->name);
    SDL_Texture *g = icon(name);
    if (g) {
        int w, h;
        SDL_QueryTexture(g, NULL, NULL, &w, &h);
        draw_icon(name, px + (pw - w) / 2, py + (ph - h) / 2 - 8, RGBA(255, 255, 255, scale_a(238, alpha)));
    }
}

static void draw_preview(Page *p, int x)
{
    int px = x + SPLIT_LIST_W, pw = SCREEN_W - SPLIT_LIST_W, py = CONTENT_Y, ph = CONTENT_H;
    int cx0 = px < 0 ? 0 : px, cx1 = px + pw > SCREEN_W ? SCREEN_W : px + pw;
    if (cx1 <= cx0)
        return;
    const char *want = "art";
    if (p->loaded && p->sel < p->count && p->items[p->sel].preview)
        want = p->items[p->sel].preview;
    if (!p->shown || strcmp(p->shown, want)) {
        bool first = !p->shown;
        SDL_free(p->prev_shown);
        p->prev_shown = p->shown;
        p->shown = xstrdup(want);
        p->shown_at = first ? SDL_GetTicks() - 1000 : SDL_GetTicks();
    }
    SDL_RenderSetClipRect(app.renderer, &(SDL_Rect){cx0, py, cx1 - cx0, ph});
    float t = ease_out((SDL_GetTicks() - p->shown_at) / 300.0f);
    if (t < 1 && p->prev_shown) {
        draw_panel(p, p->prev_shown, px, py, pw, ph, 255);
        wants_frame = true;
    }
    draw_panel(p, p->shown, px, py, pw, ph, (Uint8)(255 * (t < 1 ? t : 1)));
    SDL_RenderSetClipRect(app.renderer, NULL);
    fill_rect(px - 1, py, 1, ph, RGBA(0, 0, 0, 36));
}

/* cover flow ------------------------------------------------------------- */

#define CF_STRIPS 12
#define CF_ANGLE 1.2915f /* 74 degrees */
#define CF_GAP 200.0f
#define CF_STEP 30.0f
#define CF_DEPTH 160.0f
#define CF_FOCAL 900.0f
#define CF_FLIP_MS 440
#define CF_TOP (CONTENT_Y + 28)

/* A cover at depth z, turned by angle about its vertical axis, seen from the front. */
static void cf_quad(SDL_Texture *t, float cx, float cy, float x, float z, float angle, float w, float h, Uint8 shade,
                    Uint8 alpha, bool reflect)
{
    SDL_FPoint top[CF_STRIPS + 1], bot[CF_STRIPS + 1], rbot[CF_STRIPS + 1];
    float u[CF_STRIPS + 1], ca = cosf(angle), sa = sinf(angle);
    /* A cover facing the viewer is one rectangle; strips there only leave seams. */
    int strips = fabsf(angle) < 0.001f ? 1 : CF_STRIPS;
    for (int i = 0; i <= strips; i++) {
        float uu = i / (float)strips, lx = -w / 2 + uu * w;
        float X = x + lx * ca, Z = z + lx * sa;
        float k = CF_FOCAL / (CF_FOCAL + Z);
        top[i] = (SDL_FPoint){cx + X * k, cy - h / 2 * k};
        bot[i] = (SDL_FPoint){cx + X * k, cy + h / 2 * k};
        rbot[i] = (SDL_FPoint){cx + X * k, cy + (h / 2 + h * 0.42f) * k};
        u[i] = uu;
    }
    SDL_Color c = {shade, shade, shade, alpha};
    draw_strips(t, top, bot, u, strips + 1, 0, 1, c, c);
    if (reflect) {
        SDL_Color r0 = {shade, shade, shade, (Uint8)(alpha * 0.28f)}, r1 = {shade, shade, shade, 0};
        draw_strips(t, bot, rbot, u, strips + 1, 1, 0.58f, r0, r1);
    }
}

/* Covers at the sides turn to face the middle: the edge nearer the centre falls back, so
 * each one tucks behind its inner neighbour and none reaches the cover in front. */
static void cf_place(float d, float *x, float *z, float *angle)
{
    float ad = fabsf(d), sg = d < 0 ? -1.0f : 1.0f;
    if (ad < 1) {
        *x = d * CF_GAP;
        *z = ad * CF_DEPTH;
        *angle = -d * CF_ANGLE;
    } else {
        *x = sg * (CF_GAP + (ad - 1) * CF_STEP);
        *z = CF_DEPTH;
        *angle = -sg * CF_ANGLE;
    }
}

static SDL_Texture *card_tex;

/* The back of a flipped album: its songs, drawn into a texture so it can turn in 3D. */
static SDL_Texture *render_card(Page *p)
{
    Page *c = p->card;
    if (!card_tex) {
        card_tex = SDL_CreateTexture(app.renderer, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_TARGET, CARD_W, CARD_H);
        if (!card_tex)
            return NULL;
        SDL_SetTextureBlendMode(card_tex, SDL_BLENDMODE_BLEND);
    }
    SDL_Texture *prev = SDL_GetRenderTarget(app.renderer);
    if (SDL_SetRenderTarget(app.renderer, card_tex) != 0)
        return NULL;
    SDL_SetRenderDrawColor(app.renderer, 0, 0, 0, 0);
    SDL_RenderClear(app.renderer);
    fill_rect(0, 0, CARD_W, CARD_H, C_WHITE);
    Item *album = &p->items[p->sel];
    draw_text(FONT_BOLD, FONT_VALUE, album->title, 20, 11, C_TEXT, CARD_W - 40);
    if (album->subtitle)
        draw_text(FONT_REGULAR, FONT_HEADER, album->subtitle, 20, 39, C_TEXT2, CARD_W - 40);
    fill_rect(0, CARD_HEAD - 1, CARD_W, 1, C_SEP);
    draw_list(c, 0, CARD_HEAD, CARD_W, CARD_LIST_H);
    SDL_SetRenderTarget(app.renderer, prev);
    return card_tex;
}

static void draw_coverflow(Page *p, int x)
{
    fill_gradient(x, CONTENT_Y, SCREEN_W, CONTENT_H / 2, RGB(26, 26, 30), RGB(0, 0, 0));
    fill_rect(x, CONTENT_Y + CONTENT_H / 2, SCREEN_W, CONTENT_H - CONTENT_H / 2, RGB(0, 0, 0));
    if (!p->loaded) {
        draw_spinner(x + SCREEN_W / 2, CONTENT_Y + CONTENT_H / 2, C_WHITE);
        wants_frame = true;
        return;
    }
    if (!p->count) {
        fill_rect(x, CONTENT_Y, SCREEN_W, CONTENT_H, C_BG);
        draw_empty(p, x, CONTENT_Y, SCREEN_W, CONTENT_H);
        return;
    }

    Uint32 now = SDL_GetTicks();
    float dt = p->cf_at ? (now - p->cf_at) / 1000.0f : 0;
    p->cf_at = now;
    float target = (float)p->sel, k = 1 - expf(-dt * 11);
    p->cf_pos += (target - p->cf_pos) * k;
    if (fabsf(target - p->cf_pos) < 0.002f)
        p->cf_pos = target;
    else
        wants_frame = true;

    float flip = 0;
    if (p->card) {
        float t = (now - p->flip_at) / (float)CF_FLIP_MS;
        if (t >= 1 && p->closing) {
            page_free(p->card);
            p->card = NULL;
            p->closing = false;
        } else {
            t = t > 1 ? 1 : t;
            flip = t * t * (3 - 2 * t);
            if (p->closing)
                flip = 1 - flip;
            if (t < 1)
                wants_frame = true;
        }
    }

    float cx = x + SCREEN_W / 2.0f, cy = CF_TOP + CF_SIZE / 2.0f;
    int lo = (int)floorf(p->cf_pos) - 6, hi = (int)ceilf(p->cf_pos) + 6;
    lo = lo < 0 ? 0 : lo;
    hi = hi >= p->count ? p->count - 1 : hi;
    /* farthest from the middle first, so every cover lies over the ones behind it */
    int order[16], n = 0;
    for (int i = lo; i <= hi && n < 16; i++)
        if (!(p->card && i == p->sel))
            order[n++] = i;
    for (int a = 1; a < n; a++)
        for (int b = a; b > 0 && fabsf(order[b] - p->cf_pos) > fabsf(order[b - 1] - p->cf_pos); b--) {
            int swap = order[b];
            order[b] = order[b - 1];
            order[b - 1] = swap;
        }
    for (int j = 0; j < n; j++) {
        int i = order[j];
        float d = i - p->cf_pos, ad = fabsf(d);
        float px, pz, pa;
        cf_place(d, &px, &pz, &pa);
        float dim = (ad < 1 ? ad * 40 : 40 + (ad - 1) * 14);
        Uint8 shade = (Uint8)(255 - (dim > 105 ? 105 : dim));
        Uint8 alpha = (Uint8)(255 * (1 - 0.72f * flip));
        SDL_Texture *t = image_or(p->items[i].art, "art-default", 256, 0);
        if (t)
            cf_quad(t, cx, cy, px, pz, pa, CF_SIZE, CF_SIZE, shade, alpha, true);
    }

    if (p->card) {
        float ang = flip * (float)M_PI;
        float w = CF_SIZE + (CARD_W - CF_SIZE) * flip, h = CF_SIZE + (CARD_H - CF_SIZE) * flip;
        float fcy = cy + ((CONTENT_Y + CONTENT_H / 2.0f) - cy) * flip;
        if (ang < (float)M_PI / 2) {
            SDL_Texture *t = image_or(p->items[p->sel].art, "art-default", 256, 0);
            if (t)
                cf_quad(t, cx, fcy, 0, 0, ang, w, h, 255, 255, flip < 0.02f);
        } else {
            SDL_Texture *t = render_card(p);
            if (t)
                cf_quad(t, cx, fcy, 0, 0, ang - (float)M_PI, w, h, 255, 255, false);
        }
    }

    int shown = (int)lroundf(p->cf_pos);
    shown = shown < 0 ? 0 : shown >= p->count ? p->count - 1 : shown;
    float settle = 1 - fabsf(p->cf_pos - shown) * 2.5f;
    Uint8 ta = (Uint8)(255 * (settle < 0.2f ? 0.2f : settle) * (1 - flip));
    if (ta) {
        Item *it = &p->items[shown];
        draw_text_center(FONT_BOLD, FONT_VALUE + 2, it->title, (int)cx, SCREEN_H - 82, RGBA(245, 245, 247, ta),
                         SCREEN_W - 80);
        if (it->subtitle)
            draw_text_center(FONT_REGULAR, FONT_SUB, it->subtitle, (int)cx, SCREEN_H - 48,
                             RGBA(160, 160, 168, ta), SCREEN_W - 80);
    }
}

static void draw_page(Page *p, int x)
{
    if (!p)
        return;
    if (p->style == STYLE_NOWPLAYING)
        draw_nowplaying(p, x);
    else if (p->style == STYLE_SLIDER)
        draw_slider(p, x);
    else if (p->style == STYLE_COVERFLOW)
        draw_coverflow(p, x);
    else if (p->style == STYLE_LYRICS)
        draw_lyrics(p, x);
    else if (p->split) {
        draw_list(p, x, CONTENT_Y, SPLIT_LIST_W, CONTENT_H);
        draw_preview(p, x);
    } else
        draw_list(p, x, CONTENT_Y, SCREEN_W, CONTENT_H);
}

static float overlay_t(Uint32 at, int ms)
{
    float t = ease_out((SDL_GetTicks() - at) / (float)ms);
    if (t < 1)
        wants_frame = true;
    return t;
}

static void draw_sheet_panel(int top, int height, float t)
{
    fill_rect(0, 0, SCREEN_W, SCREEN_H, RGBA(0, 0, 0, (Uint8)(90 * t)));
    int y = top + (int)((1 - t) * (SCREEN_H - top));
    fill_round(0, y, SCREEN_W, height + 24, 18, C_SHEET);
}

static void draw_overlay_page(Page *p)
{
    float t = overlay_t(p->opened_at, 260);
    int rows = p->loaded ? list_height(p) : ROW_H * 3;
    bool titled = p->title && *p->title;
    int head = titled ? 62 : 18;
    int h = head + (rows > 330 ? 330 : rows);
    int top = SCREEN_H - h;
    draw_sheet_panel(top, h, t);
    int y = top + (int)((1 - t) * h);
    if (titled) {
        draw_text_center(FONT_SEMIBOLD, FONT_BAR, p->title, SCREEN_W / 2, y + 20, C_TEXT, 400);
        fill_rect(0, y + 60, SCREEN_W, 1, C_SEP);
    }
    draw_list(p, 0, y + head, SCREEN_W, h - head);
}

static void draw_action_sheet(void)
{
    Sheet *s = &app.sheet;
    float t = overlay_t(s->at, 240);
    bool has_title = s->title && *s->title;
    int h = (has_title ? 56 : 18) + s->count * ROW_H + 18;
    int top = SCREEN_H - h;
    draw_sheet_panel(top, h, t);
    int y = top + (int)((1 - t) * h) + (has_title ? 22 : 14);
    if (has_title) {
        draw_text_center(FONT_SEMIBOLD, FONT_HEADER + 1, s->title, SCREEN_W / 2, y, C_TEXT2, 520);
        y += 36;
    }
    for (int i = 0; i < s->count; i++) {
        bool sel = i == s->sel;
        if (sel)
            draw_selection(0, y, SCREEN_W, ROW_H);
        Rgba c = sel ? C_WHITE : (s->items[i].destructive ? C_RED : C_ACCENT);
        draw_text_center(FONT_MEDIUM, FONT_ROW, s->items[i].title, SCREEN_W / 2, y + 9, c, 520);
        if (s->items[i].value)
            draw_text_right(FONT_REGULAR, FONT_VALUE, s->items[i].value, SCREEN_W - PAD_X, y + 11,
                            sel ? C_WHITE : C_TEXT2);
        y += ROW_H;
    }
}

static void draw_confirm(void)
{
    Confirm *c = &app.confirm;
    float t = overlay_t(c->at, 200);
    fill_rect(0, 0, SCREEN_W, SCREEN_H, RGBA(0, 0, 0, (Uint8)(100 * t)));
    int w = 440, h = 190, x = (SCREEN_W - w) / 2, y = (SCREEN_H - h) / 2 + (int)((1 - t) * 30);
    fill_round(x, y + 4, w, h, 20, RGBA(0, 0, 0, 30));
    fill_round(x, y, w, h, 20, C_SHEET);
    draw_text_wrap(FONT_SEMIBOLD, FONT_NP_LINE + 1, c->title, x + 30, y + 34, w - 60, 32, 2, C_TEXT);
    const char *labels[2] = {S("cancel", "Cancel"), c->ok ? c->ok : S("ok", "OK")};
    int bw = (w - 60 - 16) / 2;
    for (int i = 0; i < 2; i++) {
        int bx = x + 30 + i * (bw + 16), by = y + h - 72;
        bool sel = c->choice == i;
        if (sel) {
            fill_round(bx, by, bw, 50, 12, C_SEL_BOTTOM);
            fill_gradient(bx + 6, by + 1, bw - 12, 24, RGBA(255, 255, 255, 45), RGBA(255, 255, 255, 0));
        } else {
            fill_round(bx, by, bw, 50, 12, RGB(229, 229, 234));
        }
        Rgba col = sel ? C_WHITE : (i == 1 && c->destructive ? C_RED : C_ACCENT);
        draw_text_center(FONT_SEMIBOLD, FONT_VALUE + 1, labels[i], bx + bw / 2, by + 11, col, bw - 16);
    }
}

static const char *kb_rows[2][2][4] = {
    {{"qwertyuiop", "asdfghjkl", "zxcvbnm", ""}, {"QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM", ""}},
    {{"1234567890", "-/:;()$&@\"", ".,?!'_", ""}, {"[]{}#%^*+=", "\\|~<>`", ".,?!'_", ""}},
};

/* Dubeolsik, in the same shape as the QWERTY rows. */
static const int kb_jamo_rows[2][3][10] = {
    {{0x3142, 0x3148, 0x3137, 0x3131, 0x3145, 0x315B, 0x3155, 0x3151, 0x3150, 0x3154},
     {0x3141, 0x3134, 0x3147, 0x3139, 0x314E, 0x3157, 0x3153, 0x314F, 0x3163},
     {0x314B, 0x314C, 0x314A, 0x314D, 0x3160, 0x315C, 0x3161}},
    {{0x3143, 0x3149, 0x3138, 0x3132, 0x3146, 0x315B, 0x3155, 0x3151, 0x3152, 0x3156},
     {0x3141, 0x3134, 0x3147, 0x3139, 0x314E, 0x3157, 0x3153, 0x314F, 0x3163},
     {0x314B, 0x314C, 0x314A, 0x314D, 0x3160, 0x315C, 0x3161}},
};

int kb_row_len(int row)
{
    Keyboard *k = &app.keyboard;
    if (row == 3)
        return 4;
    int n = (int)strlen(kb_rows[k->symbols][k->shift][row]);
    return row == 2 ? n + 2 : n;
}

/* The jamo under a letter key in Hangul mode, or 0. */
int kb_jamo(int row, int col)
{
    Keyboard *k = &app.keyboard;
    if (!k->hangul || k->symbols || row > 2)
        return 0;
    if (row == 2 && (col == 0 || col == kb_row_len(2) - 1))
        return 0;
    return kb_jamo_rows[k->shift][row][row == 2 ? col - 1 : col];
}

const char *kb_label(int row, int col, char *buf)
{
    Keyboard *k = &app.keyboard;
    if (row == 3) {
        const char *labels[4] = {k->symbols ? (k->hangul ? "가" : "ABC") : "123", k->hangul ? "A" : "한",
                                 S("space", "space"), S("done", "Done")};
        return labels[col];
    }
    if (row == 2 && col == 0)
        return k->symbols ? (k->shift ? "123" : "#+=") : "⇧";
    if (row == 2 && col == kb_row_len(2) - 1)
        return "⌫";
    int jamo = kb_jamo(row, col);
    if (jamo) {
        hangul_utf8(jamo, buf);
        return buf;
    }
    const char *s = kb_rows[k->symbols][k->shift][row];
    int idx = row == 2 ? col - 1 : col;
    buf[0] = s[idx];
    buf[1] = 0;
    return buf;
}

static void draw_keyboard(void)
{
    Keyboard *k = &app.keyboard;
    float t = overlay_t(k->at, 240);
    fill_rect(0, 0, SCREEN_W, SCREEN_H, RGBA(0, 0, 0, (Uint8)(60 * t)));
    int panel = 268, py = SCREEN_H - panel + (int)((1 - t) * panel);

    int fy = BAR_H + 26;
    fill_round(24, fy - 2, SCREEN_W - 48, 128, 16, RGBA(255, 255, 255, 245));
    draw_text(FONT_SEMIBOLD, FONT_VALUE, k->title ? k->title : "", 44, fy + 12, C_TEXT, SCREEN_W - 88);
    fill_round(44, fy + 52, SCREEN_W - 88, 50, 10, RGB(242, 242, 247));
    char shown[512];
    if (k->secret) {
        size_t n = 0;
        for (const char *c = k->text; *c && n + 4 < sizeof(shown); c++)
            if (((unsigned char)*c & 0xC0) != 0x80) {
                memcpy(shown + n, "•", 3);
                n += 3;
            }
        shown[n] = 0;
    } else {
        SDL_strlcpy(shown, k->text, sizeof(shown));
    }
    int tx = 60;
    if (*shown) {
        tx += draw_text(FONT_MEDIUM, FONT_ROW, shown, 60, fy + 62, C_TEXT, SCREEN_W - 140);
    } else if (k->placeholder) {
        draw_text(FONT_REGULAR, FONT_ROW, k->placeholder, 60, fy + 62, C_TEXT3, SCREEN_W - 140);
    }
    if ((SDL_GetTicks() / 530) % 2 == 0)
        fill_rect(tx + 2, fy + 63, 2, 30, C_ACCENT);

    fill_rect(0, py, SCREEN_W, panel, C_KEYBOARD_BG);
    int ky = py + 12;
    char buf[8];
    for (int row = 0; row < 4; row++) {
        int n = kb_row_len(row);
        int gap = 6, kh = 54;
        int widths[12], total = 0;
        for (int c = 0; c < n; c++) {
            if (row == 3)
                widths[c] = c == 2 ? 290 : c == 1 ? 90 : 110;
            else if (row == 2 && (c == 0 || c == n - 1))
                widths[c] = 84;
            else
                widths[c] = 56;
            total += widths[c] + (c ? gap : 0);
        }
        int kx = (SCREEN_W - total) / 2;
        for (int c = 0; c < n; c++) {
            bool special = row == 3 ? c != 2 : (row == 2 && (c == 0 || c == n - 1));
            bool sel = k->row == row && k->col == c;
            fill_round(kx, ky + 2, widths[c], kh, 9, RGBA(0, 0, 0, 45));
            if (sel)
                fill_round(kx, ky, widths[c], kh, 9, C_SEL_BOTTOM);
            else
                fill_round(kx, ky, widths[c], kh, 9, special ? C_KEY_DARK : C_KEY);
            const char *label = kb_label(row, c, buf);
            int size = row == 3 || (row == 2 && special) ? FONT_VALUE : FONT_KEY;
            if (row == 2 && c == 0 && !k->symbols && k->shift && !sel)
                fill_round(kx, ky, widths[c], kh, 9, RGB(255, 255, 255));
            draw_text_center(FONT_MEDIUM, size, label, kx + widths[c] / 2, ky + (kh - size - 10) / 2,
                             sel ? C_WHITE : C_TEXT, widths[c] - 8);
            kx += widths[c] + gap;
        }
        ky += kh + 8;
    }
}

static void draw_toast(void)
{
    Uint32 age = SDL_GetTicks() - app.toast_at;
    if (!app.toast_at || age > TOAST_MS)
        return;
    wants_frame = true;
    float a = age < 160 ? age / 160.0f : age > TOAST_MS - 300 ? (TOAST_MS - age) / 300.0f : 1;
    bool has_icon = app.toast_icon[0] != 0;
    int tw = text_width(FONT_SEMIBOLD, FONT_SMALL, app.toast);
    if (tw > 460)
        tw = 460;
    int w = tw + 44 + (has_icon ? 34 : 0), h = 46, x = (SCREEN_W - w) / 2;
    int y = BAR_H + 14 - (int)((1 - a) * 12);
    fill_round(x, y, w, h, 23, RGBA(28, 28, 30, (Uint8)(228 * a)));
    int cx = x + 22;
    if (has_icon) {
        char name[48];
        snprintf(name, sizeof(name), "toast-%s", app.toast_icon);
        draw_icon(name, cx, y + 12, RGBA(255, 255, 255, (Uint8)(255 * a)));
        cx += 34;
    }
    draw_text(FONT_SEMIBOLD, FONT_SMALL, app.toast, cx, y + 11, RGBA(255, 255, 255, (Uint8)(255 * a)), 460);
}

static void draw_hud(void)
{
    Page *p = top();
    Uint32 age = SDL_GetTicks() - app.hud_at;
    if (!app.hud_at || age > HUD_MS || (p && p->style == STYLE_NOWPLAYING && !app.overlay))
        return;
    wants_frame = true;
    float a = age > HUD_MS - 250 ? (HUD_MS - age) / 250.0f : 1;
    int w = 220, h = 190, x = (SCREEN_W - w) / 2, y = (SCREEN_H - h) / 2 + 10;
    fill_round(x, y, w, h, 24, RGBA(28, 28, 30, (Uint8)(215 * a)));
    const char *name = app.hud_value == 0 ? "hud-mute" : (!strcmp(app.status.output, "bluetooth") ? "hud-headphones" : "hud-speaker");
    draw_icon(name, x + w / 2 - 40, y + 30, RGBA(255, 255, 255, (Uint8)(255 * a)));
    fill_round(x + 26, y + h - 40, w - 52, 10, 5, RGBA(255, 255, 255, (Uint8)(60 * a)));
    fill_round(x + 26, y + h - 40, (w - 52) * app.hud_value / 100 + 1, 10, 5, RGBA(255, 255, 255, (Uint8)(255 * a)));
}

static void draw_letter(void)
{
    Uint32 age = SDL_GetTicks() - app.letter_at;
    if (!app.letter_at || age > 600)
        return;
    wants_frame = true;
    float a = age > 400 ? (600 - age) / 200.0f : 1;
    fill_round(SCREEN_W / 2 - 64, SCREEN_H / 2 - 50, 128, 128, 24, RGBA(28, 28, 30, (Uint8)(200 * a)));
    draw_text_center(FONT_BOLD, 60, app.letter, SCREEN_W / 2, SCREEN_H / 2 - 30, RGBA(255, 255, 255, (Uint8)(255 * a)), 0);
}

/* frame --------------------------------------------------------------------- */

void render(void)
{
    wants_frame = wants_slow_frame = false;
    gfx_frame_begin();
    if (!app.status.screen_on) {
        SDL_SetRenderDrawColor(app.renderer, 0, 0, 0, 255);
        SDL_RenderClear(app.renderer);
        SDL_RenderPresent(app.renderer);
        return;
    }
    SDL_SetRenderDrawColor(app.renderer, 255, 255, 255, 255);
    SDL_RenderClear(app.renderer);

    Page *p = top();
    const char *title = bar_title(p);
    if (app.transition && app.leaving) {
        float t = ease_out((SDL_GetTicks() - app.transition_at) / (float)ANIM_PUSH_MS);
        int dir = app.transition;
        int off = (int)(SCREEN_W * t);
        draw_page(app.leaving, dir > 0 ? -off : off);
        draw_page(p, dir > 0 ? SCREEN_W - off : off - SCREEN_W);
        draw_bar(title, (Uint8)(255 * t), bar_title(app.leaving), (Uint8)(255 * (1 - t)),
                 p && p->style == STYLE_COVERFLOW);
        wants_frame = true;
        if (t >= 1) {
            if (dir < 0)
                page_free(app.leaving);
            app.leaving = NULL;
            app.transition = 0;
        }
    } else {
        draw_page(p, 0);
        draw_bar(title, 255, NULL, 0, p && p->style == STYLE_COVERFLOW);
    }
    if (app.overlay)
        draw_overlay_page(app.overlay);
    if (app.sheet.active)
        draw_action_sheet();
    if (app.confirm.active)
        draw_confirm();
    if (app.keyboard.active)
        draw_keyboard();
    draw_letter();
    draw_hud();
    draw_toast();
    SDL_RenderPresent(app.renderer);
}

/* Milliseconds until the next frame is needed; -1 means sleep until an event. */
int next_frame_delay(void)
{
    if (!app.status.screen_on)
        return -1;
    if (wants_frame || app.transition)
        return 16;
    int delay = wants_slow_frame ? 40 : -1;
    Page *p = top();
    if (p && p->style == STYLE_LYRICS && !strcmp(app.status.player.state, "play"))
        delay = 120;
    if (p && p->style == STYLE_NOWPLAYING && !strcmp(app.status.player.state, "play")) {
        double e = app.status.player.elapsed + (SDL_GetTicks() - app.status.player.elapsed_at) / 1000.0;
        delay = (int)((1.0 - (e - (int)e)) * 1000) + 5;
        /* and the line of the words changes when its time comes, not on the second */
        for (int i = 0; i < p->count && i < p->time_count; i++) {
            if (p->times[i] > e + 0.25) {
                int ms = (int)((p->times[i] - 0.25 - e) * 1000) + 5;
                delay = ms < delay ? ms : delay;
                break;
            }
        }
    }
    if (app.keyboard.active)
        delay = delay < 0 || delay > 530 ? 530 - (int)(SDL_GetTicks() % 530) + 1 : delay;
    if (p && !strcmp(p->path, "/")) {
        /* the clock in the title turns over with the minute */
        int ms = (60 - (int)(time(NULL) % 60)) * 1000 + 30;
        delay = delay < 0 || delay > ms ? ms : delay;
    }
    return delay;
}

bool animating(void)
{
    return wants_frame || app.transition;
}
