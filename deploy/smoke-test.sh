#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-arclane.cloud}"
BASE_URL="https://${DOMAIN}"
EMAIL="${ARCLANE_SMOKE_EMAIL:-}"
PASSWORD="${ARCLANE_SMOKE_PASSWORD:-}"
DESCRIPTION="${ARCLANE_SMOKE_DESCRIPTION:-A workflow automation service for dental practices that reduces front-desk phone work.}"
COOKIE_JAR="$(mktemp)"
BODY_FILE="$(mktemp)"

cleanup() {
    rm -f "$COOKIE_JAR" "$BODY_FILE"
}
trap cleanup EXIT

say() {
    printf '[smoke] %s\n' "$1"
}

fetch_status() {
    local method="$1"
    local url="$2"
    local data="${3:-}"
    if [[ -n "$data" ]]; then
        curl -sS -o "$BODY_FILE" -w "%{http_code}" \
            -X "$method" \
            -H "Content-Type: application/json" \
            -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
            --data "$data" \
            "$url"
    else
        curl -sS -o "$BODY_FILE" -w "%{http_code}" \
            -X "$method" \
            -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
            "$url"
    fi
}

expect_status() {
    local got="$1"
    local expected="$2"
    local label="$3"
    if [[ "$got" != "$expected" ]]; then
        printf '[smoke] %s failed: expected %s, got %s\n' "$label" "$expected" "$got" >&2
        cat "$BODY_FILE" >&2 || true
        exit 1
    fi
}

say "Checking public routes"
status="$(fetch_status GET "${BASE_URL}/health")"
expect_status "$status" "200" "health"

status="$(fetch_status GET "${BASE_URL}/")"
expect_status "$status" "200" "landing"

status="$(fetch_status GET "${BASE_URL}/dashboard")"
expect_status "$status" "200" "dashboard shell"

status="$(fetch_status GET "${BASE_URL}/live")"
expect_status "$status" "200" "live page"

if [[ -z "$EMAIL" || -z "$PASSWORD" ]]; then
    say "Skipping authenticated flow. Set ARCLANE_SMOKE_EMAIL and ARCLANE_SMOKE_PASSWORD to continue."
    exit 0
fi

say "Registering or logging in test account"
register_payload="$(printf '{"email":"%s","password":"%s"}' "$EMAIL" "$PASSWORD")"
status="$(fetch_status POST "${BASE_URL}/api/auth/register" "$register_payload")"
if [[ "$status" == "409" ]]; then
    status="$(fetch_status POST "${BASE_URL}/api/auth/login" "$register_payload")"
    expect_status "$status" "200" "login"
else
    expect_status "$status" "201" "register"
fi

say "Validating browser session"
status="$(fetch_status GET "${BASE_URL}/api/auth/validate")"
expect_status "$status" "200" "session validate"

say "Creating a smoke-test business"
create_payload="$(ARCLANE_SMOKE_DESCRIPTION="$DESCRIPTION" python - <<'PY'
import json
import os
print(json.dumps({
    "description": os.environ["ARCLANE_SMOKE_DESCRIPTION"],
}))
PY
)"
status="$(fetch_status POST "${BASE_URL}/api/businesses" "$create_payload")"
if [[ "$status" != "201" ]]; then
    printf '[smoke] business creation returned %s\n' "$status" >&2
    cat "$BODY_FILE" >&2 || true
    exit 1
fi

subdomain="$(python - "$BODY_FILE" <<'PY'
import json
import sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
print(data["subdomain"])
PY
)"

say "Checking tenant subdomain ${subdomain}"
status="$(curl -sS -o "$BODY_FILE" -w "%{http_code}" "https://${subdomain}")"
if [[ "$status" != "200" && "$status" != "502" && "$status" != "503" ]]; then
    printf '[smoke] tenant subdomain check failed with %s\n' "$status" >&2
    cat "$BODY_FILE" >&2 || true
    exit 1
fi

say "Listing businesses"
status="$(fetch_status GET "${BASE_URL}/api/businesses")"
expect_status "$status" "200" "business list"

say "Smoke test passed"
