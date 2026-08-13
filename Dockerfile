FROM alpine:3.19

# Platform args are injected by buildx per target platform.
# Do NOT set defaults here — "=linux/amd64" / "=amd64" would override buildx
# and every arch manifest would download amd64 binaries (see issue #arm64).
ARG TARGETPLATFORM
ARG TARGETARCH
ARG CACHEBUST
# Install necessary dependencies (tzdata needed for TZ environment variable)
RUN echo "cache=${CACHEBUST:-}" > /dev/null \
    && { [ -n "${http_proxy}" ] && echo "Using proxy: ${http_proxy}" || true; } \
    && apk add --no-cache curl bash wget gzip tar tzdata dcron \
    && rm -rf /tmp/* /var/tmp/* /var/lib/apt/lists/* /var/cache/apt/archives/*

# Set the working directory
WORKDIR /clash-for-linux

# Download Clash Meta binary based on target architecture.
# amd64: use mihomo-linux-amd64-v1-<version>.gz (x86-64-v1 baseline). The short name
# mihomo-linux-amd64-<version>.gz is the v3 microarchitecture build and fails on older
# CPUs with: "This program can only be run on AMD64 processors with v3 microarchitecture support."
ARG TARGETARCH
RUN MIHOMO_VERSION="v1.19.24" && \
    case ${TARGETARCH} in \
        amd64) \
            CLASH_ASSET="mihomo-linux-amd64-v1-${MIHOMO_VERSION}.gz" ;; \
        arm64) \
            CLASH_ASSET="mihomo-linux-arm64-${MIHOMO_VERSION}.gz" ;; \
        arm) \
            CLASH_ASSET="mihomo-linux-armv7-${MIHOMO_VERSION}.gz" ;; \
        *) \
            echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    echo "Building for ${TARGETPLATFORM}, downloading Mihomo ${MIHOMO_VERSION} (${CLASH_ASSET})..." && \
    wget -O /tmp/clash.gz "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/${CLASH_ASSET}" && \
    gunzip /tmp/clash.gz && \
    mv /tmp/clash /usr/local/bin/clash && \
    chmod +x /usr/local/bin/clash && \
    rm -rf /tmp/* /var/tmp/* && \
    echo "Mihomo ${MIHOMO_VERSION} downloaded and installed as /usr/local/bin/clash"

# Download and extract MetaCubeXD dashboard
RUN mkdir -p /root/.config/clash/dashboard && \
    METACUBEXD_VERSION="v1.247.1" && \
    echo "Downloading MetaCubeXD dashboard ${METACUBEXD_VERSION}..." && \
    wget -O /tmp/dashboard.tgz "https://github.com/MetaCubeX/metacubexd/releases/download/${METACUBEXD_VERSION}/compressed-dist.tgz" && \
    tar -xzf /tmp/dashboard.tgz -C /root/.config/clash/dashboard/ && \
    rm -rf /tmp/* /var/tmp/* && \
    echo "MetaCubeXD dashboard downloaded and extracted"

# Download GeoIP database and geosite database into the runtime directory.
# Failure-tolerant strategy (geo data is degradable, so it must not abort the build):
#   1. Each URL is retried up to 3 times;
#   2. If GEO_DAT_URL (custom mirror) is set it is tried first, then falls back
#      to the official GitHub release and a public mirror;
#   3. If every attempt fails the build continues with a warning — the missing
#      file can later be provided via the /app/config mount, or mihomo will try
#      to fetch it itself at startup.
# Note: downloads of the mihomo binary / dashboard / subconverter stay fatal,
# because the image cannot work without them.
ARG GEO_DAT_URL=""
RUN set -u; \
    mkdir -p /root/.config/clash; \
    if [ -n "${GEO_DAT_URL:-}" ]; then \
        GEO_URL_LIST="${GEO_DAT_URL} https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest https://ghfast.top/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest"; \
    else \
        GEO_URL_LIST="https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest https://ghfast.top/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest"; \
    fi; \
    fetch_geo() { \
        _file="$1"; \
        for _base in $GEO_URL_LIST; do \
            _try=1; \
            while [ "$_try" -le 3 ]; do \
                echo "⬇️  Downloading ${_file} from ${_base} (attempt ${_try}/3) ..."; \
                if wget -T 30 -q -O "/root/.config/clash/${_file}" "${_base}/${_file}"; then \
                    echo "✅ ${_file} downloaded from ${_base}"; \
                    return 0; \
                fi; \
                rm -f "/root/.config/clash/${_file}"; \
                _try=$((_try + 1)); \
                sleep 3; \
            done; \
        done; \
        echo "⚠️ WARNING: failed to download ${_file} from all sources; build continues."; \
        echo "    You can provide it later via the /app/config mount (${_file})."; \
        return 0; \
    }; \
    fetch_geo geoip.metadb; \
    fetch_geo geosite.dat; \
    rm -rf /tmp/* /var/tmp/*; \
    echo "Geo databases present in /root/.config/clash:"; ls -lh /root/.config/clash/ || true


# Download subconverter and copy to runtime directory
ARG TARGETARCH
RUN mkdir -p /app/tools && \
    SUBCONVERTER_VERSION="v0.9.0" && \
    case ${TARGETARCH} in \
        amd64) \
            SUBCONVERTER_ARCH="linux64" ;; \
        arm64) \
            SUBCONVERTER_ARCH="aarch64" ;; \
        arm) \
            SUBCONVERTER_ARCH="armv7" ;; \
        *) \
            echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    echo "Downloading subconverter ${SUBCONVERTER_VERSION} for $SUBCONVERTER_ARCH..." && \
    wget -O /tmp/subconverter.tar.gz "https://github.com/tindy2013/subconverter/releases/download/${SUBCONVERTER_VERSION}/subconverter_${SUBCONVERTER_ARCH}.tar.gz" && \
    tar -xzf /tmp/subconverter.tar.gz -C /app/tools && \
    chmod +x /app/tools/subconverter/subconverter && \
    rm -rf /tmp/* /var/tmp/* && \
    echo "Subconverter ${SUBCONVERTER_VERSION} downloaded and installed"

# Copy configuration files
COPY config/config.yaml.example /config/config.yaml.example

# Expose the necessary ports (adjust as needed)
EXPOSE 7890 7891 9090

# Copy healthcheck script and use it for Docker HEALTHCHECK
COPY scripts/*.sh /app/scripts/
RUN chmod +x /app/scripts/*.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "/app/scripts/healthcheck.sh"]

# Set the entrypoint
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
