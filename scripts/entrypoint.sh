#!/bin/sh

set -e

# Create a directory for Clash if it doesn't exist
mkdir -p /root/.config/clash

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

    if curl -f -L --connect-timeout 5 -o "$target.tmp" "$url" 2>/dev/null; then
        if [ -s "$target.tmp" ]; then
            mv "$target.tmp" "$target"
            echo "[$(date)] Configuration downloaded successfully"
            return 0
        fi

        echo "[$(date)] Downloaded configuration is empty"
        rm -f "$target.tmp"
        return 1
    else
        echo "[$(date)] Failed to download configuration"
        rm -f "$target.tmp"
        return 1
    fi
}

# Apply configuration overrides from environment variables
apply_config_overrides() {
    local config_file=$1

    if [ ! -f "$config_file" ]; then
        return 0
    fi

    # Override secret if provided
    if [ -n "$CLASH_SECRET" ]; then
        if grep -q '^secret:' "$config_file"; then
            echo "[$(date)] Setting secret for API authentication"
            sed -i "s|^secret:.*|secret: '$CLASH_SECRET'|" "$config_file"
        else
            echo "[$(date)] secret field not found in config, skipping override"
        fi
    fi

    # Disable DNS only when explicitly requested
    if [ -n "$DISABLE_CLASH_DNS" ]; then
        case "$(echo "$DISABLE_CLASH_DNS" | tr '[:upper:]' '[:lower:]')" in
            true|1|yes)
                if grep -q '^dns:' "$config_file" && grep -A 20 '^dns:' "$config_file" | grep -q '^\s*enable:'; then
                    echo "[$(date)] Disabling dns.enable per DISABLE_CLASH_DNS"
                    sed -i '/^dns:/,/^[^ ]/ { s/^\(  *\)enable: .*/\1enable: false/; }' "$config_file"
                else
                    echo "[$(date)] dns.enable not found in config, skipping override"
                fi
                ;;
            *)
                echo "[$(date)] DISABLE_CLASH_DNS is not true, skipping override"
                ;;
        esac
    fi
}


# Function to update configuration periodically
update_config_loop() {
    local url=$1
    local config_file=$2
    local interval=${3:-3600}  # Default: 1 hour
    local raw_config_file="/app/config/config.yaml"

    while true; do
        sleep "$interval"
        echo "[$(date)] Checking for configuration updates..."

        if download_config "$url" "$config_file"; then
            if [ -d /app/config ]; then
                cp "$config_file" "$raw_config_file"
                echo "[$(date)] Copied raw config to $raw_config_file"
            fi
            apply_config_overrides "$config_file"
            echo "[$(date)] Configuration updated successfully"
        else
            echo "[$(date)] Failed to update configuration"
        fi
    done
}

CONFIG_FILE="/root/.config/clash/config.yaml"
SUBSCRIPTION_URL="${CLASH_SUBSCRIPTION_URL}"
UPDATE_INTERVAL="${CLASH_UPDATE_INTERVAL:-3600}"  # Default: 1 hour

# Priority 1: Subscription URL (if provided in environment)
if [ -n "$SUBSCRIPTION_URL" ]; then
    echo "Using subscription URL for configuration"
    if download_config "$SUBSCRIPTION_URL" "$CONFIG_FILE"; then
        if [ -d /app/config ]; then
            cp "$CONFIG_FILE" /app/config/config.yaml
            echo "[$(date)] Copied raw config to /app/config/config.yaml"
        fi
    else
        echo "[$(date)] Failed to download from subscription URL, using fallback"
        if [ -f /app/config/config.yaml ]; then
            cp /app/config/config.yaml "$CONFIG_FILE"
        elif [ -f /config/config.yaml.example ]; then
            cp /config/config.yaml.example "$CONFIG_FILE"
        fi
    fi

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
    echo "[$(date)] Updating geoip.metadb from /app/config/"
    cp /app/config/geoip.metadb /root/.config/clash/geoip.metadb
fi

apply_config_overrides "$CONFIG_FILE"

# Start the Clash application
exec /usr/local/bin/clash -d /root/.config/clash -ext-ui dashboard
