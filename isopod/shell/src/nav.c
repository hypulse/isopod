#include <stdio.h>
#include <string.h>

#include "shell.h"

/* pages --------------------------------------------------------------------- */

static Page *page_new(const char *path, const char *title)
{
    Page *p = SDL_calloc(1, sizeof(Page));
    p->path = xstrdup(path);
    p->title = xstrdup(title ? title : "");
    p->opened_at = p->marquee_at = SDL_GetTicks();
    p->max = 100;
    p->step = 5;
    return p;
}

static void items_free(Page *p)
{
    for (int i = 0; i < p->count; i++) {
        Item *it = &p->items[i];
        SDL_free(it->key);
        SDL_free(it->title);
        SDL_free(it->subtitle);
        SDL_free(it->value);
        SDL_free(it->art);
        SDL_free(it->icon);
        SDL_free(it->preview);
        SDL_free(it->link);
    }
    SDL_free(p->items);
    p->items = NULL;
    p->count = 0;
}

static void art_free(Page *p)
{
    for (int i = 0; i < p->art_count; i++)
        SDL_free(p->art[i]);
    SDL_free(p->art);
    p->art = NULL;
    p->art_count = 0;
}

void page_free(Page *p)
{
    if (!p)
        return;
    page_free(p->card);
    SDL_free(p->times);
    items_free(p);
    art_free(p);
    SDL_free(p->shown);
    SDL_free(p->prev_shown);
    SDL_free(p->path);
    SDL_free(p->title);
    SDL_free(p->empty_title);
    SDL_free(p->empty_text);
    SDL_free(p->empty_icon);
    SDL_free(p->note);
    SDL_free(p);
}

Page *top(void)
{
    return app.depth ? app.stack[app.depth - 1] : NULL;
}

static Page *active_page(void)
{
    return app.overlay ? app.overlay : top();
}

static bool page_alive(Page *p)
{
    if (p == app.overlay || p == app.leaving)
        return true;
    for (int i = 0; i < app.depth; i++)
        if (app.stack[i] == p || app.stack[i]->card == p)
            return true;
    return false;
}

static int viewport_h(Page *p)
{
    if (p->viewport > 0)
        return p->viewport;
    if (p->sheet) {
        int rows = list_height(p);
        return (rows > 330 ? 330 : rows) + 2;
    }
    return SCREEN_H - BAR_H;
}

static bool selectable(Page *p, int i)
{
    return i >= 0 && i < p->count && !p->items[i].header;
}

static void scroll_to(Page *p, float target, bool animate)
{
    int max = list_height(p) - viewport_h(p);
    if (target > max)
        target = (float)max;
    if (target < 0)
        target = 0;
    float now = p->scroll_to;
    Uint32 t = SDL_GetTicks();
    if (animate && t - p->scroll_at < ANIM_SCROLL_MS) {
        float k = ease_out((t - p->scroll_at) / (float)ANIM_SCROLL_MS);
        now = p->scroll_from + (p->scroll_to - p->scroll_from) * k;
    }
    p->scroll_from = animate ? now : target;
    p->scroll_to = target;
    p->scroll_at = animate ? t : 0;
}

static void ensure_visible(Page *p, bool animate)
{
    if (!p->count)
        return;
    int y = item_y(p, p->sel), h = row_height(p, p->sel), vh = viewport_h(p);
    if (p->sel > 0 && p->items[p->sel - 1].header && y - HEADER_H < p->scroll_to)
        y -= HEADER_H;
    float target = p->scroll_to;
    if (y < target)
        target = (float)y;
    else if (y + h > target + vh)
        target = (float)(y + h - vh);
    if (target != p->scroll_to)
        scroll_to(p, target, animate);
}

static int accessory_of(const char *s)
{
    if (!s)
        return ACC_NONE;
    if (!strcmp(s, "chevron"))
        return ACC_CHEVRON;
    if (!strcmp(s, "check"))
        return ACC_CHECK;
    if (!strcmp(s, "switch"))
        return ACC_SWITCH;
    if (!strcmp(s, "spinner"))
        return ACC_SPINNER;
    if (!strcmp(s, "playing"))
        return ACC_PLAYING;
    return ACC_NONE;
}

