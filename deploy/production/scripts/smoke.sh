#!/usr/bin/env bash
# Checks a running deployment from outside, through the proxy only. Changes no data: every call is
# either a read or is refused before it reaches the application's logic.
#
#   smoke.sh <https-origin> [<http-origin>]
#   smoke.sh https://qarz.example.uz
#   SMOKE_INSECURE=1 smoke.sh https://127.0.0.1:18443 http://127.0.0.1:18480     (self-signed, local)
#
# Environment:
#   SMOKE_INSECURE=1     accept a certificate that cannot be verified (the local proof only)
#   SMOKE_AUTH_BURST=10  the burst of the sign-in limit in nginx/conf.d/qarz.conf
#   SMOKE_AUTH_WAIT=22   seconds to let the sign-in limit refill before testing it (an earlier run on
#                        the same address may have used it up); 0 to skip the wait
# Exit status 0 only when every check passed.
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: smoke.sh <https-origin> [<http-origin>]" >&2; exit 2; }
HTTPS="${1%/}"
HTTP="${2:-http://${HTTPS#https://}}"
HTTP="${HTTP%/}"
AUTH_BURST="${SMOKE_AUTH_BURST:-10}"
AUTH_WAIT="${SMOKE_AUTH_WAIT:-22}"
TLS=()
if [ "${SMOKE_INSECURE:-0}" = "1" ]; then TLS=(--insecure); fi

MIB=1048576
UUID=00000000-0000-4000-8000-000000000000
failed=0
STATUS=""
HEADERS=""

pass() { printf 'ok    %s\n' "$1"; }
fail() { printf 'FAIL  %s\n' "$1"; failed=$((failed + 1)); }
check() { # check <description> <command...>: passes when the command succeeds
  local description="$1"; shift
  if "$@"; then pass "$description"; else fail "$description"; fi
}

# Status and headers of the final answer; the body is thrown away.
probe() {
  HEADERS="$(curl -sS -o /dev/null -D - --max-time 30 "${TLS[@]}" "$@" 2>/dev/null | tr -d '\r' || true)"
  STATUS="$(printf '%s\n' "$HEADERS" | grep -E '^HTTP/' | tail -n 1 | awk '{print $2}' || true)"
}
body() { curl -sS --max-time 30 "${TLS[@]}" "$@" 2>/dev/null || true; }
header() { printf '%s\n' "$HEADERS" | grep -i "^$1:" | sed 's/^[^:]*: *//' || true; }
header_once() { [ "$(printf '%s\n' "$HEADERS" | grep -ci "^$1:" || true)" = "1" ]; }
header_has() { header "$1" | grep -qF -- "$2"; }
header_absent() { [ -z "$(header "$1")" ]; }
status_is() { [ "$STATUS" = "$1" ]; }
status_is_not() { [ -n "$STATUS" ] && [ "$STATUS" != "$1" ]; }
contains() { printf '%s' "$1" | grep -qF -- "$2"; }
zeros() { head -c "$1" /dev/zero; }

security_headers() { # the headers every answer must carry, each exactly once
  local what="$1" name
  for name in Strict-Transport-Security Content-Security-Policy X-Content-Type-Options Referrer-Policy \
    Permissions-Policy X-Request-Id; do
    check "$what: $name is sent once" header_once "$name"
  done
  check "$what: HSTS has a max-age of a year" header_has Strict-Transport-Security "max-age=31536000"
  check "$what: X-Content-Type-Options is nosniff" header_has X-Content-Type-Options "nosniff"
}

echo "smoke test of $HTTPS"

echo "# health"
probe "$HTTPS/healthz"
check "/healthz answers 200" status_is 200
check "/healthz says ok" contains "$(body "$HTTPS/healthz")" '"ok"'

echo "# plain HTTP"
probe "$HTTP/healthz"
check "HTTP is redirected (301)" status_is 301
check "the redirect goes to HTTPS" header_has Location "https://"
probe "$HTTP/api/v1/me"
check "HTTP serves no API (301)" status_is 301

echo "# security headers"
probe "$HTTPS/api/v1/me"
security_headers "API"
check "API: nothing may frame it" header_has Content-Security-Policy "frame-ancestors 'none'"
check "API: Cache-Control is no-store" header_has Cache-Control "no-store"
check "API: Cache-Control is sent once" header_once Cache-Control
for entry in panel admin; do
  probe "$HTTPS/$entry/"
  security_headers "/$entry/"
  check "/$entry/: nothing may frame it" header_has Content-Security-Policy "frame-ancestors 'none'"
  check "/$entry/: X-Frame-Options is DENY" header_has X-Frame-Options "DENY"
  check "/$entry/: scripts from itself and telegram.org only" header_has Content-Security-Policy "script-src 'self' https://telegram.org"
  check "/$entry/: the sign-in frame is oauth.telegram.org" header_has Content-Security-Policy "frame-src https://oauth.telegram.org"
  check "/$entry/: HTML is not cached" header_has Cache-Control "no-store"
done
probe "$HTTPS/app/"
security_headers "/app/"
check "/app/: Telegram's web client may frame it" header_has Content-Security-Policy "frame-ancestors https://web.telegram.org"
check "/app/: no X-Frame-Options (frame-ancestors decides)" header_absent X-Frame-Options
check "/app/: no eval" bash -c '! printf "%s" "$1" | grep -q unsafe-eval' _ "$(header Content-Security-Policy)"
check "/app/: HTML is not cached" header_has Cache-Control "no-store"

