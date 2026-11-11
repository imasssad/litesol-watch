# Deploying to a server

Deploys the backend + dashboard with docker compose, **in paper mode**. The
bootstrap script never enables live trading and never overwrites an existing
`backend/.env`.

## One-time: from your machine

From a checkout of this repo on your own machine (not the server):

```bash
rsync -az --delete \
  --exclude .git --exclude backend/.venv --exclude backend/data \
  --exclude backend/.env --exclude web-app/node_modules --exclude data \
  ./ <user>@<server-ip>:~/litesol-watch/
```

```bash
ssh <user>@<server-ip> 'bash ~/litesol-watch/deploy/bootstrap.sh'
```

(`rsync` instead of a server-side `git clone` so the server needs no GitHub
credentials. If the server has docker missing, the script prints the exact
install commands and exits.)

## Updating

Re-run the same two commands. `--exclude backend/.env --exclude data` keeps
the server's token and trade history; compose rebuilds only what changed.

## Accessing the dashboard

The API binds to `127.0.0.1` on the server **on purpose** — a trading bot's
control endpoints (kill switch included) must not face the internet, even
with token auth. Reach it through an SSH tunnel:

```bash
ssh -L 8000:127.0.0.1:8000 <user>@<server-ip>
```

then open <http://localhost:8000>. Paste the `API_AUTH_TOKEN` from the
server's `backend/.env` into the dashboard's token field to use the controls.

If you later want it on a hostname, put a TLS reverse proxy (caddy/nginx)
with its own auth in front — do not change the compose port binding to
`0.0.0.0`.

## Server hygiene

- **Use SSH keys, not passwords** (`ssh-copy-id <user>@<server-ip>`, then set
  `PasswordAuthentication no` in `/etc/ssh/sshd_config`). Rotate any password
  that has ever been shared in a chat, ticket or email.
- Firewall: only 22 open inbound (`ufw allow OpenSSH && ufw enable`). The bot
  needs nothing inbound.
- The SQLite history lives in `~/litesol-watch/data/` on the server; back it
  up if you care about it.

## Going live later

Don't, until the **NEEDS LIVE VALIDATION** list in [`REPORT.md`](../REPORT.md)
is worked through on testnet/tiny size. When the time comes: trade-only,
IP-allowlisted exchange keys into `backend/.env`, then set both
`LIVE_TRADING=true` and `EXECUTION_ENABLED=true` and restart. The app refuses
to start live with a placeholder API token, and DEX execution is still
intentionally unimplemented (see AUDIT.md) — with `DEX_VENUE=jupiter` the
two-leg strategy cannot run live yet.
