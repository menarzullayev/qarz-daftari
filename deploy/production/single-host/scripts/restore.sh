#!/usr/bin/env bash
# Restore the database from the bucket into the data directory of the single host.
#
#   restore.sh                      everything the repository holds: the latest backup, then every
#                                   archived WAL segment after it
#   restore.sh --time '<moment>'    up to a moment, e.g. '2026-10-08 14:05:00+05' (point-in-time)
#
# Run by `scripts/single-host.sh restore`, in a one-shot container, with the database stopped.
#
# It REFUSES a data directory that already holds a database, and removes nothing: restoring over a
# database is how the last good copy gets destroyed. To look at an earlier state while the live
# database stays as it is, use the scratch copy instead (single-host.sh pitr). To restore after the
# machine or its disk was lost, the directory is empty and this is the command.
#
# After it ends, starting the database replays the archive and opens for writing; it then archives
# to the same repository on a new timeline. Exit codes: 0 restored, 65 refused, 64 wrong usage.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
target=""
while [ $# -gt 0 ]; do
    case "$1" in
    --time)
        target="${2:-}"
        [ -n "$target" ] || {
            echo "usage: restore.sh [--time '<moment>']" >&2
            exit 64
        }
        shift 2
        ;;
    *)
        echo "usage: restore.sh [--time '<moment>']" >&2
        exit 64
        ;;
    esac
done

if [ -e "$PGDATA/PG_VERSION" ] || [ -n "$(ls -A "$PGDATA" 2> /dev/null)" ]; then
    echo "REFUSED: $PGDATA is not empty. This command restores into an empty data directory only and never removes a database." >&2
    exit 65
fi

args=(--stanza="$QD_STANZA" --pg1-path="$PGDATA" --log-level-console=warn restore)
if [ -n "$target" ]; then
    args+=(--type=time "--target=$target" --target-action=promote)
else
    args+=(--type=default)
fi

started="$(date +%s)"
pgbackrest "${args[@]}"
label="$(pgbackrest --stanza="$QD_STANZA" --log-level-console=warn info --output=json \
    | jq -r '.[0].backup | sort_by(.timestamp.stop) | last | .label // ""')"
jq -cn --arg event restore --arg outcome ok --arg newest_backup "$label" --arg target "${target:-end of archive}" \
    --argjson duration_seconds "$(($(date +%s) - started))" '$ARGS.named'
