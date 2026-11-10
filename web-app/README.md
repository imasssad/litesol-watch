# web-app — `solana_dex_bot_test_app.jsx`

A single-file React dashboard for a 5-pair Solana DEX bot, shipped loose in the
uploaded archive. It is **a UI prototype, not a deployable app**, and its live
path has been disabled. Read this before changing it.

## It does not build in this repository

The file imports dependencies that are not vendored here and for which there is
no `package.json`, bundler config or Tailwind setup:

- `react`, `framer-motion`, `lucide-react`
- `@solana/wallet-adapter-react`, `@solana/wallet-adapter-react-ui`, `@solana/web3.js`
- `@/components/ui/card`, `@/components/ui/button` (a shadcn/ui-style alias)
- Tailwind utility classes throughout

It parses as valid JSX, but to run it you need a Vite/Next project with those
packages, the `@/*` path alias and Tailwind configured.

## Why live mode is off

`LIVE_MODE_SUPPORTED` is `false` at the top of the file, and the live-mode
checkbox is disabled.

The reason is not caution for its own sake. In live mode the original code:

1. submitted a **real** Jupiter swap on mainnet, then
2. recorded the resulting position — entry price, token amount, highest price —
   from `pair.price`, which comes from `randomWalk()`, a random number
   generator seeded from the hardcoded `DEFAULT_PAIRS` prices.

Every subsequent take-profit, stop-loss, trailing-stop and PnL figure in live
mode was therefore computed from invented prices while the swaps themselves were
real and irreversible. The daily-loss limit (`dailyLossLimit`) was enforced
against that same invented PnL, so the one risk control in the file could not
have worked in live mode either.

Re-enabling live mode is not a matter of flipping the constant. It requires:

- reading the actual fill (amount in, amount out, price) back from the confirmed
  transaction or from token-account balance deltas, and using that as the
  position's entry — never the simulated price;
- a real price feed for each pair (Jupiter price API or an on-chain oracle) in
  place of `randomWalk()`;
- replacing the random entry trigger (`Math.random() > 0.88`) with an actual
  signal;
- per-pair concurrency control: `busyPair` is a single global value, so one pair
  executing blocks every other pair, and rapid price ticks can still re-enter
  `buy`/`sell` before it is set;
- reconciliation for a swap that is submitted but never confirms.

## Defects fixed in this file

| Issue | Fix |
| --- | --- |
| The sell leg passed a human-readable token amount where Jupiter expects base units (`String(Math.floor(tokenAmount))`), making every sell ~10^decimals too small | `tokensToBaseUnits()` in `lib.mjs` plus a `decimals` field per pair |
| `btoa(String.fromCharCode(...signedTx.serialize()))` spreads the whole byte array as function arguments and throws on a large transaction | Chunked `bytesToBase64()` in `lib.mjs` |
| `sweepProfitSol()` transferred a hardcoded 0.001 SOL but returned the *intended* USD sweep amount, so the books recorded a sweep that never happened | Returns `0` and logs that it is a stub |
| No per-trade ceiling existed beyond each pair's editable `maxUsd` | `assertWithinLiveCap()` enforces a hard `MAX_LIVE_TRADE_USD = 10` at the swap entry point; it throws rather than clamping |
| Live mode was a bare checkbox | Enabling it (were `LIVE_MODE_SUPPORTED` ever flipped) now requires an explicit `window.confirm` naming the cap; the checkbox stays disabled regardless |

## Tests

The pure trading logic (unit conversion, base64, the TP/SL/trailing-stop
decision, the live cap) was extracted into `lib.mjs`, which the component
imports — so the tested code is the shipped code. Run them with:

```bash
cd web-app
npm install
npm test        # vitest, 16 tests; npm audit is clean
```

The TP/SL/trailing logic in `evaluate()` now routes through `exitReason()` from
`lib.mjs`. The remaining React-level hazards (stale closures over `positions`
in the `useEffect` that calls `evaluate`, and `busyPair` being one global value
rather than per-pair) are documented above and only matter if live mode is ever
rebuilt; in simulation they can at worst double-log a simulated sell.

## Endpoint status (checked November 2026)

`https://api.jup.ag/swap/v2` with `GET /order` + `POST /execute` is the
*current* Jupiter API — it superseded Ultra v1, and the response fields this
file reads (`transaction`, `requestId`, then `signedTransaction` to
`/execute`) match the current shape. Keyless access to `api.jup.ag` is heavily
rate-limited; production use needs a portal.jup.ag key in `x-api-key`.
Unverified against the live API from this environment: NEEDS LIVE VALIDATION.

## Relationship to the rest of the repository

This file is standalone. It does not talk to the FastAPI backend in `backend/`
and shares no code with it — it is a browser-side bot that signs its own swaps,
whereas the backend is a server-side CEX↔DEX scanner. The two were simply
bundled in the same archive.
