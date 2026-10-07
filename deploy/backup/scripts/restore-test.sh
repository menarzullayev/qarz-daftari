#!/usr/bin/env bash
# The weekly restore test: restore the LATEST backup into a throwaway instance, start it, check it,
# remove it, and say how long it took.
#
# Runs on the repository host as the operating-system user that owns the repository and can run
# PostgreSQL (postgres). The throwaway instance listens on a Unix socket in its own directory only,
# never on the network, and never archives. Nothing of the live database is touched.
#
# Checks, in order (the first that fails names itself in "failed_check"):
#   restore       pgBackRest restores the newest backup (it verifies every file's checksum)
#   start         PostgreSQL starts on the restored files
#   consistent    recovery reached a consistent state and ended; the server accepts queries
#   migration     alembic_version equals the head of the repository's migrations
#                 (QD_EXPECTED_MIGRATION_HEAD, or computed from QD_MIGRATIONS_DIR)
#   rows          each table of QD_ROWCOUNT_TABLES has rows if it has rows in the source database
#   ledger        open_debt_mismatches(NULL) is empty and no customer has a negative balance (INV-3)
#
# Standard output is exactly one JSON line. Exit codes: 0 passed, 1 a check failed, 64 wrong usage.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

: "${QD_RESTORE_TEST_DIR:=/var/tmp}"
: "${QD_RESTORE_TEST_PORT:=55440}"
: "${QD_RESTORE_TEST_START_TIMEOUT:=1800}"
: "${QD_ROWCOUNT_TABLES:=shop membership customer ledger_entry}"
: "${QD_MIGRATIONS_DIR:=$(dirname "${BASH_SOURCE[0]}")/../../../backend/migrations/versions}"

if [ -z "${QD_PG_BIN:-}" ] && ! command -v pg_ctl > /dev/null 2>&1; then
    # Debian and Ubuntu keep the server programs outside PATH.
    QD_PG_BIN="$(find /usr/lib/postgresql -maxdepth 2 -type d -name bin 2> /dev/null | sort -V | tail -n 1)"
fi
[ -z "${QD_PG_BIN:-}" ] || PATH="$QD_PG_BIN:$PATH"
need pgbackrest jq psql pg_ctl flock awk df
[ "$(id -u)" != 0 ] || die "run as the operating-system user that owns the repository (postgres), not as root"

started="$(now)"
outcome=failed
failed_check=""
label=""
backup_type=""
restore_seconds=0
start_seconds=0
head=""
rows_checked=0
work=""

stop_and_remove() {
    local dir="$1"
    if [ -s "$dir/data/postmaster.pid" ]; then
        pg_ctl -D "$dir/data" -m immediate -w -t 60 stop > /dev/null 2>&1 || true
    fi
    rm -rf -- "$dir"
}

finish() {
    local code=$? ended success=0 last_success
    trap - EXIT
    if [ -n "$work" ] && [ -d "$work" ]; then
        if [ "$outcome" != ok ] && [ -s "$work/postgres.log" ]; then
            note "last lines of the throwaway instance's log:"
            tail -n 15 "$work/postgres.log" >&2 || true
        fi
        stop_and_remove "$work"
    fi
    ended="$(now)"
    if [ "$outcome" = ok ]; then
        code=0
        success=1
        printf '%s\n' "${ended%.*}" > "$QD_STATE_DIR/restore-test.last-success" || true
    elif [ "$code" = 0 ]; then
        code=1
    fi
    last_success="$(cat "$QD_STATE_DIR/restore-test.last-success" 2> /dev/null || echo 0)"
    write_metrics qd_backup_restore_test << EOF
# HELP qd_backup_restore_test_last_success_timestamp_seconds When the restore test last passed; 0 if it never has.
# TYPE qd_backup_restore_test_last_success_timestamp_seconds gauge
qd_backup_restore_test_last_success_timestamp_seconds ${last_success}
# HELP qd_backup_restore_test_last_run_success Whether the last restore test passed (1) or failed (0).
# TYPE qd_backup_restore_test_last_run_success gauge
qd_backup_restore_test_last_run_success ${success}
# HELP qd_backup_restore_test_last_run_duration_seconds How long the last restore test took, start to removal.
# TYPE qd_backup_restore_test_last_run_duration_seconds gauge
qd_backup_restore_test_last_run_duration_seconds $(elapsed "$started" "$ended")
EOF
    json_line event restore_test outcome "$outcome" failed_check "$failed_check" \
        backup "$label" backup_type "$backup_type" migration_head "$head" \
        started_at "$(iso "$started")" ended_at "$(iso "$ended")" \
        restore_seconds "$restore_seconds" start_seconds "$start_seconds" \
        duration_seconds "$(elapsed "$started" "$ended")" tables_checked_count "$rows_checked"
    exit "$code"
}
trap finish EXIT

