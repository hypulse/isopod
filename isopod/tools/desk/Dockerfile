# A headless desktop for recording isoPod with a real app: sway and mpv.
FROM isopod-dev

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      sway wf-recorder grim mpv \
      libgl1-mesa-dri libegl1 libegl-mesa0 libgles2 \
      libxkbcommon0 libwayland-client0 libwayland-egl1 libwayland-cursor0 \
 && rm -rf /var/lib/apt/lists/*

# mpv draws with shared memory and stays silent; there is no GPU or sound card here.
RUN printf '#!/bin/sh\nexec /usr/bin/mpv --vo=wlshm --ao=null "$@"\n' > /usr/local/bin/mpv \
 && chmod +x /usr/local/bin/mpv
