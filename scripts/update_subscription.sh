#!/bin/sh
set -eo pipefail

# Paths and defaults
CLASH_CONFIG_DIR="/root/.config/clash"
CLASH_CONFIG_FILE="${CLASH_CONFIG_DIR}/config.yaml"
CLASH_API="${CLASH_API:-http://127.0.0.1:9090}"
APP_CONFIG_DIR="/app/config"
APP_CONFIG_FILE="${APP_CONFIG_DIR}/config.yaml"

# -----------------------------
# Configurable environment variables
# - These can be provided via environment (docker-compose, systemd, etc.)
# - Defaults are safe for typical containers; override as needed.
# -----------------------------
# Subscription URL(s) to fetch config (required)
# Supports YAML block scalar in docker-compose: multiple URLs separated by newline.
# Can contain a single URL or multiple lines/CSV; parsed by the script.
CLASH_SUBSCRIPTION_URL="${CLASH_SUBSCRIPTION_URL:-}"
# Clash API secret (optional)
CLASH_API_SECRET="${CLASH_API_SECRET:-}"
# Allow skipping TLS certificate verification (false/true)
ALLOW_INSECURE_TLS="${ALLOW_INSECURE_TLS:-false}"
# Optional custom User-Agent for download (can help bypass UA filters)
SUBSCRIPTION_USER_AGENT="${SUBSCRIPTION_USER_AGENT:-Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36}"
# Optional custom headers to include in subscription download (single header string)
SUBSCRIPTION_HEADERS="${SUBSCRIPTION_HEADERS:-}"
# Option to disable Clash DNS behavior (true/false)
DISABLE_CLASH_DNS="${DISABLE_CLASH_DNS:-}"

# Subconverter controls
# Enable subconverter (true/false). When true, URLs will be sent to subconverter
# for conversion. Default: false
SUBCONVERTER_ENABLED="${SUBCONVERTER_ENABLED:-false}"
# Subconverter endpoint base (will call `${SUBCONVERTER_URL}/sub` with data-urlencode)
SUBCONVERTER_URL="${SUBCONVERTER_URL:-http://localhost:25500}"


log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Export variables for use in child processes (e.g. cron jobs)
export CLASH_SUBSCRIPTION_URL
export CLASH_API_SECRET
export SUBCONVERTER_URL


