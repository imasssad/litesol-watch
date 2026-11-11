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