static void page_fill(Page *p, cJSON *pg)
{
    char *keep = selectable(p, p->sel) && p->loaded ? xstrdup(p->items[p->sel].key) : NULL;
    bool first = !p->loaded;
    items_free(p);
    char *title = json_str(pg, "title");
    if (title) {
        SDL_free(p->title);
        p->title = title;
    }
    char *style = json_str(pg, "style");
    p->style = !style ? STYLE_LIST
             : !strcmp(style, "nowplaying") ? STYLE_NOWPLAYING
             : !strcmp(style, "slider")     ? STYLE_SLIDER
             : !strcmp(style, "about")      ? STYLE_ABOUT
             : !strcmp(style, "coverflow")  ? STYLE_COVERFLOW
             : !strcmp(style, "lyrics")     ? STYLE_LYRICS
                                            : STYLE_LIST;
    SDL_free(style);
    SDL_free(p->times);
    p->times = NULL;
    p->time_count = 0;
    cJSON *times = cJSON_GetObjectItemCaseSensitive(pg, "times"), *tm;
    int nt = cJSON_GetArraySize(times);
    if (nt > 0) {
        p->times = SDL_calloc((size_t)nt, sizeof(float));
        cJSON_ArrayForEach(tm, times)
        {
            p->times[p->time_count++] = cJSON_IsNumber(tm) ? (float)tm->valuedouble : 0;
        }
    }
    char *layout = json_str(pg, "layout");
    p->split = layout && !strcmp(layout, "split");
    SDL_free(layout);
    art_free(p);
    cJSON *art = cJSON_GetObjectItemCaseSensitive(pg, "art"), *a;
    int na = cJSON_GetArraySize(art);
    if (na > 0) {
        p->art = SDL_calloc((size_t)na, sizeof(char *));
        cJSON_ArrayForEach(a, art)
        {
            if (cJSON_IsString(a))
                p->art[p->art_count++] = xstrdup(a->valuestring);
        }
    }
    char *rows = json_str(pg, "rows");
    p->art_rows = rows && !strcmp(rows, "art");
    p->tall = rows && (!strcmp(rows, "tall") || p->art_rows);
    SDL_free(rows);
    char *pres = json_str(pg, "presentation");
    p->sheet = pres && !strcmp(pres, "sheet");
    SDL_free(pres);
    p->live = json_bool(pg, "live");
    p->index = json_bool(pg, "index");
    p->value = json_int(pg, "value", p->value);
    p->min = json_int(pg, "min", 0);
    p->max = json_int(pg, "max", 100);
    p->step = json_int(pg, "step", 5);
    xfree(&p->empty_title);
    xfree(&p->empty_text);
    xfree(&p->empty_icon);
    xfree(&p->note);
    p->note = json_str(pg, "note");
    cJSON *empty = cJSON_GetObjectItemCaseSensitive(pg, "empty");
    if (empty) {
        p->empty_title = json_str(empty, "title");
        p->empty_text = json_str(empty, "text");
        p->empty_icon = json_str(empty, "icon");
    }

    cJSON *items = cJSON_GetObjectItemCaseSensitive(pg, "items");
    int n = cJSON_GetArraySize(items);
    p->items = SDL_calloc(n ? (size_t)n : 1, sizeof(Item));
    cJSON *it;
    cJSON_ArrayForEach(it, items)
    {
        Item *d = &p->items[p->count++];
        d->key = json_str(it, "key");
        d->title = json_str(it, "title");
        d->subtitle = json_str(it, "subtitle");
        d->value = json_str(it, "value");
        d->art = json_str(it, "art");
        d->icon = json_str(it, "icon");
        d->preview = json_str(it, "preview");
        d->link = json_str(it, "link");
        char *acc = json_str(it, "accessory");
        d->accessory = accessory_of(acc);
        SDL_free(acc);
        d->on = json_bool(it, "on");
        d->lock = json_bool(it, "lock");
        d->star = json_bool(it, "star");
        d->header = json_bool(it, "header");
        d->spinner = json_bool(it, "spinner");
        d->signal = json_int(it, "signal", -1);
        if (p->index)
            index_letter(d->title, d->letter);
    }
    p->loaded = true;

    int sel = -1;
    if (keep) {
        for (int i = 0; i < p->count; i++)
            if (p->items[i].key && !strcmp(p->items[i].key, keep))
                sel = i;
        SDL_free(keep);
    }
    /* A list of choices opens on the current choice. */
    if (sel < 0 && first && !cJSON_HasObjectItem(pg, "selected"))
        for (int i = 0; i < p->count && sel < 0; i++)
            if (p->items[i].accessory == ACC_CHECK)
                sel = i;
    if (sel < 0)
        sel = first ? json_int(pg, "selected", 0) : p->sel;
    if (sel >= p->count)
        sel = p->count - 1;
    if (sel < 0)
        sel = 0;
    while (sel < p->count - 1 && !selectable(p, sel))
        sel++;
    p->sel = sel;
    if (first && p->style == STYLE_COVERFLOW)
        p->cf_pos = (float)sel;
    ensure_visible(p, false);
    if (first && p->sheet && p == top()) {
        app.depth--;
        app.transition = 0;
        app.leaving = NULL;
        page_free(app.overlay);
        app.overlay = p;
        p->opened_at = SDL_GetTicks();
    }
}

