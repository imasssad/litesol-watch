import { describe, expect, it } from "vitest";

import {
  assertWithinLiveCap,
  bytesToBase64,
  exitReason,
  getTxFromOrder,
  tokensToBaseUnits,
  usdToUsdcBaseUnits,
} from "./lib.mjs";

describe("usdToUsdcBaseUnits", () => {
  it("converts USD to 6-decimal base units, floored", () => {
    expect(usdToUsdcBaseUnits(25)).toBe("25000000");
    expect(usdToUsdcBaseUnits(0.000001)).toBe("1");
    expect(usdToUsdcBaseUnits(1.9999999)).toBe("1999999");
  });
  it("rejects negatives and non-numbers", () => {
    expect(() => usdToUsdcBaseUnits(-1)).toThrow();
    expect(() => usdToUsdcBaseUnits("abc")).toThrow();
  });
});

describe("tokensToBaseUnits", () => {
  it("scales by the token's decimals (the original sent Math.floor(amount))", () => {
    // 595.238 tokens of a 6-decimal mint
    expect(tokensToBaseUnits(595.238, 6)).toBe("595238000");
    // the original bug: this is what actually went out - 595 base units
    expect(String(Math.floor(595.238))).toBe("595");
  });
  it("handles 9-decimal mints like SOL", () => {
    expect(tokensToBaseUnits(1.5, 9)).toBe("1500000000");
  });
  it("rejects bad inputs", () => {
    expect(() => tokensToBaseUnits(-1, 6)).toThrow();
    expect(() => tokensToBaseUnits(1, -1)).toThrow();
    expect(() => tokensToBaseUnits(1, 6.5)).toThrow();
    expect(() => tokensToBaseUnits(NaN, 6)).toThrow();
  });
});

describe("bytesToBase64", () => {
  it("matches btoa on small inputs", () => {
    const bytes = new Uint8Array([72, 101, 108, 108, 111]);
    expect(bytesToBase64(bytes)).toBe(btoa("Hello"));
  });
  it("survives inputs that break the spread form", () => {
    // String.fromCharCode(...bytes) throws RangeError at this size.
    const big = new Uint8Array(300_000).fill(65);
    expect(() => String.fromCharCode(...big)).toThrow(RangeError);
    const encoded = bytesToBase64(big);
    expect(encoded.length).toBe(Math.ceil(300_000 / 3) * 4);
  });
});

describe("getTxFromOrder", () => {
  it("reads every known response shape", () => {
    expect(getTxFromOrder({ transaction: "a" })).toBe("a");
    expect(getTxFromOrder({ swapTransaction: "b" })).toBe("b");
    expect(getTxFromOrder({ tx: "c" })).toBe("c");
    expect(getTxFromOrder({ serializedTransaction: "d" })).toBe("d");
    expect(getTxFromOrder({})).toBeUndefined();
    expect(getTxFromOrder(null)).toBeUndefined();
  });
});

describe("assertWithinLiveCap", () => {
  it("passes sizes at or under the cap", () => {
    expect(assertWithinLiveCap(10, 10)).toBe(10);
    expect(assertWithinLiveCap(0.5, 10)).toBe(0.5);
  });
  it("throws instead of clamping over-cap sizes", () => {
    expect(() => assertWithinLiveCap(25, 10)).toThrow(/hard cap/);
  });
  it("rejects zero, negative and NaN sizes", () => {
    expect(() => assertWithinLiveCap(0, 10)).toThrow();
    expect(() => assertWithinLiveCap(-5, 10)).toThrow();
    expect(() => assertWithinLiveCap(NaN, 10)).toThrow();
  });
});

describe("exitReason (take-profit / stop-loss / trailing-stop)", () => {
  const base = { entryPrice: 100, highestPrice: 100, takeProfit: 20, stopLoss: 8, trailingStop: 6 };

  it("take profit fires at or above the threshold", () => {
    expect(exitReason({ ...base, price: 120 })).toBe("take profit");
    expect(exitReason({ ...base, price: 119.9 })).toBeNull();
  });

  it("stop loss fires at or below the threshold", () => {
    expect(exitReason({ ...base, price: 92 })).toBe("stop loss");
    expect(exitReason({ ...base, price: 92.1 })).toBeNull();
  });

  it("trailing stop fires only while the position is still in profit", () => {
    // ran to 115, dropped 6% to 108.1: still +8.1% from entry -> trailing stop
    expect(exitReason({ ...base, highestPrice: 115, price: 108.1 })).toBe("trailing stop");
    // same drop but position underwater -> handled by stop loss, not trailing
    expect(exitReason({ ...base, highestPrice: 104, price: 97.7 })).toBeNull();
  });

  it("stop loss takes precedence over trailing when both would fire", () => {
    expect(exitReason({ ...base, highestPrice: 120, price: 91 })).toBe("stop loss");
  });

  it("is inert on invalid prices", () => {
    expect(exitReason({ ...base, price: 0 })).toBeNull();
    expect(exitReason({ ...base, entryPrice: 0, price: 100 })).toBeNull();
  });
});
