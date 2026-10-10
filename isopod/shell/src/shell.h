#ifndef ISOPOD_SHELL_H
#define ISOPOD_SHELL_H

#include <SDL.h>
#include <SDL_ttf.h>
#include <stdbool.h>

#include "cJSON.h"
#include "theme.h"

enum { ACC_NONE, ACC_CHEVRON, ACC_CHECK, ACC_SWITCH, ACC_SPINNER, ACC_PLAYING, ACC_STAR };
enum { STYLE_LIST, STYLE_NOWPLAYING, STYLE_SLIDER, STYLE_ABOUT, STYLE_COVERFLOW, STYLE_LYRICS };
enum { FONT_REGULAR, FONT_MEDIUM, FONT_SEMIBOLD, FONT_BOLD, FONT_WEIGHTS };

typedef struct {
    char *key, *title, *subtitle, *value, *art, *icon, *preview, *link;
    int accessory;
    int signal;
    bool on, header, lock, spinner;
    char letter[8];
} Item;

typedef struct Page {
    char *path, *title;
    int style;
    Item *items;
    int count;
    bool loaded, loading_shown, tall, art_rows, live, index, sheet, split;
    char *empty_title, *empty_text, *empty_icon;
    int value, min, max, step;
    int sel;
    float scroll, scroll_from, scroll_to;
    Uint32 scroll_at;
    Uint32 opened_at, marquee_at;
    int req;
    int viewport;
    /* split menus: the artwork panel and what it shows */
    char **art;
    int art_count;
    char *shown, *prev_shown;
    Uint32 shown_at;
    /* cover flow: the position that glides after the selection, and the flipped album */
    float cf_pos;
    Uint32 cf_at;
    struct Page *card;
    Uint32 flip_at;
    bool closing;
    /* lyrics: seconds for each line when they are synced */
    float *times;
    int time_count;
    float ly_scroll;
    Uint32 ly_at;
} Page;

typedef struct {
    char *key, *title, *value;
    bool destructive;
} SheetItem;

typedef struct {
    bool active;
    char *title, *token;
    SheetItem items[8];
    int count, sel;
    Uint32 at;
} Sheet;

typedef struct {
    bool active;
    char *title, *ok, *token;
    bool destructive;
    int choice;
    Uint32 at;
} Confirm;

typedef struct {
    bool active;
    char *title, *placeholder, *token;
    bool secret, shift, symbols, hangul;
    char base[256], text[256];
    int cho, jung, jong;
    int row, col;
    Uint32 at;
} Keyboard;

typedef struct {
    char state[8], kind[8];
    char *title, *artist, *album, *art, *file;
    double elapsed, duration;
    Uint32 elapsed_at;
    int pos, count;
    bool shuffle, repeat, single, buffering;
} Player;

typedef struct {
    Player player;
    int volume;
    char output[16];
    int battery;
    bool charging;
    char *bt_audio;
    bool screen_on;
} Status;

typedef struct {
    SDL_Window *window;
    SDL_Renderer *renderer;
    TTF_Font *fonts[FONT_WEIGHTS][64];
    char font_dir[512], asset_dir[512];

    Page *stack[32];
    int depth;
    Page *overlay;
    int transition;
    Uint32 transition_at;
    Page *leaving;

    Sheet sheet;
    Confirm confirm;
    Keyboard keyboard;
    Status status;
    cJSON *strings;

    char toast[256], toast_icon[32];
    Uint32 toast_at;
    int hud_value;
    char hud_kind[16];
    Uint32 hud_at;
    char letter[8];
    Uint32 letter_at;

    bool connected, focused, dirty, running;
    Uint32 wake_guard;
    int pending;
} App;

extern App app;

/* util.c */
char *xstrdup(const char *s);
void xfree(char **p);
char *json_str(cJSON *obj, const char *key);
int json_int(cJSON *obj, const char *key, int fallback);
double json_num(cJSON *obj, const char *key, double fallback);
bool json_bool(cJSON *obj, const char *key);
const char *S(const char *key, const char *fallback);
void fmt_time(double seconds, char *out, size_t n);
void index_letter(const char *text, char out[8]);
float ease_out(float t);

