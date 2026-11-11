#!/usr/bin/env bash
# Bootstrap / update the arb bot on a Linux server, in PAPER MODE.
#
# Run ON THE SERVER from the repo root's deploy/ dir (or via ssh):
#   bash deploy/bootstrap.sh
#
# Safe to re-run: it never overwrites an existing backend/.env, and
# `docker compose up -d --build` only rebuilds what changed.
#
# It will NOT enable live trading. LIVE_TRADING / EXECUTION_ENABLED stay
# false unless you edit backend/.env yourself.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

echo "==> Deploying from: $REPO_DIR"

# --- docker ------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
    DOCKER=docker
else
    echo "ERROR: docker is not installed." >&2
    echo "Install it first (Debian/Ubuntu):" >&2
    echo "  curl -fsSL https://get.docker.com | sudo sh" >&2
    echo "  sudo usermod -aG docker \$USER   # then log out and back in" >&2
    exit 1
fi

if $DOCKER compose version >/dev/null 2>&1; then
    COMPOSE="$DOCKER compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose plugin not found (apt install docker-compose-plugin)." >&2
    exit 1
fi

if ! $DOCKER info >/dev/null 2>&1; then
    echo "ERROR: cannot talk to the docker daemon." >&2
    echo "Either run this script with sudo, or add your user to the docker group:" >&2
    echo "  sudo usermod -aG docker \$USER   # then log out and back in" >&2
    exit 1
fi

# --- backend/.env -------------------------------------------------------
if [ -f backend/.env ]; then
    echo "==> backend/.env exists - keeping it (not overwritten)."
else
    echo "==> Creating backend/.env (paper mode, generated API token)."
    TOKEN="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    umask 077
    cat > backend/.env <<EOF
APP_NAME=Arb Bot Production v1
ENVIRONMENT=server

# Control-endpoint auth. Generated at deploy time; keep secret.
API_AUTH_TOKEN=${TOKEN}

# PAPER MODE. Do not flip these until you have read README.md, AUDIT.md and
# REPORT.md - several items are marked NEEDS LIVE VALIDATION.
LIVE_TRADING=false
EXECUTION_ENABLED=false

PAIR=SOL/USDC
BASE_ASSET=SOL
QUOTE_ASSET=USDC
CEX_VENUE=binance
DEX_VENUE=jupiter

# Keyless Jupiter tier is ~60 req/min; 3s scan keeps the two quote calls
# per scan under it. Get a key at portal.jup.ag to go faster.
SCAN_INTERVAL_SECONDS=3
JUPITER_QUOTE_URL=https://lite-api.jup.ag/swap/v1/quote
JUPITER_SWAP_URL=https://lite-api.jup.ag/swap/v1/swap
JUPITER_API_KEY=

DATABASE_URL=sqlite+aiosqlite:///./data/arb_bot.db
EOF
    chmod 600 backend/.env
fi

# The container runs as the non-root arbbot user (uid 10001). A bind-mounted
# host directory keeps its HOST ownership inside the container, which overrides
# the Dockerfile's chown entirely - so data/ must be writable by that uid or
# SQLite fails at startup with "unable to open database file".
CONTAINER_UID=10001
mkdir -p data
CURRENT_UID="$(stat -c %u data)"
if [ "$CURRENT_UID" != "$CONTAINER_UID" ]; then
    echo "==> data/ is owned by uid $CURRENT_UID but the container runs as $CONTAINER_UID."
    echo "    Fixing ownership (may prompt for your sudo password)..."
    if sudo chown -R "$CONTAINER_UID:$CONTAINER_UID" data; then
        echo "    data/ now owned by uid $CONTAINER_UID."
    else
        echo "ERROR: could not change ownership of data/." >&2
        echo "Run this, then re-run this script:" >&2
        echo "  sudo chown -R $CONTAINER_UID:$CONTAINER_UID $REPO_DIR/data" >&2
        exit 1
    fi
fi


# --- build + run ----------------------------------------------------------
echo "==> Building and starting (docker compose up -d --build)..."
$COMPOSE up -d --build

echo "==> Waiting for the API..."
ok=""
for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/api/ping >/dev/null 2>&1; then ok=1; break; fi
    sleep 2
done

if [ -z "$ok" ]; then
    echo "ERROR: API did not come up. Logs:" >&2
    $COMPOSE logs --tail 50
    exit 1
fi

echo
echo "==> UP. Status:"
# /api/status needs the token now, so read it back from the env file.
BOOT_TOKEN="$(grep '^API_AUTH_TOKEN=' backend/.env | head -1 | cut -d= -f2-)"
curl -s -H "X-API-Token: ${BOOT_TOKEN}" http://127.0.0.1:8000/api/status; echo
echo
echo "The API listens on 127.0.0.1 ONLY (never exposed publicly by design)."
echo "Open the dashboard from your machine through an SSH tunnel:"
echo "    ssh -L 8000:127.0.0.1:8000 $(whoami)@<server-ip>"
echo "then browse http://localhost:8000"
echo
echo "Your API token is in backend/.env on this server (API_AUTH_TOKEN)."
echo "Logs:   $COMPOSE logs -f"
echo "Stop:   $COMPOSE down"
echo "Update: re-sync the repo and re-run this script."
