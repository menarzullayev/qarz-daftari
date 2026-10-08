#!/usr/bin/env bash
# The monthly dumps of deploy/backup/scripts/monthly-archive.sh, on a host with no second server: the
# dump is written into the state volume and copied to the bucket, because a dump that exists only on
# the machine it protects is not a backup.
#
#   monthly.sh upload             copy every dump of $QD_MONTHLY_DIR to qdcrypt:monthly
#   monthly.sh prune [--dry-run]  keep the newest QD_KEEP_MONTHS (12) months in the bucket
#
# The dump is already encrypted with the backup passphrase (openssl); the copy goes through rclone's
# crypt remote as everything else does, so its name is hidden as well. Standard output: one JSON line.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh
# shellcheck source-path=SCRIPTDIR source=../../../backup/scripts/lib.sh
. /opt/qarz-backup/scripts/lib.sh
need rclone jq awk
qd_rclone_env

: "${QD_KEEP_MONTHS:=12}"
mode="${1:-}"
dry=0
[ "${2:-}" = "--dry-run" ] && dry=1
REMOTE=qdcrypt:monthly

started="$(now)"
outcome=failed
local_count=0
remote_count=0
removed=0

finish() {
    local code=$? ended
    trap - EXIT
    ended="$(now)"
    if [ "$outcome" = ok ]; then code=0; elif [ "$code" = 0 ]; then code=1; fi
    json_line event "monthly_${mode}" outcome "$outcome" \
        started_at "$(iso "$started")" ended_at "$(iso "$ended")" \
        duration_seconds "$(elapsed "$started" "$ended")" \
        local_dumps_count "$local_count" remote_dumps_count "$remote_count" removed_count "$removed"
    exit "$code"
}
trap finish EXIT

# The months that have a dump in the bucket, oldest first.
remote_months() { rc lsf --files-only "$REMOTE" 2> /dev/null | sed -n 's/^db-\([0-9]\{6\}\)\.dump\.enc$/\1/p' | sort; }

case "$mode" in
upload)
    mkdir -p "$QD_MONTHLY_DIR"
    local_count="$(find "$QD_MONTHLY_DIR" -maxdepth 1 -type f -name 'db-*.dump.enc' | wc -l | tr -d ' ')"
    rc copy "$QD_MONTHLY_DIR" "$REMOTE" --include 'db-*.dump.enc' --include 'globals-*.sql.enc' >&2
    remote_count="$(remote_months | wc -l | tr -d ' ')"
    [ "$remote_count" -ge "$local_count" ] || die "the bucket holds $remote_count monthly dumps, this host $local_count"
    ;;
prune)
    while IFS= read -r month; do
        [ -n "$month" ] || continue
        if [ "$dry" = 1 ]; then
            note "would remove the monthly dump of $month from the bucket"
        else
            rc deletefile "$REMOTE/db-$month.dump.enc" >&2
            rc deletefile "$REMOTE/globals-$month.sql.enc" >&2 || true
            note "removed the monthly dump of $month from the bucket"
        fi
        removed=$((removed + 1))
    done < <(remote_months | awk -v m="$QD_KEEP_MONTHS" '{ month[NR] = $1 } END { for (i = 1; i <= NR - m; i++) print month[i] }')
    remote_count="$(remote_months | wc -l | tr -d ' ')"
    ;;
*)
    note "usage: monthly.sh <upload|prune [--dry-run]>"
    exit 64
    ;;
esac
outcome=ok
