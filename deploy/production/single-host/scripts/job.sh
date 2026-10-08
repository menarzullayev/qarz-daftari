#!/usr/bin/env bash
# One backup job, now. The scheduler runs every job through this script, and so does a person
# (scripts/single-host.sh backup, restore-test, status), so the two never overlap: one lock for all.
#
#   job.sh stanza          create the pgBackRest stanza if it is not there (safe to repeat)
#   job.sh full | diff     one backup (deploy/backup/scripts/backup.sh)
#   job.sh check           ages of the newest backup and of the newest archived WAL segment
#   job.sh restore-test    restore the latest backup into a throwaway instance and check it
#   job.sh expire          remove what the retention rules no longer keep
#   job.sh monthly         the monthly dump, and its copy in the bucket
#   job.sh heartbeat       one WAL record, so that an idle database still archives
#
# Standard output: the job's own JSON line. Exit code: the job's.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh

S=/opt/qarz-backup/scripts
job="${1:-}"
mkdir -p "$QD_STATE_DIR" "$QD_TEXTFILE_DIR" "$QD_RESTORE_TEST_DIR" "$QD_MONTHLY_DIR"

# The heartbeat and the check touch nothing another job could be writing, and must not wait behind a
# backup that takes minutes: they run without the lock.
case "$job" in
heartbeat)
    exec bash "$S/archive-heartbeat.sh"
    ;;
check)
    shift
    code=0
    bash "$S/check.sh" "$@" || code=$?
    # What the container's health check reads: the verdict and when it was reached.
    printf '%s %s\n' "$code" "$(date +%s)" > "$QD_STATE_DIR/check.status.tmp"
    mv -f "$QD_STATE_DIR/check.status.tmp" "$QD_STATE_DIR/check.status"
    exit "$code"
    ;;
esac

exec 8> "$QD_STATE_DIR/job.lock"
flock -w "${QD_JOB_LOCK_WAIT:-1800}" 8 || {
    echo "ERROR: another backup job has been running for too long; this one ($job) did not start" >&2
    exit 75
}

case "$job" in
stanza)
    # Refuses, and changes nothing, when the bucket already holds the stanza of ANOTHER database
    # cluster: a fresh, empty database must never be allowed to write over the backups of the real one.
    exec pgbackrest --stanza="$QD_STANZA" --log-level-console=warn stanza-create
    ;;
full | diff)
    exec bash "$S/backup.sh" "$job"
    ;;
restore-test)
    # "The restored copy is the application's database, at the schema this host runs": the live
    # database says which migration that is (the repository's migrations are not in this image).
    if [ -z "${QD_EXPECTED_MIGRATION_HEAD:-}" ]; then
        QD_EXPECTED_MIGRATION_HEAD="$(psql -X -qAt -v ON_ERROR_STOP=1 -U "$QD_PGUSER" -d "$QD_DATABASE" \
            -c 'select version_num from alembic_version')" || {
            echo "ERROR: the live database could not be asked for its migration" >&2
            exit 1
        }
        export QD_EXPECTED_MIGRATION_HEAD
    fi
    exec bash "$S/restore-test.sh"
    ;;
expire)
    shift
    bash "$S/expire.sh" "$@"
    exec bash /opt/qarz-single/monthly.sh prune "$@"
    ;;
monthly)
    bash "$S/monthly-archive.sh"
    exec bash /opt/qarz-single/monthly.sh upload
    ;;
*)
    echo "usage: job.sh <stanza|full|diff|check|restore-test|expire|monthly|heartbeat>" >&2
    exit 64
    ;;
esac
