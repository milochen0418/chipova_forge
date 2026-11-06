# Chipova Forge's board image (profile "forge-board"): the schematic and PCB checks and outputs.
#
#   kicad-cli     KiCad 9 from Debian (trixie-backports): ERC, DRC with schematic parity,
#                 Gerbers, drill files, BOM, SVG previews. Forge writes the KiCad files itself;
#                 KiCad checks them and makes the fabrication outputs.
#   Freerouting   the autorouter (GPL-3.0, a separate program: Specctra DSN in, SES out), its
#                 jar on OpenJDK 25 (its native command-line build of 2.5.0 crashes mid-route:
#                 a GraalVM serialization error, then it hangs)
#   Python 3      the board scripts (standard library only), and KiCad's own pcbnew module,
#                 which fills the ground pours
#
# Build (from the repo root):
#   docker build -t chipova-forge/board:1 infra/dockerfiles/forge-board
# With the platform's download cache (re_ddns pkg_cache), add
#   --build-arg PKG_CACHE=http://host.docker.internal:3142
FROM debian:trixie-slim

ARG PKG_CACHE=
ARG FREEROUTING_VERSION=2.5.0
ARG FREEROUTING_SHA256=f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN set -eux; \
    printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie-backports\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' \
      > /etc/apt/sources.list.d/backports.sources; \
    if [ -n "$PKG_CACHE" ]; then \
      sed -i "s#http://deb.debian.org#${PKG_CACHE}/apt/deb.debian.org#g" /etc/apt/sources.list.d/*.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl python3 openjdk-25-jre-headless; \
    apt-get install -y --no-install-recommends -t trixie-backports kicad; \
    rm -rf /var/lib/apt/lists/*; \
    if [ -n "$PKG_CACHE" ]; then \
      sed -i "s#${PKG_CACHE}/apt/deb.debian.org#http://deb.debian.org#g" /etc/apt/sources.list.d/*.sources; \
    fi; \
    kicad-cli version

RUN set -eux; \
    base="${PKG_CACHE:+${PKG_CACHE}/github}"; base="${base:-https://github.com}"; \
    mkdir -p /opt/freerouting; \
    curl -fsSL -o /opt/freerouting/freerouting.jar "${base}/freerouting/freerouting/releases/download/v${FREEROUTING_VERSION}/freerouting-${FREEROUTING_VERSION}.jar"; \
    echo "${FREEROUTING_SHA256}  /opt/freerouting/freerouting.jar" | sha256sum -c -; \
    printf '#!/bin/sh\nexec java -Xmx1g -jar /opt/freerouting/freerouting.jar --gui.enabled=false "$@"\n' > /usr/local/bin/freerouting; \
    chmod 755 /usr/local/bin/freerouting

RUN useradd -m -u 1000 forge
ENV HOME=/home/forge
WORKDIR /work
