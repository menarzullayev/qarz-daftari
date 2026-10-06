#!/usr/bin/env bash
# Point-in-time restore with pgBackRest into a fresh, separate instance
# (service pg-pitr, 127.0.0.1:55534 unless QD_PORT_PITR is set). The live databases are not touched.
#
#   scripts/pitr.sh '2026-10-06 12:34:56.789+00'    restore to that moment
#   scripts/pitr.sh --archive-end [--timeline N]    replay every archived segment
#                                                   (what survives if both database
#                                                   servers are lost)
#   --no-guard   skip this script's own check of the target time and let
#                pgBackRest and PostgreSQL answer by themselves
#
# Prints PITR_ELAPSED_SECONDS (script start to the restored instance answering
# queries as a normal, writable database).
#
# Exit codes: 0 restored, 5 target refused by the guard, 6 restore failed, 1 other failure.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

target=""
timeline=""
guard=1
archive_end=0
while [ $# -gt 0 ]; do
    case "$1" in
    --archive-end) archive_end=1 ;;
    --timeline)
        shift
        timeline="${1:?--timeline needs a number}"
        ;;
    --no-guard) guard=0 ;;
    -*) die "unknown option: $1" ;;
    *) target="$1" ;;
    esac
    shift
done
if [ "$archive_end" = 1 ] && [ -n "$target" ]; then
    die "give either a timestamp or --archive-end, not both"
fi
if [ "$archive_end" = 0 ] && [ -z "$target" ]; then
    die "usage: pitr.sh '<timestamp with time zone>' | --archive-end [--timeline N]"
fi

t_start="$(now)"

# Runs a command in a throwaway container that sees only the backup repository.
# It works even when both database containers are dead.
in_repo() {
    dc --profile pitr run --rm -T --no-deps -u postgres -e QD_T="$target" \
        --entrypoint bash pg-pitr -c "$1"
}

if [ -n "$target" ] && [ "$guard" = 1 ]; then
    verdict="$(in_repo '
        set -eu
        first=$(pgbackrest --stanza=qarz --log-level-console=error info --output=json \
            | jq "[.[0].backup[].timestamp.stop] | min")
        want=$(date -u -d "$QD_T" +%s)
        echo "$want $first $(date -u +%s) $(date -u -d "@$first" "+%Y-%m-%d %H:%M:%S+00")"
    ')" || die "could not read the backup repository or parse the target time"
    read -r want first current first_text <<< "$verdict"
    if [ "$want" -le "$first" ]; then
        echo "REFUSED: target $target is not after the end of the first base backup ($first_text)." >&2
        echo "REFUSED: there is no backup to start from; nothing was restored." >&2
        exit 5
    fi
    if [ "$want" -gt "$current" ]; then
        echo "REFUSED: target $target is in the future." >&2
        exit 5
    fi
fi

# A target time can only be reached once the WAL that covers it is in the archive.
# If a writable database is still alive, close its current segment now instead of
# waiting up to archive_timeout (60 s). Not done for --archive-end, which measures
# what the archive already holds.
if [ "$archive_end" = 0 ]; then
    for svc in pg-primary pg-standby; do
        [ "$(container_state "$svc")" = "running" ] || continue
        [ "$(pg_sql "$svc" 'select pg_is_in_recovery()' 2>/dev/null || true)" = "f" ] || continue
        segment="$(pg_sql "$svc" 'select pg_walfile_name(pg_switch_wal())')"
        if wait_sql_true "$svc" "select coalesce(last_archived_wal, '') >= '$segment' from pg_stat_archiver" 60; then
            log "current WAL segment closed and archived on $svc ($segment)"
        else
            die "segment $segment was not archived within 60 s"
        fi
        break
    done
fi

log "removing any previous restore instance"
dc --profile pitr rm --stop --force --volumes pg-pitr > /dev/null 2>&1 || true

log "restoring into a fresh instance (pg-pitr)"
QD_PITR_TARGET="$target" QD_PITR_TIMELINE="$timeline" dc --profile pitr up -d pg-pitr

waited=0
while :; do
    state="$(container_state pg-pitr)"
    if [ "$state" != "running" ]; then
        echo "RESTORE FAILED: pg-pitr is $state. Last log lines:" >&2
        dc --profile pitr logs --no-log-prefix --tail 25 pg-pitr >&2 || true
        exit 6
    fi
    if [ "$(pg_sql pg-pitr 'select pg_is_in_recovery()' 2>/dev/null || true)" = "f" ]; then
        break
    fi
    [ "$waited" -lt 600 ] || die "restore did not finish within about 5 minutes"
    sleep 0.5
    waited=$((waited + 1))
done
t_done="$(now)"

log "restored instance answers on 127.0.0.1:${QD_PORT_PITR:-55534} (service pg-pitr)"
echo "PITR_ELAPSED_SECONDS=$(seconds_between "$t_start" "$t_done")"
