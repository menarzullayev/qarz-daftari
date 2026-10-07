#!/usr/bin/env bash
# How old are the newest backup and the newest archived WAL segment? This is what monitoring calls.
#
#   check.sh            print the ages and the verdict
#   check.sh --quiet    print only what is wrong (for the timer that runs every minute)
#
# Runs on the repository host, where the repository is a local directory. It reads the repository,
# not the database: it answers "what could be restored", whatever the primary believes it archived.
# Also writes the figures for the monitoring system (qd_backup_repo.prom).
#
# Thresholds (seconds), each changeable through the environment:
#   QD_MAX_WAL_AGE_SECONDS     300     operations document, Monitoring: "Log archive age older than 5 minutes"
#   QD_MAX_BACKUP_AGE_SECONDS  93600   a backup of any type is taken daily; 26 hours allows a slow night
#   QD_MAX_FULL_AGE_SECONDS    691200  a full backup is taken weekly; 8 days
#
# Exit codes: 0 all fresh, 1 something is older than its threshold or missing, 2 the repository could not be read.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

quiet=0
[ "${1:-}" = "--quiet" ] && quiet=1
need pgbackrest jq find sort awk

: "${QD_MAX_WAL_AGE_SECONDS:=300}"
: "${QD_MAX_BACKUP_AGE_SECONDS:=93600}"
: "${QD_MAX_FULL_AGE_SECONDS:=691200}"

info="$(repo_info)" || {
    note "ERROR: pgBackRest could not read the repository"
    exit 2
}
archive_dir="$QD_REPO_PATH/archive/$QD_STANZA"
[ -d "$archive_dir" ] || {
    note "ERROR: $archive_dir is not a directory; check.sh runs on the repository host"
    exit 2
}

at="$(date +%s)"
stale=0

# stop time and label of the newest backup of a type ("any" for every type); "0 -" when there is none
newest_of() {
    jq -r --arg type "$1" '
        [.[0].backup[] | select($type == "any" or .type == $type)] | sort_by(.timestamp.stop) | last
        | if . == null then "0 - -" else "\(.timestamp.stop) \(.label) \(.type)" end' <<< "$info"
}

say() { [ "$quiet" = 1 ] || printf '%s\n' "$*"; }
complain() {
    stale=1
    printf '%s\n' "$*"
}

# judge WHAT STOPPED LIMIT DETAIL
judge() {
    local what="$1" stopped="$2" limit="$3" detail="$4" age
    if [ "$stopped" = 0 ]; then
        complain "$what: none in the repository. MISSING"
        return 0
    fi
    age=$((at - stopped))
    if [ "$age" -gt "$limit" ]; then
        complain "$what: $detail, age $age s, limit $limit s. TOO OLD"
    else
        say "$what: $detail, age $age s, limit $limit s. ok"
    fi
}

read -r any_stop any_label any_type <<< "$(newest_of any)"
read -r full_stop full_label _ <<< "$(newest_of full)"
read -r diff_stop _ _ <<< "$(newest_of diff)"
judge "newest backup" "$any_stop" "$QD_MAX_BACKUP_AGE_SECONDS" "$any_label ($any_type)"
judge "newest full backup" "$full_stop" "$QD_MAX_FULL_AGE_SECONDS" "$full_label"

# WAL segments lie in <archive>/<stanza>/<version-id>/<16 hex digits>/<24 hex digits>-<checksum>[.ext].
# The modification time of the newest one is when the repository received it.
read -r wal_stop wal_name <<< "$(find "$archive_dir" -type f -name '????????????????????????-*' -printf '%T@ %f\n' \
    | sort -n | tail -n 1 | awk '{ printf "%d %s", $1, substr($2, 1, 24) }')"
wal_stop="${wal_stop:-0}"
judge "newest archived WAL segment" "$wal_stop" "$QD_MAX_WAL_AGE_SECONDS" "${wal_name:--}"

wal_age=-1
[ "$wal_stop" = 0 ] || wal_age=$((at - wal_stop))

{
    echo "# HELP qd_backup_last_success_timestamp_seconds When the newest backup of this type in the repository ended."
    echo "# TYPE qd_backup_last_success_timestamp_seconds gauge"
    echo "qd_backup_last_success_timestamp_seconds{type=\"full\"} $full_stop"
    echo "qd_backup_last_success_timestamp_seconds{type=\"diff\"} $diff_stop"
    echo "# HELP qd_wal_archive_newest_age_seconds Age of the newest WAL segment in the repository when it was last checked; -1 when there is none."
    echo "# TYPE qd_wal_archive_newest_age_seconds gauge"
    echo "qd_wal_archive_newest_age_seconds $wal_age"
    echo "# HELP qd_backup_check_timestamp_seconds When the repository was last checked."
    echo "# TYPE qd_backup_check_timestamp_seconds gauge"
    echo "qd_backup_check_timestamp_seconds $at"
} | write_metrics qd_backup_repo

exit "$stale"
