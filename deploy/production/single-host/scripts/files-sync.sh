#!/usr/bin/env bash
# The stored files (receipts, imports, exports) of the single host: their second copy, in the bucket.
#
# The application keeps its files in a directory on a volume of this machine (QD_FILE_STORE=filesystem).
# This script copies that directory to the bucket through rclone's `crypt` remote: contents, file names
# and directory names are encrypted here, with the backup passphrase, before anything is sent.
#
#   files-sync.sh sync      make qdcrypt:current equal to the directory (every five minutes)
#   files-sync.sh expire    remove what was moved to qdcrypt:removed more than QD_FILES_REMOVED_KEEP_DAYS ago
#   files-sync.sh restore   copy qdcrypt:current back into the directory (a new machine; never deletes)
#
# A file removed or replaced on this machine is not deleted in the bucket at once: it is moved to
# qdcrypt:removed/<day>/ and deleted QD_FILES_REMOVED_KEEP_DAYS (30) days later, so a mistake here does
# not become a loss there the same minute. Two refusals protect the copy from a broken source:
#   - an EMPTY directory is never synchronised over a copy that holds files (a new or wiped machine);
#   - a run that would remove more than QD_FILES_MAX_DELETE (100) files stops with an error.
#
# Runs as the user that owns the files (10001, the application's). Standard output: one JSON line.
# Exit codes: 0 done, 64 wrong usage, anything else: failed.
set -euo pipefail
# shellcheck source-path=SCRIPTDIR source=env.sh
. /opt/qarz-single/env.sh

: "${QD_FILES_DIR:=/var/lib/qarz/files}"
: "${QD_FILES_STATE_DIR:=/var/lib/qarz-files-backup}"
: "${QD_FILES_MAX_DELETE:=100}"
: "${QD_FILES_REMOVED_KEEP_DAYS:=30}"
CURRENT=qdcrypt:current
REMOVED=qdcrypt:removed

mode="${1:-}"
case "$mode" in
sync | expire | restore) ;;
*)
    echo "usage: files-sync.sh <sync|expire|restore>" >&2
    exit 64
    ;;
esac

note() { printf '%s\n' "$*" >&2; }
die() {
    note "ERROR: $*"
    exit 1
}

started="$(date +%s)"
outcome=failed
source_objects=0
objects=0
bytes=0
removed=0
counted=0
weighed=0

finish() {
    local code=$? ended success=0 tmp
    trap - EXIT
    ended="$(date +%s)"
    if [ "$outcome" = ok ]; then
        code=0
        success=1
    elif [ "$code" = 0 ]; then
        code=1
    fi
    # The same figures as deploy/backup/scripts/filestore-sync.sh, so the same alert rules read them.
    if [ -d "$QD_FILES_STATE_DIR" ] && [ -w "$QD_FILES_STATE_DIR" ]; then
        tmp="$(mktemp "$QD_FILES_STATE_DIR/.qd_filestore_${mode}.XXXXXX")"
        {
            echo "# HELP qd_filestore_last_run_success Whether the last run of this file store job succeeded (1) or failed (0)."
            echo "# TYPE qd_filestore_last_run_success gauge"
            echo "qd_filestore_last_run_success{mode=\"${mode}\"} ${success}"
            echo "# HELP qd_filestore_last_run_timestamp_seconds When the last run of this file store job ended."
            echo "# TYPE qd_filestore_last_run_timestamp_seconds gauge"
            echo "qd_filestore_last_run_timestamp_seconds{mode=\"${mode}\"} ${ended}"
        } > "$tmp"
        chmod 644 "$tmp"
        mv -f "$tmp" "$QD_FILES_STATE_DIR/qd_filestore_${mode}.prom"
        [ "$success" = 0 ] || printf '%s\n' "$ended" > "$QD_FILES_STATE_DIR/${mode}.last-success"
    fi
    jq -cn --arg event "filestore_${mode}" --arg outcome "$outcome" \
        --arg started_at "$(date -u -d "@$started" +%Y-%m-%dT%H:%M:%SZ)" \
        --arg ended_at "$(date -u -d "@$ended" +%Y-%m-%dT%H:%M:%SZ)" \
        --argjson duration_seconds "$((ended - started))" --argjson source_objects_count "$source_objects" \
        --argjson objects_count "$objects" --argjson size_bytes "$bytes" --argjson removed_count "$removed" \
        '$ARGS.named'
    exit "$code"
}
trap finish EXIT

qd_rclone_env
[ -d "$QD_FILES_DIR" ] || die "$QD_FILES_DIR is not a directory"
# The application writes a file beside its target as tmp-<random> and renames it; half a file is not copied.
FILTER=(--exclude 'tmp-*')
# measure PATH-OR-REMOTE -> sets $counted and $weighed (objects, bytes); stops the script when it cannot.
measure() {
    local answer
    answer="$(rc size --json "${FILTER[@]}" "$1")" || die "could not count the files of $1"
    counted="$(jq -r '.count' <<< "$answer")"
    weighed="$(jq -r '.bytes' <<< "$answer")"
    [[ "$counted" =~ ^[0-9]+$ ]] || die "could not count the files of $1"
}

case "$mode" in
sync)
    measure "$QD_FILES_DIR"
    source_objects="$counted"
    measure "$CURRENT"
    objects="$counted"
    if [ "$source_objects" = 0 ] && [ "$objects" != 0 ]; then
        die "the directory is empty and the copy in the bucket holds $objects files: nothing was changed. On a new machine run the restore first (files-sync.sh restore)."
    fi
    day="$(date +%Y%m%d)"
    rc sync "${FILTER[@]}" "$QD_FILES_DIR" "$CURRENT" --backup-dir "$REMOVED/$day" --max-delete "$QD_FILES_MAX_DELETE" >&2
    measure "$CURRENT"
    objects="$counted"
    bytes="$weighed"
    [ "$objects" = "$source_objects" ] || die "the copy holds $objects files, the directory $source_objects"
    ;;
expire)
    limit="$(date -d "-${QD_FILES_REMOVED_KEEP_DAYS} days" +%Y%m%d)"
    while IFS= read -r dir; do
        dir="${dir%/}"
        [[ "$dir" =~ ^[0-9]{8}$ ]] || continue
        [ "$dir" -lt "$limit" ] || continue
        rc purge "$REMOVED/$dir" >&2
        note "removed for good: what was moved aside on $dir"
        removed=$((removed + 1))
    done < <(rc lsf --dirs-only "$REMOVED" 2> /dev/null || true)
    ;;
restore)
    measure "$CURRENT"
    source_objects="$counted"
    bytes="$weighed"
    [ "$source_objects" != 0 ] || die "the copy in the bucket holds no files that this passphrase can read"
    rc copy "$CURRENT" "$QD_FILES_DIR" >&2
    measure "$QD_FILES_DIR"
    objects="$counted"
    [ "$objects" -ge "$source_objects" ] || die "the directory holds $objects files, the copy $source_objects"
    ;;
esac
outcome=ok
