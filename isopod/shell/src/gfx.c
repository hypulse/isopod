#include <SDL_image.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "shell.h"

#define TEXT_SLOTS 1024
#define IMAGE_SLOTS 64
#define ICON_SLOTS 64
#define ROUND_SLOTS 32

typedef struct {
    char *s;
    int weight, size, max_w;
    SDL_Texture *tex;
    int w, h;
    Uint32 used;
} TextEntry;

typedef struct {
    char *path;
    int size, radius;
    SDL_Texture *tex;
    Uint32 used;
} ImageEntry;

typedef struct {
    char name[48];
    SDL_Texture *tex;
} IconEntry;

typedef struct {
    int r, t;
    SDL_Texture *tex;
} RoundEntry;

static const char *weight_file[FONT_WEIGHTS] = {"Pretendard-Regular.otf", "Pretendard-Medium.otf",
                                                "Pretendard-SemiBold.otf", "Pretendard-Bold.otf"};
static TextEntry texts[TEXT_SLOTS];
static int text_count;
static ImageEntry images[IMAGE_SLOTS];
static IconEntry icons[ICON_SLOTS];
static RoundEntry rounds[ROUND_SLOTS];
static Uint32 frame;

static void set_color(Rgba c)
{
    SDL_SetRenderDrawColor(app.renderer, c.r, c.g, c.b, c.a);
}

static void tint(SDL_Texture *t, Rgba c)
{
    SDL_SetTextureColorMod(t, c.r, c.g, c.b);
    SDL_SetTextureAlphaMod(t, c.a);
}

bool gfx_init(void)
{
    SDL_SetRenderDrawBlendMode(app.renderer, SDL_BLENDMODE_BLEND);
    return font(FONT_REGULAR, FONT_ROW) != NULL;
}

void gfx_frame_begin(void)
{
    frame++;
    if (text_count < TEXT_SLOTS * 3 / 4)
        return;
    for (int i = 0; i < TEXT_SLOTS; i++) {
        if (texts[i].s && frame - texts[i].used > 90) {
            SDL_DestroyTexture(texts[i].tex);
            SDL_free(texts[i].s);
            texts[i] = (TextEntry){0};
            text_count--;
        }
    }
}

TTF_Font *font(int weight, int size)
{
    if (size <= 0 || size >= 64)
        return NULL;
    if (!app.fonts[weight][size]) {
        char path[768];
        snprintf(path, sizeof(path), "%s/%s", app.font_dir, weight_file[weight]);
        TTF_Font *f = TTF_OpenFont(path, size);
        if (!f && weight != FONT_REGULAR)
            return font(FONT_REGULAR, size);
        if (!f) {
            SDL_Log("font: %s", TTF_GetError());
            return NULL;
        }
        TTF_SetFontHinting(f, TTF_HINTING_LIGHT);
        app.fonts[weight][size] = f;
    }
    return app.fonts[weight][size];
}

void fill_rect(int x, int y, int w, int h, Rgba c)
{
    set_color(c);
    SDL_RenderFillRect(app.renderer, &(SDL_Rect){x, y, w, h});
}

void fill_gradient(int x, int y, int w, int h, Rgba top, Rgba bottom)
{
    SDL_Color a = {top.r, top.g, top.b, top.a}, b = {bottom.r, bottom.g, bottom.b, bottom.a};
    SDL_Vertex v[4] = {
        {{(float)x, (float)y}, a, {0, 0}},
        {{(float)(x + w), (float)y}, a, {0, 0}},
        {{(float)(x + w), (float)(y + h)}, b, {0, 0}},
        {{(float)x, (float)(y + h)}, b, {0, 0}},
    };
    int idx[6] = {0, 1, 2, 0, 2, 3};
    SDL_RenderGeometry(app.renderer, NULL, v, 4, idx, 6);
}

