#!/usr/bin/env bash
# Prepare this server for auto-deploy from GitHub Actions.
#
# Run ON THE SERVER:
#   bash deploy/setup-cicd.sh
#
# It generates a dedicated deploy key, installs the public half pinned to a
# forced command so that key can ONLY trigger a deploy, captures this host's
# SSH host key, and prints exactly what to paste into GitHub.
#
# It changes nothing about the running bot and needs no GitHub access.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

KEY=~/.ssh/litesol_deploy
DEPLOY_SCRIPT="$REPO_DIR/deploy/pull-deploy.sh"

[ -x "$DEPLOY_SCRIPT" ] || chmod +x "$DEPLOY_SCRIPT"
[ -f backend/.env ] || { echo "ERROR: backend/.env missing. Run deploy/bootstrap.sh first." >&2; exit 1; }

# --- SSH port -------------------------------------------------------------
DETECTED_PORT="$(grep -oP '^\s*Port\s+\K[0-9]+' /etc/ssh/sshd_config 2>/dev/null | head -1 || true)"
DETECTED_PORT="${DETECTED_PORT:-${SSH_CONNECTION##* }}"
[[ "$DETECTED_PORT" =~ ^[0-9]+$ ]] || DETECTED_PORT=22
read -r -p "SSH port of this server [$DETECTED_PORT]: " PORT
PORT="${PORT:-$DETECTED_PORT}"

PUBLIC_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
if [ -z "$PUBLIC_IP" ]; then
    read -r -p "Public IP or hostname of this server: " PUBLIC_IP
fi

# --- key ------------------------------------------------------------------
mkdir -p ~/.ssh && chmod 700 ~/.ssh
if [ -f "$KEY" ]; then
    echo "==> Reusing the existing deploy key at $KEY"
else
    echo "==> Generating a dedicated deploy key (separate from your login key)"
    ssh-keygen -t ed25519 -f "$KEY" -N "" -C "github-actions-deploy" >/dev/null
fi

# --- forced command -------------------------------------------------------
# The key can run this one script and nothing else: no shell, no port
# forwarding, no reading backend/.env. Whatever CI sends as a command is
# ignored.
PUBKEY="$(cat "$KEY.pub")"
LINE="command=\"$DEPLOY_SCRIPT\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty $PUBKEY"

touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys

# Identify the key by its base64 material (field 2), NOT by the comment.
# An empty marker would make `grep -vF ""` match every line and erase the
# file - taking your login key with it and locking you out of SSH.
KEY_BLOB="$(awk '{print $2}' <<<"$PUBKEY")"
if [ -z "$KEY_BLOB" ] || [ ${#KEY_BLOB} -lt 40 ]; then
    echo "ERROR: could not read the public key material from $KEY.pub." >&2
    echo "Refusing to touch authorized_keys." >&2
    exit 1
fi

# Always back up before rewriting the file that controls SSH access.
BACKUP=~/.ssh/authorized_keys.bak.$(date +%Y%m%d%H%M%S)
cp ~/.ssh/authorized_keys "$BACKUP"
chmod 600 "$BACKUP"
echo "==> Backed up authorized_keys to $BACKUP"

if grep -qF -- "$KEY_BLOB" ~/.ssh/authorized_keys 2>/dev/null; then
    echo "==> Deploy key already present; refreshing its restrictions"
    grep -vF -- "$KEY_BLOB" ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.tmp || true
    mv ~/.ssh/authorized_keys.tmp ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
fi
printf '%s\n' "$LINE" >> ~/.ssh/authorized_keys

# Never leave the file with fewer keys than it had: that would mean a login
# key was dropped.
BEFORE="$(grep -c 'ssh-' "$BACKUP" 2>/dev/null || echo 0)"
AFTER="$(grep -c 'ssh-' ~/.ssh/authorized_keys 2>/dev/null || echo 0)"
if [ "$AFTER" -lt "$BEFORE" ]; then
    echo "ERROR: authorized_keys lost entries ($BEFORE -> $AFTER). Restoring." >&2
    cp "$BACKUP" ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
    exit 1
fi
echo "==> Installed, pinned to: $DEPLOY_SCRIPT"

# --- host key -------------------------------------------------------------
echo "==> Capturing this host's SSH host key"
KNOWN_HOSTS="$(ssh-keyscan -p "$PORT" "$PUBLIC_IP" 2>/dev/null | grep -v '^#' || true)"
if [ -z "$KNOWN_HOSTS" ]; then
    echo "WARNING: ssh-keyscan returned nothing. Run it from your own machine:" >&2
    echo "    ssh-keyscan -p $PORT $PUBLIC_IP" >&2
fi

# --- output ---------------------------------------------------------------
cat <<EOF

================================================================
 GitHub -> Settings -> Secrets and variables -> Actions
================================================================

Variables tab, "New repository variable":

  DEPLOY_ENABLED = true

Secrets tab, "New repository secret" (one per row):

  DEPLOY_HOST = $PUBLIC_IP
  DEPLOY_PORT = $PORT
  DEPLOY_USER = $USER

  DEPLOY_KNOWN_HOSTS =
$(printf '%s\n' "$KNOWN_HOSTS" | sed 's/^/    /')

----------------------------------------------------------------
 DEPLOY_SSH_KEY  (private key - handle carefully)
----------------------------------------------------------------
 Paste this ONLY into the GitHub secret field. Do not paste it
 into a chat, an issue, or anywhere else. Anyone holding it can
 trigger a deploy on this server.

 It cannot open a shell: it is pinned to a forced command.
 To revoke, delete its line from ~/.ssh/authorized_keys.
----------------------------------------------------------------

$(cat "$KEY")

================================================================

Once all five are set, every merge to main deploys automatically
after CI passes. Test it now without waiting for a merge:
GitHub -> Actions -> Deploy -> "Run workflow".

Verify the key is restricted (this should deploy, never give a shell):

  ssh -i $KEY -p $PORT $USER@$PUBLIC_IP "echo this is ignored"

EOF
