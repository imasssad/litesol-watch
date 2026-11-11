#!/usr/bin/env bash
# Remote deploy script, run on the SERVER by CI over SSH.
#
# This is pinned as a forced command in ~/.ssh/authorized_keys, so the CI key
# can ONLY run this - it cannot open a shell, forward ports, or run anything
# else, even though it is stored in GitHub. See deploy/README.md.
#
# It takes no arguments from the client on purpose: $SSH_ORIGINAL_COMMAND is
# logged but never executed, so a stolen key cannot turn it into a shell.
#
# It deliberately does NOT use sudo, so the forced command needs no TTY and no
# privilege. That means data/ must already be owned by the container uid -
# bootstrap.sh does this on the first interactive deploy.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

log "requested by: ${SSH_ORIGINAL_COMMAND:-<none>}"
log "repo: $REPO_DIR"

# --- preconditions --------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is not installed on this server." >&2
    exit 1
fi
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose is not available." >&2
    exit 1
fi
if [ ! -f backend/.env ]; then
    echo "ERROR: backend/.env is missing. Run deploy/bootstrap.sh once by hand first." >&2
    exit 1
fi

PREVIOUS="$(git rev-parse HEAD)"
log "current commit: $PREVIOUS"

# --- fetch and fast-forward to origin/main --------------------------------
# reset --hard, not pull: the server is a deploy target, not a workspace, and a
# local edit must never be able to block or silently alter a deploy. Untracked
# files survive, so backend/.env and data/ are untouched.
log "fetching origin/main"
git fetch --prune origin main
git reset --hard origin/main
TARGET="$(git rev-parse HEAD)"
log "deploying commit: $TARGET"

if [ "$PREVIOUS" = "$TARGET" ]; then
    log "already at this commit; rebuilding anyway to pick up any image changes"
fi

# --- build and start ------------------------------------------------------
log "building and starting"
$COMPOSE up -d --build

# --- health gate ----------------------------------------------------------
log "waiting for the API"
for _ in $(seq 1 45); do
    if curl -sf -o /dev/null http://127.0.0.1:8000/api/ping; then
        log "UP"
        # /api/status needs the token now; /api/ping is the liveness probe.
        DEPLOY_TOKEN="$(grep '^API_AUTH_TOKEN=' backend/.env | head -1 | cut -d= -f2-)"
        curl -s -H "X-API-Token: ${DEPLOY_TOKEN}" http://127.0.0.1:8000/api/status
        echo
        log "deploy succeeded: $TARGET"
        exit 0
    fi
    sleep 2
done

echo "ERROR: the API did not come up after deploying $TARGET." >&2
$COMPOSE logs --tail 60 >&2

# Roll back to the commit that was running, so a bad deploy does not leave the
# bot down. Logs above keep the evidence of why it failed.
if [ "$PREVIOUS" != "$TARGET" ]; then
    echo "Rolling back to $PREVIOUS" >&2
    git reset --hard "$PREVIOUS"
    $COMPOSE up -d --build
    for _ in $(seq 1 45); do
        if curl -sf -o /dev/null http://127.0.0.1:8000/api/ping; then
            echo "Rolled back to $PREVIOUS and the API is up." >&2
            exit 1
        fi
        sleep 2
    done
    echo "ERROR: rollback also failed to come up. Manual intervention needed." >&2
fi
exit 1
