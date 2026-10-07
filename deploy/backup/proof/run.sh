#!/usr/bin/env bash
# Local proof of deploy/backup, in containers on one machine. It runs the backup scripts against
# the rehearsal stack of deploy/rehearsal under its own Compose project (qd-backup-proof).
#
#   proof/run.sh up      start the stack, build the application's schema with the real migrations
#                        and fill it with a small generated data set
#   proof/run.sh run     run the scripts and the negative proofs; prints PASS / FAIL lines
#   proof/run.sh down    remove every container, volume, network and image of the proof
#   proof/run.sh all     up, run, down (down also when run fails)
#
# "up" needs a Python that has the backend installed (QD_PYTHON, default backend/.venv); the other
# steps need only Docker and bash. This proves the scripts, not an installation: one machine, one
# disk, a repository that every container mounts, no systemd, no second server.
#
# Commands for the containers are written in single quotes on purpose.
# shellcheck disable=SC2016
set -euo pipefail

export QD_COMPOSE_PROJECT=qd-backup-proof
export QD_COMPOSE_OVERLAY=../backup/proof/compose.proof.yml
export QD_PORT_PRIMARY="${QD_PORT_PRIMARY:-55632}"
export QD_PORT_STANDBY="${QD_PORT_STANDBY:-55633}"
export QD_PORT_PITR="${QD_PORT_PITR:-55634}"
export QD_PORT_FILESTORE="${QD_PORT_FILESTORE:-55910}"
export QD_PORT_FILESTORE_STANDBY="${QD_PORT_FILESTORE_STANDBY:-55911}"

PROOF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PROOF_DIR/../../.." && pwd)"
REHEARSAL_SCRIPTS="$REPO_ROOT/deploy/rehearsal/scripts"
PROOF_DB=qd_load_proof
S=/qd-backup/scripts

# ---------------------------------------------------------------------------------------------- up
up() {
    bash "$REHEARSAL_SCRIPTS/up.sh" | grep -v "^$\|^  \|^Next:"
    # shellcheck source=/dev/null
    . "$REHEARSAL_SCRIPTS/lib.sh"

    log "starting the standby's object store and the tools container"
    dc up -d filestore-standby tools
    garage2() { dc exec -T -e RUST_LOG=warn filestore-standby /garage "$@"; }
    local waited=0 node_id
    until garage2 status > /dev/null 2>&1; do
        [ "$waited" -lt 30 ] || die "the standby's object store did not start"
        sleep 1
        waited=$((waited + 1))
    done
    node_id="$(garage2 node id -q | cut -d@ -f1)"
    garage2 layout assign -z local -c 1G "$node_id" > /dev/null
    garage2 layout apply --version 1 > /dev/null
    garage2 key import --yes -n rehearsal "$QD_FILESTORE_KEY_ID" "$QD_FILESTORE_KEY_SECRET" > /dev/null
    garage2 bucket create qd-files > /dev/null
    garage2 bucket allow --read --write --owner qd-files --key rehearsal > /dev/null

    dc exec -T -u root pg-primary install -d -o postgres -g postgres \
        /var/lib/qarz-backup /var/lib/qarz-backup/textfile

    log "building the application's schema with the migrations and loading the 'tiny' data set"
    local py="${QD_PYTHON:-}"
    if [ -z "$py" ]; then
        for py in "$REPO_ROOT/backend/.venv/Scripts/python.exe" "$REPO_ROOT/backend/.venv/bin/python" python; do
            [ -x "$py" ] && break
        done
    fi
    mkdir -p "$OUT_DIR"
    (
        cd "$REPO_ROOT/backend"
        QD_LOAD_ADMIN_URL="postgresql://postgres:${QD_POSTGRES_PASSWORD}@127.0.0.1:${QD_PORT_PRIMARY}/postgres" \
            "$py" -m loadtest.load --database "$PROOF_DB" --profile tiny --seed 1
    ) > "$OUT_DIR/backup-proof-load.json"
    log "loaded: $(tr -d ' \n' < "$OUT_DIR/backup-proof-load.json" | head -c 400)"
    wait_sql_true pg-standby "select exists (select 1 from pg_database where datname = '$PROOF_DB')" 30 \
        || die "the loaded database did not reach the standby"
    log "the proof stack is up"
}

