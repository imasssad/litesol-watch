# Audit — LITESOL_WATCH bundle

Audit of the code uploaded as `LITESOL_WATCH.zip`, with every finding resolved
in the commit that follows the import. Commit `cfe9628` is the code exactly as
uploaded, so every change here is reviewable as a diff against it.

## What was in the archive

| Payload | Disposition |
| --- | --- |
| `arb_bot_production_v1.zip` | Superseded — `_mexc_bingx` is a strict superset of it (adds the CCXT/MEXC/BingX connectors and the venue factory, changes nothing else). Not imported; recoverable from the archive if needed. |
| `arb_bot_production_v1_mexc_bingx.zip` | Imported as `backend/`, `frontend/`, `README.md`, `docker-compose.yml`. |
| `solana_dex_bot_test_app.jsx` | Imported as `web-app/`. Unbuildable prototype — see `web-app/README.md`. |

No credentials were present in the archive. `backend/.env.example` ships only
empty values.

## Severity summary

| # | Severity | Issue |
| --- | --- | --- |
| 1 | **Blocker** | The app could not start at all |
| 2 | **Critical** | Control endpoints were unauthenticated by default |
| 3 | **Critical** | The pre-trade requote did not re-check the spread |
| 4 | **Critical** | A one-sided fill left an unhedged position unrecorded |
| 5 | **Critical** | React prototype traded live against simulated prices |
| 6 | High | "Consecutive" failure limit counted non-consecutive failures |
| 7 | High | Live sell orders ignored exchange lot-size rules |
| 8 | High | SQLite database was not persisted by `docker-compose` |
| 9 | Medium | `/api/health` returned 500 whenever a venue was unreachable |
| 10 | Medium | Scan failures were swallowed silently |
| 11 | Medium | Zero/negative venue prices crashed the scanner |
| 12 | Medium | `PAIR` could silently disagree with `BASE_ASSET`/`QUOTE_ASSET` |
| 13 | Medium | Dashboard rendered remote strings as unescaped HTML |
| 14 | Medium | A single failed poll froze the dashboard permanently |
| 15 | Low | Jupiter quoted a different size than the bot traded |
| 16 | Low | Unsupported assets raised a bare `KeyError` |
| 17 | Low | Manual and background scans could interleave |
| 18 | Low | Network connections were never closed |
| 19 | Low | Timestamps lost their timezone on the database round trip |
| 20 | Low | Realized PnL was reported gross of fees |
| 21 | Low | Container ran as root; port published on all interfaces |
| 22 | Low | Mock connectors were unreachable dead code |

---

## Critical and blocking

### 1. The app could not start at all — `backend/app/main.py`

```python
FRONTEND_DIR = Path(__file__).resolve().parents[2] / ".." / "frontend"
```

`parents[2]` is already the project root, so the extra `..` resolved one level
above it. `StaticFiles` raises at import time when its directory is missing, so
**every** deployment died on startup:

```
RuntimeError: Directory '/home/user/litesol-watch/../frontend' does not exist
```

Fixed to `parents[2] / "frontend"`. The mount is now also guarded so a
misdeployed frontend logs an error and disables the dashboard instead of
preventing the trading engine from booting.

### 2. Control endpoints were unauthenticated by default — `backend/app/main.py`

```python
def require_auth(x_api_token: str | None = Header(default=None)) -> None:
    if settings.api_auth_token == "change-me-long-random-token":
        return   # <-- auth disabled
```

`backend/.env.example` ships exactly that token, so on any deployment that had
not edited it, `POST /api/start`, `/api/stop`, `/api/scan-once`,
`/api/kill-switch/on` and `/api/kill-switch/off` were open to anyone who could
reach the port. The placeholder made the API *less* safe than leaving the token
empty, and the kill switch — the one control that stops a running bot — was
anonymous.

Now:

- auth **fails closed**: an unconfigured token returns `503` and the endpoints
  are unusable, rather than open;
- a wrong or missing token returns `401`;
- comparison uses `secrets.compare_digest`;
- the config refuses to start at all if live execution is enabled while the
  token is still the placeholder;
- `/api/status` exposes `auth_configured`, and the dashboard shows a red
  **Auth: NOT SET** card.

### 3. The pre-trade requote did not re-check the spread — `backend/app/engine/executor.py`

The most expensive bug in the bundle. The executor refetched both quotes before
firing, then did:

```python
fresh_opp = opportunity.model_copy(update={
    "buy_price": buy_quote.ask,
    "sell_price": sell_quote.bid,
})
ok, reason = self.risk.validate(fresh_opp, [buy_quote, sell_quote])
```