static void on_page(cJSON *reply, void *ctx)
{
    Page *p = ctx;
    cJSON *id = cJSON_GetObjectItemCaseSensitive(reply, "id");
    if (!page_alive(p) || !cJSON_IsNumber(id) || (int)id->valuedouble != p->req)
        return;
    cJSON *pg = cJSON_GetObjectItemCaseSensitive(reply, "page");
    if (pg)
        page_fill(p, pg);
    app.dirty = true;
}

static void page_request(Page *p)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "page");
    cJSON_AddStringToObject(req, "path", p->path);
    p->req = ipc_request(req, on_page, p);
}

static void send_simple(const char *op, const char *path, const char *key)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", op);
    if (path)
        cJSON_AddStringToObject(req, "path", path);
    if (key)
        cJSON_AddStringToObject(req, "key", key);
    ipc_request(req, NULL, NULL);
}

/* navigation ---------------------------------------------------------------- */

static void begin_transition(int dir, Page *leaving)
{
    if (app.leaving && app.transition < 0)
        page_free(app.leaving);
    app.leaving = leaving;
    app.transition = leaving ? dir : 0;
    app.transition_at = SDL_GetTicks();
}

void nav_push(const char *path, const char *title)
{
    if (app.depth >= (int)SDL_arraysize(app.stack))
        return;
    Page *prev = top();
    if (prev && prev->card) {
        page_free(prev->card);
        prev->card = NULL;
    }
    Page *p = page_new(path, title);
    if (!strcmp(path, "/nowplaying")) {
        /* The player's state draws it at once; the song's details and words follow. */
        p->style = STYLE_NOWPLAYING;
        p->loaded = true;
        SDL_free(p->title);
        p->title = xstrdup(S("now_playing", "Now Playing"));
    }
    page_request(p);
    app.stack[app.depth++] = p;
    begin_transition(1, prev);
    app.dirty = true;
}

static void close_overlay(void)
{
    if (!app.overlay)
        return;
    send_simple("leave", app.overlay->path, NULL);
    page_free(app.overlay);
    app.overlay = NULL;
    app.dirty = true;
}

static bool reload_on_return(Page *p)
{
    return p && (!strcmp(p->path, "/") || !SDL_strncmp(p->path, "/settings", 9) ||
                 !strcmp(p->path, "/music/queue"));
}

void nav_pop(void)
{
    if (app.overlay) {
        close_overlay();
        return;
    }
    if (app.depth <= 1)
        return;
    Page *leaving = app.stack[--app.depth];
    send_simple("leave", leaving->path, NULL);
    begin_transition(-1, leaving);
    if (reload_on_return(top()))
        nav_reload(top());
    app.dirty = true;
}

void nav_home(void)
{
    close_overlay();
    app.sheet.active = app.keyboard.active = false;
    if (app.depth <= 1)
        return;
    Page *leaving = app.stack[--app.depth];
    while (app.depth > 1)
        page_free(app.stack[--app.depth]);
    begin_transition(-1, leaving);
    nav_reload(top());
}

void nav_reload(Page *p)
{
    if (p)
        page_request(p);
}

/* A pattern ending in "/" "*" names that page and everything below it; others match exactly. */
static bool path_matches(const char *pattern, const char *path)
{
    size_t n = strlen(pattern);
    if (n >= 2 && !strcmp(pattern + n - 2, "/*"))
        return !strncmp(path, pattern, n - 2) && (path[n - 2] == '\0' || path[n - 2] == '/');
    return !strcmp(pattern, path);
}

