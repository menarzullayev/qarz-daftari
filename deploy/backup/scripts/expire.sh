#!/usr/bin/env bash
# Remove what the retention rules no longer keep.
#
#   1. pgBackRest expiry, by the retention settings of pgbackrest.conf (full, differential, WAL).
#   2. Weekly file store archives (files-YYYYMMDD.tar.gz.enc): the newest QD_FILES_KEEP_WEEKS (8) are
#      kept, and the first archive of each of the newest QD_KEEP_MONTHS (12) months.
#   3. Monthly database dumps (db-YYYYMM.dump.enc with globals-YYYYMM.sql.enc): the newest
#      QD_KEEP_MONTHS (12) months are kept.
#
#   expire.sh --dry-run    say what would be removed and remove nothing
#
# Standard output is exactly one JSON line. Exit codes: 0 done, anything else: expiry failed.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

dry=0
[ "${1:-}" = "--dry-run" ] && dry=1
need pgbackrest jq awk

: "${QD_FILES_KEEP_WEEKS:=8}"
: "${QD_KEEP_MONTHS:=12}"

started="$(now)"
outcome=failed
backups_before=0
backups_after=0
files_removed=0
dumps_removed=0

finish() {
    local code=$? ended
    trap - EXIT
    ended="$(now)"
    if [ "$outcome" = ok ]; then code=0; elif [ "$code" = 0 ]; then code=1; fi
    json_line event expire outcome "$outcome" dry_run "$dry" \
        started_at "$(iso "$started")" ended_at "$(iso "$ended")" \
        duration_seconds "$(elapsed "$started" "$ended")" \
        backups_before_count "$backups_before" backups_after_count "$backups_after" \
        file_archives_removed_count "$files_removed" monthly_dumps_removed_count "$dumps_removed"
    exit "$code"
}
trap finish EXIT

count_backups() { repo_info | jq '.[0].backup | length'; }

# names DIR PREFIX SUFFIX -> the date part of every DIR/PREFIX<digits>SUFFIX, oldest first
names() {
    local dir="$1" prefix="$2" suffix="$3" file base
    [ -d "$dir" ] || return 0
    for file in "$dir/$prefix"*"$suffix"; do
        [ -f "$file" ] || continue
        base="${file##*/}"
        base="${base#"$prefix"}"
        base="${base%"$suffix"}"
        [[ "$base" =~ ^[0-9]+$ ]] && printf '%s\n' "$base"
    done | sort
}

remove() {
    if [ "$dry" = 1 ]; then
        note "would remove $1"
    else
        rm -f -- "$1"
        note "removed $1"
    fi
}

backups_before="$(count_backups)"
if [ "$dry" = 1 ]; then
    pgbr expire --dry-run >&2
else
    pgbr expire >&2
fi
backups_after="$(count_backups)"

# Weekly archives: keep the newest W, and the first of each of the newest M months.
while IFS= read -r day; do
    [ -n "$day" ] || continue
    remove "$QD_FILES_ARCHIVE_DIR/files-$day.tar.gz.enc"
    files_removed=$((files_removed + 1))
done < <(names "$QD_FILES_ARCHIVE_DIR" files- .tar.gz.enc | awk -v w="$QD_FILES_KEEP_WEEKS" -v m="$QD_KEEP_MONTHS" '
    { day[NR] = $1; month = substr($1, 1, 6); if (!(month in first)) { first[month] = NR; months[++nm] = month } }
    END {
        for (i = NR; i > NR - w && i >= 1; i--) keep[i] = 1
        for (j = nm; j > nm - m && j >= 1; j--) keep[first[months[j]]] = 1
        for (i = 1; i <= NR; i++) if (!(i in keep)) print day[i]
    }')

# Monthly dumps: keep the newest M.
while IFS= read -r month; do
    [ -n "$month" ] || continue
    remove "$QD_MONTHLY_DIR/db-$month.dump.enc"
    [ ! -f "$QD_MONTHLY_DIR/globals-$month.sql.enc" ] || remove "$QD_MONTHLY_DIR/globals-$month.sql.enc"
    dumps_removed=$((dumps_removed + 1))
done < <(names "$QD_MONTHLY_DIR" db- .dump.enc | awk -v m="$QD_KEEP_MONTHS" '
    { month[NR] = $1 }
    END { for (i = 1; i <= NR - m; i++) print month[i] }')

outcome=ok
