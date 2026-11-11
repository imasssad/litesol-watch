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
