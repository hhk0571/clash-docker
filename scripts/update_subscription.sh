#!/bin/sh
set -eo pipefail

# Paths and defaults
CLASH_CONFIG_DIR="/root/.config/clash"
CLASH_CONFIG_FILE="${CLASH_CONFIG_DIR}/config.yaml"
CLASH_SUBSCRIPTION_URL="${CLASH_SUBSCRIPTION_URL:-}"
CLASH_API="${CLASH_API:-http://127.0.0.1:9090}"
APP_CONFIG_DIR="/app/config"
APP_CONFIG_FILE="${APP_CONFIG_DIR}/config.yaml"

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }


download_config() {
    local url="$1"
    local target="$2"
    local tmp_target="${target}.tmp"

    if [ -z "$url" ]; then
        log "⚠️  URL not set, skipping download"
        return 1
    fi

    masked_url=$(echo "$url" | sed -E 's|(https?://[^/]+/).*|\1***|')
    log "📥 Downloading subscription from: $masked_url"

    MAX_RETRIES=3
    i=0
    while [ $i -lt $MAX_RETRIES ]; do
        i=$((i + 1))
        if curl -fsSL --retry 2 --max-time 30 -o "$tmp_target" "$url"; then
            break
        fi
        log "⚠️  Retry $i/$MAX_RETRIES failed"
        sleep 5
    done

    if [ ! -f "$tmp_target" ] || [ ! -s "$tmp_target" ]; then
        log "❌ Download failed or file empty"
        rm -f "$tmp_target" 2>/dev/null || true
        return 1
    fi

    # Basic validation
    if ! grep -q "^proxies:\|^proxies:" "$tmp_target" 2>/dev/null; then
        log "❌ Downloaded config looks invalid"
        rm -f "$tmp_target" 2>/dev/null || true
        return 1
    fi

    mv "$tmp_target" "$target"
    log "✅ Subscription downloaded to $target"

    cp "$target" "$APP_CONFIG_FILE" 2>/dev/null || true
    log "💾 Copied downloaded config to ${APP_CONFIG_FILE} for backup"
    return 0
}


# Apply configuration overrides from environment variables
apply_config_overrides() {
    local config_file="$1"
    [ -f "$config_file" ] || return 0

    # Override secret if provided
    if [ -n "$CLASH_SECRET" ]; then
        if grep -q '^secret:' "$config_file"; then
            log "🔒 Setting secret for API authentication"
            sed -i "s|^secret:.*|secret: '$CLASH_SECRET'|" "$config_file"
        else
            log "⚠️ secret field not found in config, skipping override"
        fi
    fi

    # Disable DNS only when explicitly requested
    if [ -n "${DISABLE_CLASH_DNS:-}" ]; then
        case "$(echo "$DISABLE_CLASH_DNS" | tr '[:upper:]' '[:lower:]')" in
            true|1|yes)
                if grep -q '^dns:' "$config_file" && grep -A 20 '^dns:' "$config_file" | grep -q '^\s*enable:'; then
                    log "⚙️  Disabling dns.enable per DISABLE_CLASH_DNS"
                    sed -i '/^dns:/,/^[^ ]/ { s/^\(  *\)enable: .*/\1enable: false/; }' "$config_file"
                else
                    log "⚠️ dns.enable not found in config, skipping override"
                fi
                ;;
            *)
                ;;
        esac
    fi
}


reload_clash() {
    log "🔄 Reloading Clash configuration via API: ${CLASH_API}/configs"
    local url="${CLASH_API%/}/configs/?force=true"
    local payload='{"path":"","payload":""}'

    set +e
    if [ -n "$CLASH_SECRET" ]; then
        resp=$(curl -X PUT "$url" -sS --connect-timeout 5 --max-time 10 -w '\n%{http_code}'  \
            -H "Authorization: Bearer ${CLASH_SECRET}" -H "Content-Type: application/json" -d "$payload" 2>&1)
    else
        resp=$(curl -X PUT "$url" -sS --connect-timeout 5 --max-time 10 -w '\n%{http_code}' \
            -H "Content-Type: application/json" -d "$payload" 2>&1)
    fi
    set -e

    http_code=$(echo "$resp" | tail -n1)
    msg=$(echo "$resp" | sed '$d')

    if [ "$http_code" = "200" ] || [ "$http_code" = "204" ]; then
        log "✅ Clash reloaded successfully (HTTP: $http_code, message: $msg)"
        return 0
    else
        log "⚠️ Failed to reload via API (HTTP: $http_code, message: $msg)"
        return 1
    fi
}


download_config_and_apply(){
    local url="$1"
    local target="$2"

    download_config "$url" "$target" || return 1
    apply_config_overrides "$target"
    reload_clash || return 1
    return 0
}


# Run update loop if this script is executed directly (not sourced)
if [ "$(basename "$0")" = "update_subscription.sh" ]; then
    log "⏰ Checking for configuration updates..."
    download_config_and_apply "$CLASH_SUBSCRIPTION_URL" "$CLASH_CONFIG_FILE"
fi