# -------------------------------------------------------------------------------------------- down
down() {
    QD_COMPOSE_OVERLAY="$QD_COMPOSE_OVERLAY" bash "$REHEARSAL_SCRIPTS/down.sh" --all
    docker image rm -f qd-backup-proof-postgres:16 qd-backup-proof-filestore:1 qd-backup-proof-tools:1 \
        > /dev/null 2>&1 || true
    local left
    left="$(docker image ls -q --filter 'reference=qd-backup-proof-*' | wc -l)"
    echo "images of the proof left: ${left// /}"
}

# --------------------------------------------------------------------------------------------- run
passes=0
failures=0
pass() {
    passes=$((passes + 1))
    echo "PASS  $*"
}
fail() {
    failures=$((failures + 1))
    echo "FAIL  $*"
}
section() { printf '\n== %s\n' "$*"; }

on_primary() { dc exec -T -u postgres pg-primary "$@"; }
on_standby() { dc exec -T -u postgres pg-standby "$@"; }
in_tools() { dc exec -T -u postgres tools "$@"; }
primary_sql() { dc exec -T pg-primary psql -U postgres -d "$1" -v ON_ERROR_STOP=1 -qAtX -c "$2"; }

last_out=""
# step NAME EXPECTED_EXIT COMMAND... -> runs it, shows its standard output and the telling lines of
# its standard error, and judges the exit code. EXPECTED_EXIT is a number or "nonzero".
step() {
    local name="$1" expect="$2" code=0 ok=0
    shift 2
    "$@" > "$OUT_DIR/proof-$name.out" 2> "$OUT_DIR/proof-$name.err" || code=$?
    last_out="$(cat "$OUT_DIR/proof-$name.out")"
    sed 's/^/      /' "$OUT_DIR/proof-$name.out"
    grep -E '^(FAILED|ERROR|rows in)' "$OUT_DIR/proof-$name.err" | sed 's/^/      /' || true
    if [ "$expect" = nonzero ]; then
        [ "$code" != 0 ] && ok=1
    else
        [ "$code" = "$expect" ] && ok=1
    fi
    if [ "$ok" = 1 ]; then
        pass "$name: exit $code (expected $expect)"
    else
        fail "$name: exit $code, expected $expect"
        tail -n 12 "$OUT_DIR/proof-$name.err" | sed 's/^/      | /'
    fi
}
# field NAME -> that field of the (flat) JSON line the last step printed. The host needs no jq.
field() { sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p" <<< "$last_out" | tail -n 1; }
# expect_field NAME VALUE
expect_field() {
    local got
    got="$(field "$1")"
    if [ "$got" = "$2" ]; then pass "$1 is $2"; else fail "$1 is '$got', expected '$2'"; fi
}

# Close the current WAL segment on the primary and wait until the archive has it.
switch_and_wait() {
    local segment
    segment="$(primary_sql postgres "select pg_logical_emit_message(true, 'proof', ''); select pg_walfile_name(pg_switch_wal())" | tail -n 1)"
    wait_sql_true pg-primary "select coalesce(last_archived_wal, '') >= '$segment' from pg_stat_archiver" "${1:-60}"
}

newest_label() {
    on_standby bash -c "pgbackrest --stanza=qarz --log-level-console=warn info --output=json \
        | jq -r '.[0].backup | sort_by(.timestamp.stop) | last | .label'"
}

run() {
    # shellcheck source=/dev/null
    . "$REHEARSAL_SCRIPTS/lib.sh"
    [ "$(container_state pg-standby)" = running ] || die "the proof stack is not up; run: proof/run.sh up"
    mkdir -p "$OUT_DIR"
    local t_run label file
    t_run="$(now)"

    section "1. Weekly full, then daily differential (backup.sh)"
    step backup-full 0 on_primary bash "$S/backup.sh" full
    expect_field taken_type full
    primary_sql qarz "insert into rehearsal_marker (label) select 'proof' from generate_series(1, 20000)"
    step backup-diff 0 on_primary bash "$S/backup.sh" diff
    expect_field taken_type diff
    step backup-usage 64 on_primary bash "$S/backup.sh" weekly

    section "2. Ages of the newest backup and of the newest archived WAL segment (check.sh)"
    switch_and_wait || fail "the WAL segment was not archived"
    step check-fresh 0 on_standby bash "$S/check.sh"

    section "3. Restore test of the latest backup (restore-test.sh)"
    step restore-test 0 on_standby bash "$S/restore-test.sh"
    expect_field outcome ok
    expect_field backup_type diff
    echo "MEASURE restore_seconds $(field restore_seconds), start_seconds $(field start_seconds), whole test $(field duration_seconds) s"
    if [ -z "$(on_standby bash -c 'ls -d /var/tmp/qd-restore-test.* 2>/dev/null')" ]; then
        pass "the throwaway instance was removed"
    else
        fail "the throwaway instance was left behind"
    fi

    section "4. Negative: the restored database is not at the repository's migration head"
    step restore-test-wrong-head 1 on_standby env QD_EXPECTED_MIGRATION_HEAD=9999 bash "$S/restore-test.sh"
    expect_field failed_check migration

    section "5. Negative: a stored open debt differs from the ledger in the backup"
    local customer
    customer="$(primary_sql "$PROOF_DB" "delete from open_debt where entry_id = (select entry_id from open_debt order by entry_id limit 1) returning customer_id")"
    step backup-diff-damaged-ledger 0 on_primary bash "$S/backup.sh" diff
    step restore-test-damaged-ledger 1 on_standby bash "$S/restore-test.sh"
    expect_field failed_check ledger
    if grep -q 'differ from the ledger' "$OUT_DIR/proof-restore-test-damaged-ledger.err"; then
        pass "it was the open-debt comparison that refused"
    else
        fail "another check refused"
    fi
    primary_sql "$PROOF_DB" "select refresh_open_debts(array['$customer'::uuid])" > /dev/null
    if [ "$(primary_sql "$PROOF_DB" 'select count(*) from open_debt_mismatches(NULL)')" = 0 ]; then
        pass "the damage was repaired in the source (open_debt_mismatches is empty again)"
    else
        fail "the source still has mismatches"
    fi
    section "5b. Negative: a customer has a negative balance in the backup (INV-3)"
    local payment
    payment="$(primary_sql "$PROOF_DB" "
        with c as (select e.shop_id, e.customer_id, max(e.seq) s, min(e.author_id::text)::uuid a
                     from ledger_entry e group by 1, 2 order by 2 limit 1)
        insert into ledger_entry (id, shop_id, customer_id, seq, kind, amount, author_id)
        select gen_random_uuid(), shop_id, customer_id, s + 1, 'payment', 999999999999, a from c returning id")"
    step backup-diff-negative-balance 0 on_primary bash "$S/backup.sh" diff
    step restore-test-negative-balance 1 on_standby bash "$S/restore-test.sh"
    expect_field failed_check ledger
    if grep -q 'negative balance' "$OUT_DIR/proof-restore-test-negative-balance.err"; then
        pass "it was the balance check that refused"
    else
        fail "another check refused"
    fi
    # The ledger is append-only: the wrong payment is undone by a reversal, as the product does it.
    primary_sql "$PROOF_DB" "
        insert into ledger_entry (id, shop_id, customer_id, seq, kind, amount, reverses_id, author_id)
        select gen_random_uuid(), shop_id, customer_id, seq + 1, 'reversal', amount, id, author_id
          from ledger_entry where id = '$payment'" > /dev/null
    step backup-diff-repaired 0 on_primary bash "$S/backup.sh" diff
    step restore-test-repaired 0 on_standby bash "$S/restore-test.sh"

    section "6. Negative: the newest backup's files are damaged"
    label="$(newest_label)"
    file="$(on_standby bash -c "find /var/lib/pgbackrest/backup/qarz/$label -type f ! -name 'backup.manifest*' -printf '%s %p\n' | sort -n | tail -n 1 | cut -d' ' -f2-")"
    on_standby bash -c "head -c \"\$(stat -c %s '$file')\" /dev/urandom > '$file'"
    echo "      overwrote with random bytes: ${file#/var/lib/pgbackrest/}"
    step restore-test-damaged-backup 1 on_standby bash "$S/restore-test.sh"
    expect_field failed_check restore
    expect_field backup "$label"
    step backup-diff-after-damage 0 on_primary bash "$S/backup.sh" diff
    step restore-test-after-new-backup 0 on_standby bash "$S/restore-test.sh"

    section "7. Negative: archiving stops (threshold shortened to 20 s through QD_MAX_WAL_AGE_SECONDS; the real one is 300 s)"
    switch_and_wait || fail "the WAL segment was not archived"
    step check-short-threshold-archiving 0 on_standby env QD_MAX_WAL_AGE_SECONDS=20 bash "$S/check.sh"
    on_primary pgbackrest --stanza=qarz --log-level-console=warn stop
    echo "      archiving stopped on the primary (pgbackrest stop); writing for 30 s"
    local i
    for i in 1 2 3 4 5 6; do
        primary_sql postgres "select pg_logical_emit_message(true, 'proof', '$i'); select pg_switch_wal()" > /dev/null
        sleep 5
    done
    step check-archive-stopped 1 on_standby env QD_MAX_WAL_AGE_SECONDS=20 bash "$S/check.sh"
    on_primary pgbackrest --stanza=qarz --log-level-console=warn start
    if switch_and_wait 150; then
        step check-archive-resumed 0 on_standby env QD_MAX_WAL_AGE_SECONDS=20 bash "$S/check.sh"
    else
        fail "archiving did not resume within 150 s"
    fi

    section "8. Negative: no backup is recent enough (thresholds shortened to 1 s)"
    sleep 2
    step check-backup-too-old 1 on_standby env QD_MAX_BACKUP_AGE_SECONDS=1 QD_MAX_FULL_AGE_SECONDS=1 bash "$S/check.sh"

    section "9. The heartbeat keeps the archive moving on an idle database (archive-heartbeat.sh, archive_timeout 60 s)"
    local before after waited=0
    before="$(primary_sql postgres 'select archived_count from pg_stat_archiver')"
    on_primary bash "$S/archive-heartbeat.sh"
    after="$before"
    while [ "$after" = "$before" ] && [ "$waited" -lt 90 ]; do
        sleep 3
        waited=$((waited + 3))
        after="$(primary_sql postgres 'select archived_count from pg_stat_archiver')"
    done
    if [ "$after" != "$before" ]; then
        pass "one heartbeat record and nothing else: a segment was archived within $waited s"
    else
        fail "no segment was archived within 90 s of a heartbeat"
    fi
    if [ "${QD_PROOF_IDLE:-0}" = 1 ]; then
        before="$after"
        sleep 150
        after="$(primary_sql postgres 'select archived_count from pg_stat_archiver')"
        echo "MEASURE segments archived in 150 s without any write: $((after - before))"
    fi

    section "10. File store: copy to the standby's store, weekly archive (filestore-sync.sh)"
    in_tools bash -c 'rm -rf /tmp/in && mkdir -p /tmp/in/receipts /tmp/in/imports \
        && head -c 30000 /dev/urandom > /tmp/in/receipts/a.jpg && head -c 5000 /dev/urandom > /tmp/in/receipts/b.jpg \
        && echo "name,amount" > /tmp/in/imports/c.csv && rclone copy /tmp/in primary:qd-files'
    step filestore-sync 0 in_tools bash "$S/filestore-sync.sh" sync
    expect_field objects_count 3
    if in_tools bash -c 'rclone check primary:qd-files standby:qd-files --download 2>&1 | grep -q "0 differences found"'; then
        pass "the standby's bucket holds the same three objects, compared byte by byte"
    else
        fail "the two buckets differ"
    fi
    step filestore-archive 0 in_tools bash "$S/filestore-sync.sh" archive
    expect_field objects_count 3
    in_tools bash -c 'rclone delete primary:qd-files'
    step filestore-sync-mass-delete nonzero in_tools env QD_FILES_MAX_DELETE=1 bash "$S/filestore-sync.sh" sync
    if [ "$(in_tools bash -c 'rclone size --json standby:qd-files | jq .count')" -ge 2 ]; then
        pass "an emptied source did not empty the copy (QD_FILES_MAX_DELETE=1 for the proof; 100 by default)"
    else
        fail "the copy was emptied"
    fi

    section "11. Monthly dump (monthly-archive.sh)"
    step monthly-archive 0 on_standby bash "$S/monthly-archive.sh"
    expect_field outcome ok
    echo "MEASURE monthly dump: $(field size_bytes) bytes, $(field entries_count) objects in its table of contents"

    section "12. Expiry (expire.sh) and the retention settings of pgbackrest.conf"
    step expire 0 on_standby bash "$S/expire.sh"
    # 60 weekly archives up to today and 15 monthly dumps, as empty files in a scratch directory.
    on_standby bash -c 'rm -rf /tmp/prune && mkdir -p /tmp/prune/files /tmp/prune/monthly \
        && for i in $(seq 0 59); do : > "/tmp/prune/files/files-$(date -d "2026-10-04 -$((i * 7)) days" +%Y%m%d).tar.gz.enc"; done \
        && for i in $(seq 0 14); do m=$(date -d "2026-10-01 -$i months" +%Y%m); : > "/tmp/prune/monthly/db-$m.dump.enc"; : > "/tmp/prune/monthly/globals-$m.sql.enc"; done'
    step expire-prune 0 on_standby env QD_FILES_ARCHIVE_DIR=/tmp/prune/files QD_MONTHLY_DIR=/tmp/prune/monthly bash "$S/expire.sh"
    local kept
    kept="$(on_standby bash -c 'cd /tmp/prune && ls files | tr "\n" " "; echo; ls monthly | grep -c "^db-"; ls monthly | grep -c "^globals-"')"
    echo "      kept: $kept" | tr '\n' ' '
    echo
    # Expected: the 8 newest Sundays (20260816 .. 20261004) and the first Sunday of each of the 12
    # newest months (November 2025 .. October 2026); nothing of October 2025 or earlier.
    if on_standby bash -c 'cd /tmp/prune/files \
        && [ "$(ls | wc -l)" = 18 ] \
        && [ -e files-20260816.tar.gz.enc ] && [ ! -e files-20260809.tar.gz.enc ] \
        && [ -e files-20251102.tar.gz.enc ] && [ ! -e files-20251109.tar.gz.enc ] \
        && [ ! -e files-20251005.tar.gz.enc ] \
        && cd ../monthly && [ "$(ls db-* | wc -l)" = 12 ] && [ "$(ls globals-* | wc -l)" = 12 ] \
        && [ -e db-202511.dump.enc ] && [ ! -e db-202510.dump.enc ]'; then
        pass "weekly archives: the 8 newest and the first of each of 12 months are kept (18 of 60); monthly dumps: 12 of 15"
    else
        fail "the pruning kept the wrong files"
    fi
    # The real installation's retention options, applied to this repository without removing anything.
    on_standby mkdir -p /tmp/no-include
    step expire-production-settings 0 on_standby pgbackrest --config=/qd-backup/pgbackrest.conf \
        --config-include-path=/tmp/no-include --stanza=qarz --log-path=/tmp --log-level-file=off \
        --log-level-console=info expire --dry-run

    section "13. The figures written for monitoring"
    on_standby bash -c 'cd /var/lib/qarz-backup/textfile && grep -h -v "^#" -- *.prom | sort' | sed 's/^/      /'
    local wanted
    for wanted in 'qd_backup_last_success_timestamp_seconds{type="full"}' \
        'qd_backup_last_success_timestamp_seconds{type="diff"}' \
        'qd_backup_restore_test_last_success_timestamp_seconds' 'qd_wal_archive_newest_age_seconds'; do
        if on_standby bash -c 'cat /var/lib/qarz-backup/textfile/*.prom' | grep -F "$wanted " | grep -qv ' 0$'; then
            pass "written and not zero: $wanted"
        else
            fail "missing or zero: $wanted"
        fi
    done

    section "14. shellcheck and systemd-analyze"
    step shellcheck 0 in_tools bash -c 'cd /qd-backup && shellcheck --external-sources --severity=style scripts/*.sh proof/run.sh'
    # The units are copied first: a directory mounted from Windows makes every file look executable.
    step systemd-units 0 in_tools bash -c 'rm -rf /tmp/units && mkdir /tmp/units && cp /qd-backup/systemd/* /tmp/units/ \
        && chmod 644 /tmp/units/* && systemd-analyze verify --man=no /tmp/units/*.service /tmp/units/*.timer'
    step systemd-calendars 0 in_tools bash -c 'for t in /qd-backup/systemd/*.timer; do sed -n "s/^OnCalendar=//p" "$t" | while read -r c; do systemd-analyze calendar "$c" > /dev/null || exit 1; done || exit 1; done'

    printf '\n== Result: %s passed, %s failed, in %s s\n' "$passes" "$failures" "$(seconds_between "$t_run" "$(now)")"
    [ "$failures" = 0 ]
}

case "${1:-}" in
up) up ;;
run) run ;;
down) down ;;
all)
    up
    code=0
    (run) || code=$?
    down
    exit "$code"
    ;;
*)
    echo "usage: run.sh <up|run|down|all>" >&2
    exit 64
    ;;
esac
