/* Dubeolsik composition for the on-screen keyboard. Jamo are Hangul compatibility
 * code points (U+3131..U+3163); the syllable being typed stays open in the
 * keyboard until a key that cannot join it arrives. */

#include "shell.h"

static const int CHO[19] = {0x3131, 0x3132, 0x3134, 0x3137, 0x3138, 0x3139, 0x3141, 0x3142, 0x3143, 0x3145,
                            0x3146, 0x3147, 0x3148, 0x3149, 0x314A, 0x314B, 0x314C, 0x314D, 0x314E};
static const int JONG[28] = {0,      0x3131, 0x3132, 0x3133, 0x3134, 0x3135, 0x3136, 0x3137, 0x3139, 0x313A,
                             0x313B, 0x313C, 0x313D, 0x313E, 0x313F, 0x3140, 0x3141, 0x3142, 0x3144, 0x3145,
                             0x3146, 0x3147, 0x3148, 0x314A, 0x314B, 0x314C, 0x314D, 0x314E};
static const int JONG_PAIRS[][3] = {{0x3131, 0x3145, 0x3133}, {0x3134, 0x3148, 0x3135}, {0x3134, 0x314E, 0x3136},
                                    {0x3139, 0x3131, 0x313A}, {0x3139, 0x3141, 0x313B}, {0x3139, 0x3142, 0x313C},
                                    {0x3139, 0x3145, 0x313D}, {0x3139, 0x314C, 0x313E}, {0x3139, 0x314D, 0x313F},
                                    {0x3139, 0x314E, 0x3140}, {0x3142, 0x3145, 0x3144}};
static const int VOWEL_PAIRS[][3] = {{0x3157, 0x314F, 0x3158}, {0x3157, 0x3150, 0x3159}, {0x3157, 0x3163, 0x315A},
                                     {0x315C, 0x3153, 0x315D}, {0x315C, 0x3154, 0x315E}, {0x315C, 0x3163, 0x315F},
                                     {0x3161, 0x3163, 0x3162}};

#define NPAIRS(t) (int)(sizeof(t) / sizeof(t[0]))

static int index_of(const int *table, int n, int cp)
{
    for (int i = 0; i < n; i++)
        if (table[i] == cp)
            return i;
    return -1;
}

static int join(const int (*pairs)[3], int n, int a, int b)
{
    for (int i = 0; i < n; i++)
        if (pairs[i][0] == a && pairs[i][1] == b)
            return pairs[i][2];
    return 0;
}

static const int *split(const int (*pairs)[3], int n, int joined)
{
    for (int i = 0; i < n; i++)
        if (pairs[i][2] == joined)
            return pairs[i];
    return NULL;
}

bool hangul_is_vowel(int cp)
{
    return cp >= 0x314F && cp <= 0x3163;
}

bool hangul_is_jamo(int cp)
{
    return cp >= 0x3131 && cp <= 0x3163;
}

static int encode(int cp, char *out)
{
    if (cp < 0x80) {
        out[0] = (char)cp;
        return 1;
    }
    if (cp < 0x800) {
        out[0] = (char)(0xC0 | cp >> 6);
        out[1] = (char)(0x80 | (cp & 0x3F));
        return 2;
    }
    out[0] = (char)(0xE0 | cp >> 12);
    out[1] = (char)(0x80 | (cp >> 6 & 0x3F));
    out[2] = (char)(0x80 | (cp & 0x3F));
    return 3;
}

int hangul_utf8(int cp, char out[4])
{
    int n = encode(cp, out);
    out[n] = 0;
    return n;
}

static int composed(const Keyboard *k)
{
    if (k->cho && k->jung) {
        int jong = k->jong ? index_of(JONG, 28, k->jong) : 0;
        return 0xAC00 + (index_of(CHO, 19, k->cho) * 21 + (k->jung - 0x314F)) * 28 + jong;
    }
    return k->cho ? k->cho : k->jung;
}

void kb_sync(Keyboard *k)
{
    char tail[4];
    int cp = composed(k);
    tail[cp ? encode(cp, tail) : 0] = 0;
    snprintf(k->text, sizeof(k->text), "%s%s", k->base, tail);
}

void hangul_commit(Keyboard *k)
{
    kb_sync(k);
    SDL_strlcpy(k->base, k->text, sizeof(k->base));
    k->cho = k->jung = k->jong = 0;
}

static void start(Keyboard *k, int cho, int jung)
{
    hangul_commit(k);
    k->cho = cho;
    k->jung = jung;
}

void hangul_input(Keyboard *k, int cp)
{
    if (strlen(k->base) + 8 >= sizeof(k->base))
        return;
    if (!hangul_is_vowel(cp)) {
        int joined;
        if (k->cho && k->jung && !k->jong && index_of(JONG, 28, cp) > 0)
            k->jong = cp;
        else if (k->jong && (joined = join(JONG_PAIRS, NPAIRS(JONG_PAIRS), k->jong, cp)))
            k->jong = joined;
        else
            start(k, cp, 0);
    } else if (k->cho && !k->jung) {
        k->jung = cp;
    } else if (k->jong) {
        /* The last consonant moves on to begin the next syllable. */
        const int *pair = split(JONG_PAIRS, NPAIRS(JONG_PAIRS), k->jong);
        int moved = pair ? pair[1] : k->jong;
        k->jong = pair ? pair[0] : 0;
        start(k, moved, cp);
    } else {
        int joined = k->jung ? join(VOWEL_PAIRS, NPAIRS(VOWEL_PAIRS), k->jung, cp) : 0;
        if (joined)
            k->jung = joined;
        else
            start(k, 0, cp);
    }
    kb_sync(k);
}

void kb_backspace(Keyboard *k)
{
    const int *pair;
    if (k->jong) {
        pair = split(JONG_PAIRS, NPAIRS(JONG_PAIRS), k->jong);
        k->jong = pair ? pair[0] : 0;
    } else if (k->jung) {
        pair = split(VOWEL_PAIRS, NPAIRS(VOWEL_PAIRS), k->jung);
        k->jung = pair ? pair[0] : 0;
    } else if (k->cho) {
        k->cho = 0;
    } else {
        int n = (int)strlen(k->base);
        while (n > 0 && ((unsigned char)k->base[n - 1] & 0xC0) == 0x80)
            n--;
        if (n > 0)
            n--;
        k->base[n] = 0;
    }
    kb_sync(k);
}

void kb_append(Keyboard *k, const char *s)
{
    hangul_commit(k);
    if (strlen(k->base) + strlen(s) < sizeof(k->base) - 1)
        strcat(k->base, s);
    kb_sync(k);
}
