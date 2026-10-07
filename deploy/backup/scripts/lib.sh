#!/usr/bin/env bash
# Shared helpers for the backup scripts. Sourced, never run directly.
#
# Settings come from the environment. When a script is started by hand, the file named by
# QD_BACKUP_ENV (default /etc/qarz-backup/backup.env, the same file the systemd units read) fills in
# whatever the environment does not already set. See ../backup.env.example for every name.
set -euo pipefail

note() { printf '%s\n' "$*" >&2; }
die() {
    note "ERROR: $*"
    exit 1
}

# qd_load_env FILE -> export KEY=VALUE lines of FILE for keys that are not set yet.
# The format is that of a systemd EnvironmentFile: no quoting, no expansion.
qd_load_env() {
    local line key
    [ -r "$1" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in '' | '#'*) continue ;; esac
        key="${line%%=*}"
        [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
        if [ -z "${!key+x}" ]; then
            export "$key=${line#*=}"
        fi
    done < "$1"
}
qd_load_env "${QD_BACKUP_ENV:-/etc/qarz-backup/backup.env}"

: "${QD_STANZA:=qarz}"
: "${QD_DATABASE:=qarz}"
: "${QD_PGUSER:=postgres}"
: "${QD_REPO_PATH:=/var/lib/pgbackrest}"
: "${QD_STATE_DIR:=/var/lib/qarz-backup}"
: "${QD_TEXTFILE_DIR:=/var/lib/node_exporter/textfile_collector}"
: "${QD_CIPHER_FILE:=/etc/pgbackrest/conf.d/cipher.conf}"
: "${QD_FILES_ARCHIVE_DIR:=$QD_STATE_DIR/files-archive}"
: "${QD_MONTHLY_DIR:=$QD_STATE_DIR/monthly}"

need() {
    local tool
    for tool in "$@"; do
        command -v "$tool" > /dev/null 2>&1 || die "missing tool: $tool"
    done
}

now() { date +%s.%N; }
# iso EPOCH -> 2026-10-07T01:30:00Z
iso() { date -u -d "@${1%.*}" +%Y-%m-%dT%H:%M:%SZ; }
# elapsed START END -> END - START with two decimals
elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.2f", b - a }'; }

# pgBackRest with the stanza set. Its console log (standard output) is silenced below "warn", and
# warnings and errors go to standard error, so standard output carries only what was asked for.
pgbr() { pgbackrest --stanza="$QD_STANZA" --log-level-console=warn "$@"; }
repo_info() { pgbr info --output=json; }

# json_line KEY VALUE [KEY VALUE ...] -> one JSON object on one line.
# Values of keys ending in _seconds, _bytes or _count are written as numbers; everything else as text.
json_line() {
    jq -cn '
        [$ARGS.positional | _nwise(2)
         | {(.[0]): (if (.[0] | test("(_seconds|_bytes|_count)$")) then (.[1] | tonumber? // .[1]) else .[1] end)}]
        | add' --args "$@"
}

# write_metrics NAME < text -> $QD_TEXTFILE_DIR/NAME.prom, replaced atomically.
# A missing directory is a warning, not a failure: the backup itself is worth more than its figure,
# and the alert rules fire on a figure that is absent.
write_metrics() {
    local name="$1" tmp
    if [ ! -d "$QD_TEXTFILE_DIR" ] || [ ! -w "$QD_TEXTFILE_DIR" ]; then
        note "WARNING: $QD_TEXTFILE_DIR is not a writable directory; ${name}.prom was not written"
        cat > /dev/null
        return 0
    fi
    tmp="$(mktemp "$QD_TEXTFILE_DIR/.${name}.XXXXXX")"
    cat > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$QD_TEXTFILE_DIR/${name}.prom"
}

# The repository passphrase, for the archives that pgBackRest does not write itself (file store,
# monthly dump). Read from the environment if pgBackRest gets it that way, otherwise from the
# pgBackRest include file. Never printed; callers hand it to openssl through the environment.
qd_cipher_pass() {
    if [ -n "${PGBACKREST_REPO1_CIPHER_PASS:-}" ]; then
        printf '%s' "$PGBACKREST_REPO1_CIPHER_PASS"
        return 0
    fi
    [ -r "$QD_CIPHER_FILE" ] || die "no passphrase: $QD_CIPHER_FILE is not readable"
    sed -n 's/^[[:space:]]*repo1-cipher-pass[[:space:]]*=[[:space:]]*//p' "$QD_CIPHER_FILE" | head -n 1
}

# qd_encrypt < plain > cipher ; qd_decrypt < cipher > plain   (AES-256-CBC, key derived with PBKDF2)
qd_encrypt() {
    QD_PASS="$(qd_cipher_pass)" openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:QD_PASS
}
qd_decrypt() {
    QD_PASS="$(qd_cipher_pass)" openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:QD_PASS
}

# source_sql SQL -> one statement against the database this host serves (the replica on the standby,
# the primary in the local proof). Extra psql arguments: QD_SOURCE_PSQL_ARGS.
source_sql() {
    local -a extra=()
    if [ -n "${QD_SOURCE_PSQL_ARGS:-}" ]; then
        read -r -a extra <<< "$QD_SOURCE_PSQL_ARGS"
    fi
    psql -X -qAt -v ON_ERROR_STOP=1 "${extra[@]}" -U "$QD_PGUSER" -d "$QD_DATABASE" -c "$1"
}
