PKG_CONFIG ?= pkg-config
CFLAGS ?= -O2
CFLAGS += -std=gnu11 -Wall -Wextra -Wno-unused-parameter -Isrc -Ithird_party/cjson
CFLAGS += $(shell $(PKG_CONFIG) --cflags sdl2 SDL2_ttf SDL2_image)
LDLIBS += $(shell $(PKG_CONFIG) --libs sdl2 SDL2_ttf SDL2_image) -lm

SRC = src/main.c src/ipc.c src/gfx.c src/views.c src/nav.c src/hangul.c src/util.c third_party/cjson/cJSON.c
HDR = src/shell.h src/theme.h

PREFIX ?= /usr
DESTDIR ?=

all: isopod-shell isopod-splash

isopod-shell: $(SRC) $(HDR)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRC) $(LDLIBS)

isopod-splash: src/splash.c
	$(CC) -O2 -std=gnu11 -Wall -Wextra $(LDFLAGS) -o $@ $<

install: isopod-shell
	install -Dm755 isopod-shell $(DESTDIR)$(PREFIX)/bin/isopod-shell

clean:
	rm -f isopod-shell isopod-splash

.PHONY: all install clean
