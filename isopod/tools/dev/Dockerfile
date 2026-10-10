FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential pkg-config \
      libsdl2-dev libsdl2-ttf-dev libsdl2-image-dev \
      python3 python3-pil \
      mpd mpc ffmpeg \
      ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

ARG PRETENDARD=https://raw.githubusercontent.com/orioncactus/pretendard/v1.3.9/packages/pretendard/dist/public/static
RUN mkdir -p /usr/share/fonts/pretendard \
 && for w in Regular Medium SemiBold Bold; do \
      curl -fsSL -o /usr/share/fonts/pretendard/Pretendard-$w.otf $PRETENDARD/Pretendard-$w.otf; \
    done

WORKDIR /src