echo "# front-end entries"
for entry in app panel admin; do
  page="$(body "$HTTPS/$entry/")"
  probe "$HTTPS/$entry/"
  check "/$entry/ answers 200" status_is 200
  check "/$entry/ is HTML" header_has Content-Type "text/html"
  check "/$entry/ is the application page" contains "$page" '<div id="root">'
  asset="$(printf '%s' "$page" | grep -oE '/assets/[A-Za-z0-9._-]+\.js' | head -n 1 || true)"
  probe "$HTTPS${asset:-/assets/none}"
  check "/$entry/ script ${asset:-(none found)} answers 200" status_is 200
  check "/$entry/ script is cached as immutable" header_has Cache-Control "immutable"
  probe "$HTTPS/$entry/no/such/page"
  check "/$entry/ falls back to its page for an unknown path" status_is 200
done
probe "$HTTPS/assets/no-such-file.js"
check "a missing asset is 404, not a page" status_is 404

echo "# metrics are not reachable"
for path in /metrics /metrics/ //metrics /api/../metrics /api/%2e%2e/metrics /healthz/../metrics; do
  probe --path-as-is "$HTTPS$path"
  check "$path answers 404" status_is 404
  check "$path gives no metrics" bash -c '! printf "%s" "$1" | grep -q "qd_requests_total"' _ "$(body --path-as-is "$HTTPS$path")"
done
probe -H "Authorization: Bearer smoke" "$HTTPS/metrics"
check "/metrics with a bearer token answers 404" status_is 404

echo "# API without a session"
probe -H "X-Request-Id: smoke-chosen-by-caller" "$HTTPS/api/v1/me"
answer="$(body "$HTTPS/api/v1/me")"
check "an unauthenticated call answers 401" status_is 401
check "the answer is JSON" header_has Content-Type "application/json"
check "the answer has the API's error shape" contains "$answer" '"error"'
check "X-Request-Id is 8 to 64 safe characters" bash -c 'printf "%s" "$1" | grep -qE "^[A-Za-z0-9_-]{8,64}$"' _ "$(header X-Request-Id)"
check "X-Request-Id is the proxy's, not the caller's" bash -c '[ "$1" != smoke-chosen-by-caller ]' _ "$(header X-Request-Id)"
probe "$HTTPS/docs"
check "the API's documentation page is not published" status_is 404
probe "$HTTPS/tg/webhook"
check "GET /tg/webhook is refused (403)" status_is 403

echo "# request body limits"
# HTTP/1.1 with Expect, so the proxy can refuse before the body is sent and the answer is read cleanly.
big=(--http1.1 -X POST -H "Content-Type: application/octet-stream" -H "Expect: 100-continue" --data-binary @-)
probe "${big[@]}" "$HTTPS/api/v1/me" < <(zeros $((MIB + 1)))
check "1 MiB + 1 byte on an ordinary route answers 413" status_is 413
check "the 413 carries X-Request-Id" header_once X-Request-Id
check "the 413 is the API's error shape" contains "$(zeros $((MIB + 1)) | body "${big[@]}" "$HTTPS/api/v1/me")" '"BODY_TOO_LARGE"'
probe "${big[@]}" "$HTTPS/api/v1/me" < <(zeros "$MIB")
check "exactly 1 MiB is not refused for its size" status_is_not 413
probe "${big[@]}" "$HTTPS/api/v1/shops/$UUID/imports" < <(zeros $((MIB + 1)))
check "1 MiB + 1 byte on the import upload route is let through" status_is_not 413
probe "${big[@]}" "$HTTPS/api/v1/shops/$UUID/imports" < <(zeros $((5 * MIB + 1)))
check "5 MiB + 1 byte on the import upload route answers 413" status_is 413
probe "${big[@]}" "$HTTPS/api/v1/shops/$UUID/subscription/receipts" < <(zeros $((5 * MIB + 16 * 1024)))
check "5 MiB + 16 KiB on the receipt upload route is let through" status_is_not 413
probe "${big[@]}" "$HTTPS/api/v1/me/accounts/$UUID/payment-notices" < <(zeros $((5 * MIB + 16 * 1024 + 1)))
check "one byte more on the notice upload route answers 413" status_is 413
probe "${big[@]}" "$HTTPS/pay/payme" < <(zeros $((16 * 1024 + 1)))
check "16 KiB + 1 byte on /pay/ answers 413" status_is 413

echo "# sign-in rate limit by address (burst $AUTH_BURST)"
if [ "$AUTH_WAIT" != "0" ]; then sleep "$AUTH_WAIT"; fi
let_through=0
for _ in $(seq 1 $((AUTH_BURST + 1))); do
  probe -X POST -H "Content-Type: application/json" --data '{}' "$HTTPS/api/v1/auth/telegram-webapp"
  if [ -n "$STATUS" ] && [ "$STATUS" != "429" ]; then let_through=$((let_through + 1)); fi
done
check "the first $((AUTH_BURST + 1)) sign-in calls are let through (got $let_through)" [ "$let_through" -eq $((AUTH_BURST + 1)) ]
limited=0
for _ in 1 2 3 4; do
  probe -X POST -H "Content-Type: application/json" --data '{}' "$HTTPS/api/v1/auth/telegram-webapp"
  if [ "$STATUS" = "429" ]; then limited=1; break; fi
done
check "a further sign-in call answers 429" [ "$limited" -eq 1 ]
check "the 429 says when to retry" header_once Retry-After
check "the 429 carries X-Request-Id" header_once X-Request-Id
probe "$HTTPS/api/v1/me"
check "other routes are not limited with it" status_is 401

echo
if [ "$failed" -eq 0 ]; then
  echo "smoke: all checks passed"
else
  echo "smoke: $failed check(s) FAILED"
  exit 1
fi
