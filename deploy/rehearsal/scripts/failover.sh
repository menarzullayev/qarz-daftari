#!/usr/bin/env bash
# Manual failover (ADR-014): promote the standby after the primary is gone.
#
# Order of steps:
#   1. Refuse if the standby was already promoted.
#   2. Refuse if the primary still answers. This is the split-brain guard: the
#      script never creates a second writable database next to a live one.
#   3. Wait until the standby has replayed everything it received.
#   4. Write the fence file, so the old primary refuses to start again.
#   5. Promote, then prove the node accepts a write.
#
# Prints FAILOVER_ELAPSED_SECONDS (script start to accepted write), split into
# FAILOVER_PROBE_SECONDS (step 2) and FAILOVER_PROMOTE_SECONDS (steps 3 to 5). If the
# caller exports QD_KILL_EPOCH (date +%s.%N taken just before the primary was
# killed) it also prints KILL_TO_WRITE_SECONDS.
#
# Exit codes: 0 promoted, 3 primary still answers, 4 already promoted, 1 other failure.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

t_start="$(now)"

[ "$(container_state pg-standby)" = "running" ] || die "the standby is not running; nothing to promote"

if [ "$(pg_sql pg-standby 'select pg_is_in_recovery()')" != "t" ]; then
    echo "REFUSED: pg-standby is not in recovery; it has already been promoted." >&2
    exit 4
fi

# Three probes, one second apart, from the standby's side of the network.
for attempt in 1 2 3; do
    if dc exec -T pg-standby pg_isready -q -h pg-primary -p 5432 -t 2; then
        echo "REFUSED: pg-primary still answers on 5432 (probe $attempt)." >&2
        echo "REFUSED: stop or isolate the primary first; promoting now would create two primaries." >&2
        exit 3
    fi
    [ "$attempt" = 3 ] || sleep 1
done
t_probed="$(now)"
log "primary does not answer (3 probes, $(seconds_between "$t_start" "$t_probed") s)"

if ! wait_sql_true pg-standby \
    "select pg_last_wal_receive_lsn() is null or pg_last_wal_replay_lsn() >= pg_last_wal_receive_lsn()" 60; then
    die "the standby did not finish replaying received WAL within 60 s"
fi
log "standby replayed up to $(pg_sql pg-standby 'select pg_last_wal_replay_lsn()')"

dc exec -T -u postgres pg-standby sh -c 'echo "pg-standby $(date -u +%Y-%m-%dT%H:%M:%SZ)" > /fence/promoted'
log "fence file written: the old primary will refuse to start"

[ "$(pg_sql pg-standby 'select pg_promote(true, 60)')" = "t" ] || die "pg_promote did not complete within 60 s"

probe="$(pg_sql pg-standby "insert into rehearsal_failover_probe (node) values ('pg-standby') returning id")"
[ -n "$probe" ] || die "the promoted standby did not accept a write"
t_done="$(now)"

timeline="$(pg_sql pg-standby 'select substr(pg_walfile_name(pg_current_wal_lsn()), 1, 8)')"
log "promoted; write accepted (rehearsal_failover_probe id $probe), now writing timeline $timeline"
echo "FAILOVER_PROBE_SECONDS=$(seconds_between "$t_start" "$t_probed")"
echo "FAILOVER_PROMOTE_SECONDS=$(seconds_between "$t_probed" "$t_done")"
echo "FAILOVER_ELAPSED_SECONDS=$(seconds_between "$t_start" "$t_done")"
if [ -n "${QD_KILL_EPOCH:-}" ]; then
    echo "KILL_TO_WRITE_SECONDS=$(seconds_between "$QD_KILL_EPOCH" "$t_done")"
fi
