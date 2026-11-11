"""Deployment regressions.

The first container deploy crashed with
`PermissionError: [Errno 13] Permission denied: '.env'` because there was no
.dockerignore: `COPY backend /app/backend` baked backend/.env into the image
root-owned and mode 600, which the non-root `arbbot` runtime user cannot read.
It also put the API token into an image layer.
"""

from pathlib import Path

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


def _dockerignore_patterns() -> set[str]:
    path = REPO_ROOT / ".dockerignore"
    assert path.is_file(), ".dockerignore is missing; secrets would be copied into the image"
    return {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_dockerignore_excludes_the_env_file():
    patterns = _dockerignore_patterns()
    assert "backend/.env" in patterns, "backend/.env must never enter an image layer"
    assert "backend/.env.*" in patterns


def test_dockerignore_keeps_the_env_example():
    assert "!backend/.env.example" in _dockerignore_patterns()


def test_dockerignore_excludes_local_runtime_state():
    patterns = _dockerignore_patterns()
    # The database is a mounted volume; baking one in would shadow the mount
    # and ship stale trade history.
    assert "data" in patterns
    assert "backend/data" in patterns
    assert "backend/.venv" in patterns


def test_settings_load_with_no_dotenv_file_present():
    """The container is configured purely by env vars from compose's env_file.

    If this breaks, the image would need a readable .env inside it, which is
    exactly what caused the original failure.
    """
    settings = Settings(
        _env_file=None,
        api_auth_token="deployment-test-token-0123456789",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert settings.auth_configured is True
    assert settings.live_execution_allowed is False


def test_compose_supplies_env_and_keeps_api_on_loopback():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "backend/.env" in compose, "compose must inject config via env_file"
    assert "127.0.0.1:8000:8000" in compose, "control API must not bind all interfaces"


def test_sqlalchemy_declares_the_asyncio_extra():
    """SQLAlchemy 2.1 moved greenlet out to the [asyncio] extra.

    Without the extra, a clean install - which is exactly what the Docker
    image does - has no greenlet, and the app dies importing
    sqlalchemy.ext.asyncio. A dev virtualenv that ever held an older
    SQLAlchemy keeps greenlet lying around, so this is invisible locally and
    only shows up in the container.
    """
    reqs = (REPO_ROOT / "backend" / "requirements.txt").read_text()
    assert "SQLAlchemy[asyncio]==" in reqs, (
        "requirements.txt must pin SQLAlchemy with the [asyncio] extra so "
        "greenlet is installed"
    )


def test_greenlet_is_actually_importable():
    """Runtime counterpart to the pin check above."""
    import greenlet  # noqa: F401


def test_async_engine_can_be_constructed():
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert engine is not None


def test_bootstrap_chowns_data_to_the_container_uid():
    """The bind mount keeps host ownership, overriding the Dockerfile chown.

    If the uid in the Dockerfile and the one bootstrap.sh chowns to ever drift
    apart, the container cannot write its SQLite file and dies at startup with
    "unable to open database file".
    """
    import re

    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()
    match = re.search(r"--uid\s+(\d+)", dockerfile)
    assert match, "Dockerfile must create its runtime user with an explicit --uid"
    uid = match.group(1)

    bootstrap = (REPO_ROOT / "deploy" / "bootstrap.sh").read_text()
    assert f"CONTAINER_UID={uid}" in bootstrap, (
        f"bootstrap.sh must chown data/ to uid {uid} to match the Dockerfile"
    )
    assert "chown" in bootstrap and "data" in bootstrap


def test_auth_verify_endpoint_exists_and_is_protected():
    """The unlock screen needs a side-effect-free way to test a token.

    Every other protected endpoint changes state (start, stop, scan, kill
    switch), so none of them is safe to call just to validate a token.
    """
    main = (REPO_ROOT / "backend" / "app" / "main.py").read_text()
    assert "/api/auth/verify" in main
    verify = main[main.index("/api/auth/verify") :]
    assert "Depends(require_auth)" in verify[:200]


def test_dashboard_token_is_not_persisted_to_local_storage():
    """sessionStorage only: the token must not outlive the browser tab."""
    app_js = (REPO_ROOT / "frontend" / "app.js").read_text()
    assert "sessionStorage.getItem" in app_js
    # Match member access, not the word: a comment explains the choice.
    assert "localStorage." not in app_js
    assert "localStorage[" not in app_js


def test_ci_workflow_runs_the_real_checks():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for expected in ["ruff check", "mypy", "pytest", "pip-audit", "npm test", "smoke.js"]:
        assert expected in ci, f"CI does not run {expected}"


def test_deploy_requires_ci_and_is_opt_in():
    deploy = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "needs: ci" in deploy, "a deploy must not run before CI passes"
    assert "vars.DEPLOY_ENABLED == 'true'" in deploy, "deploy must be opt-in"
    assert "StrictHostKeyChecking=yes" in deploy, "host key must be pinned"


def test_pull_deploy_rolls_back_on_a_failed_healthcheck():
    script = (REPO_ROOT / "deploy" / "pull-deploy.sh").read_text()
    assert "Rolling back" in script
    assert 'git reset --hard "$PREVIOUS"' in script


def test_pull_deploy_never_calls_sudo():
    """It runs as a forced command with no TTY, so sudo could only hang."""
    script = (REPO_ROOT / "deploy" / "pull-deploy.sh").read_text()
    code = [
        line
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("sudo" in line for line in code)


def test_pull_deploy_gates_on_the_api_coming_up():
    script = (REPO_ROOT / "deploy" / "pull-deploy.sh").read_text()
    assert "/api/status" in script, "a deploy must verify the app actually starts"


# --- public exposure ------------------------------------------------------


def _compose() -> str:
    return (REPO_ROOT / "docker-compose.yml").read_text()


def test_bot_itself_is_never_published_publicly():
    """Only Caddy may face the internet; the app stays on loopback.

    A bare "8000:8000" would publish the control API - kill switch included -
    on every interface in cleartext.
    """
    compose = _compose()
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"8000:8000"' not in compose
    assert '"0.0.0.0:8000:8000"' not in compose


def test_caddy_does_not_receive_the_trading_secrets():
    """The TLS proxy must not be handed the API token or exchange keys."""
    compose = _compose()
    caddy_block = compose[compose.index("caddy:") :]
    # Strip comments: a comment explaining the choice must not satisfy or
    # break this check - only real directives count.
    directives = [
        line.split("#", 1)[0].strip()
        for line in caddy_block.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    assert "- deploy/caddy.env" in directives
    assert not any("backend/.env" in line for line in directives)


def test_caddy_env_is_git_ignored():
    assert "deploy/caddy.env" in (REPO_ROOT / ".gitignore").read_text()


def test_caddyfile_tls_mode_is_configurable():
    """Behind a CDN, public ACME issuance is impossible without CDN access.

    `tls {$TLS_ARG}` takes either an email (public certificate) or the word
    "internal" (Caddy's own CA), so an origin behind Cloudflare can be served
    without touching Cloudflare at all.
    """
    caddyfile = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    assert "tls {$TLS_ARG}" in caddyfile
    # The old global email block broke when the value was blank.
    assert "email {$ACME_EMAIL}" not in caddyfile


def test_enable_public_picks_internal_tls_behind_a_proxy():
    script = (REPO_ROOT / "deploy" / "enable-public.sh").read_text()
    assert 'DEFAULT_TLS="internal"' in script
    assert "TLS_ARG" in script


def test_caddyfile_has_no_proxy_level_password():
    """The dashboard's token gate is the single login.

    That only holds because the application authenticates every data endpoint
    (see test_every_data_endpoint_requires_the_token) - without that, removing
    the proxy password would expose quotes, trades and config to anyone with
    the URL.
    """
    caddyfile = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    assert "basic_auth" not in caddyfile
    assert "BASIC_AUTH" not in caddyfile
    assert "reverse_proxy arb-bot:8000" in caddyfile


def test_every_data_endpoint_requires_the_token():
    """Load-bearing for the above: nothing but the page and the liveness
    probe may be readable without the token."""
    main = (REPO_ROOT / "backend" / "app" / "main.py").read_text()
    for path in [
        "/api/status",
        "/api/health",
        "/api/quotes",
        "/api/opportunities",
        "/api/trades",
        "/api/scan-once",
        "/api/start",
        "/api/stop",
        "/api/kill-switch/on",
        "/api/kill-switch/off",
    ]:
        decorator = main[main.index(f'"{path}"') : main.index(f'"{path}"') + 120]
        assert "Depends(require_auth)" in decorator, f"{path} is unauthenticated"


def test_ping_is_the_only_public_api_endpoint():
    """The unlock screen must be able to show reachability before it has a
    token, and that probe must reveal nothing else."""
    from app.main import ping

    assert ping is not None
    main = (REPO_ROOT / "backend" / "app" / "main.py").read_text()
    ping_decorator = main[main.index('"/api/ping"') : main.index('"/api/ping"') + 60]
    assert "require_auth" not in ping_decorator


def test_caddyfile_sets_security_headers():
    caddyfile = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    for header in [
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Content-Security-Policy",
    ]:
        assert header in caddyfile, f"{header} is not set"


def test_certificate_volumes_persist():
    """Losing /data on every restart would re-request certificates and burn
    straight through the issuance rate limit."""
    compose = _compose()
    assert "caddy_data:/data" in compose
    assert "caddy_data:" in compose.split("volumes:")[-1]


def test_enable_public_requires_a_non_empty_acme_email():
    """A blank email expands to a bare `email` directive, which cannot parse.

    This broke a real deploy; the validate step caught it before anything
    started, but the script should not have accepted the blank.
    """
    script = (REPO_ROOT / "deploy" / "enable-public.sh").read_text()
    assert "Email for certificate expiry notices (required)" in script
    assert "while [ -z" in script, "the email prompt must loop until answered"


def test_ci_validates_the_deployment_config():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "caddy validate" in ci, "the Caddyfile must be adapted in CI"
    assert "docker compose config" in ci


def test_enable_public_is_rerunnable_while_the_stack_is_up():
    """Our own caddy container holds 80/443 on every re-run.

    Treating that as a port conflict made the script impossible to re-run,
    which is exactly when you need it (changing hostname or TLS mode).
    """
    script = (REPO_ROOT / "deploy" / "enable-public.sh").read_text()
    assert "ps -q caddy" in script, "must recognise its own container"
    assert "OURS_RUNNING" in script


def test_enable_public_has_a_dry_run_mode():
    """`bash -n` passed on a $RESOLVED-before-assignment bug that `set -u`
    aborts on at runtime. Only running the script catches that class."""
    script = (REPO_ROOT / "deploy" / "enable-public.sh").read_text()
    assert 'DRY_RUN="${DRY_RUN:-}"' in script
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "DRY_RUN=1" in ci, "CI must actually run the script, not just parse it"


def test_enable_public_resolves_before_deciding_tls_mode():
    """Ordering guard: the TLS decision depends on the DNS lookup."""
    script = (REPO_ROOT / "deploy" / "enable-public.sh").read_text()
    assigned = script.index('RESOLVED="$(getent hosts')
    used = script.index('if [ -z "$RESOLVED" ]; then')
    assert assigned < used, "RESOLVED is read before it is assigned"
