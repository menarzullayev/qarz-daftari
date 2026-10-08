#!/usr/bin/env bash
# Asked by `single-host.sh up` before the database is started: is it safe to start a database here?
#
# The dangerous case is a NEW machine (or a lost disk) in front of the bucket that holds the real
# database's backups. Started as it is, the service would come up empty and look healthy, and shops
# would start recording into a database that is about to be replaced by the restore. So:
#
#   - the data volume holds a database           -> go on (exit 0)
#   - the volume is empty, the bucket is empty   -> go on: this is the first installation (exit 0)
#   - the volume is empty, the bucket has backups -> REFUSE (exit 66): restore first
#   - the bucket cannot be read                  -> stop (exit 2): wrong address, key or passphrase,
#                                                  found out now and not at the first backup
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh

PGDATA="${PGDATA:-/var/lib/postgresql/data}"
if [ -s "$PGDATA/PG_VERSION" ]; then
    echo "the data volume holds a database"
    exit 0
fi

info="$(pgbackrest --log-level-console=warn info --output=json)" || {
    echo "ERROR: the bucket could not be read. Check DEPLOY_R2_ENDPOINT, DEPLOY_R2_BUCKET, the key and the passphrase." >&2
    exit 2
}
# pgBackRest answers "info" even when it could not read the repository (a wrong passphrase: the files
# are there and are noise). Only "ok" (0) and "no backup yet" (2) are answers about what is there.
unreadable="$(jq -r --arg stanza "$QD_STANZA" '
    [.[] | select(.name == $stanza) | .status | select(.code != 0 and .code != 2) | .message | split("\n")[0]]
    | first // empty' <<< "$info")"
if [ -n "$unreadable" ]; then
    echo "ERROR: the bucket holds a repository that could not be read with this passphrase and key." >&2
    echo "ERROR: pgBackRest said: ${unreadable:0:200}" >&2
    exit 2
fi
backups="$(jq --arg stanza "$QD_STANZA" '[.[] | select(.name == $stanza) | .backup | length] | add // 0' <<< "$info")"
if [ "$backups" -gt 0 ]; then
    echo "REFUSED: the data volume is empty and the bucket holds $backups backup(s) of a database." >&2
    echo "REFUSED: on a new machine, restore first (single-host.sh restore). A new, empty database is started only in front of an empty bucket." >&2
    exit 66
fi
echo "the data volume and the bucket are both empty: a first installation"
