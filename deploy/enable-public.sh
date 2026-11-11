#!/usr/bin/env bash
# Publish the dashboard on the public internet, behind HTTPS and basic auth.
#
# Run ON THE SERVER:
#   bash deploy/enable-public.sh
#
# It derives an sslip.io hostname from the server's public IP (so a certificate
# can be issued without you owning a domain), asks for a password, writes
# deploy/caddy.env, validates the Caddy config, and starts the proxy.
#
# Safe to re-run: existing values are kept unless you choose to replace them,
# and it never touches backend/.env or data/.
#
# The bot itself stays bound to 127.0.0.1. Caddy reaches it over the internal
# compose network, so the public route is always through TLS + basic auth.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

CADDY_ENV=deploy/caddy.env
CADDY_IMAGE=caddy:2.8-alpine

[ -f backend/.env ] || { echo "ERROR: backend/.env not found. Run deploy/bootstrap.sh first." >&2; exit 1; }

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose is not available." >&2; exit 1
fi

get_env() { [ -f "$CADDY_ENV" ] && grep "^$1=" "$CADDY_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true; }

# --- hostname -------------------------------------------------------------
echo "==> Determining this server's public IP..."
IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null \
   || curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true)"
if [ -z "$IP" ]; then
    read -r -p "Could not detect it. Enter the server's public IPv4: " IP
fi
echo "    public IP: $IP"
DEFAULT_HOST="bot.${IP//./-}.sslip.io"

HOST="$(get_env PUBLIC_HOSTNAME)"
if [ -n "$HOST" ]; then
    read -r -p "PUBLIC_HOSTNAME is already '$HOST'. Keep it? [Y/n] " keep
    [[ "$keep" =~ ^[Nn] ]] && HOST=""
fi
if [ -z "$HOST" ]; then
    echo
    echo "    Default uses sslip.io, which resolves any IP-shaped hostname to that IP,"
    echo "    so a certificate can be issued without owning a domain."
    echo "    If you own a domain, point an A record at $IP and enter it instead."
    read -r -p "Public hostname [$DEFAULT_HOST]: " input
    HOST="${input:-$DEFAULT_HOST}"
fi

# --- ACME email -----------------------------------------------------------
# Must be non-empty: the Caddyfile's `email` directive takes an argument, and a
# blank one expands to `email` alone, which fails to parse.
EMAIL="$(get_env ACME_EMAIL)"
while [ -z "${EMAIL// }" ] || [[ "$EMAIL" != *@*.* ]]; do
    [ -n "$EMAIL" ] && echo "    '$EMAIL' does not look like an email address." >&2
    read -r -p "Email for certificate expiry notices (required): " EMAIL
done

