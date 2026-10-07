#!/usr/bin/env bash
# Shared helpers for the rehearsal scripts. Sourced, never run directly.
set -euo pipefail

# Git Bash on Windows rewrites arguments that look like POSIX paths
# (for example /fence/promoted). Docker needs them untouched.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REHEARSAL_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
ENV_FILE=".env"
OUT_DIR=".out"
DB=qarz

# Every path below is relative to the rehearsal directory, so that native
# Windows tools (curl, docker) and POSIX tools agree on them.
cd "$REHEARSAL_DIR"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "./$ENV_FILE"
    set +a
fi

# The Compose project. Another directory can run its own copy of this stack beside this one by
# setting QD_COMPOSE_PROJECT and adding services through QD_COMPOSE_OVERLAY (a second compose file,
# path relative to this directory); deploy/backup/proof does.
PROJECT="${QD_COMPOSE_PROJECT:-qd-rehearsal}"

dc() {
    docker compose --progress quiet -p "$PROJECT" -f compose.yml ${QD_COMPOSE_OVERLAY:+-f "$QD_COMPOSE_OVERLAY"} "$@"
}

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

now() { date +%s.%N; }
# seconds_between START END -> END - START with two decimals
seconds_between() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", b - a }'; }

rand_hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }

# container_id SERVICE -> id of the service container, running or not; empty if none
container_id() { dc ps -aq "$1" 2>/dev/null | head -n 1; }

# container_state SERVICE -> running | exited | ... | absent
container_state() {
    local id
    id="$(container_id "$1")"
    if [ -z "$id" ]; then
        echo absent
    else
        docker inspect -f '{{.State.Status}}' "$id"
    fi
}

# pg_sql SERVICE SQL -> unaligned, tuples-only output of one statement
pg_sql() {
    local svc="$1"
    shift
    dc exec -T "$svc" psql -U postgres -d "$DB" -v ON_ERROR_STOP=1 -qAtX -c "$1"
}

# wait_healthy SERVICE TIMEOUT_SECONDS
wait_healthy() {
    local svc="$1" timeout="$2" id status waited=0
    id="$(container_id "$svc")"
    [ -n "$id" ] || die "$svc has no container"
    while :; do
        status="$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")"
        case "$status" in
        running/healthy) return 0 ;;
        exited/* | dead/*)
            dc logs --tail 30 "$svc" >&2
            die "$svc stopped while starting"
            ;;
        esac
        [ "$waited" -lt "$timeout" ] || die "$svc not healthy after ${timeout}s (state: $status)"
        sleep 1
        waited=$((waited + 1))
    done
}

# wait_sql_true SERVICE SQL TIMEOUT_SECONDS -> waits until the statement returns "t"
wait_sql_true() {
    local svc="$1" sql="$2" timeout="$3" waited=0
    while [ "$(pg_sql "$svc" "$sql" 2>/dev/null || true)" != "t" ]; do
        [ "$waited" -lt "$timeout" ] || return 1
        sleep 1
        waited=$((waited + 1))
    done
}
