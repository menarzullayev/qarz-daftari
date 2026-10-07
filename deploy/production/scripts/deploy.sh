#!/usr/bin/env bash
# Deploy one commit: images, migrations, worker, API, proxy, health check.
#
#   deploy.sh <git-ref>
#
# Environment: DEPLOY_ENV_FILE (default /etc/qarz/production.env), DEPLOY_PROJECT (default qarz),
# DEPLOY_STATE_DIR (default /var/lib/qarz/deploy). Prints no value from the env file.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ $# -eq 1 ] || die "usage: deploy.sh <git-ref>"
require_env_file
RELEASE="$(resolve_commit "$1")"
say "deploying $RELEASE (was: $(current_release || true))"

ensure_images "$RELEASE"

# Forward-only and compatible with the previous release (docs/10-operations), so the old code keeps
# working while this runs and after a roll back.
say "migrations: alembic upgrade head"
compose run --rm migrate
say "migrations: now at $(compose run --rm migrate alembic current 2>/dev/null | tail -n 1)"

restart_services
wait_for_healthz
record_release "$RELEASE" deploy
print_deployed
say "next: run smoke.sh against the public address"
