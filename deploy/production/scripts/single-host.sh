#!/usr/bin/env bash
# The whole service on one machine: compose.yml plus compose.single-host.yml (PostgreSQL, the Cloudflare
# Tunnel, the backups to R2). The guide, in the order things are done, is deploy/production/SINGLE-HOST.md.
#
#   single-host.sh env-init [<file>]      write a new env file with generated passwords and secrets
#   single-host.sh up [<git-ref>]         build what is missing, start the database, migrate, set the
#                                         roles' passwords, deploy the release (default HEAD), then start
#                                         the backups and the tunnel. Also the command for a new release.
#   single-host.sh start | stop           start everything at the current release / stop everything
#   single-host.sh status                 what runs, how old the newest backup and WAL segment are,
#                                         and what the operations watch has firing
#   single-host.sh alert-test             send one TEST alert to QD_ALERT_CHAT_IDS and say whether
#                                         Telegram took it (runbook 16; launch criterion 9)
#   single-host.sh catalog-import <dir>   load the seed of the shared product catalogue from <dir>
#                                         (source.json, categories.json, img/) into the database and the
#                                         file store; safe to run again; turns no switch on
#   single-host.sh territories-import <dir>   load the seed of the territory reference from <dir>
#                                         (regions.csv, districts.csv, mahallas.csv, streets.csv) into
#                                         the database; safe to run again; turns no switch on
#   single-host.sh admin-key create <telegram-id> <label> | list | revoke <label>
#                                         a service key of an administrator: a bearer token that does not
#                                         run out; written once to admin-key.<label> beside the env file
#   single-host.sh admin-password <telegram-id> <login>
#                                         set an administrator's password, typed on this terminal
#   single-host.sh backup <full|diff>     one backup, now
#   single-host.sh restore-test           restore the latest backup into a throwaway instance and check it
#   single-host.sh restore [--time '<moment>']   restore the bucket into the EMPTY data volume
#   single-host.sh restore-files          copy the stored files back from the bucket
#   single-host.sh pitr ['<moment>']      a point-in-time copy beside the live database (runbook 3)
#   single-host.sh pitr-down              remove that copy
#   single-host.sh rollback <git-ref>     the previous images (rollback.sh); the schema is not changed
#   single-host.sh smoke                  smoke.sh against https://$DEPLOY_PUBLIC_HOST
#   single-host.sh logs [<service>...]    the last lines of the logs
#
# Environment: DEPLOY_ENV_FILE (default ~/.qarz/single-host.env), DEPLOY_PROJECT (default qarz),
# DEPLOY_STATE_DIR (default ~/.qarz/deploy). Runs in bash on Linux or in Git Bash on Windows, beside
# Docker Desktop; it changes nothing on the host but those two paths and Docker's own objects of the
# project. No command here removes a volume that holds data, and none prints a value from the env file.
set -euo pipefail

export DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$HOME/.qarz/single-host.env}"
export DEPLOY_PROJECT="${DEPLOY_PROJECT:-qarz}"
export DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-$HOME/.qarz/deploy}"
export DEPLOY_COMPOSE_OVERLAY
DEPLOY_COMPOSE_OVERLAY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compose.single-host.yml"
# shellcheck source-path=SCRIPTDIR source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# docker compose with arguments that are paths INSIDE a container: Git Bash on Windows must leave them alone.
dce() { MSYS_NO_PATHCONV=1 compose "$@"; }

# Names that must have a value before anything is started. Names only: a value is never printed.
REQUIRED="CLOUDFLARE_TUNNEL_TOKEN DEPLOY_R2_ENDPOINT DEPLOY_R2_BUCKET DEPLOY_R2_ACCESS_KEY_ID
DEPLOY_R2_SECRET_ACCESS_KEY DEPLOY_BACKUP_PASSPHRASE QD_MIGRATION_URL QD_DATABASE_URL QD_WORKER_DATABASE_URL
QD_BOT_TOKEN QD_WEBHOOK_SECRET QD_SECRETS_KEY"

# The release and the database image that run now, for the commands that need a running stack.
use_current() {
  require_env_file
  RELEASE="$(current_release)"
  [ -n "$RELEASE" ] || die "nothing is deployed here yet: run single-host.sh up"
  DEPLOY_PGBACKUP_TAG="$(cat "$STATE_DIR/pgbackup-tag" 2>/dev/null || true)"
  [ -n "$DEPLOY_PGBACKUP_TAG" ] || die "$STATE_DIR/pgbackup-tag is missing: run single-host.sh up"
  export DEPLOY_PGBACKUP_TAG
}