void fill_gradient_h(int x, int y, int w, int h, Rgba left, Rgba right)
{
    SDL_Color a = {left.r, left.g, left.b, left.a}, b = {right.r, right.g, right.b, right.a};
    SDL_Vertex v[4] = {
        {{(float)x, (float)y}, a, {0, 0}},
        {{(float)(x + w), (float)y}, b, {0, 0}},
        {{(float)(x + w), (float)(y + h)}, b, {0, 0}},
        {{(float)x, (float)(y + h)}, a, {0, 0}},
    };
    int idx[6] = {0, 1, 2, 0, 2, 3};
    SDL_RenderGeometry(app.renderer, NULL, v, 4, idx, 6);
}

/* A texture over a strip mesh: column i runs from top[i] to bottom[i] with texture
 * column u[i]. Many narrow strips keep perspective honest without a 3D pipeline. */
void draw_strips(SDL_Texture *t, const SDL_FPoint *top, const SDL_FPoint *bottom, const float *u, int n,
                 float v0, float v1, SDL_Color c0, SDL_Color c1)
{
    SDL_Vertex v[2 * 33];
    int idx[6 * 32], k = 0;
    if (n > 33)
        n = 33;
    for (int i = 0; i < n; i++) {
        v[2 * i] = (SDL_Vertex){top[i], c0, {u[i], v0}};
        v[2 * i + 1] = (SDL_Vertex){bottom[i], c1, {u[i], v1}};
    }
    for (int i = 0; i + 1 < n; i++) {
        int a = 2 * i, b = 2 * i + 1, c = 2 * i + 2, d = 2 * i + 3;
        int tri[6] = {a, c, d, a, d, b};
        for (int j = 0; j < 6; j++)
            idx[k++] = tri[j];
    }
    SDL_RenderGeometry(app.renderer, t, v, 2 * n, idx, k);
}

/* White disc (t == 0) or ring of thickness t, anti-aliased by pixel coverage. */
static SDL_Texture *round_tex(int r, int t)
{
    for (int i = 0; i < ROUND_SLOTS; i++)
        if (rounds[i].tex && rounds[i].r == r && rounds[i].t == t)
            return rounds[i].tex;
    int d = r * 2;
    SDL_Surface *s = SDL_CreateRGBSurfaceWithFormat(0, d, d, 32, SDL_PIXELFORMAT_ARGB8888);
    Uint32 *px = s->pixels;
    for (int y = 0; y < d; y++) {
        for (int x = 0; x < d; x++) {
            float dx = x + 0.5f - r, dy = y + 0.5f - r;
            float dist = sqrtf(dx * dx + dy * dy);
            float outer = fminf(fmaxf(r - dist + 0.5f, 0), 1);
            float inner = t ? fminf(fmaxf((r - t) - dist + 0.5f, 0), 1) : 0;
            Uint32 a = (Uint32)((outer - inner) * 255 + 0.5f);
            px[y * (s->pitch / 4) + x] = (a << 24) | 0xFFFFFF;
        }
    }
    SDL_Texture *tex = SDL_CreateTextureFromSurface(app.renderer, s);
    SDL_FreeSurface(s);
    SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);
    for (int i = 0; i < ROUND_SLOTS; i++) {
        if (!rounds[i].tex) {
            rounds[i] = (RoundEntry){r, t, tex};
            break;
        }
    }
    return tex;
}

static void corners(SDL_Texture *tex, int x, int y, int w, int h, int r)
{
    SDL_Rect q[4] = {{0, 0, r, r}, {r, 0, r, r}, {0, r, r, r}, {r, r, r, r}};
    SDL_Rect d[4] = {{x, y, r, r}, {x + w - r, y, r, r}, {x, y + h - r, r, r}, {x + w - r, y + h - r, r, r}};
    for (int i = 0; i < 4; i++)
        SDL_RenderCopy(app.renderer, tex, &q[i], &d[i]);
}

void fill_round(int x, int y, int w, int h, int r, Rgba c)
{
    if (r * 2 > h)
        r = h / 2;
    if (r * 2 > w)
        r = w / 2;
    if (r <= 0) {
        fill_rect(x, y, w, h, c);
        return;
    }
    SDL_Texture *tex = round_tex(r, 0);
    tint(tex, c);
    corners(tex, x, y, w, h, r);
    fill_rect(x + r, y, w - 2 * r, h, c);
    fill_rect(x, y + r, r, h - 2 * r, c);
    fill_rect(x + w - r, y + r, r, h - 2 * r, c);
}

