# REPORT — LITESOL_WATCH audit and remediation

Companion to [`AUDIT.md`](AUDIT.md) (the full finding-by-finding write-up, 27
findings with file references and failure scenarios). This report covers the
process, what was fixed vs deferred, before/after results, and what still
blocks real funds.

This system is **not** certified profitable or safe. The work below makes its
claimed safety mechanisms function and removes defects; it does not validate
the strategy, and several items explicitly need live validation.

---

## Phase 1 — Setup

- Read: both READMEs, `.env.example`, `docker-compose.yml`, `requirements.txt`.
- **(A) vs (B) diff** — (B) `arb_bot_production_v1_mexc_bingx` is a strict
  superset of (A). (B) adds `ccxt_spot.py`, `mexc_spot.py`, `bingx_spot.py`,
  `factory.py`; the only other differences wire those in:
  - `config.py`: +MEXC/BingX settings, +`symbol_ccxt` property
  - `scanner.py`: `BinanceSpotConnector()` → `build_cex_connector()`
  - `jupiter.py`: +USDT mint/decimals
  - `.env.example`, `README.md`: +MEXC/BingX sections
  - Everything else byte-identical.
- **Decision: (A) deleted, (B) kept** as the single codebase (now `backend/` +
  `frontend/` at the repo root). Maintaining duplicate trees means fixing every
  bug twice and guarantees drift; (A) contains nothing (B) lacks and remains
  recoverable from the original archive and from commit `cfe9628`, which is the
  upload imported verbatim.
- **Tests: none existed.** `pip install` succeeded on the shipped pins.
- Paper mode: the backend **did not start at all** (finding 1); after that fix
  it ran in paper mode and was exercised end to end.
- `AUDIT.md` created and maintained throughout.

## Phase 2 — Audit

27 findings: **1 blocker, 4 critical, 5 high, 8 medium, 9 low** — severity
table and full details in `AUDIT.md`. Headlines:

| Sev | Finding |
| --- | --- |
| BLOCKER | App could not boot (frontend path resolved outside the repo) |
| CRITICAL | Control endpoints (incl. kill switch) unauthenticated on default config |
| CRITICAL | Pre-trade requote validated the *stale* spread (`model_copy` kept old `net_spread_bps`) — demonstrated passing risk checks on a −196 bps edge |
| CRITICAL | One leg filling + one failing left an unhedged position recorded as a generic failure |
| CRITICAL | React app's live mode placed real swaps while tracking entry/PnL/stops from a random-walk simulator |
| HIGH | Default Jupiter endpoint (`quote-api.jup.ag/v6`) retired 2025-10-01 — every DEX quote fails against today's API |
| HIGH | Kill switch and daily loss did not survive a restart |
| HIGH | "Consecutive" failure counter never reset on success |
| HIGH | Live sell quantities ignored LOT_SIZE/minNotional (Binance `-1111`) |
| HIGH | docker-compose DB path mismatch — history discarded every restart |

Specific checklist answers:

- **Fees/costs:** CEX+DEX fee bps, slippage buffer and a flat gas buffer are
  modeled; withdrawal/bridge costs are correctly out of scope for the
  prefunded-inventory model, but **inventory rebalancing cost is unmodeled**
  (deferred, documented). Fee constants are hardcoded guesses (deferred).
- **Float vs Decimal:** money stays `float` (deferred — see below).
- **Paper default:** yes — both flags default false, and execution requires
  both (`live_execution_allowed = live_trading AND execution_enabled`);
  covered by `test_live_execution_needs_both_gates`. One flag alone cannot
  execute anywhere: every `market_order` also re-checks the gate itself.
- **Races:** background loop vs `POST /api/scan-once` could interleave — now
  serialized by an `asyncio.Lock`.
- **Jupiter cannot send:** confirmed — `JupiterConnector.market_order` raises
  `NotImplementedError` unconditionally after the gate check; no signing code
  or private-key use exists in the backend. (`SOLANA_PRIVATE_KEY_BASE64`
  exists in config but is read by nothing.) Proposed design in `AUDIT.md`:
  keep the connector boundary and integrate an external signer
  (Fireblocks/MPC/HSM or a sign-only sidecar) so the key never enters this
  process.
- **Security:** no keys in logs/responses/frontend; token comparison now
  `secrets.compare_digest`; no CORS middleware (same-origin only — correct for
  a token-holding dashboard); read endpoints take no user input; compose holds
  secrets via `env_file` with `.env` git-ignored; `pip-audit`/`npm audit` run
  (findings 26 and below).
- **Persistence/reliability:** aiosqlite everywhere (no blocking I/O in async
  paths found); silent `except Exception` swallows removed in favor of logged
  failures with reasons; restart recovery added (finding 24).
- **Frontend:** innerHTML XSS fixed with escaping; token lives only in a
  password input in memory (never localStorage) and is sent only on control
  POSTs; mock-venue data is labeled `source: "mock"` end to end and the mocks
  refuse real orders.

## Phase 3 — Fixes

All CRITICAL, HIGH and MEDIUM findings are fixed; every fix carries a test
written to fail first (verified failing against the pre-fix tree, then
passing). LOW findings fixed opportunistically; deferred items below.

Shared-code policy: fixes land once, in the kept codebase (see Phase 1
decision — (A) deleted rather than double-patched).

Gates were never weakened: paper mode remains the default, both flags remain
required, auth became stricter (fail-closed), the React live mode went from
"checkbox" to "disabled constant + confirm dialog + hard $10 cap", and no key
material was added anywhere.

