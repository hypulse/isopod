# A headless desktop for recording isoPod with real apps: sway, mpv and KOReader.
FROM isopod-dev

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      sway wtype wf-recorder grim mpv \
      libgl1-mesa-dri libegl1 libegl-mesa0 libgles2 \
      libxkbcommon0 libwayland-client0 libwayland-egl1 libwayland-cursor0 \
 && rm -rf /var/lib/apt/lists/*

ARG KOREADER=https://github.com/koreader/koreader/releases/download/v2026.07.1/koreader-linux-arm64-v2026.07.1.tar.xz
RUN curl -fsSL "$KOREADER" | tar -xJ -C /usr

# mpv draws with shared memory and stays silent; there is no GPU or sound card here.
RUN printf '#!/bin/sh\nexec /usr/bin/mpv --vo=wlshm --ao=null "$@"\n' > /usr/local/bin/mpv \
 && chmod +x /usr/local/bin/mpv
