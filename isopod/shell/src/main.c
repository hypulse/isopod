#include <SDL_image.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "shell.h"

App app;

typedef struct {
    char **lines;
    int count, pos;
    Uint32 wait_until, idle_since, idle_started, input_at;
    bool idle;
} Script;

static Script script;

/* --record: frames go to a command's stdin as raw RGB at a steady rate, with the
 * script's captions drawn in a strip under the screen. For demos, not for the device. */
#define REC_FPS 30
#define REC_STRIP 64

typedef struct {
    FILE *pipe;
    SDL_Surface *frame, *caption;
    Uint32 started;
    long frames;
} Recorder;

static Recorder rec;

static bool record_start(const char *cmd)
{
    rec.pipe = popen(cmd, "w");
    rec.frame = SDL_CreateRGBSurfaceWithFormat(0, SCREEN_W, SCREEN_H + REC_STRIP, 24, SDL_PIXELFORMAT_RGB24);
    rec.started = SDL_GetTicks();
    return rec.pipe && rec.frame && rec.frame->pitch == SCREEN_W * 3;
}

static void record_caption(const char *text)
{
    SDL_FreeSurface(rec.caption);
    rec.caption = NULL;
    TTF_Font *f = font(FONT_MEDIUM, 23);
    if (f && text && *text)
        rec.caption = TTF_RenderUTF8_Blended_Wrapped(f, text, (SDL_Color){242, 242, 247, 255}, SCREEN_W - 40);
}

static void record_frame(void)
{
    if (!rec.pipe)
        return;
    long due = (long)((SDL_GetTicks() - rec.started) * (Uint64)REC_FPS / 1000) + 1;
    if (rec.frames >= due)
        return;
    SDL_RenderReadPixels(app.renderer, NULL, SDL_PIXELFORMAT_RGB24, rec.frame->pixels, rec.frame->pitch);
    SDL_Rect strip = {0, SCREEN_H, SCREEN_W, REC_STRIP};
    SDL_FillRect(rec.frame, &strip, SDL_MapRGB(rec.frame->format, 28, 28, 30));
    if (rec.caption) {
        SDL_Rect at = {(SCREEN_W - rec.caption->w) / 2, SCREEN_H + (REC_STRIP - rec.caption->h) / 2, 0, 0};
        SDL_BlitSurface(rec.caption, NULL, rec.frame, &at);
    }
    size_t size = (size_t)rec.frame->pitch * (size_t)rec.frame->h;
    for (; rec.frames < due; rec.frames++)
        if (fwrite(rec.frame->pixels, 1, size, rec.pipe) != size) {
            pclose(rec.pipe);
            rec.pipe = NULL;
            return;
        }
}

static void daemon_key(const char *key)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "key");
    cJSON_AddStringToObject(req, "key", key);
    ipc_request(req, NULL, NULL);
}

/* Input that reaches the shell without passing through isopodd still counts as use. */
static void daemon_activity(void)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "activity");
    ipc_request(req, NULL, NULL);
}

static void daemon_volume(int delta)
{
    cJSON *req = cJSON_CreateObject();
    cJSON_AddStringToObject(req, "op", "volume");
    cJSON_AddNumberToObject(req, "delta", delta);
    ipc_request(req, NULL, NULL);
}

/* Desktop keyboard stand-in for the handheld's buttons during development. */
static void keyboard(SDL_KeyboardEvent *e)
{
    bool rep = e->repeat != 0;
    daemon_activity();
    switch (e->keysym.sym) {
    case SDLK_UP: nav_key("up", rep); break;
    case SDLK_DOWN: nav_key("down", rep); break;
    case SDLK_LEFT: nav_key("left", rep); break;
    case SDLK_RIGHT: nav_key("right", rep); break;
    case SDLK_RETURN: nav_key("confirm", rep); break;
    case SDLK_ESCAPE:
    case SDLK_BACKSPACE: nav_key("back", rep); break;
    case SDLK_SPACE: nav_key("play", rep); break;
    case SDLK_m: nav_key("more", rep); break;
    case SDLK_n: nav_key("now", rep); break;
    case SDLK_c: nav_key("control", rep); break;
    case SDLK_LEFTBRACKET: nav_key("prev", rep); break;
    case SDLK_RIGHTBRACKET: nav_key("next", rep); break;
    case SDLK_PAGEUP: nav_key("pageup", rep); break;
    case SDLK_PAGEDOWN: nav_key("pagedown", rep); break;
    case SDLK_h: daemon_key("home"); break;
    case SDLK_p: daemon_key("power"); break;
    case SDLK_EQUALS: daemon_volume(1); break;
    case SDLK_MINUS: daemon_volume(-1); break;
    default: break;
    }
}