void nav_reload_path(const char *path)
{
    for (int i = 0; i < app.depth; i++) {
        Page *card = app.stack[i]->card;
        if (path_matches(path, app.stack[i]->path) && app.stack[i]->loaded)
            nav_reload(app.stack[i]);
        if (card && card->loaded && path_matches(path, card->path))
            nav_reload(card);
    }
    if (app.overlay && path_matches(path, app.overlay->path))
        nav_reload(app.overlay);
}

/* actions ------------------------------------------------------------------- */

static void on_do(cJSON *reply, void *ctx)
{
    char *hint = ctx;
    cJSON *act = cJSON_GetObjectItemCaseSensitive(reply, "do");
    if (act)
        nav_apply(act, hint);
    SDL_free(hint);
    app.dirty = true;
}

static void request_do(const char *op, const char *path, const char *key, const char *hint)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", op);
    cJSON_AddStringToObject(req, "path", path);
    if (key)
        cJSON_AddStringToObject(req, "key", key);
    ipc_request(req, on_do, xstrdup(hint));
}

static void resolve(const char *token, const char *value)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "resolve");
    cJSON_AddStringToObject(req, "token", token);
    if (value)
        cJSON_AddStringToObject(req, "value", value);
    ipc_request(req, on_do, NULL);
}

static void sheet_clear(void)
{
    Sheet *s = &app.sheet;
    for (int i = 0; i < s->count; i++) {
        xfree(&s->items[i].key);
        xfree(&s->items[i].title);
        xfree(&s->items[i].value);
    }
    xfree(&s->title);
    xfree(&s->token);
    s->count = 0;
    s->active = false;
}

static void open_sheet(cJSON *sh)
{
    sheet_clear();
    Sheet *s = &app.sheet;
    s->title = json_str(sh, "title");
    s->token = json_str(sh, "token");
    cJSON *it;
    cJSON_ArrayForEach(it, cJSON_GetObjectItemCaseSensitive(sh, "items"))
    {
        if (s->count >= (int)SDL_arraysize(s->items))
            break;
        SheetItem *d = &s->items[s->count++];
        d->key = json_str(it, "key");
        d->title = json_str(it, "title");
        d->value = json_str(it, "value");
        d->destructive = json_bool(it, "destructive");
    }
    s->sel = 0;
    s->at = SDL_GetTicks();
    s->active = true;
}

static bool kb_last_hangul;

static void open_keyboard(cJSON *in)
{
    Keyboard *k = &app.keyboard;
    xfree(&k->title);
    xfree(&k->placeholder);
    xfree(&k->token);
    k->title = json_str(in, "title");
    k->placeholder = json_str(in, "placeholder");
    k->token = json_str(in, "token");
    k->secret = json_bool(in, "secret");
    k->base[0] = 0;
    k->cho = k->jung = k->jong = 0;
    kb_sync(k);
    k->hangul = !k->secret && kb_last_hangul;
    k->shift = k->symbols = false;
    k->row = k->col = 0;
    k->at = SDL_GetTicks();
    k->active = true;
}

void nav_apply(cJSON *act, const char *hint)
{
    if (json_bool(act, "dismiss"))
        close_overlay();
    if (json_bool(act, "relabel")) {
        cJSON *req = cJSON_CreateObject();
        cJSON_AddStringToObject(req, "op", "hello");
        ipc_request(req, on_hello, NULL);
    }
    if (json_bool(act, "back"))
        nav_pop();
    char *push = json_str(act, "push");
    if (push) {
        nav_push(push, hint);
        SDL_free(push);
    }
    if (json_bool(act, "nowplaying")) {
        Page *t = top();
        if (!t || t->style != STYLE_NOWPLAYING)
            nav_push("/nowplaying", NULL);
    }
    if (json_bool(act, "reload"))
        nav_reload(active_page());
    cJSON *x;
    if ((x = cJSON_GetObjectItemCaseSensitive(act, "sheet")))
        open_sheet(x);
    if ((x = cJSON_GetObjectItemCaseSensitive(act, "input")))
        open_keyboard(x);
    app.dirty = true;
}

/* input --------------------------------------------------------------------- */

static void player(const char *cmd, int value)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "player");
    cJSON_AddStringToObject(req, "cmd", cmd);
    if (value)
        cJSON_AddNumberToObject(req, "value", value);
    ipc_request(req, NULL, NULL);
}

