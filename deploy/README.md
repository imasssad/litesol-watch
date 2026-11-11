# Deploying to a server

Deploys the backend + dashboard with docker compose, **in paper mode**. The
bootstrap script never enables live trading and never overwrites an existing
`backend/.env`.

Deployment path: **`/var/www/litesol`**.

## Prerequisites on the server

Ubuntu 24.04 ships with neither git nor docker. Install both:

```bash
sudo apt update && sudo apt install -y git docker.io docker-compose-v2
```

Then give your user access to the docker socket. The `exit` matters — group
membership only applies to a new login session:

```bash
sudo usermod -aG docker $USER && exit
```

Reconnect and confirm:

```bash
docker compose version && docker info >/dev/null && echo "docker OK"
```

## First deploy

Create the directory owned by your user, so compose and the SQLite volume do
not need root:

```bash
sudo mkdir -p /var/www && sudo install -d -o "$USER" -g "$USER" /var/www/litesol
```

Clone `main` into it. `-b main` is required while GitHub's default branch is
still the older `claude/...` one, which predates `deploy/`:

```bash
git clone -b main https://github.com/imasssad/litesol-watch.git /var/www/litesol
```

The repository is private, so the clone prompts for credentials: username
`imasssad`, password = a **fine-grained GitHub token** with *Contents:
Read-only* on `litesol-watch` (GitHub → Settings → Developer settings →
Fine-grained tokens). A GitHub account password will not work.

```bash
bash /var/www/litesol/deploy/bootstrap.sh
```

That builds the image, generates a random `API_AUTH_TOKEN` into
`backend/.env`, starts the stack in paper mode, waits for the API and prints
the status JSON.

## Updating

```bash
cd /var/www/litesol && git pull && bash deploy/bootstrap.sh
```

`backend/.env` and `data/` are git-ignored and never overwritten, so the
server keeps its token and trade history. Compose rebuilds only what changed.

## Accessing the dashboard

The API binds to `127.0.0.1` on the server **on purpose** — a trading bot's
control endpoints (kill switch included) must not face the internet, even
with token auth. Reach it through an SSH tunnel from your own machine:

```bash
ssh -p 2222 -L 8000:127.0.0.1:8000 dev2@<server-ip>
```

Leave that open and browse to <http://localhost:8000>. Paste the
`API_AUTH_TOKEN` from `/var/www/litesol/backend/.env` into the dashboard's
token field to use the controls.

(SSH on this host listens on **2222**, not 22. `scp` needs a capital
`-P 2222`.)

If you later want it on a hostname, put a TLS reverse proxy (caddy/nginx)
with its own auth in front — do not change the compose port binding to
`0.0.0.0`.

## The data directory and the container user

The container runs as the non-root `arbbot` user, **uid 10001**. `data/` is a
bind mount, and a bind-mounted host directory keeps its host ownership inside
the container — the Dockerfile's `chown` does not apply to it. So `data/` must
be writable by uid 10001, or the app dies at startup with:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) unable to open database file
```

`bootstrap.sh` handles this: it checks the owner of `data/` and chowns it to
10001 with sudo if needed. To do it by hand:

```bash
sudo chown -R 10001:10001 /var/www/litesol/data
```

Your own user can still read `data/` for backups, but not write to it. If you
change the uid in `backend/Dockerfile`, change `CONTAINER_UID` in
`deploy/bootstrap.sh` to match — a test enforces that they agree.

## Operations

```bash
cd /var/www/litesol
docker compose logs -f        # follow logs
docker compose ps             # container state
docker compose restart        # restart
docker compose down           # stop and remove
```

## Continuous deployment

`.github/workflows/deploy.yml` deploys to this server whenever a PR merges to
`main`. The full CI suite (lint, types, pytest, pip-audit, vitest, npm audit,
and a real-browser dashboard smoke test) runs first, and a failure blocks the
deploy. It is **inert until you opt in** — the deploy job is skipped unless the
repository variable `DEPLOY_ENABLED` is `true`.

### Understand the trade-off first

Auto-deploy means GitHub holds a key to a server that runs a trading bot and
holds exchange API keys. Anyone who can push to `main`, approve a workflow
change, or compromise an action in the pipeline can reach it. The setup below
limits the blast radius with a **forced command**, so the key can only run one
script — not open a shell, forward ports, or read `backend/.env`.

If you would rather GitHub held nothing at all, run `deploy/pull-deploy.sh`
from a cron job on the server instead and skip this section entirely. That is
pull-based: slower to react, but nothing inbound to steal.

### 1. Make a dedicated deploy key

On your own machine — not the server, and not reused from your login key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/litesol_deploy -N "" -C "github-actions-deploy"
```

### 2. Pin it to the deploy script on the server

Add the **public** half to `~/.ssh/authorized_keys` with a forced command, all
on one line:

```text
command="/var/www/litesol/deploy/pull-deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA...your-public-key... github-actions-deploy
```

The `command=` prefix is what matters: whatever CI sends is ignored and only
that script runs. `no-pty` blocks an interactive shell. Verify from your
machine — it should run a deploy and exit, never give you a prompt:

```bash
ssh -i ~/.ssh/litesol_deploy -p 2222 dev2@<server-ip> "echo this is ignored"
```

### 3. Capture the server's host key

```bash
ssh-keyscan -p 2222 <server-ip>
```

This pins the server's identity so the deploy cannot be redirected to another
host. Copy the whole output.

### 4. Add the repository settings

