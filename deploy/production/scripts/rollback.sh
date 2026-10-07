#!/usr/bin/env bash
# Go back to a release that ran here before: its images, the schema as it is now.
#
#   rollback.sh <previous-ref>      (the last one is in $DEPLOY_STATE_DIR/previous)
#
# Never runs `alembic downgrade` and never runs migrations at all: the migrations have no tested
# downgrade, and because each is compatible with the release before it, the previous code runs on the
# newer schema. If data was damaged, that is a point-in-time restore (runbook 3), not this script.
set -euo pipefail
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ $# -eq 1 ] || die "usage: rollback.sh <previous-ref>   (last: $(cat "$STATE_DIR/previous" 2>/dev/null || echo unknown))"
require_env_file
RELEASE="$(resolve_commit "$1")"
say "rolling back to $RELEASE (from: $(current_release || true))"

if [ "$(env_value DEPLOY_PULL)" = "1" ]; then ensure_images "$RELEASE"; else require_images "$RELEASE"; fi

restart_services
wait_for_healthz
record_release "$RELEASE" rollback
print_deployed
say "the database schema was not changed"