/* A press at either end of a list goes around to the other; a held button stops there. */
static void move_sel(Page *p, int delta, bool repeat)
{
    if (!p->count || !delta)
        return;
    int i = p->sel;
    int step = delta > 0 ? 1 : -1;
    for (int n = delta > 0 ? delta : -delta; n > 0; n--) {
        int j = i + step;
        while (j >= 0 && j < p->count && !selectable(p, j))
            j += step;
        if (!selectable(p, j))
            break;
        i = j;
    }
    if (i == p->sel && !repeat) {
        int j = step > 0 ? 0 : p->count - 1;
        while (j >= 0 && j < p->count && !selectable(p, j))
            j += step;
        if (selectable(p, j))
            i = j;
    }
    if (i == p->sel)
        return;
    p->sel = i;
    p->marquee_at = SDL_GetTicks() + 250;
    ensure_visible(p, true);
    if (repeat && p->index && p->items[i].letter[0]) {
        SDL_strlcpy(app.letter, p->items[i].letter, sizeof(app.letter));
        app.letter_at = SDL_GetTicks();
    }
}

static void jump_letter(Page *p, int dir)
{
    if (!p->index || !p->count)
        return;
    const char *cur = p->items[p->sel].letter;
    int i = p->sel;
    if (dir > 0) {
        while (i < p->count - 1 && !strcmp(p->items[i].letter, cur))
            i++;
    } else {
        if (i > 0 && strcmp(p->items[i - 1].letter, cur))
            cur = p->items[--i].letter;
        while (i > 0 && !strcmp(p->items[i - 1].letter, cur))
            i--;
    }
    move_sel(p, i - p->sel, false);
    SDL_strlcpy(app.letter, p->items[p->sel].letter, sizeof(app.letter));
    app.letter_at = SDL_GetTicks();
}

static void activate(Page *p)
{
    if (!selectable(p, p->sel))
        return;
    Item *it = &p->items[p->sel];
    if (p->style == STYLE_ABOUT)
        return;
    if (it->accessory == ACC_SWITCH)
        it->on = !it->on;
    request_do("activate", p->path, it->key, it->title);
}

static void list_key(Page *p, const char *key, bool repeat)
{
    if (!strcmp(key, "up"))
        move_sel(p, -1, repeat);
    else if (!strcmp(key, "down"))
        move_sel(p, 1, repeat);
    else if (!strcmp(key, "left"))
        jump_letter(p, -1);
    else if (!strcmp(key, "right"))
        jump_letter(p, 1);
    else if ((!strcmp(key, "pageup") || !strcmp(key, "pagedown")) && p->count) {
        /* L1 and R1 turn a page: the rows on screen less one, which keeps the place. */
        int rows = viewport_h(p) / row_height(p, p->sel) - 1;
        move_sel(p, (rows < 1 ? 1 : rows) * (key[4] == 'u' ? -1 : 1), true);
    } else if (!strcmp(key, "confirm") && !repeat)
        activate(p);
    else if (!strcmp(key, "more") && !repeat && selectable(p, p->sel))
        request_do("alt", p->path, p->items[p->sel].key, p->items[p->sel].title);
    else if (!strcmp(key, "back") && !repeat)
        nav_pop();
}

static void slider_key(Page *p, const char *key)
{
    int delta = 0;
    if (!strcmp(key, "left") || !strcmp(key, "down"))
        delta = -p->step;
    else if (!strcmp(key, "right") || !strcmp(key, "up"))
        delta = p->step;
    else if (!strcmp(key, "back") || !strcmp(key, "confirm")) {
        nav_pop();
        return;
    }
    int v = p->value + delta;
    v = v < p->min ? p->min : v > p->max ? p->max : v;
    if (v == p->value)
        return;
    p->value = v;
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "slider");
    cJSON_AddStringToObject(req, "path", p->path);
    cJSON_AddNumberToObject(req, "value", v);
    ipc_request(req, NULL, NULL);
}

/* Cover Flow: left and right glide through albums, A flips the album over to its songs
 * and B flips it back. */
static void open_card(Page *p)
{
    Item *it = &p->items[p->sel];
    if (!it->link) {
        activate(p);
        return;
    }
    page_free(p->card);
    p->card = page_new(it->link, it->title);
    p->card->viewport = CARD_LIST_H;
    page_request(p->card);
    p->flip_at = SDL_GetTicks();
    p->closing = false;
}