# For the commands that must also work on a machine where nothing was deployed yet (a restore onto a
# new machine): the current release if there is one, otherwise the checkout's HEAD; the database image
# is built if it is not here.
use_current_or_head() {
  require_env_file
  RELEASE="$(current_release)"
  [ -n "$RELEASE" ] || RELEASE="$(resolve_commit HEAD)"
  DEPLOY_PGBACKUP_TAG="$(cat "$STATE_DIR/pgbackup-tag" 2>/dev/null || true)"
  [ -n "$DEPLOY_PGBACKUP_TAG" ] || DEPLOY_PGBACKUP_TAG="$(pgbackup_tag "$RELEASE")"
  export DEPLOY_PGBACKUP_TAG
  ensure_pgbackup_image "$RELEASE"
}

# A restored database replays the write-ahead log from the bucket before it accepts writes. However long
# the archive, it is waited for: up to DEPLOY_RECOVERY_WAIT seconds (default 3600).
wait_for_writable() {
  local waited=0 limit="${DEPLOY_RECOVERY_WAIT:-3600}" answer
  while :; do
    answer="$(dce exec -T db psql -X -qAt -U postgres -d postgres -c 'select pg_is_in_recovery()' 2>/dev/null | tr -d '\r' || true)"
    [ "$answer" != f ] || return 0
    [ "$waited" -lt "$limit" ] || return 1
    sleep 3; waited=$((waited + 3))
  done
}

preflight() {
  local name missing=""
  for name in $REQUIRED; do
    [ -n "$(env_value "$name")" ] || missing="$missing $name"
  done
  [ -z "$missing" ] || die "these names have no value in $ENV_FILE:$missing"
}

# --- env-init -----------------------------------------------------------------------------------------
env_init() {
  local target="${1:-$ENV_FILE}" owner app admin worker
  [ ! -e "$target" ] || die "$target exists; it is not overwritten"
  mkdir -p "$(dirname "$target")"
  owner="$(openssl rand -hex 24)"; app="$(openssl rand -hex 24)"
  admin="$(openssl rand -hex 24)"; worker="$(openssl rand -hex 24)"
  ( umask 077; cat > "$target" <<EOF
# Settings of the single-host deployment. Written by single-host.sh env-init on $(date -u +%Y-%m-%d).
# Readable by its owner only. Never commit it, never paste it into a chat.
# What each name means: deploy/production/single-host.env.example and the root .env.example.

# --- Fill in (deploy/production/SINGLE-HOST.md says where each comes from) ---------------------------
DEPLOY_PUBLIC_HOST=
CLOUDFLARE_TUNNEL_TOKEN=
DEPLOY_R2_ENDPOINT=
DEPLOY_R2_BUCKET=
DEPLOY_R2_ACCESS_KEY_ID=
DEPLOY_R2_SECRET_ACCESS_KEY=
VITE_BOT_USERNAME=
QD_BOT_TOKEN=
QD_ADMIN_TG_IDS=
# The administrators' second factor: required, or off (no code is asked for anywhere; SINGLE-HOST.md).
QD_ADMIN_SECOND_FACTOR=required
# The host administrators' passkeys are made for: the same name as DEPLOY_PUBLIC_HOST. Empty: none.
QD_PASSKEY_HOST=
# Whom the operations watch tells (your Telegram identifier, or a group's). Empty: nobody is told.
QD_ALERT_CHAT_IDS=

# --- Generated. Copy DEPLOY_BACKUP_PASSPHRASE to two places that are NOT this machine, now. ----------
# Without it the backups in the bucket cannot be read by anybody, you included.
DEPLOY_BACKUP_PASSPHRASE=$(openssl rand -hex 32)
QD_MIGRATION_URL=postgresql://postgres:$owner@db:5432/qarz
QD_DATABASE_URL=postgresql://qd_app:$app@db:5432/qarz
QD_ADMIN_DATABASE_URL=postgresql://qd_admin:$admin@db:5432/qarz
QD_WORKER_DATABASE_URL=postgresql://qd_worker:$worker@db:5432/qarz
QD_WEBHOOK_SECRET=$(openssl rand -hex 32)
QD_SECRETS_KEY=$(openssl rand -base64 32)
QD_METRICS_TOKEN=$(openssl rand -hex 24)
EOF
  )
  say "written: $target"
  say "generated: the backup passphrase, four database passwords, the webhook secret, the server secret, the metrics token"
  say "next: 1. copy DEPLOY_BACKUP_PASSPHRASE and QD_SECRETS_KEY from that file to your password manager and one more place off this machine"
  say "      2. fill in the names at the top of the file (deploy/production/SINGLE-HOST.md)"
}