static void handle(SDL_Event *ev)
{
    if (ev->type == SDL_QUIT) {
        app.running = false;
    } else if (ev->type == SDL_WINDOWEVENT) {
        if (ev->window.event == SDL_WINDOWEVENT_FOCUS_GAINED)
            app.focused = true;
        else if (ev->window.event == SDL_WINDOWEVENT_FOCUS_LOST && !script.lines)
            app.focused = false;
        app.dirty = true;
    } else if (ev->type == SDL_KEYDOWN) {
        keyboard(&ev->key);
    } else if (ev->type == ipc_event_type()) {
        ipc_dispatch(ev);
    }
}

/* scripted runs: drive the UI and capture screenshots without a person */

/* Replies are in, pages are loaded and the last transition has played out. Spinners and
   HUDs keep animating on purpose, so they do not count. */
static bool settled(void)
{
    if (app.pending || app.transition || !app.depth || SDL_GetTicks() - script.input_at < 380)
        return false;
    for (int i = 0; i < app.depth; i++)
        if (!app.stack[i]->loaded)
            return false;
    return !app.overlay || app.overlay->loaded;
}

static bool load_script(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f)
        return false;
    char line[1024];
    while (fgets(line, sizeof(line), f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!line[0] || line[0] == '#')
            continue;
        script.lines = realloc(script.lines, sizeof(char *) * (size_t)(script.count + 1));
        script.lines[script.count++] = strdup(line);
    }
    fclose(f);
    script.idle = true;
    script.idle_started = SDL_GetTicks();
    return true;
}

static void script_step(void)
{
    Uint32 now = SDL_GetTicks();
    if (script.wait_until && !SDL_TICKS_PASSED(now, script.wait_until))
        return;
    script.wait_until = 0;
    if (script.idle) {
        if (settled()) {
            if (!script.idle_since)
                script.idle_since = now;
            if (now - script.idle_since < 150)
                return;
        } else {
            script.idle_since = 0;
            if (now - script.idle_started < 10000)
                return;
            SDL_Log("script: gave up waiting at line %d", script.pos);
        }
        script.idle = false;
        script.idle_since = 0;
    }
    if (script.pos >= script.count) {
        app.running = false;
        return;
    }
    char *line = script.lines[script.pos++];
    char cmd[32] = {0}, arg[960] = {0};
    sscanf(line, "%31s %959[^\n]", cmd, arg);
    bool settle = true;
    script.input_at = now;
    if (!strcmp(cmd, "key") || !strcmp(cmd, "hold") || !strcmp(cmd, "type"))
        daemon_activity();
    if (!strcmp(cmd, "key")) {
        nav_key(arg, false);
    } else if (!strcmp(cmd, "hold")) {
        char key[32];
        int n = 1;
        sscanf(arg, "%31s %d", key, &n);
        nav_key(key, false);
        for (int i = 1; i < n; i++)
            nav_key(key, true);
    } else if (!strcmp(cmd, "daemon")) {
        daemon_key(arg);
        settle = false;
    } else if (!strcmp(cmd, "volume")) {
        daemon_volume(atoi(arg));
        settle = false;
    } else if (!strcmp(cmd, "type")) {
        /* Jamo go through the composer as if typed on the Hangul layout. */
        for (const unsigned char *c = (const unsigned char *)arg; *c && app.keyboard.active;) {
            int len = *c < 0x80 ? 1 : *c < 0xE0 ? 2 : *c < 0xF0 ? 3 : 4;
            int cp = len == 3 ? (c[0] & 0x0F) << 12 | (c[1] & 0x3F) << 6 | (c[2] & 0x3F) : 0;
            char one[5] = {0};
            memcpy(one, c, (size_t)len);
            if (hangul_is_jamo(cp))
                hangul_input(&app.keyboard, cp);
            else
                kb_append(&app.keyboard, one);
            c += len;
        }
    } else if (!strcmp(cmd, "send")) {
        cJSON *req = cJSON_Parse(arg);
        if (req)
            ipc_request(req, NULL, NULL);
        settle = false;
    } else if (!strcmp(cmd, "wait")) {
        script.wait_until = now + (Uint32)atoi(arg);
        settle = false;
    } else if (!strcmp(cmd, "caption")) {
        record_caption(arg);
        settle = false;
    } else if (!strcmp(cmd, "sim")) {
        cJSON *req = cJSON_Parse(arg);
        if (req) {
            cJSON_AddStringToObject(req, "op", "sim");
            ipc_request(req, NULL, NULL);
        }
        settle = false;
    } else if (!strcmp(cmd, "shot")) {
        render();
        if (!save_screenshot(arg))
            SDL_Log("script: screenshot %s failed", arg);
        settle = false;
    } else if (!strcmp(cmd, "quit")) {
        app.running = false;
    }
    if (settle) {
        script.idle = true;
        script.idle_started = now;
    }
}

