# Test image matching the Raspberry Pi OS userland (Debian trixie). Not a Pi:
# no VC4, HDMI or LightDM session. See tests/run-container.sh.
FROM debian:trixie-slim
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential pkg-config libx11-dev libxrandr-dev libdrm-dev \
      python3 edid-decode shellcheck xvfb x11-xserver-utils xauth \
      initramfs-tools-core cpio zstd \
 && rm -rf /var/lib/apt/lists/*
RUN useradd -m tester
WORKDIR /src
