#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "shell.h"

char *xstrdup(const char *s)
{
    return s ? SDL_strdup(s) : NULL;
}

void xfree(char **p)
{
    SDL_free(*p);
    *p = NULL;
}

char *json_str(cJSON *obj, const char *key)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsString(v) ? xstrdup(v->valuestring) : NULL;
}

int json_int(cJSON *obj, const char *key, int fallback)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsNumber(v) ? (int)v->valuedouble : fallback;
}

double json_num(cJSON *obj, const char *key, double fallback)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsNumber(v) ? v->valuedouble : fallback;
}

bool json_bool(cJSON *obj, const char *key)
{
    return cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(obj, key));
}

const char *S(const char *key, const char *fallback)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(app.strings, key);
    return cJSON_IsString(v) ? v->valuestring : fallback;
}

void fmt_time(double seconds, char *out, size_t n)
{
    int s = seconds > 0 ? (int)seconds : 0;
    if (s >= 3600)
        snprintf(out, n, "%d:%02d:%02d", s / 3600, (s / 60) % 60, s % 60);
    else
        snprintf(out, n, "%d:%02d", s / 60, s % 60);
}

static const char *skip_article(const char *s)
{
    if (!SDL_strncasecmp(s, "the ", 4))
        return s + 4;
    if (!SDL_strncasecmp(s, "an ", 3))
        return s + 3;
    if (!SDL_strncasecmp(s, "a ", 2))
        return s + 2;
    return s;
}

void index_letter(const char *text, char out[8])
{
    static const char *initials[] = {"ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
                                     "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"};
    const unsigned char *s = (const unsigned char *)skip_article(text ? text : "");
    strcpy(out, "#");
    if (*s < 0x80) {
        if ((*s >= 'a' && *s <= 'z') || (*s >= 'A' && *s <= 'Z')) {
            out[0] = (char)(*s & ~0x20);
            out[1] = 0;
        }
        return;
    }
    if ((*s & 0xF0) == 0xE0 && s[1] && s[2]) {
        unsigned cp = ((*s & 0x0F) << 12) | ((s[1] & 0x3F) << 6) | (s[2] & 0x3F);
        if (cp >= 0xAC00 && cp <= 0xD7A3)
            strcpy(out, initials[(cp - 0xAC00) / 588]);
    }
}

float ease_out(float t)
{
    if (t >= 1)
        return 1;
    if (t <= 0)
        return 0;
    return 1 - powf(1 - t, 3);
}
