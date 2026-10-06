#!/usr/bin/env bash
# Entrypoint wrapper for the three database roles of the rehearsal:
#   primary  - starts normally; refuses to start once another node has been promoted
#   standby  - on first start, restores the latest base backup as a streaming standby
#   pitr     - restores into an empty data directory up to a target, then starts with archiving off
set -euo pipefail

ROLE="${QD_ROLE:?QD_ROLE must be primary, standby or pitr}"
NODE="${QD_NODE:?QD_NODE must be set to the service name}"
PGDATA="${PGDATA:-/var/lib/postgresql/data}"
STANZA=qarz
FENCE_FILE=/fence/promoted

if [ "${1:-}" != "postgres" ]; then
    exec docker-entrypoint.sh "$@"
fi
shift

common=(
    -c wal_level=replica
    -c max_wal_senders=5
    -c wal_keep_size=256MB
    -c hot_standby=on
    -c synchronous_commit=on
    -c synchronous_standby_names=
    -c archive_timeout=60
)
archiving=(
    -c archive_mode=on
    -c "archive_command=pgbackrest --stanza=${STANZA} archive-push %p"
)

refuse_if_fenced() {
    # The failover script writes the name of the promoted node into the fence file
    # before it promotes. Any other node must not come up as a writable database.
    if [ -s "$FENCE_FILE" ]; then
        promoted="$(cut -d' ' -f1 "$FENCE_FILE")"
        if [ "$promoted" != "$NODE" ] && [ "${QD_IGNORE_FENCE:-0}" != "1" ]; then
            echo "FENCED: ${promoted} was promoted ($(cat "$FENCE_FILE"))." >&2
            echo "FENCED: ${NODE} refuses to start as a database. Rebuild it as a standby instead." >&2
            exit 78
        fi
    fi
}

prepare_empty_pgdata() {
    mkdir -p "$PGDATA"
    chown postgres:postgres "$PGDATA"
    chmod 700 "$PGDATA"
}

case "$ROLE" in
primary)
    refuse_if_fenced
    exec docker-entrypoint.sh postgres "${common[@]}" "${archiving[@]}" "$@"
    ;;
standby)
    refuse_if_fenced
    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        : "${QD_REPL_PASSWORD:?QD_REPL_PASSWORD is not set}"
        prepare_empty_pgdata
        echo "standby: restoring the latest base backup as a streaming standby"
        gosu postgres pgbackrest --stanza="$STANZA" restore --type=standby \
            "--recovery-option=primary_conninfo=host=pg-primary port=5432 user=replicator password=${QD_REPL_PASSWORD} application_name=${NODE}"
    fi
    exec docker-entrypoint.sh postgres "${common[@]}" "${archiving[@]}" "$@"
    ;;
pitr)
    if [ -s "$PGDATA/PG_VERSION" ]; then
        echo "pitr: data directory is not empty; this instance is restored once and then thrown away" >&2
        exit 64
    fi
    prepare_empty_pgdata
    restore_args=(--stanza="$STANZA" restore)
    if [ -n "${QD_PITR_TARGET:-}" ]; then
        restore_args+=(--type=time "--target=${QD_PITR_TARGET}" --target-action=promote)
    else
        # No target time: replay every archived segment of the chosen timeline;
        # PostgreSQL then ends recovery by itself and opens for writes.
        restore_args+=(--type=default)
    fi
    if [ -n "${QD_PITR_TIMELINE:-}" ]; then
        restore_args+=("--target-timeline=${QD_PITR_TIMELINE}")
    fi
    echo "pitr: pgbackrest ${restore_args[*]}"
    gosu postgres pgbackrest "${restore_args[@]}"
    # Archiving is off: a restored copy must never push WAL into the live repository.
    exec docker-entrypoint.sh postgres "${common[@]}" -c archive_mode=off "$@"
    ;;
*)
    echo "unknown QD_ROLE: $ROLE" >&2
    exit 64
    ;;
esac