`model_copy` updates only the two price fields. `gross_spread_bps`,
`net_spread_bps` and `expected_profit_usd` were **carried over from the original
scan**, and `risk.validate` checks `net_spread_bps`. So the requote verified
quote *freshness* and nothing else: the spread check re-ran against the stale
number it was supposed to replace.

Demonstrated against the uploaded code — prices inverted from (buy 100, sell 102)
to (buy 102, sell 100), a true spread of **-196 bps**:

```
fresh.net_spread_bps still reads: 200.0   (carried over, never recomputed)
risk.validate() -> True | risk checks passed   <-- would FIRE a losing trade
```

The guard described in the README as a "pre-trade requote before execution"
could not block a single adverse move.

Fixed by extracting the opportunity maths into `backend/app/engine/pricing.py`
and giving it a `reprice()` that re-derives the entire opportunity from the fresh
quotes. Scanner and executor now share one implementation.

### 4. A one-sided fill left an unhedged position unrecorded — `backend/app/engine/executor.py`

```python
buy_res, sell_res = await asyncio.gather(buy_task, sell_task)
```

Both legs fire concurrently. Without `return_exceptions=True`, the first leg to
raise propagates immediately out of `gather` into a blanket `except Exception`,
which recorded `status="failed"` with no order IDs. If the *other* leg had
already filled, the bot was now holding a real, unhedged position in a trade
recorded as a plain failure — directional exposure with nothing pointing at it.

Now the legs are gathered with `return_exceptions=True` and the three outcomes
are distinguished:

- both failed → `status="failed"`;
- both filled → `status="live_filled"`;
- **one filled** → `status="one_leg_filled"`, the surviving leg's venue,
  quantity, average price and order ID in the error field, a `CRITICAL` log
  line, and the kill switch engaged with reason
  `"one-sided fill needs manual reconciliation"`.

### 5. React prototype traded live against simulated prices — `web-app/`

In live mode the prototype submitted a real mainnet Jupiter swap and then
recorded the position's entry price and token amount from `randomWalk()`, a
random number generator. Take-profit, stop-loss, trailing-stop and the
daily-loss limit were all evaluated against those invented numbers while the
swaps were real and irreversible.

Live execution is now gated off behind `LIVE_MODE_SUPPORTED = false` and the UI
checkbox is disabled. Full reasoning and the work required to re-enable it is in
`web-app/README.md`, along with three other defects fixed there (base-unit
conversion on the sell leg, a `btoa` call that throws on large transactions, and
a profit sweep that reported moving funds it never moved).

---

## High

### 6. "Consecutive" failures were never consecutive — `backend/app/engine/risk.py`

`record_failure()` incremented `failed_trade_count` and tripped the kill switch
at `MAX_CONSECUTIVE_FAILURES`, but nothing ever reset the counter on success.
Three unrelated failures hours or days apart would halt the bot. Added
`record_success()`, called on every completed trade.

The kill switch also now carries a reason, surfaced via `/api/status` and the
dashboard; previously a halted bot gave no indication of why.

### 7. Live sell orders ignored exchange lot-size rules — `backend/app/connectors/binance_spot.py`

```python
params["quantity"] = f"{qty:.6f}"
```

A fixed six decimals matches no particular symbol's `LOT_SIZE.stepSize`, so live
sells would be rejected with Binance error `-1111` (precision over maximum) on
most pairs. Combined with finding 4, a rejected sell leg after a filled buy leg
is exactly the unhedged-position case.

Now the connector fetches and caches `/api/v3/exchangeInfo`, floors the quantity
to `stepSize`, and rejects sizes below `minQty` or `minNotional` locally — before
sending an order whose failure would leave the other leg exposed. The CCXT
connector got the equivalent `limits.amount.min` / `limits.cost.min` checks.

Order signing was also tightened: the signature is now computed over the exact
query string that is sent, rather than relying on the HTTP layer re-serialising
the dict to identical bytes. Added `recvWindow` and `newOrderRespType=FULL`, and
average fill price now prefers Binance's own `cummulativeQuoteQty`.

### 8. The SQLite database was not persisted — `docker-compose.yml`

The compose file mounted `./data:/app/backend/data`, but `DATABASE_URL` was
`sqlite+aiosqlite:///./arb_bot.db` against `WORKDIR /app/backend` — so the
database was written to `/app/backend/arb_bot.db`, *beside* the mount, inside the
container's ephemeral layer. Every restart silently discarded the full trade and
opportunity history.

The default is now `sqlite+aiosqlite:///./data/arb_bot.db`, matching the mount,
and `init_db()` creates the directory (SQLite will not).

---

## Medium

### 9. `/api/health` returned 500 when a venue was down — `backend/app/connectors/binance_spot.py`