# --- basic auth -----------------------------------------------------------
USER_NAME="$(get_env BASIC_AUTH_USER)"
HASH="$(get_env BASIC_AUTH_HASH)"
if [ -z "$USER_NAME" ] || [ -z "$HASH" ]; then
    read -r -p "Dashboard login username [admin]: " USER_NAME
    USER_NAME="${USER_NAME:-admin}"
    echo "Choose a password (not echoed). This is internet-facing, so make it strong."
    read -r -s -p "Password: " PASS; echo
    read -r -s -p "Confirm:  " PASS2; echo
    [ "$PASS" = "$PASS2" ] || { echo "ERROR: passwords do not match." >&2; exit 1; }
    [ ${#PASS} -ge 12 ] || { echo "ERROR: use at least 12 characters." >&2; exit 1; }
    echo "==> Hashing with bcrypt..."
    HASH="$(docker run --rm "$CADDY_IMAGE" caddy hash-password --plaintext "$PASS")"
    unset PASS PASS2
fi

# --- sanity-check every value before writing ------------------------------
for pair in "PUBLIC_HOSTNAME:$HOST" "ACME_EMAIL:$EMAIL" "BASIC_AUTH_USER:$USER_NAME" "BASIC_AUTH_HASH:$HASH"; do
    name="${pair%%:*}"; value="${pair#*:}"
    if [ -z "${value// }" ]; then
        echo "ERROR: $name is empty. Every setting is required." >&2
        exit 1
    fi
done

# --- write caddy.env (NOT backend/.env: the proxy gets no trading secrets) -
umask 077
cat > "$CADDY_ENV" <<EOF
# Public-facing settings for the Caddy reverse proxy. Git-ignored.
# Deliberately separate from backend/.env so the TLS proxy never holds the
# API token or exchange credentials.
PUBLIC_HOSTNAME=${HOST}
ACME_EMAIL=${EMAIL}
BASIC_AUTH_USER=${USER_NAME}
BASIC_AUTH_HASH=${HASH}
EOF
chmod 600 "$CADDY_ENV"
echo "==> Wrote $CADDY_ENV"

# --- preflight ------------------------------------------------------------
echo "==> Validating the Caddy config..."
if ! docker run --rm --env-file "$CADDY_ENV" \
        -v "$REPO_DIR/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
        "$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    echo "ERROR: the Caddyfile is not valid. Nothing was started." >&2
    exit 1
fi

echo "==> Checking ports 80 and 443 are free..."
for port in 80 443; do
    if ss -lntH "sport = :$port" 2>/dev/null | grep -q .; then
        echo "ERROR: something already listens on port $port:" >&2
        ss -lntpH "sport = :$port" 2>/dev/null >&2 || true
        echo "Caddy needs both. Stop that service and re-run." >&2
        exit 1
    fi
done

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    echo "==> Opening 80/443 in ufw..."
    sudo ufw allow 80/tcp  >/dev/null 2>&1 || true
    sudo ufw allow 443/tcp >/dev/null 2>&1 || true
fi

echo "==> Checking DNS for $HOST..."
RESOLVED="$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)"
if [ -z "$RESOLVED" ]; then
    echo "    WARNING: $HOST does not resolve. If you used your own domain, add an A record to $IP first." >&2
elif [ "$RESOLVED" != "$IP" ]; then
    echo "    WARNING: $HOST resolves to $RESOLVED, not $IP. Certificate issuance will fail." >&2
else
    echo "    resolves to $RESOLVED (correct)"
fi

# --- start ----------------------------------------------------------------
echo "==> Starting..."
$COMPOSE up -d --build

echo "==> Waiting for the certificate. This usually takes 10-60s."
STATUS=000
for _ in $(seq 1 36); do
    # No `|| echo 000`: curl already writes 000 via -w on failure, and the
    # fallback appended a second one, producing "000000" in the message.
    STATUS="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://${HOST}/" 2>/dev/null)"
    STATUS="${STATUS:-000}"
    # 401 means TLS completed and basic auth answered: that is success.
    case "$STATUS" in 200|401) break ;; esac
    sleep 5
done

echo
if [ "$STATUS" = "401" ] || [ "$STATUS" = "200" ]; then
    cat <<EOF
==> PUBLISHED

    https://${HOST}

    Browser login: ${USER_NAME} / the password you set
    Then paste API_AUTH_TOKEN into the dashboard to use the control buttons.

    The bot stays bound to 127.0.0.1; Caddy is the only public route in.
EOF
else
    cat <<EOF
==> NOT REACHABLE YET (HTTP ${STATUS})

    Watch issuance:  $COMPOSE logs -f caddy

    Common causes, most likely first:

    1. Certificate rate limit. All sslip.io hostnames share one Let's Encrypt
       budget, so issuance there can fail for reasons that have nothing to do
       with you. Caddy falls back to ZeroSSL automatically - give it a few
       minutes. If it keeps failing, use a real domain: point an A record at
       ${IP} and re-run this script with that hostname.
    2. Port 80 blocked by a provider firewall. The ACME challenge needs it
       open inbound, not just port 443.
    3. DNS not pointing at this server (see the warning above, if any).

    The dashboard is unaffected over the SSH tunnel while you sort this out.
EOF
    exit 1
fi
