#!/bin/sh

set -e

# Create a directory for Clash if it doesn't exist
mkdir -p /root/.config/clash /app/config

# Function to download configuration from subscription URL
download_config() {
    local url=$1
    local target=$2

    if [ -z "$url" ]; then
        return 1
    fi

    # Mask URL for privacy (show only domain)
    local masked_url=$(echo "$url" | sed -E 's|(https?://[^/]+/).*|\1***|')
    echo "[$(date)] Downloading configuration from: $masked_url"

    if curl -f -L -o "$target.tmp" "$url" 2>/dev/null; then
        mv "$target.tmp" "$target"
        echo "[$(date)] Configuration downloaded successfully"
        return 0
    else
        echo "[$(date)] Failed to download configuration"
        rm -f "$target.tmp"
        return 1
    fi
}

# Function to update configuration periodically
update_config_loop() {
    local url=$1
    local config_file=$2
    local interval=${3:-3600}  # Default: 1 hour

    while true; do
        sleep "$interval"
        echo "[$(date)] Checking for configuration updates..."

        # Download to a temp file first
        if curl -f -L -o "$config_file.tmp" "$url" 2>/dev/null; then
            mv "$config_file.tmp" "$config_file"
            echo "[$(date)] Configuration updated successfully"
        else
            echo "[$(date)] Failed to update configuration"
            rm -f "$config_file.tmp"
        fi
    done
}

CONFIG_FILE="/root/.config/clash/config.yaml"
SUBSCRIPTION_URL="${CLASH_SUBSCRIPTION_URL}"
UPDATE_INTERVAL="${CLASH_UPDATE_INTERVAL:-3600}"  # Default: 1 hour

# Priority 1: Subscription URL (if provided in environment)
if [ -n "$SUBSCRIPTION_URL" ]; then
    echo "Using subscription URL for configuration"
    download_config "$SUBSCRIPTION_URL" "$CONFIG_FILE" || {
        echo "Failed to download from subscription URL, using fallback"
        if [ -f /app/config/config.yaml ]; then
            cp /app/config/config.yaml "$CONFIG_FILE"
        elif [ -f /config/config.yaml.example ]; then
            cp /config/config.yaml.example "$CONFIG_FILE"
        fi
    }

    # Start background update loop
    update_config_loop "$SUBSCRIPTION_URL" "$CONFIG_FILE" "$UPDATE_INTERVAL" &
    UPDATE_PID=$!
    echo "Started config update loop (PID: $UPDATE_PID, interval: ${UPDATE_INTERVAL}s)"

# Priority 2: Use config from mounted volume
elif [ -f /app/config/config.yaml ]; then
    echo "Using config from /app/config/config.yaml"
    cp /app/config/config.yaml "$CONFIG_FILE"

# Priority 3: Use example config
elif [ -f /config/config.yaml.example ]; then
    echo "Using example config"
    cp /config/config.yaml.example "$CONFIG_FILE"
fi

# Update geoip.metadb from external mount if provided
if [ -f /app/config/geoip.metadb ]; then
    echo "Updating geoip.metadb from /app/config/"
    cp /app/config/geoip.metadb /root/.config/clash/geoip.metadb
fi

# Apply configuration overrides from environment variables
if [ -f "$CONFIG_FILE" ]; then
    # Override secret if provided
    if [ -n "$CLASH_SECRET" ]; then
        if grep -q '^secret:' "$CONFIG_FILE"; then
            echo "Setting secret for API authentication"
            sed -i "s|^secret:.*|secret: '$CLASH_SECRET'|" "$CONFIG_FILE"
        else
            echo "secret field not found in config, skipping override"
        fi
    fi

    # Override DNS enable setting if provided
    if [ -n "$CLASH_DNS_ENABLE" ]; then
        # Check if dns.enable exists in config
        if grep -q '^dns:' "$CONFIG_FILE" && grep -A 20 '^dns:' "$CONFIG_FILE" | grep -q '^\s*enable:'; then
            echo "Setting dns.enable to: $CLASH_DNS_ENABLE"
            # Update existing dns.enable value
            sed -i '/^dns:/,/^[^ ]/ { s/^\(  *\)enable: .*/\1enable: '"$CLASH_DNS_ENABLE"'/; }' "$CONFIG_FILE"
        else
            echo "dns.enable not found in config, skipping override"
        fi
    fi

    # Ensure external-ui is set if missing
    if ! grep -q '^external-ui:' "$CONFIG_FILE"; then
        echo "" >> "$CONFIG_FILE"
        echo "external-ui: dashboard" >> "$CONFIG_FILE"
    fi
fi

# Start the Clash application
exec /usr/local/bin/clash -d /root/.config/clash