# failed CHECK MESSAGE -> record which check failed and stop
failed() {
    failed_check="$1"
    note "FAILED ($1): $2"
    exit 1
}

# The head of the migrations in a directory: the one revision that no other names as its parent.
repo_migration_head() {
    awk '
        /^revision[[:space:]]*(:[^=]*)?=/ {
            if (match($0, /"[^"]+"|\047[^\047]+\047/)) rev[substr($0, RSTART + 1, RLENGTH - 2)] = 1
        }
        /^down_revision[[:space:]]*(:[^=]*)?=/ {
            s = $0
            while (match(s, /"[^"]+"|\047[^\047]+\047/)) {
                down[substr(s, RSTART + 1, RLENGTH - 2)] = 1
                s = substr(s, RSTART + RLENGTH)
            }
        }
        END {
            n = 0
            for (r in rev) if (!(r in down)) { head = r; n++ }
            if (n != 1) exit 3
            print head
        }' "$1"/*.py
}

mkdir -p "$QD_STATE_DIR"
exec 9> "$QD_STATE_DIR/restore-test.lock"
flock -n 9 || die "another restore test is running"

# A run that was killed leaves its directory behind; with the lock held it is nobody's.
for old in "$QD_RESTORE_TEST_DIR"/qd-restore-test.*; do
    [ -d "$old" ] || continue
    note "removing a directory left by an earlier run: $old"
    stop_and_remove "$old"
done

info="$(repo_info)" || failed restore "pgBackRest could not read the repository"
read -r label backup_type database_bytes <<< "$(jq -r '
    .[0].backup | sort_by(.timestamp.stop) | last
    | if . == null then "" else "\(.label) \(.type) \(.info.size)" end' <<< "$info")"
[ -n "$label" ] || failed restore "the repository holds no backup"

work="$(mktemp -d "$QD_RESTORE_TEST_DIR/qd-restore-test.XXXXXX")"
chmod 700 "$work"
mkdir -m 700 "$work/data" "$work/sock"

free_bytes="$(df --output=avail -B1 "$work" | tail -n 1 | tr -d ' ')"
if [ "$free_bytes" -lt $((database_bytes + database_bytes / 10)) ]; then
    failed restore "not enough free space in $QD_RESTORE_TEST_DIR: $free_bytes bytes for a database of $database_bytes"
fi

# --type=immediate: recover only as far as the backup itself needs to be consistent, then open.
# This tests the backup. Replay of the archive to a later moment is the point-in-time restore (runbook 3).
t="$(now)"
pgbr restore --set="$label" --pg1-path="$work/data" --type=immediate --target-action=promote \
    --archive-mode=off >&2 || failed restore "pgBackRest could not restore $label"
restore_seconds="$(elapsed "$t" "$(now)")"

# Hosts that keep the configuration outside the data directory (Debian: /etc/postgresql) restore none.
[ -e "$work/data/postgresql.conf" ] || : > "$work/data/postgresql.conf"
echo "local all all trust" > "$work/pg_hba.conf" # the socket directory is readable by this user only
: > "$work/pg_ident.conf"

t="$(now)"
pg_ctl -D "$work/data" -w -t "$QD_RESTORE_TEST_START_TIMEOUT" -l "$work/postgres.log" \
    -o "-p $QD_RESTORE_TEST_PORT -c listen_addresses='' -c unix_socket_directories='$work/sock' \
-c hba_file='$work/pg_hba.conf' -c ident_file='$work/pg_ident.conf' -c archive_mode=off -c hot_standby=off \
-c ssl=off -c shared_preload_libraries='' -c logging_collector=off -c log_destination=stderr \
-c cluster_name=qd-restore-test" start >&2 || failed start "PostgreSQL did not start on the restored files"

restored_sql() {
    psql -X -qAt -v ON_ERROR_STOP=1 -h "$work/sock" -p "$QD_RESTORE_TEST_PORT" -U "$QD_PGUSER" -d "$QD_DATABASE" -c "$1"
}

# With hot_standby off, pg_ctl reports the server as started while it still replays WAL and refuses
# connections. Wait until recovery has ended and a query is answered.
deadline=$(($(date +%s) + QD_RESTORE_TEST_START_TIMEOUT))
until [ "$(restored_sql 'select pg_is_in_recovery()' 2> /dev/null || true)" = "f" ]; do
    pg_ctl -D "$work/data" status > /dev/null 2>&1 || failed start "PostgreSQL stopped while recovering"
    [ "$(date +%s)" -lt "$deadline" ] || failed consistent "recovery did not end within $QD_RESTORE_TEST_START_TIMEOUT s"
    sleep 0.5
done
start_seconds="$(elapsed "$t" "$(now)")"
grep -q 'consistent recovery state reached' "$work/postgres.log" \
    || failed consistent "the log does not show that a consistent state was reached"

if [ -n "${QD_EXPECTED_MIGRATION_HEAD:-}" ]; then
    expected="$QD_EXPECTED_MIGRATION_HEAD"
else
    expected="$(repo_migration_head "$QD_MIGRATIONS_DIR")" \
        || failed migration "could not find one head among the migrations in $QD_MIGRATIONS_DIR"
fi
head="$(restored_sql 'select version_num from alembic_version')" || failed migration "alembic_version could not be read"
[ "$head" = "$expected" ] || failed migration "the restored database is at migration '$head', the repository at '$expected'"

for table in $QD_ROWCOUNT_TABLES; do
    [[ "$table" =~ ^[a-z_][a-z0-9_.]*$ ]] || failed rows "not a table name: $table"
    in_source="$(source_sql "select count(*) from $table")" || failed rows "the source database could not be asked about $table"
    in_restored="$(restored_sql "select count(*) from $table")" || failed rows "the restored database could not be asked about $table"
    note "rows in $table: source $in_source, restored $in_restored"
    if [ "$in_source" -gt 0 ] && [ "$in_restored" -eq 0 ]; then
        failed rows "$table is empty in the restored database and has $in_source rows in the source"
    fi
    rows_checked=$((rows_checked + 1))
done

mismatches="$(restored_sql 'select count(*) from open_debt_mismatches(NULL)')" || failed ledger "open_debt_mismatches could not be run"
[ "$mismatches" = 0 ] || failed ledger "$mismatches stored open debts differ from the ledger"
# A customer's balance: debts minus payments over the entries that were not reversed (INV-2, INV-3).
negative="$(restored_sql "
    select count(*) from (
        select e.customer_id
          from ledger_entry e
         where e.kind <> 'reversal'
           and not exists (select 1 from ledger_entry r where r.reverses_id = e.id)
         group by e.customer_id
        having sum(case when e.kind = 'payment' then -e.amount else e.amount end) < 0) t")" \
    || failed ledger "the balances could not be computed"
[ "$negative" = 0 ] || failed ledger "$negative customers have a negative balance"

outcome=ok