void stroke_round(int x, int y, int w, int h, int r, int t, Rgba c)
{
    if (r * 2 > h)
        r = h / 2;
    SDL_Texture *tex = round_tex(r, t);
    tint(tex, c);
    corners(tex, x, y, w, h, r);
    fill_rect(x + r, y, w - 2 * r, t, c);
    fill_rect(x + r, y + h - t, w - 2 * r, t, c);
    fill_rect(x, y + r, t, h - 2 * r, c);
    fill_rect(x + w - t, y + r, t, h - 2 * r, c);
}

/* text ------------------------------------------------------------------ */

static Uint32 hash_key(const char *s, int weight, int size, int max_w)
{
    Uint32 h = 2166136261u ^ (Uint32)(weight * 131 + size * 7 + max_w * 31);
    for (; *s; s++)
        h = (h ^ (unsigned char)*s) * 16777619u;
    return h;
}

/* Longest prefix (on a character boundary) that fits with a trailing ellipsis. */
static char *ellipsize(TTF_Font *f, const char *s, int max_w)
{
    int w = 0;
    TTF_SizeUTF8(f, s, &w, NULL);
    if (w <= max_w)
        return SDL_strdup(s);
    int len = (int)strlen(s), chars = 0;
    int *cut = SDL_malloc(((size_t)len + 1) * sizeof(int));
    for (int i = 0; i <= len; i++)
        if (i == len || ((unsigned char)s[i] & 0xC0) != 0x80)
            cut[chars++] = i;
    char *buf = SDL_malloc((size_t)len + 4);
    int lo = 0, hi = chars - 1;
    while (lo < hi) {
        int mid = (lo + hi + 1) / 2;
        memcpy(buf, s, (size_t)cut[mid]);
        memcpy(buf + cut[mid], "…", 4);
        TTF_SizeUTF8(f, buf, &w, NULL);
        if (w <= max_w)
            lo = mid;
        else
            hi = mid - 1;
    }
    int end = cut[lo];
    while (end > 0 && s[end - 1] == ' ')
        end--;
    memcpy(buf, s, (size_t)end);
    memcpy(buf + end, "…", 4);
    SDL_free(cut);
    return buf;
}

static TextEntry *text_entry(int weight, int size, const char *s, int max_w)
{
    if (!s || !*s)
        return NULL;
    Uint32 h = hash_key(s, weight, size, max_w);
    for (int probe = 0; probe < TEXT_SLOTS; probe++) {
        TextEntry *e = &texts[(h + probe) % TEXT_SLOTS];
        if (!e->s)
            break;
        if (e->weight == weight && e->size == size && e->max_w == max_w && !strcmp(e->s, s)) {
            e->used = frame;
            return e;
        }
    }
    TTF_Font *f = font(weight, size);
    if (!f)
        return NULL;
    char *shown = max_w > 0 ? ellipsize(f, s, max_w) : SDL_strdup(s);
    SDL_Surface *surf = TTF_RenderUTF8_Blended(f, shown, (SDL_Color){255, 255, 255, 255});
    SDL_free(shown);
    if (!surf)
        return NULL;
    SDL_Texture *tex = SDL_CreateTextureFromSurface(app.renderer, surf);
    int w = surf->w, hh = surf->h;
    SDL_FreeSurface(surf);
    for (int probe = 0; probe < TEXT_SLOTS; probe++) {
        TextEntry *e = &texts[(h + probe) % TEXT_SLOTS];
        if (!e->s) {
            *e = (TextEntry){SDL_strdup(s), weight, size, max_w, tex, w, hh, frame};
            text_count++;
            return e;
        }
    }
    SDL_DestroyTexture(tex);
    return NULL;
}

int text_width(int weight, int size, const char *s)
{
    TextEntry *e = text_entry(weight, size, s, 0);
    return e ? e->w : 0;
}

bool text_overflows(int weight, int size, const char *s, int max_w)
{
    return text_width(weight, size, s) > max_w;
}

