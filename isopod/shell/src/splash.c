/* isoPod's boot screen. ROCKNIX draws its logo from the initramfs and leaves it on the
 * framebuffer until the UI starts; once the system proper is running this puts isoPod's own
 * picture there in its place. Usage: isopod-splash [picture.ppm [framebuffer]] */
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    FILE *f = fopen(argc > 1 ? argv[1] : "/usr/share/isopod/splash.ppm", "rb");
    int w, h, max;
    if (!f || fscanf(f, "P6 %d %d %d", &w, &h, &max) != 3 || max != 255 || fgetc(f) == EOF)
        return 1;
    uint8_t *rgb = malloc((size_t)w * h * 3);
    if (!rgb || fread(rgb, 3, (size_t)w * h, f) != (size_t)w * h)
        return 1;

    int fb = open(argc > 2 ? argv[2] : "/dev/fb0", O_WRONLY);
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    if (fb < 0 || ioctl(fb, FBIOGET_VSCREENINFO, &var) || ioctl(fb, FBIOGET_FSCREENINFO, &fix))
        return 1;
    int bytes = var.bits_per_pixel / 8;
    if ((bytes != 4 && bytes != 2) || w > (int)var.xres || h > (int)var.yres)
        return 1;

    size_t line = (size_t)var.xres * bytes;
    uint8_t *row = calloc(1, line);
    if (!row)
        return 1;
    for (uint32_t y = 0; y < var.yres; y++)
        if (pwrite(fb, row, line, (off_t)(y + var.yoffset) * fix.line_length + (off_t)var.xoffset * bytes) < 0)
            return 1;

    int x0 = ((int)var.xres - w) / 2, y0 = ((int)var.yres - h) / 2;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            const uint8_t *p = rgb + ((size_t)y * w + x) * 3;
            if (bytes == 4) {
                uint32_t c = (uint32_t)p[0] << 16 | p[1] << 8 | p[2];
                memcpy(row + x * 4, &c, 4);
            } else {
                uint16_t c = (p[0] >> 3) << 11 | (p[1] >> 2) << 5 | p[2] >> 3;
                memcpy(row + x * 2, &c, 2);
            }
        }
        off_t at = (off_t)(y0 + y + var.yoffset) * fix.line_length + (off_t)(x0 + var.xoffset) * bytes;
        if (pwrite(fb, row, (size_t)w * bytes, at) < 0)
            return 1;
    }
    return 0;
}
