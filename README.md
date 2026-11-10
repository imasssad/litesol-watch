# Crypto Arbitrage Bot - Production v1

Production-oriented CEX <-> DEX arbitrage bot scaffold with a web dashboard.

> **Read [`AUDIT.md`](AUDIT.md) first.** The uploaded code did not start, shipped
> unauthenticated control endpoints, and its pre-trade spread check did not work.
> Those are fixed; `AUDIT.md` lists all 22 findings and what remains unverified.
>
> The React bot in [`web-app/`](web-app/README.md) is a separate, unbuildable
> prototype with live execution disabled.

## What changed from v1

- Live market data adapters for Binance Spot and Jupiter quotes
- Double-gated live execution: `LIVE_TRADING=true` and `EXECUTION_ENABLED=true`
- Authenticated control endpoints via `X-API-Token`
- SQLite persistence for opportunities/trades
- Pre-trade quote freshness checks
- Pre-trade requote before execution
- Max trade size, daily loss, quote age, consecutive failure limits
- Kill switch
- Venue health endpoint
- Dockerfile and docker-compose

## Important safety note

This project is suitable for paper trading and careful, small-size live testing.
It is not audited custody software, and it has never placed a live order under
test — see the limitations at the end of `AUDIT.md`. Binance live orders are implemented. Jupiter live swap signing is intentionally blocked behind the connector boundary until you integrate an audited Solana signer, MPC wallet, Fireblocks, HSM, or equivalent custody flow.

Default mode is safe/paper:

```env
LIVE_TRADING=false
EXECUTION_ENABLED=false
```

Live execution requires both to be true.

## Quick start

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set a long random API_AUTH_TOKEN in .env, or the control endpoints stay
# disabled (HTTP 503). They are never open by default.
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://localhost:8000
```

### Run it offline

No venue access or API keys needed - synthetic prices, paper trades only:

```env
CEX_VENUE=mock
DEX_VENUE=mock
MIN_NET_SPREAD_BPS=1
```

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

## Docker

```bash
cp backend/.env.example backend/.env
# edit backend/.env
mkdir -p data
docker compose up --build
```

## Production checklist before enabling live trading

1. Use exchange API keys with trading-only permission; no withdrawals.
2. Set IP allowlists on exchange keys.
3. Set `API_AUTH_TOKEN` to a long random secret. The app refuses to start with
   live execution enabled while it is still the placeholder.
4. Start with `LIVE_TRADING=false` for at least 7 days of paper logs.
5. Use very small `TRADE_SIZE_USD` first.
6. Confirm venue health is stable.
7. Confirm expected vs realized PnL on tiny trades.
8. Implement audited DEX signing before enabling DEX live swaps.
9. Run behind TLS/reverse proxy; never expose without auth.
10. Keep kill switch visible and tested.

## API controls

Control endpoints are disabled (HTTP 503) until `API_AUTH_TOKEN` is set, and
reject a missing or wrong token with HTTP 401. They require:

```bash
-H "X-API-Token: your-token"
```

Examples:

```bash
curl -X POST http://localhost:8000/api/kill-switch/on -H "X-API-Token: your-token"
curl -X POST http://localhost:8000/api/scan-once -H "X-API-Token: your-token"
```

## Env settings

See `backend/.env.example`.

Key live settings:

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
SOLANA_RPC_URL=
SOLANA_WALLET_PUBLIC_KEY=
```

## Strategy

The bot uses a prefunded inventory model:

- Buy on cheaper venue
- Sell on richer venue
- Log realized/expected PnL (realized is net of the fee and gas estimates)
- Requote and fully re-price both legs immediately before firing
- Stop on failures, stale quotes, max loss, or manual kill switch
- Halt and flag for manual reconciliation if one leg fills and the other does not

This is safer than relying on immediate cross-venue withdrawals/bridges.

## Added CEX support: MEXC + BingX

This build includes optional MEXC Spot and BingX Spot connectors via CCXT. Binance remains available as the native adapter.

Select the CEX in `backend/.env`:

```env
# Options: binance, mexc, bingx
CEX_VENUE=mexc
PAIR=SOL/USDT
BASE_ASSET=SOL
QUOTE_ASSET=USDT
MEXC_API_KEY=...
MEXC_API_SECRET=...
```

For BingX:

```env
CEX_VENUE=bingx
PAIR=SOL/USDT
BASE_ASSET=SOL
QUOTE_ASSET=USDT
BINGX_API_KEY=...
BINGX_API_SECRET=...
BINGX_API_PASSWORD=
```

Notes:
- Many MEXC/BingX spot markets are quoted in `USDT`, not `USDC`; set `QUOTE_ASSET=USDT` when needed.
- `PAIR` must agree with `BASE_ASSET`/`QUOTE_ASSET`; the app refuses to start otherwise.
- Live orders remain blocked unless both `LIVE_TRADING=true` and `EXECUTION_ENABLED=true` are set.
- Use trading-only API keys, IP allowlists, and disabled withdrawals.
- The MEXC and BingX adapters use CCXT for market data and spot market orders; validate precision/min-notional rules on test size before scaling.