int draw_text(int weight, int size, const char *s, int x, int y, Rgba c, int max_w)
{
    TextEntry *e = text_entry(weight, size, s, max_w);
    if (!e)
        return 0;
    tint(e->tex, c);
    SDL_RenderCopy(app.renderer, e->tex, NULL, &(SDL_Rect){x, y, e->w, e->h});
    return e->w;
}

int draw_text_right(int weight, int size, const char *s, int right, int y, Rgba c)
{
    TextEntry *e = text_entry(weight, size, s, 0);
    if (!e)
        return 0;
    tint(e->tex, c);
    SDL_RenderCopy(app.renderer, e->tex, NULL, &(SDL_Rect){right - e->w, y, e->w, e->h});
    return e->w;
}

void draw_text_center(int weight, int size, const char *s, int cx, int y, Rgba c, int max_w)
{
    TextEntry *e = text_entry(weight, size, s, max_w);
    if (!e)
        return;
    tint(e->tex, c);
    SDL_RenderCopy(app.renderer, e->tex, NULL, &(SDL_Rect){cx - e->w / 2, y, e->w, e->h});
}

/* Greedy word wrap that also breaks inside long words (Hangul has no spaces to rely on). */
static int wrap_text(int weight, int size, const char *s, int x, int y, int w, int line_h, int max_lines,
                     Rgba c, bool center, bool draw)
{
    TTF_Font *f = font(weight, size);
    if (!f || !s || !*s)
        return 0;
    int lines = 0, len = (int)strlen(s), start = 0;
    char *buf = SDL_malloc((size_t)len + 1);
    while (start < len && lines < max_lines) {
        while (s[start] == ' ')
            start++;
        int best = start, last_space = -1, i = start;
        bool newline = false;
        while (i < len) {
            if (s[i] == '\n') {
                newline = true;
                break;
            }
            int next = i + 1;
            while (next < len && ((unsigned char)s[next] & 0xC0) == 0x80)
                next++;
            memcpy(buf, s + start, (size_t)(next - start));
            buf[next - start] = 0;
            int tw = 0;
            TTF_SizeUTF8(f, buf, &tw, NULL);
            if (tw > w)
                break;
            if (s[i] == ' ')
                last_space = i;
            best = next;
            i = next;
        }
        int end = newline ? i : best;
        if (!newline && end < len && last_space > start)
            end = last_space;
        if (end <= start && !newline)
            end = best > start ? best : start + 1;
        if (lines == max_lines - 1 && end < len) {
            if (draw)
                draw_text(weight, size, s + start, center ? x - w / 2 : x, y + lines * line_h, c, w);
            lines++;
            break;
        }
        memcpy(buf, s + start, (size_t)(end - start));
        buf[end - start] = 0;
        int lx = x;
        if (center) {
            int tw = 0;
            TTF_SizeUTF8(f, buf, &tw, NULL);
            lx = x - tw / 2;
        }
        if (draw)
            draw_text(weight, size, buf, lx, y + lines * line_h, c, 0);
        lines++;
        start = newline ? end + 1 : end;
    }
    SDL_free(buf);
    return lines;
}

int draw_text_wrap(int weight, int size, const char *s, int x, int y, int w, int line_h, int max_lines,
                   Rgba c)
{
    return wrap_text(weight, size, s, x, y, w, line_h, max_lines, c, false, true);
}

int wrap_count(int weight, int size, const char *s, int w, int max_lines)
{
    return wrap_text(weight, size, s, 0, 0, w, 0, max_lines, RGB(0, 0, 0), false, false);
}

int draw_text_wrap_center(int weight, int size, const char *s, int cx, int y, int w, int line_h,
                          int max_lines, Rgba c)
{
    return wrap_text(weight, size, s, cx, y, w, line_h, max_lines, c, true, true);
}

