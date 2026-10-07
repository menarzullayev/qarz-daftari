#!/usr/bin/env bash
# Shared by deploy.sh, rollback.sh and local.sh. Sourced, never run.
# Nothing here prints a value from the env file.

SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRODUCTION_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"
REPO_DIR="$(cd "$PRODUCTION_DIR/../.." && pwd)"

ENV_FILE="${DEPLOY_ENV_FILE:-/etc/qarz/production.env}"
PROJECT="${DEPLOY_PROJECT:-qarz}"
STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/qarz/deploy}"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# A path the Docker client understands: unchanged on Linux, C:/... under Git Bash on Windows.
native_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

require_env_file() {
  [ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE (set DEPLOY_ENV_FILE; see deploy/production/.env.example)"
}

# One value from the env file, for the few names the scripts themselves need (never a secret).
# The shell's environment wins over the file, as it does for compose.
env_value() {
  local name="$1" fallback="${2:-}" value
  if [ -n "${!name:-}" ]; then printf '%s' "${!name}"; return; fi
  value="$(grep -E "^${name}=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '\r' || true)"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "${value:-$fallback}"
}

# docker compose for this deployment at release $RELEASE (a full commit hash).
compose() {
  local files=(-f "$(native_path "$PRODUCTION_DIR/compose.yml")")
  if [ -n "${DEPLOY_COMPOSE_OVERLAY:-}" ]; then files+=(-f "$(native_path "$DEPLOY_COMPOSE_OVERLAY")"); fi
  if [ -n "${DEPLOY_COMPOSE_OVERLAY_EXTRA:-}" ]; then files+=(-f "$(native_path "$DEPLOY_COMPOSE_OVERLAY_EXTRA")"); fi
  DEPLOY_RELEASE="$RELEASE" docker compose --project-name "$PROJECT" \
    --env-file "$(native_path "$ENV_FILE")" "${files[@]}" "$@"
}

# The full hash of a branch, tag or commit of this repository.
resolve_commit() {
  git -C "$REPO_DIR" rev-parse --verify --quiet "$1^{commit}" || die "not a commit of this repository: $1"
}

backend_image() { printf '%s:%s' "$(env_value DEPLOY_BACKEND_IMAGE qarz-daftari/backend)" "$1"; }
proxy_image() { printf '%s:%s' "$(env_value DEPLOY_PROXY_IMAGE qarz-daftari/proxy)" "$1"; }

have_image() { docker image inspect "$1" >/dev/null 2>&1; }

# Makes both images of a commit available on this host: already here, pulled (DEPLOY_PULL=1), or built
# from exactly that commit. The build never reads the working tree, so what runs is what was committed.
ensure_images() {
  local commit="$1" backend proxy source
  backend="$(backend_image "$commit")"; proxy="$(proxy_image "$commit")"
  if have_image "$backend" && have_image "$proxy"; then
    say "images: already on this host"
    return
  fi
  if [ "$(env_value DEPLOY_PULL)" = "1" ]; then
    say "images: pulling"
    docker pull --quiet "$backend"
    docker pull --quiet "$proxy"
    return
  fi
  say "images: building from commit $commit"
  source="$(mktemp -d)"
  (
    trap 'rm -rf "$source"' EXIT
    git -C "$REPO_DIR" archive --format=tar "$commit" | tar -x -C "$source"
    [ -f "$source/backend/Dockerfile" ] || die "commit $commit has no backend/Dockerfile: it predates these deployment files"
    docker build --quiet --label "org.opencontainers.image.revision=$commit" \
      -t "$backend" "$(native_path "$source/backend")"
    docker build --quiet --label "org.opencontainers.image.revision=$commit" \
      --build-arg "VITE_BOT_USERNAME=$(env_value VITE_BOT_USERNAME)" \
      -f "$(native_path "$source/deploy/production/nginx/Dockerfile")" \
      -t "$proxy" "$(native_path "$source")"
  )
}

# The images of a commit must already be here: a roll back builds nothing new unless told to.
require_images() {
  local commit="$1"
  have_image "$(backend_image "$commit")" || die "no backend image for $commit on this host"
  have_image "$(proxy_image "$commit")" || die "no proxy image for $commit on this host"
}

current_release() { cat "$STATE_DIR/current" 2>/dev/null || true; }

record_release() {
  local commit="$1" previous
  mkdir -p "$STATE_DIR"
  previous="$(current_release)"
  if [ -n "$previous" ] && [ "$previous" != "$commit" ]; then printf '%s\n' "$previous" > "$STATE_DIR/previous"; fi
  printf '%s\n' "$commit" > "$STATE_DIR/current"
  printf '%s %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" "$commit" >> "$STATE_DIR/history"
}

# The worker has no health check (it listens on nothing), and `up --wait` refuses such a service on some
# Compose versions. So: start it, and make sure it is still the same running container a moment later.
start_worker() {
  local id state
  compose up --detach --no-deps worker
  id="$(compose ps --quiet worker)"
  sleep 8
  state="$(docker inspect --format '{{.State.Status}} {{.RestartCount}}' "$id" 2>/dev/null || true)"
  [ "$state" = "running 0" ] || die "the worker did not stay up ($state); see: docker compose -p $PROJECT logs worker"
}

# Worker first, then the API, then the proxy (it carries the static files of the release).
# --no-deps: the migration is run by deploy.sh on purpose and by rollback.sh never.
restart_services() {
  say "worker: starting"
  start_worker
  say "api: starting"
  compose up --detach --no-deps --wait --wait-timeout 120 api
  say "proxy: starting"
  compose up --detach --no-deps --wait --wait-timeout 60 proxy
}

# /healthz asked the way a caller's request travels inside: from the proxy's container to the API.
wait_for_healthz() {
  local attempt answer
  for attempt in $(seq 1 30); do
    answer="$(compose exec -T proxy wget -q -T 3 -O - http://api:8000/healthz 2>/dev/null || true)"
    case "$answer" in
      *'"ok"'*) say "healthz: $answer"; return 0 ;;
    esac
    sleep 2
  done
  die "/healthz did not answer ok within 60 s (attempts: $attempt); see: docker compose -p $PROJECT logs api"
}

print_deployed() {
  say "--- deployed ---"
  say "release:  $RELEASE"
  say "subject:  $(git -C "$REPO_DIR" log -1 --format=%s "$RELEASE")"
  say "previous: $(cat "$STATE_DIR/previous" 2>/dev/null || echo none)"
  compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'
}
