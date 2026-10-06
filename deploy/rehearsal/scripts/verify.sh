#!/usr/bin/env bash
# Runs the whole rehearsal against a freshly started stack and prints one
# PASS/FAIL line per check and one MEASURE line per number.
#
# The run is destructive by design: it kills the primary and promotes the
# standby. Start again with scripts/down.sh and scripts/up.sh.
#
#   QD_WRITER_SECONDS   how long the one-row-a-second writer runs before the
#                       primary is killed (default 75, so that at least one
#                       archive_timeout of 60 s has passed)
#
# The full output is also saved to deploy/rehearsal/.out/verify-<UTC time>.log
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
WRITER_SECONDS="${QD_WRITER_SECONDS:-75}"
LAG_SAMPLES=5
mkdir -p "$OUT_DIR"
LOG_FILE="$OUT_DIR/verify-$RUN_ID.log"
FAIL_FILE="$OUT_DIR/.failures-$RUN_ID"
: > "$FAIL_FILE"

pass() { echo "PASS  $*"; }
fail() {
    echo "FAIL  $*"
    echo "$*" >> "$FAIL_FILE"
}
measure() { echo "MEASURE  $*"; }
section() { printf '\n== %s\n' "$*"; }

# expect_exit EXPECTED_CODE OUTPUT_FILE COMMAND... -> runs the command, saves its output
expect_exit() {
    local expected="$1" out="$2" rc=0
    shift 2
    "$@" > "$out" 2>&1 || rc=$?
    [ "$rc" = "$expected" ]
}

