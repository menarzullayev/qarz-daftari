#!/usr/bin/env bash
# Health of the `backup` container, as `docker ps` shows it: healthy only while the last check of the
# repository found a recent backup and a recent WAL segment, and that check itself is recent.
# It asks the bucket nothing: it reads what `job.sh check` wrote.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh

: "${QD_CHECK_INTERVAL:=300}"
read -r verdict at < "$QD_STATE_DIR/check.status" 2> /dev/null || {
    echo "no check of the repository has run yet"
    exit 1
}
age=$(($(date +%s) - at))
if [ "$age" -gt $((QD_CHECK_INTERVAL * 3 + 60)) ]; then
    echo "the last check of the repository is $age s old: the scheduler is not running its checks"
    exit 1
fi
if [ "$verdict" != 0 ]; then
    echo "the last check of the repository failed (exit $verdict): a backup or the WAL archive is too old or missing"
    exit 1
fi
echo "backups and WAL archive are fresh (checked $age s ago)"