trim() { echo "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# Parse `CLASH_SUBSCRIPTION_URL` (may contain multiple URLs) into an array (split by newline, comma or semicolon)
gather_urls() {
    # sets global variable URLS_LIST as newline-separated entries
    URLS_LIST=""
    raw="$1"
    if [ -n "$raw" ]; then
        raw=$(printf '%s' "$raw" | tr -d '\r')
        raw=$(printf '%s' "$raw" | sed 's/[;,]/\n/g')
        while IFS= read -r line; do
            line=$(trim "$line")
            if [ -n "$line" ]; then
                if [ -z "$URLS_LIST" ]; then
                    URLS_LIST="$line"
                else
                    URLS_LIST="$URLS_LIST
$line"
                fi
            fi
        done <<EOF
$raw
EOF
    fi
}

## Download config from subconverter by calling its /sub endpoint with --data-urlencode.
download_config_from_subconverter() {
    # $1: urls (may be a single URL, or multiple joined by '|' or newline)
    local urls="$1"
    local target="$2"
    local tmp_target="${target}.tmp"

    if [ -z "$urls" ]; then
        log "⚠️  subconverter: urls empty, skipping"
        return 1
    fi

    endpoint="${SUBCONVERTER_URL%/}/sub"
    masked_url=$(echo "$urls" | sed -E 's|(https?://[^/]+/).*|\1***|')
    log "📤 Calling subconverter at $endpoint for: $masked_url"

    MAX_RETRIES=3
    i=0

    while [ $i -lt $MAX_RETRIES ]; do
        i=$((i + 1))
        set +e
        if [ -n "$SUBSCRIPTION_HEADERS" ]; then
            curl -fsSLG --retry 2 --max-time 60 -A "$SUBSCRIPTION_USER_AGENT" -H "$SUBSCRIPTION_HEADERS" \
                --data-urlencode "target=clash" --data-urlencode "url=$urls" -o "$tmp_target" "$endpoint"
        else
            curl -fsSLG --retry 2 --max-time 60 -A "$SUBSCRIPTION_USER_AGENT" \
                --data-urlencode "target=clash" --data-urlencode "url=$urls" -o "$tmp_target" "$endpoint"
        fi
        rc=$?
        set -e

        if [ $rc -eq 0 ] && [ -s "$tmp_target" ]; then
            break
        fi
        log "⚠️  subconverter curl retry $i/$MAX_RETRIES failed (rc=$rc)"
        rm -f "$tmp_target" 2>/dev/null || true
        sleep 3
    done

    if [ ! -f "$tmp_target" ] || [ ! -s "$tmp_target" ]; then
        log "❌ Subconverter download failed or file empty"
        rm -f "$tmp_target" 2>/dev/null || true
        return 1
    fi

    # Basic validation: ensure it at least looks like a Clash config
    if ! grep -Eq '^(proxies:|proxy-groups:|rules:|mixed-port:|port:)' "$tmp_target" 2>/dev/null; then
        log "❌ Subconverter returned invalid config (missing key fields)"
        rm -f "$tmp_target" 2>/dev/null || true
        return 1
    fi

    mv "$tmp_target" "$target"
    log "✅ Subconverter config downloaded to $target"
    cp "$target" "$APP_CONFIG_FILE" 2>/dev/null || true
    log "💾 Copied downloaded config to ${APP_CONFIG_FILE} for backup"
    return 0
}


download_config_from_url() {
    # $1: urls (may be newline/comma/semicolon separated). This function uses the FIRST URL only.
    local urls="$1"
    local target="$2"
    local tmp_target="${target}.tmp"

    if [ -z "$urls" ]; then
        log "⚠️  URL(s) not set, skipping download"
        return 1
    fi

    # pick first URL
    first=$(printf '%s\n' "$urls" | sed -n '1p')
    if [ -z "$first" ]; then
        log "⚠️  No valid first URL found, skipping"
        return 1
    fi

    masked_url=$(echo "$first" | sed -E 's|(https?://[^/]+/).*|\1***|')
    log "📥 Downloading subscription from: $masked_url"

    MAX_RETRIES=3
    i=0
    status_code=""

    # support insecure TLS (skip cert verification) if requested
    CURL_INSECURE=""
    WGET_INSECURE=""
    if [ "${ALLOW_INSECURE_TLS:-}" = "true" ]; then
        CURL_INSECURE="-k"
        WGET_INSECURE="--no-check-certificate"
        log "⚠️  ALLOW_INSECURE_TLS=true: skipping TLS certificate verification"
    fi

    # Try curl and capture HTTP status code; treat non-2xx as failure
    while [ $i -lt $MAX_RETRIES ]; do
        i=$((i + 1))
        # prefer a single curl invocation that follows redirects and fails on non-2xx
        if [ -n "$SUBSCRIPTION_HEADERS" ]; then
            status_code=$(curl -fsSL $CURL_INSECURE --retry 2 --max-time 30 -A "$SUBSCRIPTION_USER_AGENT" -H "$SUBSCRIPTION_HEADERS" -w "%{http_code}" -o "$tmp_target" "$first")
        else
            status_code=$(curl -fsSL $CURL_INSECURE --retry 2 --max-time 30 -A "$SUBSCRIPTION_USER_AGENT" -w "%{http_code}" -o "$tmp_target" "$first")
        fi
        rc=$?
        if [ $rc -eq 0 ] && echo "$status_code" | grep -Eq '^[23][0-9]{2}$'; then
            break
        fi
        log "⚠️  Retry $i/$MAX_RETRIES failed (curl rc=$rc http=$status_code)"
        rm -f "$tmp_target" 2>/dev/null || true
        sleep 5
    done

    # If curl didn't produce a usable file, try wget as a fallback
    if [ ! -f "$tmp_target" ] || [ ! -s "$tmp_target" ]; then
        log "ℹ️  curl failed or produced empty file, trying wget fallback"
        WGET_RETRIES=3
        j=0
        while [ $j -lt $WGET_RETRIES ]; do
            j=$((j + 1))
            if [ -n "$SUBSCRIPTION_HEADERS" ]; then
                if wget $WGET_INSECURE -q --user-agent="$SUBSCRIPTION_USER_AGENT" --header="$SUBSCRIPTION_HEADERS" -O "$tmp_target" "$first"; then
                    break
                fi
            else
                if wget $WGET_INSECURE -q --user-agent="$SUBSCRIPTION_USER_AGENT" -O "$tmp_target" "$first"; then
                    break
                fi
            fi
            log "⚠️  wget retry $j/$WGET_RETRIES failed"
            rm -f "$tmp_target" 2>/dev/null || true
            sleep 5
        done
    fi

    if [ ! -f "$tmp_target" ] || [ ! -s "$tmp_target" ]; then
        log "❌ Download failed or file empty"
        rm -f "$tmp_target" 2>/dev/null || true
        return 1
    fi

    # Basic validation: ensure it at least looks like a Clash config
    if ! grep -Eq '^(proxies:|proxy-groups:|rules:|mixed-port:|port:)' "$tmp_target" 2>/dev/null; then
        log "❌ Downloaded config looks invalid (missing key fields)"
        rm -f "$tmp_target" 2>/dev/null || true
        return 1
    fi

    mv "$tmp_target" "$target"
    log "✅ Subscription downloaded to $target"

    cp "$target" "$APP_CONFIG_FILE" 2>/dev/null || true
    log "💾 Copied downloaded config to ${APP_CONFIG_FILE} for backup"
    return 0
}


# Unified download entrypoint: choose subconverter or direct URL based on SUBCONVERTER_ENABLED
download_config() {
    # $1: raw urls (may be newline/comma/semicolon separated). If empty, fallback to envs.
    local raw_urls="$1"
    local target="$2"

    if [ -z "$raw_urls" ]; then
        raw_urls="$CLASH_SUBSCRIPTION_URL"
    fi

    gather_urls "$raw_urls" # 更新全局变量 URLS_LIST
    if [ -z "$URLS_LIST" ]; then
        log "❌ No subscription URL provided"
        return 1
    fi

    SUBCONVERTER_ENABLED_NORM=$(echo "${SUBCONVERTER_ENABLED:-false}" | tr '[:upper:]' '[:lower:]')
    if [ "$SUBCONVERTER_ENABLED_NORM" = "true" ]; then
        log "ℹ️ download_config: SUBCONVERTER_ENABLED=true -> using subconverter for urls"
        # Try combined conversion first
        joined=$(printf '%s\n' "$URLS_LIST" | tr '\n' '|' )
        joined=$(echo "$joined" | sed 's/|$//')
        if download_config_from_subconverter "$joined" "$target"; then
            return 0
        fi
        # fallback: try each individually
        while IFS= read -r u; do
            [ -z "$u" ] && continue
            if download_config_from_subconverter "$u" "$target"; then
                return 0
            fi
        done <<EOF
$URLS_LIST
EOF
        return 1
    else
        log "ℹ️ download_config: SUBCONVERTER_ENABLED!=true -> downloading first URL directly"
        download_config_from_url "$URLS_LIST" "$target"
        return $?
    fi
}


# Apply configuration overrides from environment variables
apply_config_overrides() {
    local config_file="$1"
    [ -f "$config_file" ] || return 0

    # Override secret if provided
    if [ -n "$CLASH_API_SECRET" ]; then
        if grep -q '^secret:' "$config_file"; then
            log "🔒 Setting secret for API authentication"
            sed -i "s|^secret:.*|secret: '$CLASH_API_SECRET'|" "$config_file"
        else
            log "🔒 secret field not found in config, adding it at the end"
            echo "secret: '$CLASH_API_SECRET'" >> "$config_file"
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
                    log "ℹ️  dns.enable not found in config, skipping override"
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
    if [ -n "$CLASH_API_SECRET" ]; then
        resp=$(curl -X PUT "$url" -sS --connect-timeout 5 --max-time 10 -w '\n%{http_code}'  \
            -H "Authorization: Bearer ${CLASH_API_SECRET}" -H "Content-Type: application/json" -d "$payload" 2>&1)
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
    # $1: raw URLs (may be newline/comma/semicolon separated). If empty, fallback to envs.
    local raw_urls="$1"
    local target="$2"

    download_config "$raw_urls" "$target" || return 1
    apply_config_overrides "$target"
    reload_clash || return 1
    return 0
}


# Run update loop if this script is executed directly (not sourced)
if [ "$(basename "$0")" = "update_subscription.sh" ]; then
    log "⏰ Checking for configuration updates..."
    download_config_and_apply "$CLASH_SUBSCRIPTION_URL" "$CLASH_CONFIG_FILE"
fi
