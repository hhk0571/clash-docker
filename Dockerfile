FROM --platform=$BUILDPLATFORM alpine:3.19

# Build and target platform arguments for multi-arch support
ARG BUILDPLATFORM=linux/amd64
ARG TARGETPLATFORM=linux/amd64
ARG TARGETARCH=amd64

# Install necessary dependencies (tzdata needed for TZ environment variable)
RUN apk add --no-cache curl bash wget gzip tar tzdata

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
    chmod +x /usr/local/bin/clash

# Download and extract MetaCubeXD dashboard
RUN mkdir -p /root/.config/clash/dashboard && \
    METACUBEXD_VERSION="v1.241.0" && \
    echo "Downloading MetaCubeXD dashboard ${METACUBEXD_VERSION}..." && \
    wget -O /tmp/dashboard.tgz "https://github.com/MetaCubeX/metacubexd/releases/download/${METACUBEXD_VERSION}/compressed-dist.tgz" && \
    tar -xzf /tmp/dashboard.tgz -C /root/.config/clash/dashboard/ && \
    rm /tmp/dashboard.tgz && \
    echo "MetaCubeXD dashboard downloaded and extracted"

# Download GeoIP database and copy to runtime directory
RUN echo "Downloading GeoIP database..." && \
    wget -O /root/.config/clash/geoip.metadb \
    https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/geoip.metadb && \
    echo "GeoIP database downloaded: $(ls -lh /root/.config/clash/geoip.metadb | awk '{print $5}')"

# Copy configuration files
COPY config/config.yaml.example /config/config.yaml.example

# Expose the necessary ports (adjust as needed)
EXPOSE 7890 7891 9090

# Copy entrypoint script
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/entrypoint.sh"]