main() {
    echo "Qarz Daftari local rehearsal, run $RUN_ID"
    echo "docker $(docker version --format '{{.Server.Version}}'), compose $(docker compose version --short)"
    echo "$(pg_sql pg-primary 'select version()')"
    echo "$(dc exec -T pg-primary pgbackrest version)"

    section "0. Preconditions"
    [ "$(pg_sql pg-primary 'select pg_is_in_recovery()')" = "f" ] || die "pg-primary is not a primary; run down.sh and up.sh"
    [ "$(pg_sql pg-standby 'select pg_is_in_recovery()')" = "t" ] || die "pg-standby is not a standby; run down.sh and up.sh"
    repl="$(pg_sql pg-primary "select state || '/' || sync_state from pg_stat_replication where application_name = 'pg-standby'")"
    if [ "$repl" = "streaming/async" ]; then
        pass "standby is streaming, asynchronous ($repl)"
    else
        fail "standby replication state is '$repl', expected streaming/async"
    fi
    archive_timeout="$(pg_sql pg-primary 'show archive_timeout')"
    if [ "$archive_timeout" = "1min" ]; then
        pass "archive_timeout on the primary is $archive_timeout"
    else
        fail "archive_timeout is '$archive_timeout', expected 1min"
    fi

    # ------------------------------------------------------------------
    section "1. Replication: a row written on the primary appears on the standby"
    # The standby polls every millisecond inside one session and reports how
    # long after the row's own timestamp (taken on the primary) it became visible.
    read -r -d '' waiter_template <<'SQL' || true
DO $do$
DECLARE t timestamptz;
BEGIN
    FOR i IN 1..30000 LOOP
        SELECT written_at INTO t FROM rehearsal_marker WHERE label = '@LABEL@';
        IF FOUND THEN
            RAISE NOTICE 'LAG_MS %', round((extract(epoch FROM clock_timestamp() - t) * 1000)::numeric, 2);
            RETURN;
        END IF;
        PERFORM pg_sleep(0.001);
    END LOOP;
    RAISE EXCEPTION 'row not seen on the standby within 30 s';
END
$do$;
SQL
    lags=""
    for i in $(seq 1 "$LAG_SAMPLES"); do
        label="lag-$RUN_ID-$i"
        pg_sql pg-standby "${waiter_template//@LABEL@/$label}" > "$OUT_DIR/lag-$i.txt" 2>&1 &
        waiter=$!
        sleep 1
        pg_sql pg-primary "insert into rehearsal_marker (label) values ('$label')"
        wait "$waiter" || true
        ms="$(sed -n 's/.*LAG_MS \([0-9.]*\).*/\1/p' "$OUT_DIR/lag-$i.txt")"
        if [ -n "$ms" ]; then
            lags="$lags $ms"
        else
            fail "replication sample $i: row not seen on the standby ($(tr '\n' ' ' < "$OUT_DIR/lag-$i.txt"))"
        fi
    done
    if [ "$(echo "$lags" | wc -w)" -eq "$LAG_SAMPLES" ]; then
        pass "all $LAG_SAMPLES rows written on the primary became visible on the standby"
        measure "replication_lag_ms samples:$lags"
        measure "replication_lag_ms $(echo "$lags" | tr ' ' '\n' | sed '/^$/d' | sort -n \
            | awk '{ v[NR] = $1 } END { printf "min=%s median=%s max=%s", v[1], v[int((NR + 1) / 2)], v[NR] }')"
    fi
    measure "pg_stat_replication write/flush/replay lag: $(pg_sql pg-primary \
        "select coalesce(write_lag::text, 'idle') || ' / ' || coalesce(flush_lag::text, 'idle') || ' / ' || coalesce(replay_lag::text, 'idle') from pg_stat_replication where application_name = 'pg-standby'")"

    # ------------------------------------------------------------------
    section "N1. Negative: the standby rejects writes before promotion"
    if expect_exit 1 "$OUT_DIR/n1.txt" pg_sql pg-standby "insert into rehearsal_marker (label) values ('must-not-exist')" \
        && grep -q "read-only transaction" "$OUT_DIR/n1.txt"; then
        pass "standby refused the insert: $(tr -d '\r' < "$OUT_DIR/n1.txt" | head -n 1)"
    else
        fail "standby did not refuse the insert: $(tr '\n' ' ' < "$OUT_DIR/n1.txt")"
    fi

    # ------------------------------------------------------------------
    section "N2. Negative: failover is refused while the primary still answers"
    if expect_exit 3 "$OUT_DIR/n2.txt" bash "$SCRIPTS_DIR/failover.sh" \
        && [ "$(pg_sql pg-standby 'select pg_is_in_recovery()')" = "t" ]; then
        pass "failover.sh exited 3 and the standby is still a standby: $(grep -m 1 REFUSED "$OUT_DIR/n2.txt")"
    else
        fail "failover.sh did not refuse: $(tr '\n' ' ' < "$OUT_DIR/n2.txt")"
    fi

    # ------------------------------------------------------------------
    section "4. Point-in-time restore to just before damage"
    pg_sql pg-primary "insert into rehearsal_marker (label) values ('keep-$RUN_ID-1'), ('keep-$RUN_ID-2'), ('keep-$RUN_ID-3')"
    sleep 2
    target="$(pg_sql pg-primary 'select now()')"
    sleep 2
    pg_sql pg-primary "delete from rehearsal_marker where label like 'keep-$RUN_ID-%'"
    pg_sql pg-primary "insert into rehearsal_marker (label) values ('late-$RUN_ID-1'), ('late-$RUN_ID-2')"
    echo "markers written, restore target $target, then 3 rows deleted and 2 later rows written"
    if bash "$SCRIPTS_DIR/pitr.sh" "$target" > "$OUT_DIR/pitr.txt" 2>&1; then
        keep_restored="$(pg_sql pg-pitr "select count(*) from rehearsal_marker where label like 'keep-$RUN_ID-%'")"
        late_restored="$(pg_sql pg-pitr "select count(*) from rehearsal_marker where label like 'late-$RUN_ID-%'")"
        keep_live="$(pg_sql pg-primary "select count(*) from rehearsal_marker where label like 'keep-$RUN_ID-%'")"
        late_live="$(pg_sql pg-primary "select count(*) from rehearsal_marker where label like 'late-$RUN_ID-%'")"
        echo "restored instance: keep=$keep_restored late=$late_restored; live primary: keep=$keep_live late=$late_live"
        if [ "$keep_restored" = 3 ] && [ "$late_restored" = 0 ] && [ "$keep_live" = 0 ] && [ "$late_live" = 2 ]; then
            pass "deleted rows are back in the restored instance and later rows are absent"
        else
            fail "restored instance does not hold the expected rows"
        fi
        measure "pitr_seconds $(sed -n 's/^PITR_ELAPSED_SECONDS=//p' "$OUT_DIR/pitr.txt")"
    else
        fail "pitr.sh failed: $(tail -n 5 "$OUT_DIR/pitr.txt" | tr '\n' ' ')"
    fi

    # ------------------------------------------------------------------
    section "N3. Negative: a restore to a time before the first base backup is refused"
    too_early="$(date -u -d '1 day ago' '+%Y-%m-%d %H:%M:%S+00')"
    if expect_exit 5 "$OUT_DIR/n3a.txt" bash "$SCRIPTS_DIR/pitr.sh" "$too_early"; then
        pass "pitr.sh guard exited 5: $(grep -m 1 REFUSED "$OUT_DIR/n3a.txt")"
    else
        fail "pitr.sh guard did not refuse $too_early: $(tail -n 3 "$OUT_DIR/n3a.txt" | tr '\n' ' ')"
    fi
    if expect_exit 6 "$OUT_DIR/n3b.txt" bash "$SCRIPTS_DIR/pitr.sh" --no-guard "$too_early" \
        && grep -q "unable to find backup set" "$OUT_DIR/n3b.txt"; then
        pass "with the guard off, pgBackRest itself refused: $(grep -m 1 -o 'ERROR: .*' "$OUT_DIR/n3b.txt")"
    else
        fail "with the guard off, the restore was not refused: $(tail -n 3 "$OUT_DIR/n3b.txt" | tr '\n' ' ')"
    fi

    # ------------------------------------------------------------------
    section "3. Loss bound: one row a second, then the primary is killed"
    {
        for _ in $(seq 1 $((WRITER_SECONDS + 120))); do
            echo "INSERT INTO rehearsal_tick DEFAULT VALUES RETURNING id;"
            echo "DO 'BEGIN PERFORM pg_sleep(1); END';"
        done
    } > "$OUT_DIR/writer.sql"
    # Each id is printed only after its commit was acknowledged to this client.
    dc exec -T pg-primary psql -U postgres -d "$DB" -qAtX < "$OUT_DIR/writer.sql" > "$OUT_DIR/acked.txt" 2> /dev/null &
    writer=$!
    echo "writer started; waiting $WRITER_SECONDS s"
    sleep "$WRITER_SECONDS"

    archive_state="$(pg_sql pg-primary "select round(extract(epoch from now() - last_archived_time)::numeric, 1) || ' ' || last_archived_wal || ' ' || pg_walfile_name(pg_current_wal_lsn()) from pg_stat_archiver")"
    read -r archive_age archived_segment current_segment <<< "$archive_state"
    primary_id="$(container_id pg-primary)"
    kill_epoch="$(now)"
    docker kill "$primary_id" > /dev/null
    echo "primary killed (docker kill) at $(date -u +%H:%M:%S)"
    wait "$writer" 2> /dev/null || true
    acked_max="$(grep -E '^[0-9]+$' "$OUT_DIR/acked.txt" | tail -n 1)"
    acked_max="${acked_max:-0}"

    measure "newest_archived_wal_age_at_kill_seconds $archive_age (archived $archived_segment, primary was writing $current_segment)"
    if awk -v a="$archive_age" 'BEGIN { exit !(a <= 75) }'; then
        pass "newest archived segment was $archive_age s old at the kill (archive_timeout 60 s, allowance 15 s)"
    else
        fail "newest archived segment was $archive_age s old at the kill; more than archive_timeout plus allowance"
    fi

    # ------------------------------------------------------------------
    section "2. Failover: from kill to the standby accepting a write"
    if QD_KILL_EPOCH="$kill_epoch" bash "$SCRIPTS_DIR/failover.sh" > "$OUT_DIR/failover.txt" 2>&1; then
        pass "standby promoted and accepted a write"
        measure "failover_script_seconds $(sed -n 's/^FAILOVER_ELAPSED_SECONDS=//p' "$OUT_DIR/failover.txt") (probing the dead primary $(sed -n 's/^FAILOVER_PROBE_SECONDS=//p' "$OUT_DIR/failover.txt"), replay, fence, promote and first write $(sed -n 's/^FAILOVER_PROMOTE_SECONDS=//p' "$OUT_DIR/failover.txt"))"
        measure "promoted_standby_timeline $(sed -n 's/.*now writing timeline \([0-9A-F]*\).*/\1/p' "$OUT_DIR/failover.txt")"
        measure "kill_to_write_seconds $(sed -n 's/^KILL_TO_WRITE_SECONDS=//p' "$OUT_DIR/failover.txt")"
    else
        cat "$OUT_DIR/failover.txt"
        fail "failover.sh failed"
        return
    fi

    standby_max="$(pg_sql pg-standby 'select coalesce(max(id), 0) from rehearsal_tick')"
    lost=$((acked_max > standby_max ? acked_max - standby_max : 0))
    measure "rows_acknowledged_by_primary $acked_max"
    measure "rows_on_promoted_standby $standby_max"
    measure "rows_lost_after_failover $lost (one row a second, so about $lost s of entries)"
    if [ "$acked_max" -gt 0 ] && [ "$lost" -le 300 ]; then
        pass "loss after failover is within the 5-minute bound (REQ-N08)"
    else
        fail "loss after failover: acknowledged $acked_max, present $standby_max"
    fi

    lag_present="$(pg_sql pg-standby "select count(*) from rehearsal_marker where label like 'lag-$RUN_ID-%'")"
    late_present="$(pg_sql pg-standby "select count(*) from rehearsal_marker where label like 'late-$RUN_ID-%'")"
    if [ "$lag_present" = "$LAG_SAMPLES" ] && [ "$late_present" = 2 ]; then
        pass "rows committed and replicated before the kill are present on the promoted standby ($lag_present + $late_present marker rows)"
    else
        fail "marker rows missing on the promoted standby (lag=$lag_present late=$late_present)"
    fi

    # ------------------------------------------------------------------
    section "3b. Loss bound from the archive alone (as if the standby were lost too)"
    if bash "$SCRIPTS_DIR/pitr.sh" --archive-end --timeline 1 > "$OUT_DIR/archive-end.txt" 2>&1; then
        archive_max="$(pg_sql pg-pitr 'select coalesce(max(id), 0) from rehearsal_tick')"
        archive_lost=$((acked_max - archive_max))
        measure "rows_recovered_from_archive_only $archive_max"
        measure "rows_lost_archive_only $archive_lost (about $archive_lost s of entries)"
        measure "archive_only_restore_seconds $(sed -n 's/^PITR_ELAPSED_SECONDS=//p' "$OUT_DIR/archive-end.txt")"
        if [ "$archive_max" -gt 0 ] && [ "$archive_lost" -le 75 ]; then
            pass "the archive alone bounds the loss to $archive_lost s (archive_timeout 60 s, allowance 15 s)"
        else
            fail "archive-only restore lost $archive_lost rows"
        fi
    else
        fail "archive-only restore failed: $(tail -n 5 "$OUT_DIR/archive-end.txt" | tr '\n' ' ')"
    fi

    # ------------------------------------------------------------------
    section "N4. Negative: the old primary must not come back as a second primary"
    dc start pg-primary > /dev/null 2>&1 || true
    sleep 5
    old_state="$(docker inspect -f '{{.State.Status}}/{{.State.ExitCode}}' "$primary_id")"
    dc logs --no-log-prefix --tail 5 pg-primary > "$OUT_DIR/n4.txt" 2>&1 || true
    if [ "$old_state" = "exited/78" ] && grep -q FENCED "$OUT_DIR/n4.txt"; then
        pass "restarted old primary refused to start ($old_state): $(grep -m 1 FENCED "$OUT_DIR/n4.txt")"
    else
        fail "restarted old primary is '$old_state': $(tr '\n' ' ' < "$OUT_DIR/n4.txt")"
    fi
    if expect_exit 4 "$OUT_DIR/n4b.txt" bash "$SCRIPTS_DIR/failover.sh"; then
        pass "a second failover.sh run exited 4: $(grep -m 1 REFUSED "$OUT_DIR/n4b.txt")"
    else
        fail "a second failover.sh run did not refuse: $(tr '\n' ' ' < "$OUT_DIR/n4b.txt")"
    fi

    # ------------------------------------------------------------------
    section "5. File store: put, get, and anonymous access refused"
    s3_url="http://127.0.0.1:${QD_PORT_FILESTORE:-55900}/qd-files/rehearsal/$RUN_ID.txt"
    echo "rehearsal object $RUN_ID" > "$OUT_DIR/object.txt"
    put="$(curl -sS -o "$OUT_DIR/s3-put.txt" -w '%{http_code}' -X PUT --data-binary "@$OUT_DIR/object.txt" \
        --aws-sigv4 "aws:amz:garage:s3" --user "$QD_FILESTORE_KEY_ID:$QD_FILESTORE_KEY_SECRET" "$s3_url" || true)"
    got="$(curl -sS --aws-sigv4 "aws:amz:garage:s3" --user "$QD_FILESTORE_KEY_ID:$QD_FILESTORE_KEY_SECRET" "$s3_url" || true)"
    anon="$(curl -sS -o "$OUT_DIR/s3-anon.txt" -w '%{http_code}' "$s3_url" || true)"
    if [ "$put" = 200 ] && [ "$got" = "rehearsal object $RUN_ID" ]; then
        pass "object stored and read back through the S3 API"
    else
        fail "file store put/get failed (put=$put, got='$got')"
    fi
    if [ "$anon" = 403 ]; then
        pass "unsigned read refused with HTTP $anon"
    else
        fail "unsigned read returned HTTP $anon, expected 403"
    fi
}

main 2>&1 | tee "$LOG_FILE"
status="${PIPESTATUS[0]}"

failures="$(wc -l < "$FAIL_FILE" | tr -d ' ')"
rm -f "$FAIL_FILE"
{
    echo
    if [ "$status" = 0 ] && [ "$failures" = 0 ]; then
        echo "RESULT  all checks passed"
    else
        echo "RESULT  $failures check(s) failed (script status $status)"
    fi
    echo "log: deploy/rehearsal/$LOG_FILE"
} | tee -a "$LOG_FILE"
[ "$status" = 0 ] && [ "$failures" = 0 ]