# --- up -------------------------------------------------------------------------------------------------
up() {
  require_env_file
  preflight
  RELEASE="$(resolve_commit "${1:-HEAD}")"
  DEPLOY_PGBACKUP_TAG="$(pgbackup_tag "$RELEASE")"
  export DEPLOY_PGBACKUP_TAG
  ensure_images "$RELEASE"
  ensure_pgbackup_image "$RELEASE"
  mkdir -p "$STATE_DIR"
  printf '%s\n' "$DEPLOY_PGBACKUP_TAG" > "$STATE_DIR/pgbackup-tag"

  # Before a database is started: an empty data volume in front of a bucket that holds backups means
  # the restore was skipped (a new machine), and a bucket that cannot be read is found out now.
  say "checking the data volume against the bucket"
  compose run --rm --no-deps restore fresh-check \
    || die "not started (see above). No database was created and nothing was changed."

  say "database: starting"
  compose up --detach --wait --wait-timeout "${DEPLOY_RECOVERY_WAIT:-3600}" db
  # The bucket must hold THIS database's repository or none: pgBackRest refuses, and changes nothing,
  # when the repository there belongs to another database.
  # A database that was just restored opens for reading while it still replays the archive; nothing
  # below can be done before it has finished and opened for writing.
  say "database: waiting until it accepts writes"
  wait_for_writable || die "the database did not open for writing; see: docker compose -p $PROJECT logs db"
  say "backups: the repository in the bucket"
  compose run --rm --no-deps backup job stanza \
    || die "the repository in the bucket could not be opened for this database (see above). Nothing else was started."

  say "database: migrations, then the roles' passwords from the connection settings"
  compose run --rm --no-deps owner
  compose run --rm --no-deps migrate
  compose run --rm --no-deps roles

  bash "$SCRIPTS_DIR/deploy.sh" "$RELEASE"

  say "backups and tunnel: starting"
  compose up --detach --no-deps backup files-backup cloudflared
  say "next: single-host.sh status   (the first full backup is taken by itself within a minute)"
}

status() {
  use_current
  compose ps --format 'table {{.Service}}\t{{.Status}}'
  say "--- backups (repository in the bucket) ---"
  dce exec -T backup /opt/qarz-single/job.sh check || say "backups: something is too old or missing (see above)"
  say "--- stored files (copy in the bucket) ---"
  # shellcheck disable=SC2016  # expanded in the container
  dce exec -T files-backup bash -c 'at="$(cat /var/lib/qarz-files-backup/sync.last-success 2>/dev/null || echo 0)"; if [ "$at" = 0 ]; then echo "no successful copy yet"; else echo "last successful copy: $(( $(date +%s) - at )) s ago"; fi' \
    || say "the files-backup service is not running"
  say "--- disk (the Docker disk that holds the database) ---"
  dce exec -T db df -h /var/lib/postgresql/data | tail -n 1 || true
  say "--- operations watch (what is firing now; nothing below this line means nothing is) ---"
  dce exec -T db psql -X -qAt -U postgres -d qarz -c "select key || '  since ' || to_char(since, 'YYYY-MM-DD HH24:MI') || '  ' || case when firing_since is null then 'not yet firing' when resolved_at is not null then 'stopped, not yet said' when notified_at is null then 'FIRING, nobody told (' || coalesce(last_outcome, 'not tried') || ')' else 'FIRING, told ' || to_char(notified_at, 'YYYY-MM-DD HH24:MI') end from ops_alert order by key" \
    || say "the watch's state could not be read"
}

restore() {
  use_current_or_head
  say "stopping everything that uses the database"
  compose stop cloudflared proxy api worker backup db >/dev/null 2>&1 || true
  compose run --rm --no-deps restore restore "$@"
  say "restored. next: single-host.sh up   (the database replays the archive when it starts)"
}

pitr() {
  use_current_or_head
  say "starting a copy of the database${1:+ as it was at $1}; the live database is not touched"
  DEPLOY_PITR_TARGET="${1:-}" compose --profile scratch up --detach --wait --wait-timeout 1800 db-scratch
  say "the copy is up. To look at it:"
  say "  docker compose -p $PROJECT exec db-scratch psql -U postgres -d qarz"
  say "when done: single-host.sh pitr-down"
}

pitr_down() {
  use_current
  compose --profile scratch rm --stop --force db-scratch
  # The copy's own volume and nothing else; the name is spelled out so no other volume can be meant.
  docker volume rm "${PROJECT}_pgscratch" >/dev/null
  say "removed: the copy and its volume ${PROJECT}_pgscratch"
}

