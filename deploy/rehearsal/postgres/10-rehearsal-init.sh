#!/usr/bin/env bash
# Runs once, on the primary, when its data directory is first created.
set -euo pipefail

: "${QD_REPL_PASSWORD:?QD_REPL_PASSWORD is not set}"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v repl_password="$QD_REPL_PASSWORD" <<'SQL'
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD :'repl_password';

-- One row per replication-lag sample and per point-in-time marker.
CREATE TABLE rehearsal_marker (
    id         bigserial PRIMARY KEY,
    label      text        NOT NULL,
    written_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- One row a second during the loss test.
CREATE TABLE rehearsal_tick (
    id         bigserial PRIMARY KEY,
    written_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Written by the failover script to prove the promoted node accepts writes.
CREATE TABLE rehearsal_failover_probe (
    id         bigserial PRIMARY KEY,
    node       text        NOT NULL,
    written_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
SQL

# The default rule of the image covers ordinary databases only; replication needs its own line.
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
