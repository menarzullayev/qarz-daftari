#!/usr/bin/env bash
# Health of the `files-backup` container, as `docker ps` shows it: healthy only while the stored files
# were copied to the bucket recently. It asks the bucket nothing: it reads what files-sync.sh wrote.
set -euo pipefail

: "${QD_FILES_INTERVAL:=300}"
: "${QD_FILES_STATE_DIR:=/var/lib/qarz-files-backup}"

at="$(cat "$QD_FILES_STATE_DIR/sync.last-success" 2> /dev/null || echo 0)"
if [ "$at" = 0 ]; then
    echo "the stored files have not been copied to the bucket yet"
    exit 1
fi
age=$(($(date +%s) - at))
if [ "$age" -gt $((QD_FILES_INTERVAL * 3 + 120)) ]; then
    echo "the last successful copy of the stored files is $age s old"
    exit 1
fi
echo "the stored files were copied $age s ago"
