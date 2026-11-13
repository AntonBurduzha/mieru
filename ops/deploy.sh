#!/usr/bin/env bash
# Deploys one commit of main to this server: its config (the checkout), its image,
# and a restart of the app containers, one at a time.
#
#   CI, through the key's forced command:  ssh -i <ci key> deploy@<ip> <sha>
#   By hand, as deploy:                    /srv/mieru/ops/deploy.sh <sha>
#   This branch's copy, before it's merged: ssh deploy@<ip> 'bash -s -- <sha>' < ops/deploy.sh
#
# Stage 1 checks the commit, pulls its image and moves the checkout. A failure there
# changes nothing. Then the target commit's own copy of this script runs stage 2, so a
# commit that changes the deploy steps is deployed by those steps.
# Stage 2 migrates, sets MIERU_TAG and restarts ingest-1, ingest-2 and api, each one
# ready before the next. A failure stops the deploy; the services not reached yet keep
# the old version. To roll back, deploy the previous commit (ops/README.md).
# Everything is inside main(), called on the last line, so bash has read the whole
# script before anything runs, even when the checkout replaces this file.
set -euo pipefail

readonly APP_DIR=/srv/mieru
# Restarted in this order. Each entry is <service>:<port of its /health/ready>
readonly APP_SERVICES=(ingest-1:8001 ingest-2:8001 api:8000)
# The pause after ingest-1 before ingest-2 stops. It must be longer than Caddy's
# fail_duration (5 s) plus one health_interval (2 s), so Caddy has ingest-1 back in
# the rotation (ops/caddy/Caddyfile)
readonly SETTLE_SECONDS=10
readonly READY_SECONDS=30
readonly FETCH_SECONDS=120
readonly PULL_SECONDS=600

SHA=

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }
usage() { printf 'usage: deploy.sh <full 40-character commit sha on main>\n' >&2; exit 2; }

compose() { docker compose -f compose.prod.yaml "$@"; }

# True when sshd runs this script as the CI key's forced command. sshd then puts what
# the client sent in SSH_ORIGINAL_COMMAND, and the key can't run anything else.
forced() { [[ -n ${SSH_ORIGINAL_COMMAND+set} ]]; }

parse_sha() {
  local arg
  if forced; then
    # The whole text the client sent must be the sha: no splitting, so a second
    # word or a second line fails the match below
    arg=$SSH_ORIGINAL_COMMAND
  else
    [[ $# -eq 1 ]] || usage
    arg=$1
  fi
  # In bash's =~, ^ and $ anchor to the whole string, not to a line
  [[ $arg =~ ^[0-9a-f]{40}$ ]] || usage
  SHA=$arg
}

# Stage 1: every failure here leaves the checkout and the containers as they were
sync_checkout() {
  # One deploy at a time. The lock lives as long as file descriptor 9, which
  # survives the exec into stage 2 and closes when the deploy ends
  exec 9>"$APP_DIR/.git/mieru-deploy.lock"
  flock -n 9 || die "another deploy is running"

  [[ -z $(git status --porcelain) ]] || die "the checkout in $APP_DIR has local changes"

  log "Checking $SHA"
  # The timeouts keep a stalled GitHub or GHCR from holding the lock after CI gives up.
  # 9>&- closes the lock for git: fetch can start a background gc that outlives it
  timeout "$FETCH_SECONDS" git fetch --quiet origin main 9>&- \
    || die "git fetch failed or took over ${FETCH_SECONDS}s"
  git merge-base --is-ancestor "$SHA" origin/main 2>/dev/null || die "$SHA is not a commit on main"
  # The forced command runs $APP_DIR/ops/deploy.sh: a commit without it would leave
  # the CI key unable to deploy anything afterwards
  if forced && ! git cat-file -e "$SHA:ops/deploy.sh" 2>/dev/null; then
    die "$SHA has no ops/deploy.sh: roll back to it by hand (ops/README.md)"
  fi

  log "Pulling the image"
  # One image serves every app service; a commit CI hasn't published fails here
  # timeout can't run a shell function, so this spells out compose()
  MIERU_TAG=$SHA timeout "$PULL_SECONDS" docker compose -f compose.prod.yaml pull --quiet api \
    || die "no image for $SHA within ${PULL_SECONDS}s: has CI published it?"

  log "Moving the checkout to $SHA"
  git checkout --quiet --detach "$SHA"

  if [[ -f $APP_DIR/ops/deploy.sh ]]; then
    MIERU_DEPLOY_STAGE=apply exec bash "$APP_DIR/ops/deploy.sh" "$SHA"
  fi
  # Only a commit from before T04 has no deploy.sh: carry on with this copy
  apply
}

migrate() {
  # T05 adds Alembic (Postgres) here and T06 the ClickHouse runner. A failed
  # migration must stop the deploy before any container restarts
  log "Migrations: none yet"
}

# Points MIERU_TAG in .env at the commit, so compose and later manual runs use its image
set_tag() {
  grep -q '^MIERU_TAG=' .env || die ".env has no MIERU_TAG= line"
  local tmp
  # mktemp creates the file with mode 600. Inside .git, so it never shows up in
  # git status, and on the same file system, so mv replaces .env in one step
  tmp=$(mktemp "$APP_DIR/.git/mieru-env.XXXXXX")
  awk -v tag="$SHA" '/^MIERU_TAG=/ { print "MIERU_TAG=" tag; next } { print }' .env >"$tmp" \
    || { rm -f "$tmp"; die "could not rewrite .env"; }
  mv "$tmp" .env
}

wait_ready() {
  local svc=$1 port=$2
  local deadline=$((SECONDS + READY_SECONDS))
  # From inside the container: the image has Python, and nothing else can reach the port
  until compose exec -T "$svc" python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/health/ready', timeout=2)" \
    >/dev/null 2>&1; do
    ((SECONDS < deadline)) || die "$svc is not ready after ${READY_SECONDS}s; see: docker compose -f compose.prod.yaml logs $svc"
    sleep 1
  done
}

restart() {
  local svc=$1 port=$2 image
  log "Restarting $svc"
  # --force-recreate: a redeploy of the same commit restarts too.
  # --no-deps: never touch the databases. --wait: until Docker's healthcheck passes
  compose up -d --no-deps --force-recreate --wait --wait-timeout 60 "$svc" \
    || die "$svc did not become healthy"
  image=$(compose ps --format '{{.Image}}' "$svc")
  [[ $image == *":$SHA" ]] || die "$svc runs $image, not the tag $SHA"
  wait_ready "$svc" "$port"
}

# Stage 2
apply() {
  [[ $(git rev-parse HEAD) == "$SHA" ]] || die "the checkout is not at $SHA"
  migrate
  set_tag

  local entry svc
  for entry in "${APP_SERVICES[@]}"; do
    svc=${entry%%:*}
    restart "$svc" "${entry##*:}"
    if [[ $svc == ingest-1 ]]; then
      log "Waiting ${SETTLE_SECONDS}s for Caddy to route to ingest-1 again"
      sleep "$SETTLE_SECONDS"
    fi
  done

  log "Reloading Caddy's config"
  # A no-op when the Caddyfile hasn't changed
  compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile

  log "Deployed $SHA"
}

main() {
  parse_sha "$@"
  cd "$APP_DIR"
  if [[ ${MIERU_DEPLOY_STAGE-} == apply ]]; then
    apply
  else
    sync_checkout
  fi
}

main "$@"
