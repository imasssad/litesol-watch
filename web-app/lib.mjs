// Pure trading-logic helpers for the Solana DEX bot prototype.
// Kept free of React and wallet-adapter imports so they can be unit-tested
// with Vitest without a browser or a build of the main component.

/** USD -> USDC base units (6 decimals), floored, as the string Jupiter wants. */
export function usdToUsdcBaseUnits(usd) {
  const n = Number(usd);
  if (!Number.isFinite(n) || n < 0) throw new Error(`invalid USD amount: ${usd}`);
  return Math.floor(n * 1_000_000).toString();
}

/**
 * Human-readable token amount -> base units for the token's decimals.
 * The original code sent Math.floor(tokenAmount) - i.e. treated 595.2 tokens
 * as 595 base units, a sell ~10^decimals too small.
 */
export function tokensToBaseUnits(amount, decimals = 6) {
  const n = Number(amount);
  const d = Number(decimals);
  if (!Number.isFinite(n) || n < 0) throw new Error(`invalid token amount: ${amount}`);
  if (!Number.isInteger(d) || d < 0 || d > 18) throw new Error(`invalid decimals: ${decimals}`);
  return Math.floor(n * 10 ** d).toString();
}

/** Chunked base64 so a large transaction cannot blow the argument limit. */
export function bytesToBase64(bytes) {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

/** The transaction field across Jupiter response shapes. */
export function getTxFromOrder(order) {
  return order?.transaction || order?.swapTransaction || order?.tx || order?.serializedTransaction;
}

/**
 * Hard per-trade cap for live execution, independent of per-pair settings.
 * Throws rather than clamping: a silently reduced size would desync the
 * position bookkeeping from the actual fill.
 */
export function assertWithinLiveCap(usdAmount, capUsd) {
  const n = Number(usdAmount);
  if (!Number.isFinite(n) || n <= 0) throw new Error(`invalid trade size: ${usdAmount}`);
  if (n > capUsd) {
    throw new Error(`live trade of $${n} exceeds the hard cap of $${capUsd}`);
  }
  return n;
}

/** Exit decision for an open position. Pure so TP/SL/trailing can be tested. */
export function exitReason({ price, entryPrice, highestPrice, takeProfit, stopLoss, trailingStop }) {
  if (!(entryPrice > 0) || !(price > 0)) return null;
  const changePct = ((price - entryPrice) / entryPrice) * 100;
  if (changePct >= takeProfit) return "take profit";
  if (changePct <= -stopLoss) return "stop loss";
  if (highestPrice > 0) {
    const trailingDrop = ((highestPrice - price) / highestPrice) * 100;
    if (trailingDrop >= trailingStop && changePct > 0) return "trailing stop";
  }
  return null;
}