void draw_marquee(int weight, int size, const char *s, int x, int y, int w, Rgba c, Uint32 since)
{
    TextEntry *e = text_entry(weight, size, s, 0);
    if (!e)
        return;
    const int gap = 60, pause_ms = 1100;
    int travel = e->w + gap;
    Uint32 cycle = (Uint32)(pause_ms + travel * 1000 / 55);
    Uint32 t = (SDL_GetTicks() - since) % cycle;
    int off = t < (Uint32)pause_ms ? 0 : (int)((t - pause_ms) * 55 / 1000);
    SDL_Rect clip = {x, y - 4, w, e->h + 8};
    SDL_RenderSetClipRect(app.renderer, &clip);
    tint(e->tex, c);
    SDL_RenderCopy(app.renderer, e->tex, NULL, &(SDL_Rect){x - off, y, e->w, e->h});
    if (off > 0)
        SDL_RenderCopy(app.renderer, e->tex, NULL, &(SDL_Rect){x - off + travel, y, e->w, e->h});
    SDL_RenderSetClipRect(app.renderer, NULL);
}

/* icons and images -------------------------------------------------------- */

SDL_Texture *icon(const char *name)
{
    for (int i = 0; i < ICON_SLOTS; i++)
        if (icons[i].tex && !strcmp(icons[i].name, name))
            return icons[i].tex;
    char path[768];
    snprintf(path, sizeof(path), "%s/icons/%s.png", app.asset_dir, name);
    SDL_Texture *tex = IMG_LoadTexture(app.renderer, path);
    if (!tex)
        return NULL;
    SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);
    for (int i = 0; i < ICON_SLOTS; i++) {
        if (!icons[i].tex) {
            SDL_strlcpy(icons[i].name, name, sizeof(icons[i].name));
            icons[i].tex = tex;
            break;
        }
    }
    return tex;
}

void draw_icon(const char *name, int x, int y, Rgba c)
{
    SDL_Texture *t = icon(name);
    if (!t)
        return;
    int w, h;
    SDL_QueryTexture(t, NULL, NULL, &w, &h);
    tint(t, c);
    SDL_RenderCopy(app.renderer, t, NULL, &(SDL_Rect){x, y, w, h});
}

void draw_icon_rot(const char *name, int x, int y, double angle, Rgba c)
{
    SDL_Texture *t = icon(name);
    if (!t)
        return;
    int w, h;
    SDL_QueryTexture(t, NULL, NULL, &w, &h);
    tint(t, c);
    SDL_RenderCopyEx(app.renderer, t, NULL, &(SDL_Rect){x, y, w, h}, angle, NULL, SDL_FLIP_NONE);
}

/* Center-crop to a square and box-filter down; keeps covers sharp without mip-maps.
 * A radius rounds the corners with anti-aliased alpha. */
static SDL_Surface *square_thumb(SDL_Surface *src, int size, int radius)
{
    SDL_Surface *s = SDL_ConvertSurfaceFormat(src, SDL_PIXELFORMAT_ARGB8888, 0);
    if (!s)
        return NULL;
    int side = s->w < s->h ? s->w : s->h;
    int ox = (s->w - side) / 2, oy = (s->h - side) / 2;
    SDL_Surface *out = SDL_CreateRGBSurfaceWithFormat(0, size, size, 32, SDL_PIXELFORMAT_ARGB8888);
    Uint32 *dst = out->pixels;
    const Uint8 *base = s->pixels;
    for (int y = 0; y < size; y++) {
        int y0 = oy + y * side / size, y1 = oy + (y + 1) * side / size;
        if (y1 <= y0)
            y1 = y0 + 1;
        for (int x = 0; x < size; x++) {
            int x0 = ox + x * side / size, x1 = ox + (x + 1) * side / size;
            if (x1 <= x0)
                x1 = x0 + 1;
            Uint32 acc[4] = {0}, n = 0;
            for (int yy = y0; yy < y1; yy++) {
                const Uint32 *row = (const Uint32 *)(base + yy * s->pitch);
                for (int xx = x0; xx < x1; xx++, n++) {
                    Uint32 p = row[xx];
                    acc[0] += p >> 24;
                    acc[1] += (p >> 16) & 255;
                    acc[2] += (p >> 8) & 255;
                    acc[3] += p & 255;
                }
            }
            Uint32 alpha = acc[0] / n;
            if (radius > 0) {
                float cx = x < radius ? radius : x >= size - radius ? size - radius : x + 0.5f;
                float cy = y < radius ? radius : y >= size - radius ? size - radius : y + 0.5f;
                float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
                float cover = fminf(fmaxf(radius - sqrtf(dx * dx + dy * dy) + 0.5f, 0), 1);
                alpha = (Uint32)(alpha * cover + 0.5f);
            }
            dst[y * (out->pitch / 4) + x] =
                (alpha << 24) | ((acc[1] / n) << 16) | ((acc[2] / n) << 8) | (acc[3] / n);
        }
    }
    SDL_FreeSurface(s);
    return out;
}

