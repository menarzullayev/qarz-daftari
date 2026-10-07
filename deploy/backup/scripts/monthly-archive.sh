#!/usr/bin/env bash
# The monthly archive: "monthly for 12 months" of the operations document.
#
# pgBackRest keeps backups by count or by age; it cannot keep "one a month" beside "weekly for 8
# weeks" in one repository. So once a month a logical dump of the database is written beside the
# repository, encrypted with the repository's passphrase:
#
#   $QD_MONTHLY_DIR/db-YYYYMM.dump.enc       pg_dump, custom format
#   $QD_MONTHLY_DIR/globals-YYYYMM.sql.enc   roles without passwords (pg_dumpall --roles-only)
#
# A dump restores into any PostgreSQL of the same or a later major version and needs no WAL. It is a
# state of one moment: there is no point-in-time recovery from it. expire.sh keeps the newest twelve.
#
# Runs on the standby against the replica it serves (QD_SOURCE_PSQL_ARGS), so the primary does no
# extra work. Standard output is exactly one JSON line.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need pg_dump pg_dumpall pg_restore openssl jq awk

started="$(now)"
outcome=failed
month="$(TZ=Asia/Tashkent date +%Y%m)"
size=0
entries=0
tmp=""

finish() {
    local code=$? ended success=0
    trap - EXIT
    [ -z "$tmp" ] || rm -f -- "$tmp" "$tmp.globals"
    ended="$(now)"
    if [ "$outcome" = ok ]; then
        code=0
        success=1
    elif [ "$code" = 0 ]; then
        code=1
    fi
    write_metrics qd_backup_monthly << EOF
# HELP qd_backup_monthly_last_run_success Whether the last monthly dump succeeded (1) or failed (0).
# TYPE qd_backup_monthly_last_run_success gauge
qd_backup_monthly_last_run_success ${success}
# HELP qd_backup_monthly_last_run_timestamp_seconds When the last monthly dump ended.
# TYPE qd_backup_monthly_last_run_timestamp_seconds gauge
qd_backup_monthly_last_run_timestamp_seconds ${ended%.*}
EOF
    json_line event monthly_archive outcome "$outcome" month "$month" \
        started_at "$(iso "$started")" ended_at "$(iso "$ended")" \
        duration_seconds "$(elapsed "$started" "$ended")" size_bytes "$size" entries_count "$entries"
    exit "$code"
}
trap finish EXIT

declare -a extra=()
if [ -n "${QD_SOURCE_PSQL_ARGS:-}" ]; then
    read -r -a extra <<< "$QD_SOURCE_PSQL_ARGS"
fi

umask 077
mkdir -p "$QD_MONTHLY_DIR"
tmp="$(mktemp "$QD_MONTHLY_DIR/.db-$month.XXXXXX")"

pg_dump "${extra[@]}" -U "$QD_PGUSER" -d "$QD_DATABASE" --format=custom --compress=6 | qd_encrypt > "$tmp"
pg_dumpall "${extra[@]}" -U "$QD_PGUSER" --roles-only --no-role-passwords | qd_encrypt > "$tmp.globals"

# Read it back: the file decrypts and pg_restore finds a table of contents in it. pg_restore stops
# reading after the table of contents, which openssl reports as a write error; that one is expected.
entries="$( (qd_decrypt < "$tmp" 2> /dev/null || true) | pg_restore --list | grep -c '^[0-9]')"
[ "$entries" -gt 0 ] || die "the dump holds no objects"
size="$(wc -c < "$tmp" | tr -d ' ')"

mv -f "$tmp.globals" "$QD_MONTHLY_DIR/globals-$month.sql.enc"
mv -f "$tmp" "$QD_MONTHLY_DIR/db-$month.dump.enc"
tmp=""
outcome=ok
