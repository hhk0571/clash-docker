#!/bin/sh
set -e

# healthcheck(): returns 0 when healthy, non-zero otherwise.
# Uses $CLASH_API_SECRET if set to add Authorization header.
healthcheck() {
    url="http://127.0.0.1:9090/version"

    if [ -n "${CLASH_API_SECRET}" ]; then
        http_code=$(curl -s --connect-timeout 2 --max-time 5 -o /dev/null -w "%{http_code}" -H "Authorization: Bearer ${CLASH_API_SECRET}" "$url" || echo "000")
    else
        http_code=$(curl -s --connect-timeout 2 --max-time 5 -o /dev/null -w "%{http_code}" "$url" || echo "000")
    fi

    if [ "$http_code" = "200" ]; then
        echo "clash: healthy (HTTP $http_code)"
        return 0
    else
        echo "clash: unhealthy (HTTP $http_code)"
        return 1
    fi
}

if [ "$(basename "$0")" = "healthcheck.sh" ]; then
    healthcheck
fi
