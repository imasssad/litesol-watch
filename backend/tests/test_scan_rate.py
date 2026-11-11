"""The configured scan rate must not exceed what the venue allows.

Root cause of the recurring Jupiter 429s and 500s: each scan makes two Jupiter
quote calls, and the keyless tier allows ~60 req/min. A 1-2s interval means
120-60 req/min - permanent failure that looks like a broken bot but is really
an impossible configuration.
"""

from app.config import (
    JUPITER_KEYLESS_MIN_INTERVAL_SECONDS,
    Settings,
)


def _settings(**overrides):
    base = {
        "api_auth_token": "a-long-random-test-token",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_a_too_fast_interval_is_floored_on_keyless_jupiter():
    s = _settings(dex_venue="jupiter", jupiter_api_key="", scan_interval_seconds=1)
    assert s.effective_scan_interval_seconds == JUPITER_KEYLESS_MIN_INTERVAL_SECONDS
    assert s.scan_interval_was_raised is True


def test_the_old_default_of_two_seconds_was_over_the_limit():
    """2s x 2 calls = 60 req/min, right at the ceiling - it is now floored."""
    s = _settings(dex_venue="jupiter", jupiter_api_key="", scan_interval_seconds=2)
    assert s.effective_scan_interval_seconds > 2


def test_a_safe_interval_is_left_alone():
    s = _settings(dex_venue="jupiter", jupiter_api_key="", scan_interval_seconds=20)
    assert s.effective_scan_interval_seconds == 20
    assert s.scan_interval_was_raised is False


def test_an_api_key_lifts_the_floor():
    """A paid key is what actually buys a fast scan; the floor must not
    punish someone who has one."""
    s = _settings(dex_venue="jupiter", jupiter_api_key="key-123", scan_interval_seconds=1)
    assert s.effective_scan_interval_seconds == 1
    assert s.scan_interval_was_raised is False


def test_mock_venue_is_unaffected():
    """Offline runs make no network calls, so no floor applies."""
    s = _settings(dex_venue="mock", scan_interval_seconds=0.5)
    assert s.effective_scan_interval_seconds == 0.5


def test_the_shipped_default_is_within_the_keyless_tier():
    s = _settings(dex_venue="jupiter", jupiter_api_key="")
    calls_per_min = (60.0 / s.effective_scan_interval_seconds) * 2
    assert calls_per_min <= 60, f"default makes {calls_per_min:.0f} req/min"