static void coverflow_key(Page *p, const char *key, bool repeat)
{
    Page *c = p->card;
    if (c) {
        if (p->closing)
            return;
        if (!strcmp(key, "back")) {
            if (!repeat) {
                p->closing = true;
                p->flip_at = SDL_GetTicks();
            }
        } else if (c->loaded) {
            list_key(c, key, repeat);
        }
        return;
    }
    int step = !strcmp(key, "left") ? -1 : !strcmp(key, "right") ? 1 : 0;
    if (step && p->count) {
        int s = p->sel + step;
        p->sel = s < 0 ? 0 : s >= p->count ? p->count - 1 : s;
    } else if (!strcmp(key, "confirm") && !repeat && p->count) {
        open_card(p);
    } else if (!strcmp(key, "back") && !repeat) {
        nav_pop();
    }
}

static void volume(int delta)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "volume");
    cJSON_AddNumberToObject(req, "delta", delta);
    ipc_request(req, NULL, NULL);
}

/* Left and right as on the iPod: let go at once they are the previous and the next song,
 * held they wind through this one. Which it was is known when the button comes up. */
static int np_skip;

static void nowplaying_key(const char *key, bool repeat)
{
    int dir = !strcmp(key, "left") ? -1 : !strcmp(key, "right") ? 1 : 0;
    if (!strcmp(key, "confirm") && !repeat)
        player("toggle", 0);
    else if (dir && repeat)
        player("seek", dir * 3);
    else if (dir)
        np_skip = dir;
    else if (!strcmp(key, "up"))
        volume(1);
    else if (!strcmp(key, "down"))
        volume(-1);
    else if (!strcmp(key, "more") && !repeat)
        request_do("alt", "/nowplaying", NULL, NULL);
    else if (!strcmp(key, "back") && !repeat)
        nav_pop();
}

static void sheet_key(const char *key, bool repeat)
{
    Sheet *s = &app.sheet;
    if (!strcmp(key, "up") && s->sel > 0)
        s->sel--;
    else if (!strcmp(key, "up") && !repeat && s->count)
        s->sel = s->count - 1;
    else if (!strcmp(key, "down") && s->sel < s->count - 1)
        s->sel++;
    else if (!strcmp(key, "down") && !repeat)
        s->sel = 0;
    else if (!strcmp(key, "back")) {
        s->active = false;
    } else if (!strcmp(key, "confirm")) {
        s->active = false;
        if (s->sel < s->count && s->token)
            resolve(s->token, s->items[s->sel].key);
    }
}

static void kb_submit(Keyboard *k)
{
    hangul_commit(k);
    k->active = false;
    resolve(k->token, k->text);
}

/* Like a list, the keys go around: a press at an edge lands on the opposite one. */
static void keyboard_key(const char *key, bool repeat)
{
    Keyboard *k = &app.keyboard;
    int last_col = kb_row_len(k->row) - 1;
    if (!strcmp(key, "up")) {
        if (k->row > 0)
            k->row--;
        else if (!repeat)
            k->row = 3;
    } else if (!strcmp(key, "down")) {
        if (k->row < 3)
            k->row++;
        else if (!repeat)
            k->row = 0;
    } else if (!strcmp(key, "left")) {
        if (k->col > 0)
            k->col--;
        else if (!repeat)
            k->col = last_col;
    } else if (!strcmp(key, "right")) {
        if (k->col < last_col)
            k->col++;
        else if (!repeat)
            k->col = 0;
    } else if (!strcmp(key, "back")) {
        if (k->text[0])
            kb_backspace(k);
        else
            k->active = false;
    } else if (!strcmp(key, "more")) {
        k->shift = !k->shift;
    } else if (!strcmp(key, "now")) {
        hangul_commit(k);
        k->symbols = !k->symbols;
        k->shift = false;
    } else if (!strcmp(key, "play")) {
        kb_submit(k);
    } else if (!strcmp(key, "confirm")) {
        char buf[8];
        const char *label = kb_label(k->row, k->col, buf);
        int last = kb_row_len(k->row) - 1, jamo = kb_jamo(k->row, k->col);
        if (k->row == 3 && k->col == 0) {
            hangul_commit(k);
            k->symbols = !k->symbols;
            k->shift = false;
        } else if (k->row == 3 && k->col == 1) {
            hangul_commit(k);
            k->hangul = kb_last_hangul = !k->hangul;
            k->symbols = k->shift = false;
        } else if (k->row == 3 && k->col == 3) {
            kb_submit(k);
        } else if (k->row == 2 && k->col == 0) {
            k->shift = !k->shift;
        } else if (k->row == 2 && k->col == last) {
            kb_backspace(k);
        } else {
            if (jamo)
                hangul_input(k, jamo);
            else
                kb_append(k, k->row == 3 ? " " : label);
            if (k->shift && !k->symbols)
                k->shift = false;
        }
    }
    int n = kb_row_len(k->row);
    if (k->col >= n)
        k->col = n - 1;
}

