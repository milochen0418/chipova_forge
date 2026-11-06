# Chipova Forge's simulation image (profile "forge-sim").
#
#   Verilator     the chip's own RTL, built into a library Renode loads (co-simulation)
#   Renode        the MCU, its peripherals, the sensors and the virtual time of the board
#   Arm GCC       the firmware (bare-metal C, newlib)
#   ngspice       the board's analog side: its shared library runs in step with Renode (the
#                 session agent's analog loop), the program for batch runs
#   Python 3      the session agent and the build scripts (standard library only)
#
# Build (Forge's Tools page does this; by hand from the repo root):
#   docker build -t chipova-forge/sim:1 infra/dockerfiles/forge-sim
# With the platform's download cache (re_ddns pkg_cache), add
#   --build-arg PKG_CACHE=http://host.docker.internal:3142
FROM debian:trixie-slim

ARG PKG_CACHE=
ARG TARGETARCH
ARG RENODE_VERSION=1.17.0
ARG RENODE_SHA256_ARM64=e9051578411441bfb47b77de0381cfba3badce03663367fab561472fd7807f1f
ARG RENODE_SHA256_AMD64=4ba7c68b59e2447f188ef4b4b112fcccd2802582c460beedceb63b84e5605a5f

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN set -eux; \
    if [ -n "$PKG_CACHE" ]; then \
      sed -i "s#http://deb.debian.org#${PKG_CACHE}/apt/deb.debian.org#g" /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl \
        verilator g++ make cmake \
        gcc-arm-none-eabi libnewlib-arm-none-eabi \
        ngspice libngspice0 \
        python3 python3-construct \
        libicu76; \
    rm -rf /var/lib/apt/lists/*; \
    if [ -n "$PKG_CACHE" ]; then \
      sed -i "s#${PKG_CACHE}/apt/deb.debian.org#http://deb.debian.org#g" /etc/apt/sources.list.d/debian.sources; \
    fi

# Renode, the portable build for this machine (it brings its own .NET runtime).
RUN set -eux; \
    case "${TARGETARCH:-arm64}" in \
      arm64) file="renode-${RENODE_VERSION}.linux-arm64-portable.tar.gz"; sum="${RENODE_SHA256_ARM64}";; \
      amd64) file="renode-${RENODE_VERSION}.linux-portable.tar.gz"; sum="${RENODE_SHA256_AMD64}";; \
      *) echo "no Renode build for ${TARGETARCH}"; exit 1;; \
    esac; \
    base="${PKG_CACHE:+${PKG_CACHE}/github}"; base="${base:-https://github.com}"; \
    curl -fsSL -o /tmp/renode.tar.gz "${base}/renode/renode/releases/download/v${RENODE_VERSION}/${file}"; \
    echo "${sum}  /tmp/renode.tar.gz" | sha256sum -c -; \
    mkdir -p /opt/renode; \
    tar xzf /tmp/renode.tar.gz -C /opt/renode --strip-components=1; \
    rm /tmp/renode.tar.gz; \
    ln -s /opt/renode/renode /usr/local/bin/renode; \
    renode --version; \
    # The co-simulation library of 1.17.0 (and upstream master, 2026-11) still calls the old
    # three-argument SocketCommunicationChannel::connect, so it doesn't compile; give it the
    # argument struct the channel takes now (only socket mode uses it; Forge loads the library).
    f=/opt/renode/plugins/IntegrationLibrary/src/renode_bus.cpp; \
    sed -i 's|channel->connect(receiverPort, senderPort, address);|SocketConnectionArgs args{receiverPort, senderPort, address, 3000}; channel->connect(\&args);|' "$f"; \
    grep -q 'SocketConnectionArgs args' "$f"

# Platform files Renode would download each time a session starts (sessions have no network):
# kept in the image, and the platforms that name them point at the copies.
ARG STM32F40X_SVD_SHA256=d71b3e11c870ec83346eb994e98a57c5eacdf0082d2949ed495d7a3828bf41ec
RUN set -eux; \
    mkdir -p /opt/renode/platforms/svd; \
    curl -fsSL --retry 5 --max-time 600 -o /opt/renode/platforms/svd/STM32F40x.svd.gz \
        https://dl.antmicro.com/projects/renode/svd/STM32F40x.svd.gz; \
    echo "${STM32F40X_SVD_SHA256}  /opt/renode/platforms/svd/STM32F40x.svd.gz" | sha256sum -c -; \
    sed -i 's|ApplySVD @https://dl.antmicro.com/projects/renode/svd/STM32F40x.svd.gz|ApplySVD @/opt/renode/platforms/svd/STM32F40x.svd.gz|' \
        /opt/renode/platforms/cpus/stm32f4.repl; \
    ! grep -n "@https://" /opt/renode/platforms/cpus/stm32f4.repl

ENV VERILATOR_ROOT=/usr/share/verilator \
    RENODE_ROOT=/opt/renode \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    PYTHONDONTWRITEBYTECODE=1

# The jobs and sessions run as this user (uid 1000), never as root.
RUN useradd -m -u 1000 forge
WORKDIR /work
