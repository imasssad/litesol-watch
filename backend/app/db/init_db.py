import logging
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.db.models import Base
from app.db.session import engine

logger = logging.getLogger(__name__)


def _ensure_sqlite_dir() -> None:
    """Create the SQLite parent directory if it does not exist.

    The default DATABASE_URL points at ./data/ so the path lines up with the
    volume docker-compose mounts; SQLite will not create that directory itself.
    """
    url = settings.database_url
    if "sqlite" not in url:
        return
    path = urlparse(url).path.lstrip("/")
    if not path or path == ":memory:":
        return
    parent = Path(path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
        logger.info("sqlite data directory: %s", parent.resolve())


async def init_db() -> None:
    _ensure_sqlite_dir()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