void nav_key_up(const char *key)
{
    int dir = !strcmp(key, "left") ? -1 : !strcmp(key, "right") ? 1 : 0;
    if (!dir)
        return;
    if (dir == np_skip)
        player(dir < 0 ? "prev" : "next", 0);
    np_skip = 0;
}

void nav_key(const char *key, bool repeat)
{
    np_skip = 0;
    if (!strcmp(key, "home")) {
        nav_home();
        app.dirty = true;
        return;
    }
    if (app.keyboard.active) {
        keyboard_key(key, repeat);
    } else if (app.sheet.active) {
        sheet_key(key, repeat);
    } else if (!strcmp(key, "play") && !repeat) {
        player("toggle", 0);
    } else if (!strcmp(key, "control") && !repeat) {
        if (app.overlay) {
            close_overlay();
        } else {
            app.overlay = page_new("/control", "");
            app.overlay->sheet = true;
            page_request(app.overlay);
        }
    } else if (!strcmp(key, "now") && !repeat) {
        /* Y is Now Playing; pressed there, it turns to the lyrics and back. */
        Page *t = top();
        if (app.overlay)
            close_overlay();
        if (t && t->style == STYLE_NOWPLAYING)
            nav_push("/nowplaying/lyrics", NULL);
        else if (t && t->style == STYLE_LYRICS)
            nav_pop();
        else
            nav_push("/nowplaying", NULL);
    } else {
        Page *p = active_page();
        if (!p)
            return;
        if (p->style == STYLE_NOWPLAYING || p->style == STYLE_LYRICS)
            nowplaying_key(key, repeat);
        else if (p->style == STYLE_SLIDER)
            slider_key(p, key);
        else if (p->style == STYLE_COVERFLOW)
            coverflow_key(p, key, repeat);
        else if (p->loaded)
            list_key(p, key, repeat);
        else if (!strcmp(key, "back"))
            nav_pop();
    }
    app.dirty = true;
}

/* state --------------------------------------------------------------------- */

static void set_str(char **dst, cJSON *obj, const char *key)
{
    char *v = json_str(obj, key);
    if (*dst && v && !strcmp(*dst, v)) {
        SDL_free(v);
        return;
    }
    SDL_free(*dst);
    *dst = v;
}

void status_update(cJSON *state)
{
    Status *s = &app.status;
    cJSON *pl = cJSON_GetObjectItemCaseSensitive(state, "player");
    if (pl) {
        Player *p = &s->player;
        char *v;
        if ((v = json_str(pl, "state"))) {
            SDL_strlcpy(p->state, v, sizeof(p->state));
            SDL_free(v);
        }
        set_str(&p->title, pl, "title");
        set_str(&p->artist, pl, "artist");
        set_str(&p->album, pl, "album");
        set_str(&p->art, pl, "art");
        char *was = p->file ? xstrdup(p->file) : NULL;
        set_str(&p->file, pl, "file");
        if ((was != NULL) != (p->file != NULL) || (was && strcmp(was, p->file))) {
            /* Another song: the words on Now Playing were the last one's. */
            for (int i = 0; i < app.depth; i++) {
                if (app.stack[i]->style == STYLE_NOWPLAYING) {
                    items_free(app.stack[i]);
                    app.stack[i]->time_count = 0;
                    xfree(&app.stack[i]->note);
                }
            }
        }
        SDL_free(was);
        p->elapsed = json_num(pl, "elapsed", 0);
        p->elapsed_at = SDL_GetTicks();
        p->duration = json_num(pl, "duration", 0);
        p->pos = json_int(pl, "pos", 0);
        p->count = json_int(pl, "count", 0);
        p->shuffle = json_bool(pl, "shuffle");
        p->repeat = json_bool(pl, "repeat");
        p->single = json_bool(pl, "single");
        p->favorite = json_bool(pl, "favorite");
    }
    cJSON *vol = cJSON_GetObjectItemCaseSensitive(state, "volume");
    if (vol) {
        s->volume = json_int(vol, "level", s->volume);
        char *out = json_str(vol, "output");
        if (out) {
            SDL_strlcpy(s->output, out, sizeof(s->output));
            SDL_free(out);
        }
    }
    cJSON *bat = cJSON_GetObjectItemCaseSensitive(state, "battery");
    if (bat) {
        s->battery = json_int(bat, "percent", s->battery);
        s->charging = json_bool(bat, "charging");
    }
    cJSON *bt = cJSON_GetObjectItemCaseSensitive(state, "bluetooth");
    if (bt) {
        s->bt_on = json_bool(bt, "enabled");
        set_str(&s->bt_audio, bt, "audio");
    }
    cJSON *wifi = cJSON_GetObjectItemCaseSensitive(state, "wifi");
    if (wifi) {
        s->wifi_on = json_bool(wifi, "enabled");
        s->wifi_joined = cJSON_IsString(cJSON_GetObjectItemCaseSensitive(wifi, "ssid"));
        s->wifi_signal = json_int(wifi, "signal", 0);
    }
    cJSON *scr = cJSON_GetObjectItemCaseSensitive(state, "screen");
    if (scr) {
        bool on = json_bool(scr, "on");
        if (on && !s->screen_on)
            app.wake_guard = SDL_GetTicks() + 250;
        s->screen_on = on;
    }
    app.dirty = true;
}

