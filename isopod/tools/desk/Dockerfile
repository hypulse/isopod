# A headless desktop for recording isoPod with real apps: sway, mpv and KOReader.
FROM isopod-dev

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      sway wtype wf-recorder grim mpv xz-utils \
      libgl1-mesa-dri libegl1 libegl-mesa0 libgles2 \
      libxkbcommon0 libwayland-client0 libwayland-egl1 libwayland-cursor0 \
 && rm -rf /var/lib/apt/lists/*

# The release the image's recipe installs (projects/ROCKNIX/packages/isopod/koreader).
ARG KOREADER=https://github.com/koreader/koreader/releases/download/v2026.07.1/koreader-linux-arm64-v2026.07.1.tar.xz
ARG KOREADER_SHA256=68b399a99a88bf3f036f5e8b67d043215a28e11d375cf0823900aef7b1523a0b
RUN curl -fsSL -o /tmp/koreader.tar.xz "$KOREADER" \
 && echo "$KOREADER_SHA256  /tmp/koreader.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/koreader.tar.xz -C /usr && rm /tmp/koreader.tar.xz

# mpv draws with shared memory and stays silent; there is no GPU or sound card here.
RUN printf '#!/bin/sh\nexec /usr/bin/mpv --vo=wlshm --ao=null "$@"\n' > /usr/local/bin/mpv \
 && chmod +x /usr/local/bin/mpv