/* ipc.c */
typedef void (*ReplyFn)(cJSON *reply, void *ctx);
void ipc_start(const char *path);
int ipc_request(cJSON *req, ReplyFn fn, void *ctx);
void ipc_handle_line(char *line);
void on_ipc_event(cJSON *msg);
void on_ipc_connected(void);
Uint32 ipc_event_type(void);
void ipc_dispatch(SDL_Event *ev);

/* gfx.c */
bool gfx_init(void);
void gfx_frame_begin(void);
TTF_Font *font(int weight, int size);
void fill_rect(int x, int y, int w, int h, Rgba c);
void fill_gradient(int x, int y, int w, int h, Rgba top, Rgba bottom);
void fill_round(int x, int y, int w, int h, int r, Rgba c);
void stroke_round(int x, int y, int w, int h, int r, int t, Rgba c);
int text_width(int weight, int size, const char *s);
int draw_text(int weight, int size, const char *s, int x, int y, Rgba c, int max_w);
int draw_text_right(int weight, int size, const char *s, int right, int y, Rgba c);
void draw_text_center(int weight, int size, const char *s, int cx, int y, Rgba c, int max_w);
int draw_text_wrap(int weight, int size, const char *s, int x, int y, int w, int line_h, int max_lines, Rgba c);
int draw_text_wrap_center(int weight, int size, const char *s, int cx, int y, int w, int line_h,
                          int max_lines, Rgba c);
int wrap_count(int weight, int size, const char *s, int w, int max_lines);
void draw_marquee(int weight, int size, const char *s, int x, int y, int w, Rgba c, Uint32 since);
bool text_overflows(int weight, int size, const char *s, int max_w);
SDL_Texture *icon(const char *name);
void draw_icon(const char *name, int x, int y, Rgba tint);
void draw_icon_rot(const char *name, int x, int y, double angle, Rgba tint);
SDL_Texture *image(const char *path, int size);
SDL_Texture *image_r(const char *path, int size, int radius);
SDL_Texture *image_or(const char *path, const char *fallback, int size, int radius);
void draw_image(const char *path, const char *fallback, int x, int y, int size, int radius);
void fill_gradient_h(int x, int y, int w, int h, Rgba left, Rgba right);
void draw_strips(SDL_Texture *t, const SDL_FPoint *top, const SDL_FPoint *bottom, const float *u, int n,
                 float v0, float v1, SDL_Color c0, SDL_Color c1);
void draw_spinner(int cx, int cy, Rgba tint);
void draw_battery(int x, int y, int percent, bool charging, Rgba outline);
bool save_screenshot(const char *path);

/* views.c */
void render(void);
bool animating(void);
int next_frame_delay(void);
int kb_row_len(int row);
const char *kb_label(int row, int col, char *buf);
int kb_jamo(int row, int col);

/* hangul.c */
bool hangul_is_jamo(int cp);
bool hangul_is_vowel(int cp);
int hangul_utf8(int cp, char out[4]);
void hangul_input(Keyboard *k, int cp);
void hangul_commit(Keyboard *k);
void kb_sync(Keyboard *k);
void kb_backspace(Keyboard *k);
void kb_append(Keyboard *k, const char *s);
int row_height(Page *p, int i);
int list_height(Page *p);
int item_y(Page *p, int i);

/* nav.c */
Page *top(void);
void nav_push(const char *path, const char *title);
void nav_pop(void);
void nav_home(void);
void nav_reload(Page *p);
void nav_reload_path(const char *path);
void nav_key(const char *key, bool repeat);
void nav_apply(cJSON *action, const char *title_hint);
void page_free(Page *p);
void status_update(cJSON *state);
void on_hello(cJSON *reply, void *ctx);
void show_toast(const char *text, const char *icon);
void show_hud(const char *kind, int value);

#endif