void on_hello(cJSON *reply, void *ctx)
{
    (void)ctx;
    cJSON *strings = cJSON_GetObjectItemCaseSensitive(reply, "strings");
    if (strings) {
        cJSON_Delete(app.strings);
        app.strings = cJSON_Duplicate(strings, true);
    }
    cJSON *state = cJSON_GetObjectItemCaseSensitive(reply, "state");
    if (state)
        status_update(state);
    if (!app.depth) {
        Page *root = page_new("/", "isoPod");
        page_request(root);
        app.stack[app.depth++] = root;
    } else {
        for (int i = 0; i < app.depth; i++) {
            if (app.stack[i]->style == STYLE_NOWPLAYING) {
                SDL_free(app.stack[i]->title);
                app.stack[i]->title = xstrdup(S("now_playing", "Now Playing"));
            }
            nav_reload(app.stack[i]);
        }
    }
    app.dirty = true;
}

void on_ipc_connected(void)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "hello");
    ipc_request(req, on_hello, NULL);
}

void show_toast(const char *text, const char *icon_name)
{
    SDL_strlcpy(app.toast, text ? text : "", sizeof(app.toast));
    SDL_strlcpy(app.toast_icon, icon_name ? icon_name : "", sizeof(app.toast_icon));
    app.toast_at = SDL_GetTicks();
    app.dirty = true;
}

void show_hud(const char *kind, int value)
{
    SDL_strlcpy(app.hud_kind, kind ? kind : "volume", sizeof(app.hud_kind));
    app.hud_value = value;
    app.hud_at = SDL_GetTicks();
    app.dirty = true;
}

void on_ipc_event(cJSON *msg)
{
    char *ev = json_str(msg, "event");
    if (!ev)
        return;
    if (!strcmp(ev, "state")) {
        status_update(cJSON_GetObjectItemCaseSensitive(msg, "state"));
    } else if (!strcmp(ev, "toast")) {
        char *text = json_str(msg, "text"), *ic = json_str(msg, "icon");
        show_toast(text, ic);
        SDL_free(text);
        SDL_free(ic);
    } else if (!strcmp(ev, "hud")) {
        char *kind = json_str(msg, "kind");
        show_hud(kind, json_int(msg, "value", 0));
        SDL_free(kind);
    } else if (!strcmp(ev, "key")) {
        char *key = json_str(msg, "key");
        if (key && app.focused && SDL_TICKS_PASSED(SDL_GetTicks(), app.wake_guard)) {
            if (json_bool(msg, "up"))
                nav_key_up(key);
            else
                nav_key(key, json_bool(msg, "repeat"));
        }
        SDL_free(key);
    } else if (!strcmp(ev, "page")) {
        cJSON *path;
        cJSON_ArrayForEach(path, cJSON_GetObjectItemCaseSensitive(msg, "paths"))
        {
            if (cJSON_IsString(path))
                nav_reload_path(path->valuestring);
        }
    } else if (!strcmp(ev, "present")) {
        cJSON *act = cJSON_GetObjectItemCaseSensitive(msg, "do");
        if (act)
            nav_apply(act, NULL);
    }
    SDL_free(ev);
    app.dirty = true;
}
