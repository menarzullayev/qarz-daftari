#!/usr/bin/env bash
# Sourced by every script of the single-host database image. Turns the few settings compose hands the
# container (QD_R2_*, QD_BACKUP_PASSPHRASE) into what pgBackRest and rclone read from the environment,
# so that the bucket and the passphrase are named once, in the env file, and written to no file here.
#
# Nothing in this file prints a value.

: "${QD_STANZA:=qarz}"
: "${QD_DATABASE:=qarz}"
: "${QD_PGUSER:=postgres}"
: "${QD_STATE_DIR:=/var/lib/qarz-backup}"
# The figures (*.prom) go to a directory of their own, outside the state directory: it is a volume
# the worker mounts read-only for its operations watch, and the worker must see nothing else of the
# backups' state.
: "${QD_TEXTFILE_DIR:=/var/lib/qarz-backup-figures}"
: "${QD_RESTORE_TEST_DIR:=$QD_STATE_DIR/scratch}"
: "${QD_MONTHLY_DIR:=$QD_STATE_DIR/monthly}"
# The scripts of deploy/backup read an env file when one exists; here everything is in the environment.
: "${QD_BACKUP_ENV:=/nonexistent}"
# check.sh: the repository is a bucket, not a directory of this host.
: "${QD_REPO_LISTING:=pgbackrest}"
export QD_STANZA QD_DATABASE QD_PGUSER QD_STATE_DIR QD_TEXTFILE_DIR QD_RESTORE_TEST_DIR QD_MONTHLY_DIR \
    QD_BACKUP_ENV QD_REPO_LISTING

# --- pgBackRest: the repository ----------------------------------------------------------------------
if [ -n "${QD_R2_ENDPOINT:-}" ]; then
    # Host name only, as pgBackRest wants it; a scheme written by habit is dropped.
    QD_R2_HOST="${QD_R2_ENDPOINT#https://}"
    QD_R2_HOST="${QD_R2_HOST%/}"
    export PGBACKREST_REPO1_S3_ENDPOINT="$QD_R2_HOST"
    export PGBACKREST_REPO1_S3_BUCKET="${QD_R2_BUCKET:-}"
    export PGBACKREST_REPO1_S3_REGION="${QD_R2_REGION:-auto}"
    export PGBACKREST_REPO1_S3_KEY="${QD_R2_ACCESS_KEY_ID:-}"
    export PGBACKREST_REPO1_S3_KEY_SECRET="${QD_R2_SECRET_ACCESS_KEY:-}"
fi
if [ -n "${QD_BACKUP_PASSPHRASE:-}" ]; then
    export PGBACKREST_REPO1_CIPHER_PASS="$QD_BACKUP_PASSPHRASE"
fi
# The proof's stand-in for R2 has a certificate of its own authority. Never set in production.
if [ -n "${QD_R2_CA_FILE:-}" ]; then
    export PGBACKREST_REPO1_STORAGE_CA_FILE="$QD_R2_CA_FILE"
    export RCLONE_CA_CERT="$QD_R2_CA_FILE"
fi

# --- rclone: the copy of the stored files and the monthly dumps -----------------------------------------
# Two remotes, both from the environment (no configuration file):
#   qdr2:      the bucket as it is
#   qdcrypt:   <bucket>/crypt through rclone's `crypt`: contents, file names and directory names are
#              encrypted on this machine with the backup passphrase before anything is sent.
# Called by the scripts that use rclone, not at source time: `rclone obscure` costs a process.
qd_rclone_env() {
    [ -n "${RCLONE_CONFIG_QDCRYPT_PASSWORD:-}" ] && return 0
    [ -n "${QD_R2_ENDPOINT:-}" ] || {
        echo "ERROR: QD_R2_ENDPOINT is not set" >&2
        return 1
    }
    [ -n "${QD_BACKUP_PASSPHRASE:-}" ] || {
        echo "ERROR: QD_BACKUP_PASSPHRASE is not set" >&2
        return 1
    }
    export HOME="${HOME:-/tmp}"
    export RCLONE_CONFIG=/dev/null
    export RCLONE_CONFIG_QDR2_TYPE=s3
    export RCLONE_CONFIG_QDR2_PROVIDER=Other
    export RCLONE_CONFIG_QDR2_ENDPOINT="https://$QD_R2_HOST"
    export RCLONE_CONFIG_QDR2_REGION="${QD_R2_REGION:-auto}"
    export RCLONE_CONFIG_QDR2_FORCE_PATH_STYLE=true
    export RCLONE_CONFIG_QDR2_ACCESS_KEY_ID="${QD_R2_ACCESS_KEY_ID:-}"
    export RCLONE_CONFIG_QDR2_SECRET_ACCESS_KEY="${QD_R2_SECRET_ACCESS_KEY:-}"
    # The key is limited to one bucket and may not list or create buckets.
    export RCLONE_CONFIG_QDR2_NO_CHECK_BUCKET=true
    export RCLONE_CONFIG_QDCRYPT_TYPE=crypt
    export RCLONE_CONFIG_QDCRYPT_REMOTE="qdr2:${QD_R2_BUCKET:-}/crypt"
    export RCLONE_CONFIG_QDCRYPT_FILENAME_ENCRYPTION=standard
    export RCLONE_CONFIG_QDCRYPT_DIRECTORY_NAME_ENCRYPTION=true
    # rclone wants the password in its own reversible form; the passphrase goes in on standard input.
    RCLONE_CONFIG_QDCRYPT_PASSWORD="$(printf '%s' "$QD_BACKUP_PASSPHRASE" | rclone obscure -)" || return 1
    export RCLONE_CONFIG_QDCRYPT_PASSWORD
}

# rclone, quiet unless something is wrong.
rc() { rclone --log-level NOTICE --stats 0 "$@"; }
