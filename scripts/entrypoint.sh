#!/bin/sh
set -eo pipefail

. /app/scripts/update_subscription.sh

on_exit() {
  log "Stopping..."
  [ -n "${SUBCONVERTER_PID:-}" ] && kill "$SUBCONVERTER_PID" 2>/dev/null || true
  wait "${SUBCONVERTER_PID:-}" 2>/dev/null || true
}

setup_cron() {
    # Quick test override: run every minute when CLASH_CRON_TEST is set
    if [ "${CLASH_CRON_TEST:-}" = "1" ]; then
        CRON_SCHEDULE="*/1 * * * *"
        log "➡️ cron test enabled: using schedule ${CRON_SCHEDULE} (every minute)"
    else
        # Determine cron schedule. Default: daily at 03:00
        CRON_SCHEDULE="0 ${CLASH_UPDATE_HOUR:-3} * * *"

        # If CLASH_UPDATE_INTERVAL is set to a positive integer, schedule every N hours
        # on the hour (e.g. CLASH_UPDATE_INTERVAL=3 -> "0 */3 * * *").
        if [ -n "${CLASH_UPDATE_INTERVAL:-}" ]; then
            case "${CLASH_UPDATE_INTERVAL}" in
                ''|*[!0-9]*)
                    log "⚠️ invalid CLASH_UPDATE_INTERVAL='${CLASH_UPDATE_INTERVAL}', falling back to default schedule ${CRON_SCHEDULE}"
                    ;;
                *)
                    if [ "${CLASH_UPDATE_INTERVAL}" -le 0 ]; then
                        log "⚠️ CLASH_UPDATE_INTERVAL must be >0; ignoring"
                    elif [ "${CLASH_UPDATE_INTERVAL}" -eq 1 ]; then
                        CRON_SCHEDULE="0 * * * *"
                        log "🕝 cron enabled: every hour (CLASH_UPDATE_INTERVAL=1)"
                    elif [ "${CLASH_UPDATE_INTERVAL}" -ge 24 ]; then
                        log "⚠️ CLASH_UPDATE_INTERVAL=${CLASH_UPDATE_INTERVAL} >=24 not supported, using default ${CRON_SCHEDULE}"
                    else
                        CRON_SCHEDULE="0 */${CLASH_UPDATE_INTERVAL} * * *"
                        log "🕝 cron enabled: every ${CLASH_UPDATE_INTERVAL} hours (schedule: ${CRON_SCHEDULE})"
                    fi
                    ;;
            esac
        else
            log "📅 cron enabled: daily at ${CLASH_UPDATE_HOUR:-3}:00 (schedule: ${CRON_SCHEDULE})"
        fi
    fi

    # Write crontab line; ensure absolute paths and inline env exports.
    # Run the updater via `sh -lc '...'` so output goes to container stdout
    # (Docker captures PID 1's stdout). Do NOT persist logs to /app/logs.
    printf '%s\n' "${CRON_SCHEDULE} sh -lc '/app/scripts/update_subscription.sh >/proc/1/fd/1 2>&1'" > /etc/crontabs/root || log "⚠️ warn: cannot write /etc/crontabs/root"
    crond || log "⚠️ warn: crond failed to start" || true
    log "⏰ crond started with schedule: ${CRON_SCHEDULE}"
}

trap on_exit INT TERM

mkdir -p "${CLASH_CONFIG_DIR}"

# 判断是否要启动subconverter服务
if [ "${SUBCONVERTER_ENABLED:-}" = "true" ]; then
    log "🔀 Starting subconverter service..."
    /app/tools/subconverter/subconverter >/proc/1/fd/1 2>&1 &
    SUBCONVERTER_PID=$!
    log "🔀 Subconverter started with PID ${SUBCONVERTER_PID}"
    # 等待subconverter服务启动完成，最多等待30秒
    timeout=30
    while ! curl -sS --connect-timeout 1 "${SUBCONVERTER_URL%/}/version" >/dev/null 2>&1; do
        timeout=$((timeout - 1))
        if [ $timeout -le 0 ]; then
            log "⚠️ Subconverter service failed to start within expected time"
            break
        fi
        sleep 1
    done
    log "✅ Subconverter service is running and ready to accept requests"
else
    log "🚫 Subconverter service is disabled (SUBCONVERTER_ENABLED=${SUBCONVERTER_ENABLED:-false})"
fi


# Priority 1: Subscription URL (if provided in environment)
if [ -n "$CLASH_SUBSCRIPTION_URL" ]; then
    log "🌐 Using subscription URL for configuration"
    if ! download_config "$CLASH_SUBSCRIPTION_URL" "$CLASH_CONFIG_FILE"; then
        log "❌ Failed to download from subscription URL, using fallback"
        if [ -f "$APP_CONFIG_FILE" ]; then
            cp "$APP_CONFIG_FILE" "$CLASH_CONFIG_FILE"
        elif [ -f /config/config.yaml.example ]; then
            cp /config/config.yaml.example "$CLASH_CONFIG_FILE"
        fi
    fi

# Priority 2: Use config from mounted volume
elif [ -f "$APP_CONFIG_FILE" ]; then
    log "📙 Using config from /app/config/config.yaml"
    cp "$APP_CONFIG_FILE" "$CLASH_CONFIG_FILE"

# Priority 3: Use example config
elif [ -f /config/config.yaml.example ]; then
    log "📄 Using example config"
    cp /config/config.yaml.example "$CLASH_CONFIG_FILE"
fi

# Update geoip.metadb from external mount if provided
if [ -f /app/config/geoip.metadb ]; then
    log "🌏 Updating geoip.metadb from /app/config/"
    cp /app/config/geoip.metadb "$CLASH_CONFIG_DIR/geoip.metadb"
fi
apply_config_overrides "$CLASH_CONFIG_FILE"


# Only enable the cron updater when a subscription URL is provided
if [ -n "${CLASH_SUBSCRIPTION_URL:-}" ]; then
    setup_cron
else
    log "ℹ️  No subscription URL provided; do not start cron updater"
fi

# Start the Clash application (foreground)
log "🚀 Starting Clash..."
exec /usr/local/bin/clash -d "$CLASH_CONFIG_DIR" -ext-ui dashboard
