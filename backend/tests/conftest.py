import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Settings are read at import time, so the environment must be set before the
# first `app.*` import. An in-memory DB keeps tests from touching ./data.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("API_AUTH_TOKEN", "test-token-aaaaaaaaaaaaaaaaaaaaaa")
os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_ENABLED", "false")

import pytest  # noqa: E402

from app.models.schemas import Quote  # noqa: E402


@pytest.fixture
def quote():
    from datetime import datetime, UTC

    def _make(venue: str, bid: float, ask: float, ts: datetime | None = None) -> Quote:
        return Quote(
            venue=venue,
            pair="SOL/USDC",
            bid=bid,
            ask=ask,
            ts=ts or datetime.now(UTC),
        )

    return _make
