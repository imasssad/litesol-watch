"""Shared HTTP helper with retries for transient venue failures.

Public venue APIs return the occasional 500 or time out; Jupiter's keyless
tier especially. A single blip should never reach the operator, so requests
are retried a few times over a couple of seconds before being reported.

What is NOT retried:
  - 4xx other than 429/418: the request is wrong, repeating it will not help
  - 429/418: the venue is telling us to slow down, so it becomes RateLimited
    and the scan loop backs off properly instead of hammering
"""

import asyncio
import logging
import random

import httpx

from app.connectors.base import RateLimited

logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 3
BASE_DELAY_SECONDS = 0.4


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After", "")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    venue: str,
    params: dict | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
) -> httpx.Response:
    """GET, retrying transient failures. Raises on the last attempt."""
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            logger.info(
                "%s request failed (%s), attempt %d/%d", venue, exc, attempt, attempts
            )
        else:
            if response.status_code in (429, 418):
                # Not retried here: the scan loop owns backing off.
                raise RateLimited(venue, retry_after=_retry_after(response))
            if response.status_code >= 500:
                last_error = RuntimeError(
                    f"{venue} returned HTTP {response.status_code}"
                )
                logger.info(
                    "%s returned %d, attempt %d/%d",
                    venue,
                    response.status_code,
                    attempt,
                    attempts,
                )
            else:
                response.raise_for_status()
                if attempt > 1:
                    logger.info("%s recovered on attempt %d", venue, attempt)
                return response

        if attempt < attempts:
            # Jitter so several callers do not retry in lockstep.
            delay = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            await asyncio.sleep(delay + random.uniform(0, 0.2))

    assert last_error is not None
    raise last_error