New tests (69 total: 53 pytest + 16 vitest):
- spread math & repricing (`test_audit_regressions.py`: pricing section)
- risk limits, kill switch, freshness, daily-loss (risk sections)
- execution state machine incl. one-leg fill (`test_executor.py`, fake connectors)
- auth fail-closed matrix, config validation, factory, mints, DB round-trips
- restart persistence & PnL recompute (`test_state_and_connectors.py`)
- Binance time-offset signing, Jupiter header/URL migration
- React logic (`web-app/lib.test.mjs`): unit conversions, base64, TP/SL/trailing
  `exitReason`, live cap — extracted to `lib.mjs` so the tested code is the
  code the component imports.

Connector behavior that needs a funded account or live exchange access is
**NEEDS LIVE VALIDATION** (list below); connector unit tests use fakes and
response-shaped fixtures, not recorded live traffic.

## Phase 4 — Verification

| Check | Before | After |
| --- | --- | --- |
| Backend boots | ✗ `RuntimeError` at import | ✓ |
| pytest | no tests existed | **53 passed** |
| vitest (web-app logic) | no tests existed | **16 passed** |
| ruff | n/a | clean (`backend/ruff.toml`) |
| mypy (`--ignore-missing-imports`) | n/a | clean, 26 files |
| `pip-audit` prod deps | 14 advisories (starlette 8, python-multipart 5, orjson 1) | **0** (upgraded; multipart+orjson removed as unused) |
| `pip-audit` dev deps | pytest advisory | **0** |
| `npm audit` (web-app) | 3 (2 critical, dev toolchain) | **0** (vitest 5) |
| Control endpoints, no/wrong token | **200 — executed** | 401 (503 if no token configured) |
| Control endpoints, correct token | 200 | 200 |
| `/api/health`, venue down | 500 | 200 with per-venue `ok:false` + error |
| Kill switch after restart | silently cleared | **restored with reason** (verified live: engage → kill process → restart → still on) |
| Paper mode end-to-end | n/a (would not boot) | ✓ mock venues: quotes → opportunities → paper trades → SQLite → API (verified on the upgraded dependency pins) |
| JSX | parses only | parses + bundles (esbuild), logic under vitest |

`docker compose` itself could not be executed here (no Docker daemon in this
sandbox): the Dockerfile/compose fixes (DB volume path alignment, non-root
user, loopback port binding, healthcheck) are **validated by inspection and by
running the identical uvicorn entrypoint directly** — listed under NEEDS LIVE
VALIDATION. READMEs were updated to match actual behavior.

## Deferred, with reasons

| Item | Reason |
| --- | --- |
| Decimal/int money types | All money flows through float. Sizes are small and bounded ($250 cap), rounding is controlled at exchange boundaries (stepSize flooring), and converting Pydantic+SQLite+two connector stacks to Decimal is an invasive change with its own risk. Documented limitation; worth doing before scaling size. |
| Live fee/withdrawal schedule fetching | `CEX_FEE_BPS`/`DEX_FEE_BPS` remain config constants; fetching real schedules per venue is new scope. The audit flags the defaults as guesses. |
| Inventory rebalancing economics | The prefunded model avoids per-trade transfers but eventually requires rebalancing; unmodeled, strategy-level. |
| Order retries / idempotency keys (`newClientOrderId`) | Retrying orders without exactly-once semantics risks duplicate fills — worse than the disease. Needs a reconciliation layer first. |
| Exchange-balance reconciliation | The bot trusts its own fill records. A real deployment needs periodic balance reconciliation; new subsystem. |
| React app live-mode rebuild | Requires real fills read back from chain, a real price feed, a real entry signal, and per-pair concurrency — a rebuild, not a fix. Live mode is disabled instead; path documented in `web-app/README.md`. |
| React `useEffect`/`busyPair` races | Only consequential in live mode (disabled); in simulation worst case is a double-logged simulated sell. Documented. |
| Rate limiting on Binance httpx client | 1-2 req/scan at 2s intervals is far under limits; ccxt side has `enableRateLimit`. |

## NEEDS LIVE VALIDATION

1. **Jupiter `swap/v1/quote` migration** — interface confirmed against current
   docs; never called from this sandbox (egress blocked). Includes lite-api
   free-tier rate limits vs scan interval.
2. **Binance order path** — signing, `quoteOrderQty`/`quantity` semantics,
   stepSize/minNotional handling, time-offset sync: testnet
   (`BINANCE_TESTNET=true`) first, then minimal size.
3. **MEXC/BingX via ccxt 4.5.92** — market data and spot market orders,
   precision/min-cost on the actual account tier.
4. **docker compose stack** — no Docker daemon in the audit environment.
5. **React app swap/v2 order/execute flow** — endpoint current per docs;
   disabled in code regardless.

## What blocks real funds

1. **No DEX execution** — Jupiter signing is intentionally unimplemented;
   until an audited external signer exists, any live opportunity with a DEX
   leg fails (and three consecutive failures trip the kill switch). The bot is
   CEX-live-capable at most, which breaks the two-leg strategy.
2. Everything under NEEDS LIVE VALIDATION above.
3. No balance reconciliation against the exchange (deferred list).
4. Strategy economics unvalidated: fee constants are guesses; no evidence the
   65 bps threshold clears real costs; README's own 7-day-paper /
   tiny-size checklist still applies.
5. Operational baseline: TLS reverse proxy in front of the (now
   loopback-bound) API, trade-only IP-allowlisted keys, and the kill switch
   drill — per the README checklist.
