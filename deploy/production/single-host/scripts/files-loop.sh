#!/usr/bin/env bash
# The `files-backup` service: the copy of the stored files, on its own short schedule.
#
#   every QD_FILES_INTERVAL seconds (300)   files-sync.sh sync
#   once a day                              files-sync.sh expire
#
# A separate container from the database backups because it runs as the application's user (the files
# are readable by their owner only) and needs neither the database nor its volume.
set -uo pipefail

: "${QD_FILES_INTERVAL:=300}"
: "${QD_FILES_STATE_DIR:=/var/lib/qarz-files-backup}"
export QD_FILES_STATE_DIR

trap 'exit 0' TERM INT
expired_on=""
while :; do
    /opt/qarz-single/files-sync.sh sync || true
    today="$(date +%F)"
    if [ "$today" != "$expired_on" ]; then
        if /opt/qarz-single/files-sync.sh expire; then expired_on="$today"; fi
    fi
    sleep "$QD_FILES_INTERVAL" &
    wait $!
done
