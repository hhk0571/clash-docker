FROM --platform=$BUILDPLATFORM alpine:3.19

# Build and target platform arguments for multi-arch support
ARG BUILDPLATFORM=linux/amd64
ARG TARGETPLATFORM=linux/amd64
ARG TARGETARCH=amd64
# Install necessary dependencies (tzdata needed for TZ environment variable)
RUN { [ -n "${http_proxy}" ] && echo "Using proxy: ${http_proxy}" || true; } \
    && apk add --no-cache curl bash wget gzip tar tzdata dcron \
    && rm -rf /tmp/* /var/tmp/* /var/lib/apt/lists/* /var/cache/apt/archives/*

# Set the working directory
WORKDIR /clash-for-linux

# Download Clash Meta binary based on target architecture
RUN MIHOMO_VERSION="v1.19.20" && \
    case ${TARGETARCH} in \
        amd64) \
            CLASH_ARCH="linux-amd64" ;; \
        arm64) \
            CLASH_ARCH="linux-arm64" ;; \
        arm) \
            CLASH_ARCH="linux-armv7" ;; \
        *) \
            echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    echo "Building for ${TARGETPLATFORM}, downloading Mihomo ${MIHOMO_VERSION} for $CLASH_ARCH..." && \
    wget -O /tmp/clash.gz "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-${CLASH_ARCH}-${MIHOMO_VERSION}.gz" && \
    gunzip /tmp/clash.gz && \
    mv /tmp/clash /usr/local/bin/clash && \
    chmod +x /usr/local/bin/clash #&& \
    rm -rf /tmp/* /var/tmp/* && \
    echo "Mihomo ${MIHOMO_VERSION} downloaded and installed as /usr/local/bin/clash"

# Download and extract MetaCubeXD dashboard
RUN mkdir -p /root/.config/clash/dashboard && \
    METACUBEXD_VERSION="v1.241.0" && \
    echo "Downloading MetaCubeXD dashboard ${METACUBEXD_VERSION}..." && \
    wget -O /tmp/dashboard.tgz "https://github.com/MetaCubeX/metacubexd/releases/download/${METACUBEXD_VERSION}/compressed-dist.tgz" && \
    tar -xzf /tmp/dashboard.tgz -C /root/.config/clash/dashboard/ && \
    rm -rf /tmp/* /var/tmp/* && \
    echo "MetaCubeXD dashboard downloaded and extracted"

# Download GeoIP database and copy to runtime directory
RUN mkdir -p /root/.config/clash && \
    echo "Downloading GeoIP database..." && \
    wget -O /root/.config/clash/geoip.metadb \
    https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb && \
    rm -rf /tmp/* /var/tmp/* && \
    echo "GeoIP database downloaded: $(ls -lh /root/.config/clash/geoip.metadb | awk '{print $5}')"


# Download subconverter and copy to runtime directory
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
