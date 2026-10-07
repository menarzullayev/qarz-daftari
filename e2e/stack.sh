#!/usr/bin/env bash
# The stack the end-to-end suite runs against: deploy/production with its local overlay (a throwaway
# PostgreSQL, a directory in place of the file store, a self-signed certificate) and compose.e2e.yml,
# which closes the way out to the internet.
#
#   stack.sh up      build the images from HEAD (commit first) and start everything
#   stack.sh down    remove the containers, networks, volumes, images and generated files
#   stack.sh logs    the last lines of every service
#
# Project `qd-e2e`, https://127.0.0.1:28443 and http://127.0.0.1:28480 (E2E_HTTPS_PORT, E2E_HTTP_PORT).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export DEPLOY_PROJECT="qd-e2e"
export LOCAL_DIR="$REPO_DIR/deploy/production/.local-e2e"
export LOCAL_HTTP_PORT="${E2E_HTTP_PORT:-28480}"
export LOCAL_HTTPS_PORT="${E2E_HTTPS_PORT:-28443}"
# Made-up Telegram identifiers on the administrators' allow-list. An administrator's second factor can be
# enrolled once, so each run of the suite uses the next identifier that has none yet.
export LOCAL_ADMIN_TG_IDS="${E2E_ADMIN_TG_IDS:-9100000001,9100000002,9100000003,9100000004,9100000005,9100000006,9100000007,9100000008}"
export DEPLOY_COMPOSE_OVERLAY_EXTRA="$REPO_DIR/deploy/production/compose.e2e.yml"

case "${1:-}" in
  up | down) exec bash "$REPO_DIR/deploy/production/scripts/local.sh" "$1" ;;
  logs) exec docker compose --project-name "$DEPLOY_PROJECT" logs --no-color --tail "${2:-80}" ;;
  *) echo "usage: stack.sh up | down | logs [lines]" >&2; exit 1 ;;
esac