case "${1:-}" in
  env-init) env_init "${2:-}" ;;
  up) up "${2:-}" ;;
  start) use_current; compose up --detach; compose ps --format 'table {{.Service}}\t{{.Status}}' ;;
  stop) use_current; compose stop ;;
  status) status ;;
  backup)
    case "${2:-}" in full | diff) ;; *) die "usage: single-host.sh backup <full|diff>" ;; esac
    use_current; dce exec -T backup /opt/qarz-single/job.sh "$2" ;;
  restore-test) use_current; dce exec -T backup /opt/qarz-single/job.sh restore-test ;;
  alert-test) use_current; dce exec -T worker python -m qarz.interface.alert_test ;;
  catalog-import)
    { [ $# -eq 2 ] && [ -d "$2" ]; } || die "usage: single-host.sh catalog-import <directory>"
    # A one-off container of the API's image, with the API's settings and its volume of files. The seed
    # is mounted read-only: the photos are many times the 64 MB the API's own /tmp holds.
    use_current
    seed="$(native_path "$(cd "$2" && pwd)")"
    dce run --rm --no-deps --volume "$seed:/seed:ro" api python -m qarz.interface.import_shared_catalog /seed ;;
  territories-import)
    { [ $# -eq 2 ] && [ -d "$2" ]; } || die "usage: single-host.sh territories-import <directory>"
    # A one-off container of the API's image, with the API's settings. The seed is mounted read-only; it
    # holds places and no person, and nothing of it is copied anywhere but the database.
    use_current
    seed="$(native_path "$(cd "$2" && pwd)")"
    dce run --rm --no-deps --volume "$seed:/seed:ro" api python -m qarz.interface.import_territories /seed ;;
  admin-key)
    use_current
    case "${2:-}" in
      create)
        [ $# -eq 4 ] || die "usage: single-host.sh admin-key create <telegram-id> <label>"
        # The key is shown by nothing: it goes from the command straight into a file only this user
        # reads, and the file is never written over.
        out="$(dirname "$DEPLOY_ENV_FILE")/admin-key.$4"
        [ ! -e "$out" ] || die "$out exists: revoke that key and remove the file, or choose another label"
        key="$(dce exec -T api python -m qarz.interface.admin_sign_in key-create "$3" "$4" | head -n 1)"
        case "$key" in qdk_*) ;; *) die "no key was made" ;; esac
        (umask 077 && printf '%s\n' "$key" > "$out")
        unset key
        echo "key '$4' written to $out; it is kept nowhere else. Send it as:  Authorization: Bearer <key>" ;;
      list) dce exec -T api python -m qarz.interface.admin_sign_in key-list ;;
      revoke)
        [ $# -eq 3 ] || die "usage: single-host.sh admin-key revoke <label>"
        dce exec -T api python -m qarz.interface.admin_sign_in key-revoke "$3" ;;
      *) die "usage: single-host.sh admin-key create <telegram-id> <label> | list | revoke <label>" ;;
    esac ;;
  admin-password)
    [ $# -eq 3 ] || die "usage: single-host.sh admin-password <telegram-id> <login>"
    # Asked for on this terminal, twice; it is never an argument and nothing here prints it.
    [ -t 0 ] || die "run this in a terminal: the password is typed, not passed"
    use_current
    dce exec api python -m qarz.interface.admin_sign_in password-set "$2" "$3" ;;
  restore) shift; restore "$@" ;;
  restore-files) use_current_or_head; compose run --rm --no-deps restore-files ;;
  pitr) pitr "${2:-}" ;;
  pitr-down) pitr_down ;;
  rollback)
    [ $# -eq 2 ] || die "usage: single-host.sh rollback <previous-ref>"
    use_current; bash "$SCRIPTS_DIR/rollback.sh" "$2" ;;
  smoke)
    require_env_file
    [ -n "$(env_value DEPLOY_PUBLIC_HOST)" ] || die "DEPLOY_PUBLIC_HOST has no value in $ENV_FILE"
    bash "$SCRIPTS_DIR/smoke.sh" "https://$(env_value DEPLOY_PUBLIC_HOST)" ;;
  logs) shift; use_current; compose logs --no-color --tail 80 "$@" ;;
  *) die "usage: single-host.sh env-init [<file>] | up [<git-ref>] | start | stop | status | alert-test | catalog-import <directory> | territories-import <directory> | backup <full|diff> | restore-test | restore [--time '<moment>'] | restore-files | pitr ['<moment>'] | pitr-down | rollback <git-ref> | smoke | logs [<service>...]" ;;
esac