static void usage(void)
{
    fprintf(stderr, "usage: isopod-shell [--socket PATH] [--data DIR] [--window] [--scale N] "
                    "[--software] [--script FILE] [--record COMMAND]\n");
}

int main(int argc, char **argv)
{
    const char *sock = "/run/isopod/isopodd.sock", *data = "/usr/share/isopod", *script_file = NULL;
    const char *record = NULL;
    bool windowed = false, software = false;
    int scale = 1;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--socket") && i + 1 < argc)
            sock = argv[++i];
        else if (!strcmp(argv[i], "--data") && i + 1 < argc)
            data = argv[++i];
        else if (!strcmp(argv[i], "--script") && i + 1 < argc)
            script_file = argv[++i];
        else if (!strcmp(argv[i], "--record") && i + 1 < argc)
            record = argv[++i];
        else if (!strcmp(argv[i], "--scale") && i + 1 < argc)
            scale = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--window"))
            windowed = true;
        else if (!strcmp(argv[i], "--software"))
            software = true;
        else {
            usage();
            return 2;
        }
    }
    snprintf(app.asset_dir, sizeof(app.asset_dir), "%s", data);
    snprintf(app.font_dir, sizeof(app.font_dir), "%s/fonts", data);

    SDL_setenv("SDL_VIDEO_WAYLAND_WMCLASS", "isopod-shell", 1);
    SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "1");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_TIMER) || TTF_Init() < 0) {
        SDL_Log("init: %s", SDL_GetError());
        return 1;
    }
    IMG_Init(IMG_INIT_PNG | IMG_INIT_JPG);
    Uint32 wflags = windowed || script_file ? 0 : SDL_WINDOW_FULLSCREEN_DESKTOP;
    app.window = SDL_CreateWindow("isoPod", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                  SCREEN_W * scale, SCREEN_H * scale, wflags);
    Uint32 rflags = software || script_file ? SDL_RENDERER_SOFTWARE
                                            : SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC;
    app.renderer = app.window ? SDL_CreateRenderer(app.window, -1, rflags) : NULL;
    if (!app.renderer) {
        SDL_Log("renderer: %s", SDL_GetError());
        return 1;
    }
    SDL_RenderSetLogicalSize(app.renderer, SCREEN_W, SCREEN_H);
    SDL_ShowCursor(SDL_DISABLE);
    if (!gfx_init()) {
        SDL_Log("fonts missing in %s", app.font_dir);
        return 1;
    }
    if (script_file && !load_script(script_file)) {
        SDL_Log("cannot read %s", script_file);
        return 1;
    }

    if (record && !record_start(record)) {
        SDL_Log("cannot record through: %s", record);
        return 1;
    }

    app.running = true;
    app.focused = true;
    app.status.screen_on = true;
    app.status.battery = 100;
    app.dirty = true;
    ipc_start(sock);

    while (app.running) {
        int delay = script.lines ? 4 : app.dirty ? 0 : next_frame_delay();
        SDL_Event ev;
        int got = delay < 0 ? SDL_WaitEvent(&ev) : SDL_WaitEventTimeout(&ev, delay);
        if (got) {
            handle(&ev);
            while (SDL_PollEvent(&ev))
                handle(&ev);
        }
        if (script.lines)
            script_step();
        render();
        record_frame();
        app.dirty = false;
    }
    if (rec.pipe)
        pclose(rec.pipe);
    TTF_Quit();
    SDL_Quit();
    return 0;
}