`BinanceSpotConnector.health()` had no error handling, unlike Jupiter's. One
unreachable venue turned the whole health endpoint — the thing you check when a
venue is unreachable — into a 500. Wrapped, and `scanner.health()` now gathers
with `return_exceptions=True` so either venue failing still yields a useful
per-venue payload. Verified:

```json
{"cex":{"venue":"Binance Spot","ok":false,"error":"403 Forbidden"},
 "dex":{"venue":"Jupiter","ok":false,"error":"403 Forbidden"}}
```

### 10. Scan failures were swallowed silently — `backend/app/engine/scanner.py`

```python
except Exception:
    self.risk.record_failure()
```

No logging, no message, no exception detail. A venue outage or config error
would trip the kill switch after three silent iterations, leaving an operator
with a halted bot and nothing to go on. The project had no logging configured at
all. Added `logging.basicConfig` and a logger per module; failures now record
their cause, and `asyncio.CancelledError` is re-raised rather than counted as a
trading failure.

### 11. Zero or negative venue prices crashed the scanner

`_build_opportunity` divided by `buy.ask` and `gas_buffer_usd / trade_size_usd`
with no guards, so a venue returning `0` raised `ZeroDivisionError` inside the
scan loop. Prices are now validated at the connector boundary and in
`pricing.build_opportunity`, which returns a non-executable opportunity with
reason `invalid price (...)`.

The sentinel for such an opportunity is deliberately `-1e9` bps rather than
`float("-inf")`: `-inf` is not valid JSON and FastAPI would have emitted
`-Infinity`, breaking the dashboard's parse. There is a test for this.

### 12. `PAIR` could silently disagree with the assets — `backend/app/config.py`

`pair` drove the CCXT symbol and display, while `base_asset`/`quote_asset` drove
the Binance symbol and the Jupiter mints. Nothing checked they agreed, and the
README's MEXC/BingX instructions require editing all three. Setting
`PAIR=SOL/USDT` while leaving `QUOTE_ASSET=USDC` would quote one market and trade
another. Now rejected at startup, along with unsupported venues, non-positive
sizes and intervals.

### 13. Dashboard rendered remote strings as unescaped HTML — `frontend/app.js`

`table()` interpolated `r[c.key]` straight into `innerHTML`. The `reason` and
`error` columns carry exception text built from venue API responses, so anything
able to shape an error message could inject markup into the operator's
dashboard — the page that holds the API token input. All interpolated values now
pass through an `esc()` helper.

### 14. A single failed poll froze the dashboard — `frontend/app.js`

`refresh()` had no error handling and ran under `setInterval`. The first failed
fetch threw out of the callback, leaving stale numbers on screen with no
indication they had stopped updating — an operator could watch a frozen "Kill
Switch: OFF" indefinitely. Now wrapped, with a visible error banner, and `post()`
surfaces the server's `detail` instead of a bare status code.

---

## Low

| # | Issue | Resolution |
| --- | --- | --- |
| 15 | Jupiter quoted `trade_size_usd` while the scanner traded `min(trade_size_usd, max_trade_usd)` — the sampled depth did not match the executed size | Both use `settings.effective_trade_size_usd` |
| 16 | `MINTS[quote]` raised a bare `KeyError` for any asset outside SOL/USDC/USDT | Clear `RuntimeError` naming the supported set |
| 17 | `POST /api/scan-once` could interleave with the background loop, letting two scans act on one edge | `asyncio.Lock` around `scan_once` |
| 18 | `httpx` clients and the CCXT `aiohttp` session were never closed; `CcxtSpotConnector.close()` existed but was never called | `aclose()` on the connector base, called from a `scanner.close()` wired into the FastAPI lifespan |
| 19 | Timestamps round-tripped through SQLite as naive datetimes, so the API emitted zone-less times that clients read as local | `DateTime(timezone=True)` **and** UTC re-attached on read — SQLite stores no offset, so the column type alone does not fix it |
| 20 | `realized_profit_usd` was `(sell - buy)/buy * size` — gross of fees, and scaled by notional rather than filled quantity | Computed from `min(filled_qty)` across legs, net of fee and gas estimates |
| 21 | Container ran as root; compose published `8000` on all interfaces | Non-root `arbbot` user; port bound to `127.0.0.1`; healthcheck added |
| 22 | `mock_cex.py` / `mock_dex.py` were unreachable — nothing imported them | Selectable via `CEX_VENUE=mock` / `DEX_VENUE=mock`, so the whole stack runs offline. They refuse to place orders. |

Also: `app/db/` and `app/services/` were missing `__init__.py` (working only by
namespace-package fallback), and `OpportunityRow.reason` was `String(256)` while
now holding exception text — widened to `Text`.

---

## Verification