SDL_Texture *image(const char *path, int size)
{
    return image_r(path, size, 0);
}

SDL_Texture *image_r(const char *path, int size, int radius)
{
    if (!path || !*path)
        return NULL;
    for (int i = 0; i < IMAGE_SLOTS; i++) {
        if (images[i].path && images[i].size == size && images[i].radius == radius &&
            !strcmp(images[i].path, path)) {
            images[i].used = frame;
            return images[i].tex;
        }
    }
    ImageEntry *slot = &images[0];
    for (int i = 0; i < IMAGE_SLOTS && slot->path; i++)
        if (!images[i].path || images[i].used < slot->used)
            slot = &images[i];
    SDL_Surface *raw = IMG_Load(path);
    SDL_Texture *tex = NULL;
    if (raw) {
        SDL_Surface *thumb = square_thumb(raw, size, radius);
        SDL_FreeSurface(raw);
        if (thumb) {
            tex = SDL_CreateTextureFromSurface(app.renderer, thumb);
            SDL_FreeSurface(thumb);
            if (tex)
                SDL_SetTextureBlendMode(tex, SDL_BLENDMODE_BLEND);
        }
    }
    if (slot->path) {
        SDL_free(slot->path);
        if (slot->tex)
            SDL_DestroyTexture(slot->tex);
    }
    *slot = (ImageEntry){SDL_strdup(path), size, radius, tex, frame};
    return tex;
}

SDL_Texture *image_or(const char *path, const char *fallback, int size, int radius)
{
    SDL_Texture *t = image_r(path, size, radius);
    if (!t && fallback) {
        char p[768];
        snprintf(p, sizeof(p), "%s/%s.png", app.asset_dir, fallback);
        t = image_r(p, size, radius);
    }
    return t;
}

void draw_image(const char *path, const char *fallback, int x, int y, int size, int radius)
{
    SDL_Texture *t = image_or(path, fallback, size, radius);
    if (t) {
        SDL_SetTextureColorMod(t, 255, 255, 255);
        SDL_SetTextureAlphaMod(t, 255);
        SDL_RenderCopy(app.renderer, t, NULL, &(SDL_Rect){x, y, size, size});
    }
}

void draw_spinner(int cx, int cy, Rgba c)
{
    SDL_Texture *t = icon("spinner");
    if (!t)
        return;
    int w, h;
    SDL_QueryTexture(t, NULL, NULL, &w, &h);
    double angle = (SDL_GetTicks() / 83) % 12 * 30.0;
    draw_icon_rot("spinner", cx - w / 2, cy - h / 2, angle, c);
}

void draw_battery(int x, int y, int percent, bool charging, Rgba outline)
{
    draw_icon("battery", x, y, outline);
    int inner = (int)(26 * (percent < 0 ? 0 : percent > 100 ? 100 : percent) / 100.0 + 0.5);
    Rgba fill = percent <= 20 && !charging ? C_RED : C_GREEN;
    if (inner > 0)
        fill_round(x + 4, y + 4, inner, 10, 2, fill);
    if (charging)
        draw_icon("bolt", x + 10, y + 1, RGB(255, 204, 0));
}

bool save_screenshot(const char *path)
{
    int w, h;
    SDL_GetRendererOutputSize(app.renderer, &w, &h);
    SDL_Surface *s = SDL_CreateRGBSurfaceWithFormat(0, w, h, 32, SDL_PIXELFORMAT_ARGB8888);
    if (!s)
        return false;
    SDL_RenderReadPixels(app.renderer, NULL, SDL_PIXELFORMAT_ARGB8888, s->pixels, s->pitch);
    bool ok = IMG_SavePNG(s, path) == 0;
    SDL_FreeSurface(s);
    return ok;
}
