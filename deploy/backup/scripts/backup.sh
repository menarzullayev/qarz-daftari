#!/usr/bin/env bash
# Take one pgBackRest backup and report it.
#
#   backup.sh full    the weekly full backup
#   backup.sh diff    the daily differential backup (everything changed since the last full)
#
# Runs on the repository host as the operating-system user that owns the repository. Standard
# output is exactly one JSON line (start, end, outcome, label, sizes, duration); warnings and errors
# of pgBackRest go to standard error. Expiry is not done here: see expire.sh.
#
# Exit codes: 0 backup taken, 64 wrong usage, anything else: the backup failed.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

type="${1:-}"
case "$type" in
full | diff) ;;
*)
    note "usage: backup.sh <full|diff>"
    exit 64
    ;;
esac
need pgbackrest jq awk

started="$(now)"
outcome=failed
label=""
taken=""
database_bytes=0
backup_bytes=0
repository_bytes=0

finish() {
    local code=$? ended success=0
    trap - EXIT
    ended="$(now)"
    if [ "$outcome" = ok ]; then
        code=0
        success=1
    elif [ "$code" = 0 ]; then
        code=1
    fi
    write_metrics "qd_backup_run_${type}" << EOF
# HELP qd_backup_last_run_success Whether the last run of the backup of this type succeeded (1) or failed (0).
# TYPE qd_backup_last_run_success gauge
qd_backup_last_run_success{type="${type}"} ${success}
# HELP qd_backup_last_run_timestamp_seconds When the last run of the backup of this type ended.
# TYPE qd_backup_last_run_timestamp_seconds gauge
qd_backup_last_run_timestamp_seconds{type="${type}"} ${ended%.*}
# HELP qd_backup_last_run_duration_seconds How long the last run of the backup of this type took.
# TYPE qd_backup_last_run_duration_seconds gauge
qd_backup_last_run_duration_seconds{type="${type}"} $(elapsed "$started" "$ended")
EOF
    json_line event backup type "$type" taken_type "$taken" outcome "$outcome" label "$label" \
        started_at "$(iso "$started")" ended_at "$(iso "$ended")" \
        duration_seconds "$(elapsed "$started" "$ended")" \
        database_bytes "$database_bytes" backup_bytes "$backup_bytes" repository_bytes "$repository_bytes"
    exit "$code"
}
trap finish EXIT

newest() { jq -r '.[0].backup | sort_by(.timestamp.stop) | last | .label // empty'; }

before="$(repo_info | newest)"
pgbr backup --type="$type" --no-expire-auto

info="$(repo_info)"
label="$(newest <<< "$info")"
if [ -z "$label" ] || [ "$label" = "$before" ]; then
    die "pgBackRest reported success but the repository shows no new backup"
fi
# pgBackRest takes a full backup when a differential is asked for and no full exists; say what was taken.
read -r taken database_bytes backup_bytes repository_bytes <<< "$(jq -r --arg l "$label" '
    .[0].backup[] | select(.label == $l)
    | [.type, .info.size, .info.delta, .info.repository.delta] | map(tostring) | join(" ")' <<< "$info")"
outcome=ok
