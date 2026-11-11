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

# DRY_RUN exercises every prompt and decision without touching docker, the
# network or this host. CI runs it both ways so a logic or ordering bug - like
# reading a variable before it is assigned, which `bash -n` cannot catch -
# fails on the pull request instead of on a server.
DRY_RUN="${DRY_RUN:-}"

[ -f backend/.env ] || { echo "ERROR: backend/.env not found. Run deploy/bootstrap.sh first." >&2; exit 1; }

if [ -n "$DRY_RUN" ]; then
    COMPOSE="echo [dry-run] compose"
elif docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    echo "ERROR: docker compose is not available." >&2; exit 1
fi

get_env() { [ -f "$CADDY_ENV" ] && grep "^$1=" "$CADDY_ENV" 2>/dev/null | head -1 | cut -d= -f2- || true; }

# --- hostname -------------------------------------------------------------
echo "==> Determining this server's public IP..."
if [ -n "$DRY_RUN" ]; then
    IP="${FAKE_PUBLIC_IP:-203.0.113.10}"
else
    IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null \
       || curl -4 -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true)"
fi
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

# --- resolve the hostname -------------------------------------------------
# Must happen before the TLS decision below, which depends on whether this
# name points straight at us or at something in front of us.
echo "==> Checking DNS for $HOST..."
RESOLVED="$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}' | head -1 || true)"
if [ -z "$RESOLVED" ]; then
    echo "    WARNING: $HOST does not resolve yet." >&2
elif [ "$RESOLVED" = "$IP" ]; then
    echo "    resolves straight to this server ($RESOLVED)"
else
    echo "    resolves to $RESOLVED, which is NOT this server ($IP)"
    echo "    -> something (a CDN/proxy such as Cloudflare) sits in front."
fi

# --- ACME email -----------------------------------------------------------
# Must be non-empty: the Caddyfile's `email` directive takes an argument, and a
# blank one expands to `email` alone, which fails to parse.
EMAIL="$(get_env ACME_EMAIL)"
while [ -z "${EMAIL// }" ] || [[ "$EMAIL" != *@*.* ]]; do
    [ -n "$EMAIL" ] && echo "    '$EMAIL' does not look like an email address." >&2
    read -r -p "Email for certificate expiry notices (required): " EMAIL
done

# --- TLS mode -------------------------------------------------------------
# A public ACME certificate requires the CA to reach THIS server directly. When
# a CDN proxies the hostname that is impossible without changing the CDN, so we
# serve Caddy's own certificate instead: the CDN terminates TLS for visitors and
# only has to accept this origin, which its "Full" mode does.
if [ -z "$RESOLVED" ]; then
    # Not the same as "no proxy": we simply do not know. Public issuance is
    # the right default, but say plainly that it will fail until DNS exists.
    DEFAULT_TLS="$EMAIL"
    echo
    echo "==> $HOST does not resolve, so a proxy cannot be detected."
    echo "    Defaulting to a public certificate (TLS_ARG=<email>), which will"
    echo "    fail until DNS points at this server. Add the record, then re-run."
elif [ "$RESOLVED" != "$IP" ]; then
    DEFAULT_TLS="internal"
    echo
    echo "==> A proxy/CDN sits in front of $HOST."
    echo "    Using Caddy's own certificate for the origin leg (TLS_ARG=internal)."
    echo "    Visitors still get the CDN's valid public certificate."
    echo "    If the CDN is set to 'Full (strict)' it will reject this and you"
    echo "    will need either CDN access or an origin certificate from it."
else
    DEFAULT_TLS="$EMAIL"
    echo
    echo "==> $HOST points straight at this server; Caddy will obtain a"
    echo "    public certificate."
fi

EXISTING_TLS="$(get_env TLS_ARG)"
TLS_ARG="${EXISTING_TLS:-$DEFAULT_TLS}"
if [ -n "$EXISTING_TLS" ] && [ "$EXISTING_TLS" != "$DEFAULT_TLS" ]; then
    read -r -p "TLS_ARG is '$EXISTING_TLS'; use '$DEFAULT_TLS' instead? [y/N] " swap
    [[ "$swap" =~ ^[Yy] ]] && TLS_ARG="$DEFAULT_TLS"
fi
echo "    TLS_ARG=$TLS_ARG"

# --- sanity-check every value before writing ------------------------------
for pair in "PUBLIC_HOSTNAME:$HOST" "ACME_EMAIL:$EMAIL" "TLS_ARG:$TLS_ARG"; do
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
# An email address here means Caddy obtains a public ACME certificate.
# The word "internal" means it uses its own CA - correct when a CDN
# terminates TLS for visitors and only this origin leg must satisfy it.
TLS_ARG=${TLS_ARG}
EOF
chmod 600 "$CADDY_ENV"
echo "==> Wrote $CADDY_ENV"

# --- preflight ------------------------------------------------------------
if [ -n "$DRY_RUN" ]; then
    echo
    echo "==> DRY RUN complete. $CADDY_ENV would contain:"
    sed 's/^/    /' "$CADDY_ENV"
    exit 0
fi

echo "==> Validating the Caddy config..."
if ! docker run --rm --env-file "$CADDY_ENV" \
        -v "$REPO_DIR/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
        "$CADDY_IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    echo "ERROR: the Caddyfile is not valid. Nothing was started." >&2
    exit 1
fi

echo "==> Checking ports 80 and 443..."
# Our own caddy container holds them on every re-run; that is not a conflict,
# compose simply recreates it. Only a foreign listener is a problem.
OURS="$($COMPOSE ps -q caddy 2>/dev/null || true)"
OURS_RUNNING=no
if [ -n "$OURS" ] && [ "$(docker inspect -f '{{.State.Running}}' "$OURS" 2>/dev/null)" = "true" ]; then
    OURS_RUNNING=yes
fi

if [ "$OURS_RUNNING" = "yes" ]; then
    echo "    held by this stack's own caddy container; it will be recreated."
else
    for port in 80 443; do
        if ss -lntH "sport = :$port" 2>/dev/null | grep -q .; then
            echo "ERROR: another process already listens on port $port:" >&2
            sudo ss -lntpH "sport = :$port" 2>/dev/null >&2 || ss -lntH "sport = :$port" >&2 || true
            echo >&2
            echo "Caddy needs both 80 and 443. Stop that service and re-run." >&2
            echo "If it is a stray container:  docker ps --filter publish=$port" >&2
            exit 1
        fi
    done
    echo "    both free."
fi

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    echo "==> Opening 80/443 in ufw..."
    sudo ufw allow 80/tcp  >/dev/null 2>&1 || true
    sudo ufw allow 443/tcp >/dev/null 2>&1 || true
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

    Open it and paste API_AUTH_TOKEN into the unlock screen. Every API
    endpoint requires that token; only the page itself is public.

    Read the token with:
      grep '^API_AUTH_TOKEN=' backend/.env

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
