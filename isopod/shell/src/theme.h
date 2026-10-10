#ifndef ISOPOD_THEME_H
#define ISOPOD_THEME_H

/* 640x480 is exactly twice the iPod video's 320x240, so its proportions carry over at 2x. */
#define SCREEN_W 640
#define SCREEN_H 480

#define BAR_H 44
#define ROW_H 50
#define ROW_TALL_H 72
#define HEADER_H 34
#define PAD_X 22
#define ART_THUMB 56

/* Split menus keep the list on the left and an artwork panel on the right, as the last
 * iPod classic did. */
#define SPLIT_LIST_W 336

/* Cover Flow, and the card an album flips over to. */
#define CF_SIZE 232
#define CARD_W 336
#define CARD_H 360
#define CARD_HEAD 66
#define CARD_LIST_H (CARD_H - CARD_HEAD - 12)

#define FONT_BAR 22
#define FONT_ROW 25
#define FONT_VALUE 22
#define FONT_SUB 19
#define FONT_HEADER 17
#define FONT_NP_TITLE 30
#define FONT_NP_LINE 23
#define FONT_SMALL 19
#define FONT_KEY 26
#define FONT_BIG 34

#define ANIM_PUSH_MS 230
#define ANIM_SCROLL_MS 110
#define TOAST_MS 2200
#define HUD_MS 1500

typedef struct { unsigned char r, g, b, a; } Rgba;

#define RGB(r, g, b) ((Rgba){(r), (g), (b), 255})
#define RGBA(r, g, b, a) ((Rgba){(r), (g), (b), (a)})

#define C_BG RGB(255, 255, 255)
#define C_TEXT RGB(17, 17, 17)
#define C_TEXT2 RGB(110, 110, 115)
#define C_TEXT3 RGB(160, 160, 166)
#define C_WHITE RGB(255, 255, 255)
#define C_SEP RGB(229, 229, 234)
#define C_HEADER_BG RGB(242, 242, 247)
#define C_BAR_TOP RGB(250, 250, 251)
#define C_BAR_BOTTOM RGB(236, 236, 239)
#define C_BAR_LINE RGB(206, 206, 211)
#define C_SEL_TOP RGB(64, 140, 255)
#define C_SEL_BOTTOM RGB(34, 112, 244)
#define C_ACCENT RGB(28, 110, 226)
#define C_GREEN RGB(52, 199, 89)
#define C_RED RGB(255, 59, 48)
#define C_TRACK RGB(214, 214, 219)
#define C_SWITCH_OFF RGB(222, 222, 226)
#define C_HUD RGBA(28, 28, 30, 225)
#define C_DIM RGBA(0, 0, 0, 90)
#define C_SHEET RGB(248, 248, 250)
#define C_KEY RGB(255, 255, 255)
#define C_KEY_DARK RGB(200, 203, 210)
#define C_KEYBOARD_BG RGB(214, 217, 223)

#endif
