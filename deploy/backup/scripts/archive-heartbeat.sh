#!/usr/bin/env bash
# Keep the WAL archive moving while nobody writes.
#
# PostgreSQL closes a WAL segment after archive_timeout only if something was written since the last
# one. At night, with the shops closed, nothing is: the archive would age past the five-minute alert
# although nothing is wrong. This writes one small WAL record a minute (no table, no row), so that a
# quiet archive always means a broken one.
#
# Runs on the PRIMARY as the postgres user. Does nothing on a standby. Prints nothing when it works.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need psql

psql -X -qAt -v ON_ERROR_STOP=1 -U "$QD_PGUSER" -d postgres -c "
    select pg_logical_emit_message(true, 'qd-archive-heartbeat', '')
     where not pg_is_in_recovery()" > /dev/null