A regression suite was added at `backend/tests/` — 53 tests, one or more per
finding above. Each fails against the uploaded code.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest
```

```
53 passed
```

The full stack was also run end to end offline (`CEX_VENUE=mock`,
`DEX_VENUE=mock`), confirming quotes → opportunities → paper trades → SQLite →
API, and separately against the real Binance and Jupiter endpoints to confirm
graceful degradation when venues are unreachable.

## Second pass (findings 23-27)

A follow-up pass verified the connectors against current (November 2026)
official documentation, ran dependency audits, and checked restart behavior.

### 23. HIGH — the default Jupiter endpoint no longer exists

`JUPITER_QUOTE_URL` defaulted to `https://quote-api.jup.ag/v6/quote`, which
Jupiter **retired on 2025-10-01** (announced by Jupiter DevRel; the migration
target is `swap/v1/quote` on `lite-api.jup.ag`/`api.jup.ag`, same
`inputMint/outputMint/amount/slippageBps` → `outAmount` interface). Every DEX
quote — and therefore every scan — fails against the live API today. This was
not catchable from the sandbox (its proxy blocks the hosts either way); it
surfaced from the docs check. Defaults now point at
`lite-api.jup.ag/swap/v1/quote`, with `JUPITER_API_KEY` support (sent as
`x-api-key`) for the keyed `api.jup.ag` host. Rate-limit note added to the
README: at a 2s scan interval the two quote calls per scan sit exactly at the
free tier's ~60 req/min. **NEEDS LIVE VALIDATION.**

The React prototype's `api.jup.ag/swap/v2` order/execute endpoint, by
contrast, is current — it superseded Ultra v1.

### 24. HIGH — risk state did not survive a restart

`RiskEngine` state was purely in-memory: a process restart (or a crash, or
`docker compose restart`) silently released a tripped kill switch and zeroed
the daily realized loss, so a bot that halted for a one-sided fill would
resume trading on reboot. Now: a `bot_state` row persists the kill switch and
its reason (written whenever it changes, including from the API endpoints),
and startup recomputes today's realized **live** PnL from the trades table —
re-engaging the kill switch if the daily loss limit was already breached.
Verified live: engage kill switch → kill the process → restart → `/api/status`
shows `kill_switch: true` with the original reason.

### 25. MEDIUM — order signing used the local clock

Binance order timestamps came from `time.time()`; local skew beyond
`recvWindow` (5s) rejects every order with `-1021`. The connector now syncs an
offset from `GET /api/v3/time` (cached 30 min, local-clock fallback with a 5
min retry on sync failure).

### 26. MEDIUM — vulnerable and unused dependencies

`pip-audit` on the shipped pins: `starlette 0.45.3` (8 advisories),
`python-multipart 0.0.20` (5), `orjson 3.10.15` (1), plus dev-only `pytest
8.3.4` (1). `python-multipart` and `orjson` were not imported anywhere —
removed. The rest upgraded (fastapi 0.162.2 / starlette 1.10.0 /
SQLAlchemy 2.1.8 / ccxt 4.5.92 / pydantic 2.14.3 / pytest 9.2.0); all 53 tests
pass on the new pins and `pip-audit` is clean on prod and dev requirements.
`npm audit` on the web-app dev toolchain: clean after moving to vitest 5.

### 27. LOW — tooling

`ruff` (config in `backend/ruff.toml`) and `mypy` now run clean and are pinned
in `requirements-dev.txt`. `Side` became a `StrEnum`.

## What this audit does not cover

- **No live order was ever placed.** The live paths for Binance and CCXT are
  reviewed and corrected by inspection, not exercised against a real account.
  Validate on testnet (`BINANCE_TESTNET=true`) or with minimal size first.
- **Jupiter DEX execution remains unimplemented** by design — `market_order`
  raises `NotImplementedError` pending an audited signer or custody provider.
  Note the consequence: with `DEX_VENUE=jupiter`, any opportunity involving the
  DEX leg will fail in live mode, and three such failures trip the kill switch.
  The bot is only live-tradeable once DEX signing exists.
- **The strategy itself is not assessed.** Whether a 65 bps net threshold clears
  real costs, whether the prefunded-inventory model suits the venues, and whether
  the fee and slippage constants are accurate are all unexamined. The
  `CEX_FEE_BPS`/`DEX_FEE_BPS` defaults are hardcoded guesses, not fetched from
  the venues.
- **No concurrency or load testing**, and no reconciliation layer: the bot
  trusts its own records of what filled and never verifies against exchange
  balances.
- **`web-app/` is not production-ready** and is not made so here — see
  `web-app/README.md`.

Nothing in this audit makes the project safe to run with real funds unattended.
It makes the safety mechanisms the project already claimed to have actually
work.