Settings → Secrets and variables → Actions:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `DEPLOY_ENABLED` | `true` |
| Secret | `DEPLOY_HOST` | the server IP |
| Secret | `DEPLOY_PORT` | `2222` |
| Secret | `DEPLOY_USER` | `dev2` |
| Secret | `DEPLOY_SSH_KEY` | contents of `~/.ssh/litesol_deploy` (the private half) |
| Secret | `DEPLOY_KNOWN_HOSTS` | the `ssh-keyscan` output |

Consider a `production` environment with required reviewers, so a merge waits
for a human before touching the server.

### What a deploy does

`deploy/pull-deploy.sh` fetches `origin/main`, `git reset --hard`s to it,
rebuilds and restarts the stack, then polls `/api/status`. If the API does not
come up it prints the container logs and **rolls back** to the previously
running commit, so a bad merge does not leave the bot down.

`backend/.env` and `data/` are untracked, so the reset never touches your token
or trade history. The script uses no `sudo`, which is why `data/` must already
be owned by the container uid — `bootstrap.sh` handles that on the first
interactive deploy.

Note: a deploy restarts the container. In paper mode that is harmless; if you
ever run live, be aware a merge will interrupt an in-flight scan.

## Publishing the dashboard on the internet

By default the bot binds to `127.0.0.1` and is reachable only through an SSH
tunnel. To publish it:

```bash
cd /var/www/litesol && bash deploy/enable-public.sh
```

The script asks for a hostname, an email, and a dashboard password, then
starts a Caddy reverse proxy in front of the bot.

### What it sets up

```text
internet ──HTTPS──> Caddy :443 ──basic auth──> arb-bot:8000 (internal network)
                                                     ▲
                        SSH tunnel ──> 127.0.0.1:8000 ┘
```

- **Automatic HTTPS.** Caddy obtains and renews a certificate with no manual
  steps. Certificates live in a named volume so a restart does not re-request
  them.
- **Basic auth on everything**, including the read-only panels. A port scanner
  finds a password prompt, not your trading dashboard. The password is stored
  only as a bcrypt hash.
- **The bot is never published.** It keeps its loopback binding, so Caddy is
  the only public route and the SSH tunnel keeps working unchanged.
- **Security headers**: HSTS, `nosniff`, `X-Frame-Options: DENY`, a strict CSP
  and `Referrer-Policy: no-referrer`.
- **Separate credentials.** Settings go in `deploy/caddy.env`, not
  `backend/.env` — the TLS proxy has no business holding your API token or
  exchange keys. Both files are git-ignored.

### Behind a CDN (Cloudflare and similar)

If the hostname is proxied — it resolves to the CDN's addresses rather than
this server — then **no public certificate can be issued here**, because the
certificate authority has to reach this server directly and the CDN is in the
way. Taking the proxy off needs CDN access, which you may not have.

`enable-public.sh` detects this by comparing what the hostname resolves to
against this server's public IP, and sets `TLS_ARG=internal`. Caddy then
serves its own certificate on the origin leg. Visitors still get the CDN's
valid public certificate; only the CDN-to-origin hop uses Caddy's, which is
exactly what a CDN's **Full** SSL mode accepts.

This resolves a **Cloudflare 525 (SSL handshake failed)**, which means the CDN
reached this server but the origin had no certificate it could use.

The one case it cannot fix from here: if the CDN is set to **Full (strict)**,
it validates the origin certificate against a public CA and will reject a
self-signed one. That needs either CDN access, or an origin certificate issued
by the CDN (Cloudflare calls these Origin Certificates) dropped in and
referenced from the Caddyfile with `tls /path/cert.pem /path/key.pem`.

### Hostnames without a domain

The default is an [sslip.io](https://sslip.io) hostname such as
`bot.92-113-21-159.sslip.io`, which resolves to that IP, so a certificate can
be issued without buying a domain.

**Be aware:** every sslip.io hostname shares one Let's Encrypt
certificate-per-registered-domain budget, so issuance there can fail through no
fault of yours. Caddy falls back to ZeroSSL automatically, which usually
resolves it. If certificates keep failing, use a real domain — point an A
record at the server and re-run the script with that hostname. That is the
durable fix, and the URL is nicer.

### If it does not come up

```bash
docker compose logs -f caddy
```

In order of likelihood: certificate rate limiting (above); **port 80 blocked by
a provider firewall** — the ACME challenge needs it open inbound, not just 443;
or DNS not pointing at the server. The SSH tunnel keeps working throughout.

### Two layers of auth

The basic-auth login gets you to the page. The dashboard's own
`API_AUTH_TOKEN` is still required for Start/Stop/Scan/Kill Switch. Losing the
basic-auth password does not by itself give anyone control of the bot.

### Reverting to tunnel-only

```bash
docker compose stop caddy && docker compose rm -f caddy
```

The bot keeps running on `127.0.0.1:8000`.

## Server hygiene

- **Use SSH keys, not passwords.** Generate one locally, then append the
  public half to `~/.ssh/authorized_keys` on the server. Rotate any password
  that has been shared in a chat, ticket or email.
- Firewall: allow only your SSH port inbound (`sudo ufw allow 2222/tcp &&
  sudo ufw enable`). The bot needs nothing inbound.
- The SQLite history lives in `/var/www/litesol/data/`; back it up if you
  care about it.

## Going live later

Don't, until the **NEEDS LIVE VALIDATION** list in [`REPORT.md`](../REPORT.md)
is worked through on testnet/tiny size. When the time comes: trade-only,
IP-allowlisted exchange keys into `backend/.env`, then set both
`LIVE_TRADING=true` and `EXECUTION_ENABLED=true` and restart. The app refuses
to start live with a placeholder API token, and DEX execution is still
intentionally unimplemented (see [`AUDIT.md`](../AUDIT.md)) — with
`DEX_VENUE=jupiter` the two-leg strategy cannot run live yet.
