#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "shell.h"

enum { IPC_LINE = 1, IPC_CONNECTED, IPC_DISCONNECTED };

typedef struct {
    int id;
    ReplyFn fn;
    void *ctx;
} Pending;

static Uint32 ipc_event;
static char sock_path[256];
static int sock_fd = -1;
static SDL_mutex *write_lock;
static Pending pending[128];
static int next_id = 1;

static void push(int code, void *data)
{
    SDL_Event ev = {0};
    ev.type = ipc_event;
    ev.user.code = code;
    ev.user.data1 = data;
    SDL_PushEvent(&ev);
}

static int try_connect(void)
{
    struct sockaddr_un addr = {0};
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0)
        return -1;
    addr.sun_family = AF_UNIX;
    SDL_strlcpy(addr.sun_path, sock_path, sizeof(addr.sun_path));
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static int reader(void *unused)
{
    (void)unused;
    size_t cap = 1 << 16;
    char *buf = SDL_malloc(cap);
    for (;;) {
        int fd;
        while ((fd = try_connect()) < 0)
            SDL_Delay(400);
        SDL_LockMutex(write_lock);
        sock_fd = fd;
        SDL_UnlockMutex(write_lock);
        push(IPC_CONNECTED, NULL);

        size_t len = 0;
        for (;;) {
            if (len + 4096 > cap) {
                cap *= 2;
                buf = SDL_realloc(buf, cap);
            }
            ssize_t n = read(fd, buf + len, cap - len - 1);
            if (n < 0 && errno == EINTR)
                continue;
            if (n <= 0)
                break;
            len += (size_t)n;
            char *start = buf, *nl;
            while ((nl = memchr(start, '\n', len - (size_t)(start - buf)))) {
                *nl = 0;
                push(IPC_LINE, SDL_strdup(start));
                start = nl + 1;
            }
            len -= (size_t)(start - buf);
            memmove(buf, start, len);
        }
        SDL_LockMutex(write_lock);
        close(fd);
        sock_fd = -1;
        SDL_UnlockMutex(write_lock);
        push(IPC_DISCONNECTED, NULL);
        SDL_Delay(300);
    }
    return 0;
}

void ipc_start(const char *path)
{
    SDL_strlcpy(sock_path, path, sizeof(sock_path));
    ipc_event = SDL_RegisterEvents(1);
    write_lock = SDL_CreateMutex();
    SDL_DetachThread(SDL_CreateThread(reader, "ipc", NULL));
}

Uint32 ipc_event_type(void)
{
    return ipc_event;
}

int ipc_request(cJSON *req, ReplyFn fn, void *ctx)
{
    int id = next_id++;
    cJSON_AddNumberToObject(req, "id", id);
    char *text = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    if (!text)
        return -1;
    size_t n = strlen(text);
    text = SDL_realloc(text, n + 2);
    text[n] = '\n';
    text[n + 1] = 0;

    SDL_LockMutex(write_lock);
    bool ok = sock_fd >= 0;
    size_t off = 0;
    while (ok && off <= n) {
        ssize_t w = write(sock_fd, text + off, n + 1 - off);
        if (w < 0 && errno == EINTR)
            continue;
        if (w <= 0)
            ok = false;
        else
            off += (size_t)w;
    }
    SDL_UnlockMutex(write_lock);
    SDL_free(text);
    if (!ok)
        return -1;

    for (size_t i = 0; i < SDL_arraysize(pending); i++) {
        if (!pending[i].id) {
            pending[i] = (Pending){id, fn, ctx};
            app.pending++;
            break;
        }
    }
    return id;
}

void ipc_handle_line(char *line)
{
    cJSON *msg = cJSON_Parse(line);
    SDL_free(line);
    if (!msg)
        return;
    cJSON *id = cJSON_GetObjectItemCaseSensitive(msg, "id");
    if (cJSON_IsNumber(id)) {
        for (size_t i = 0; i < SDL_arraysize(pending); i++) {
            if (pending[i].id == (int)id->valuedouble) {
                Pending p = pending[i];
                pending[i].id = 0;
                app.pending--;
                if (p.fn)
                    p.fn(msg, p.ctx);
                break;
            }
        }
    } else {
        on_ipc_event(msg);
    }
    cJSON_Delete(msg);
}

void ipc_dispatch(SDL_Event *ev)
{
    switch (ev->user.code) {
    case IPC_LINE:
        ipc_handle_line(ev->user.data1);
        break;
    case IPC_CONNECTED:
        app.connected = true;
        on_ipc_connected();
        break;
    case IPC_DISCONNECTED:
        app.connected = false;
        for (size_t i = 0; i < SDL_arraysize(pending); i++)
            pending[i].id = 0;
        app.pending = 0;
        break;
    }
}
