/* isopod-thumb IN OUT SIDE: the picture in IN, of whatever kind SDL_image reads, written to
 * OUT as a PNG no longer than SIDE on its longer side. isopodd runs it on each station logo
 * it fetches, so the shell only ever opens small pictures of a kind it knows. */
#define SDL_MAIN_HANDLED
#include <SDL.h>
#include <SDL_image.h>
#include <stdlib.h>
#include <sys/resource.h>

int main(int argc, char **argv)
{
    /* A small file can unpack into a picture larger than the memory there is. */
    setrlimit(RLIMIT_DATA, &(struct rlimit){160u << 20, 160u << 20});
    int side = argc == 4 ? atoi(argv[3]) : 0;
    SDL_Surface *raw = side > 0 ? IMG_Load(argv[1]) : NULL;
    SDL_Surface *src = raw ? SDL_ConvertSurfaceFormat(raw, SDL_PIXELFORMAT_ARGB8888, 0) : NULL;
    if (!src || src->w < 1 || src->h < 1)
        return 1;
    int longer = src->w > src->h ? src->w : src->h;
    int w = longer > side ? src->w * side / longer : src->w, h = longer > side ? src->h * side / longer : src->h;
    SDL_Surface *out = SDL_CreateRGBSurfaceWithFormat(0, w ? w : 1, h ? h : 1, 32, SDL_PIXELFORMAT_ARGB8888);
    if (!out)
        return 1;
    /* Each pixel is the mean of those it covers, colours weighed by how solid they are. */
    for (int y = 0; y < out->h; y++) {
        int y0 = y * src->h / out->h, y1 = (y + 1) * src->h / out->h;
        Uint32 *dst = (Uint32 *)((Uint8 *)out->pixels + y * out->pitch);
        for (int x = 0; x < out->w; x++) {
            int x0 = x * src->w / out->w, x1 = (x + 1) * src->w / out->w;
            Uint64 a = 0, r = 0, g = 0, b = 0, n = 0;
            for (int yy = y0; yy < (y1 > y0 ? y1 : y0 + 1); yy++) {
                const Uint32 *row = (const Uint32 *)((const Uint8 *)src->pixels + yy * src->pitch);
                for (int xx = x0; xx < (x1 > x0 ? x1 : x0 + 1); xx++, n++) {
                    Uint32 p = row[xx], pa = p >> 24;
                    a += pa;
                    r += ((p >> 16) & 255) * pa;
                    g += ((p >> 8) & 255) * pa;
                    b += (p & 255) * pa;
                }
            }
            dst[x] = a ? (Uint32)(a / n) << 24 | (Uint32)(r / a) << 16 | (Uint32)(g / a) << 8 | (Uint32)(b / a) : 0;
        }
    }
    return IMG_SavePNG(out, argv[2]) ? 1 : 0;
}
