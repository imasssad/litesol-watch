/*
 * Solana DEX bot - UNBUILT PROTOTYPE. See web-app/README.md before touching this.
 *
 * Audit summary:
 *  - This file has no build setup in this repository (it imports framer-motion,
 *    lucide-react, @solana/wallet-adapter-*, @solana/web3.js and @/components/ui/*,
 *    none of which are vendored here). It does not compile as-is.
 *  - Live mode is DISABLED below (LIVE_MODE_SUPPORTED). It used to submit real
 *    mainnet swaps while recording entry price, token amount and PnL from
 *    randomWalk() - a random number generator - so every take-profit, stop-loss
 *    and PnL figure in live mode was fiction while the swaps were real.
 *    Re-enabling it requires reading actual fills back from the chain first.
 *  - Entries are random (Math.random() > 0.88). This is a UI demo, not a strategy.
 *
 * The simulation path is unchanged and safe to run.
 */
import React, { useMemo, useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Play, Pause, Wallet, TrendingUp, Shield, Send, RotateCcw, Activity, PlugZap, AlertTriangle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConnectionProvider, WalletProvider, useWallet } from "@solana/wallet-adapter-react";
import { WalletModalProvider, WalletMultiButton } from "@solana/wallet-adapter-react-ui";
import { VersionedTransaction, Connection, PublicKey, Transaction, SystemProgram, LAMPORTS_PER_SOL } from "@solana/web3.js";
import "@solana/wallet-adapter-react-ui/styles.css";

const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const SOL_MINT = "So11111111111111111111111111111111111111112";
const DEFAULT_RPC = "https://api.mainnet-beta.solana.com";
const JUPITER_SWAP_V2 = "https://api.jup.ag/swap/v2";

// Live execution is gated off. The surrounding bookkeeping reads prices from
// randomWalk(), not from actual swap results, so a live run would place real
// orders and then manage them against made-up numbers. Do not flip this to true
// without first reconciling positions from on-chain fills.
const LIVE_MODE_SUPPORTED = false;

const DEFAULT_PAIRS = [
  { id: 1, name: "PAIR 1", mint: "TOKEN_MINT_1_REPLACE", inputMint: USDC_MINT, outputMint: "TOKEN_MINT_1_REPLACE", decimals: 6, price: 0.042, maxUsd: 25, takeProfit: 20, stopLoss: 8, trailingStop: 6, slippageBps: 150, enabled: true },
  { id: 2, name: "PAIR 2", mint: "TOKEN_MINT_2_REPLACE", inputMint: USDC_MINT, outputMint: "TOKEN_MINT_2_REPLACE", decimals: 6, price: 0.118, maxUsd: 25, takeProfit: 25, stopLoss: 10, trailingStop: 8, slippageBps: 200, enabled: true },
  { id: 3, name: "PAIR 3", mint: "TOKEN_MINT_3_REPLACE", inputMint: USDC_MINT, outputMint: "TOKEN_MINT_3_REPLACE", decimals: 6, price: 0.0065, maxUsd: 25, takeProfit: 30, stopLoss: 12, trailingStop: 10, slippageBps: 250, enabled: true },
  { id: 4, name: "PAIR 4", mint: "TOKEN_MINT_4_REPLACE", inputMint: USDC_MINT, outputMint: "TOKEN_MINT_4_REPLACE", decimals: 6, price: 1.24, maxUsd: 25, takeProfit: 15, stopLoss: 6, trailingStop: 5, slippageBps: 100, enabled: true },
  { id: 5, name: "PAIR 5", mint: "TOKEN_MINT_5_REPLACE", inputMint: USDC_MINT, outputMint: "TOKEN_MINT_5_REPLACE", decimals: 6, price: 0.33, maxUsd: 25, takeProfit: 22, stopLoss: 9, trailingStop: 7, slippageBps: 180, enabled: true },
];

function randomWalk(price) {
  const change = (Math.random() - 0.48) * 0.06;
  return Math.max(price * (1 + change), 0.000001);
}

function money(n) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(n || 0);
}

function pct(n) {
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function isValidMint(value) {
  try {
    new PublicKey(value);
    return !value.includes("REPLACE");
  } catch {
    return false;
  }
}

function usdToUsdcBaseUnits(usd) {
  return Math.floor(Number(usd) * 1_000_000).toString();
}

// A token amount is in human units; the Jupiter API wants base units. Sending
// Math.floor(tokenAmount) treated 595.2 tokens as 595 base units, i.e. 0.000595
// of a 6-decimal token - a sell ~10^6 times too small.
function tokensToBaseUnits(amount, decimals = 6) {
  return Math.floor(Number(amount) * 10 ** decimals).toString();
}

// Chunked so a large transaction cannot blow the argument limit the way
// String.fromCharCode(...bytes) does on a big spread.
function bytesToBase64(bytes) {
  let binary = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

function getTxFromOrder(order) {
  return order?.transaction || order?.swapTransaction || order?.tx || order?.serializedTransaction;
}

async function jupiterOrder({ inputMint, outputMint, amount, taker, slippageBps }) {
  const params = new URLSearchParams({ inputMint, outputMint, amount, taker, slippageBps: String(slippageBps || 150) });
  const res = await fetch(`${JUPITER_SWAP_V2}/order?${params.toString()}`);
  const json = await res.json();
  if (!res.ok || json.error) throw new Error(json.error || json.message || "Jupiter order failed");
  return json;
}

async function jupiterExecute({ signedTransaction, requestId }) {
  const res = await fetch(`${JUPITER_SWAP_V2}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ signedTransaction, requestId }),
  });
  const json = await res.json();
  if (!res.ok || json.error) throw new Error(json.error || json.message || "Jupiter execute failed");
  return json;
}

function AppShell() {
  const [rpcUrl, setRpcUrl] = useState(DEFAULT_RPC);
  const endpoint = rpcUrl || DEFAULT_RPC;
  return (
    <ConnectionProvider endpoint={endpoint}>
      <WalletProvider wallets={[]} autoConnect>
        <WalletModalProvider>
          <SolanaDexBotLiveApp rpcUrl={endpoint} setRpcUrl={setRpcUrl} />
        </WalletModalProvider>
      </WalletProvider>
    </ConnectionProvider>
  );
}

export default AppShell;

function SolanaDexBotLiveApp({ rpcUrl, setRpcUrl }) {
  const wallet = useWallet();
  const { publicKey, signTransaction, sendTransaction, connected } = wallet;
  const [connection, setConnection] = useState(() => new Connection(rpcUrl, "confirmed"));
  const [running, setRunning] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [pairs, setPairs] = useState(DEFAULT_PAIRS);
  const [positions, setPositions] = useState({});
  const [tradingBalance, setTradingBalance] = useState(5000);
  const [profitWallet, setProfitWallet] = useState(0);
  const [profitWalletAddress, setProfitWalletAddress] = useState("");
  const [logs, setLogs] = useState(["Bot loaded. Simulation mode is ON by default."]);
  const [sweepPct, setSweepPct] = useState(80);
  const [minSweep, setMinSweep] = useState(25);
  const [dailyLossLimit, setDailyLossLimit] = useState(500);
  const [realizedPnl, setRealizedPnl] = useState(0);
  const [walletSol, setWalletSol] = useState(0);
  const [busyPair, setBusyPair] = useState(null);

  useEffect(() => setConnection(new Connection(rpcUrl, "confirmed")), [rpcUrl]);

  const openPositions = Object.values(positions).filter((p) => p.status === "OPEN");

  const portfolioValue = useMemo(() => {
    const posValue = openPositions.reduce((sum, pos) => {
      const pair = pairs.find((p) => p.id === pos.pairId);
      return sum + (pair ? pair.price * pos.tokenAmount : 0);
    }, 0);
    return tradingBalance + posValue + profitWallet;
  }, [tradingBalance, profitWallet, openPositions, pairs]);

  const log = useCallback((message) => {
    const ts = new Date().toLocaleTimeString();
    setLogs((prev) => [`${ts} — ${message}`, ...prev].slice(0, 18));
  }, []);

  async function refreshWalletSol() {
    if (!publicKey) return;
    const lamports = await connection.getBalance(publicKey);
    setWalletSol(lamports / LAMPORTS_PER_SOL);
  }

  useEffect(() => {
    refreshWalletSol().catch(() => {});
  }, [publicKey, connection]);

  async function executeJupiterSwap(pair, side, usdAmount) {
    if (!LIVE_MODE_SUPPORTED) {
      throw new Error(
        "Live execution is disabled in this prototype: positions and PnL are tracked " +
          "from simulated prices, not real fills. See web-app/README.md."
      );
    }
    if (!connected || !publicKey || !signTransaction) throw new Error("Connect a Solana wallet first.");
    if (!isValidMint(pair.outputMint) || !isValidMint(pair.inputMint)) throw new Error(`${pair.name}: replace placeholder mint addresses first.`);

    const inputMint = side === "BUY" ? pair.inputMint : pair.outputMint;
    const outputMint = side === "BUY" ? pair.outputMint : pair.inputMint;
    const amount =
      side === "BUY"
        ? usdToUsdcBaseUnits(usdAmount)
        : tokensToBaseUnits(usdAmount, pair.decimals ?? 6);

    const order = await jupiterOrder({
      inputMint,
      outputMint,
      amount,
      taker: publicKey.toBase58(),
      slippageBps: pair.slippageBps,
    });

    const base64Tx = getTxFromOrder(order);
    if (!base64Tx) throw new Error("Jupiter response did not include a transaction. Check API response shape.");

    const tx = VersionedTransaction.deserialize(Uint8Array.from(atob(base64Tx), (c) => c.charCodeAt(0)));
    const signedTx = await signTransaction(tx);
    const signedBase64 = bytesToBase64(signedTx.serialize());
    const result = await jupiterExecute({ signedTransaction: signedBase64, requestId: order.requestId });

    return {
      signature: result.signature || result.txid || result.transactionSignature,
      order,
      result,
    };
  }

  async function sweepProfitSol(usdProfit) {
    if (!liveMode || !publicKey || !sendTransaction || !profitWalletAddress || usdProfit <= minSweep) return 0;
    const intendedSweepUsd = usdProfit * (sweepPct / 100);
    const lamports = Math.floor(0.001 * LAMPORTS_PER_SOL);
    const toPubkey = new PublicKey(profitWalletAddress);
    const tx = new Transaction().add(SystemProgram.transfer({ fromPubkey: publicKey, toPubkey, lamports }));
    const sig = await sendTransaction(tx, connection);
    await connection.confirmTransaction(sig, "confirmed");
    log(
      `Profit sweep STUB: sent a fixed 0.001 SOL, not the ${money(intendedSweepUsd)} ` +
        `of profit it was asked to sweep. Tx: ${sig}`
    );
    // Report what actually moved (nothing, in USD terms), not what was intended.
    // Returning intendedSweepUsd here made the books claim a sweep that never happened.
    return 0;
  }

  async function buy(pair) {
    if (positions[pair.id]?.status === "OPEN" || busyPair === pair.id) return;
    if (!pair.enabled) return log(`${pair.name}: disabled.`);
    setBusyPair(pair.id);
    try {
      if (liveMode) {
        const live = await executeJupiterSwap(pair, "BUY", pair.maxUsd);
        log(`${pair.name}: LIVE BUY submitted. Tx: ${live.signature || "pending"}`);
      } else {
        if (tradingBalance < pair.maxUsd) return log(`${pair.name}: skipped buy, insufficient simulated balance.`);
        setTradingBalance((b) => b - pair.maxUsd);
      }

      const fee = pair.maxUsd * 0.002;
      const tokenAmount = (pair.maxUsd - fee) / pair.price;
      setPositions((prev) => ({
        ...prev,
        [pair.id]: { pairId: pair.id, status: "OPEN", entryPrice: pair.price, entryUsd: pair.maxUsd, tokenAmount, highestPrice: pair.price, openedAt: Date.now() },
      }));
      log(`${pair.name}: position opened at $${pair.price.toFixed(6)}.`);
    } catch (e) {
      log(`${pair.name}: BUY failed — ${e.message}`);
    } finally {
      setBusyPair(null);
      refreshWalletSol().catch(() => {});
    }
  }

  async function sell(pair, reason) {
    const pos = positions[pair.id];
    if (!pos || pos.status !== "OPEN" || busyPair === pair.id) return;
    setBusyPair(pair.id);
    try {
      const grossValue = pos.tokenAmount * pair.price;
      const fee = grossValue * 0.002;
      const netValue = grossValue - fee;
      const pnl = netValue - pos.entryUsd;
      const sweepAmount = pnl > minSweep ? pnl * (sweepPct / 100) : 0;
      const returnedToTrading = netValue - Math.max(sweepAmount, 0);

      if (liveMode) {
        await executeJupiterSwap(pair, "SELL", pos.tokenAmount);
        if (pnl > minSweep) await sweepProfitSol(pnl);
        log(`${pair.name}: LIVE SELL submitted via ${reason}. Estimated PnL ${money(pnl)}.`);
      } else {
        setTradingBalance((b) => b + returnedToTrading);
        setProfitWallet((b) => b + Math.max(sweepAmount, 0));
        log(`${pair.name}: simulated sell via ${reason}. PnL ${money(pnl)}. Swept ${money(Math.max(sweepAmount, 0))}.`);
      }

      setRealizedPnl((p) => p + pnl);
      setPositions((prev) => ({ ...prev, [pair.id]: { ...pos, status: "CLOSED", closedAt: Date.now(), exitPrice: pair.price, pnl } }));
    } catch (e) {
      log(`${pair.name}: SELL failed — ${e.message}`);
    } finally {
      setBusyPair(null);
      refreshWalletSol().catch(() => {});
    }
  }

  function evaluate(pair) {
    if (!pair.enabled || busyPair === pair.id) return;
    if (realizedPnl < -dailyLossLimit) {
      log(`Risk pause: daily loss limit reached.`);
      setRunning(false);
      return;
    }
    const pos = positions[pair.id];
    if (!pos || pos.status !== "OPEN") {
      const buyChance = Math.random();
      if (buyChance > 0.88 && openPositions.length < 5) buy(pair);
      return;
    }

    const changePct = ((pair.price - pos.entryPrice) / pos.entryPrice) * 100;
    const trailingDrop = ((pos.highestPrice - pair.price) / pos.highestPrice) * 100;
    if (pair.price > pos.highestPrice) setPositions((prev) => ({ ...prev, [pair.id]: { ...pos, highestPrice: pair.price } }));
    if (changePct >= pair.takeProfit) sell(pair, "take profit");
    else if (changePct <= -pair.stopLoss) sell(pair, "stop loss");
    else if (trailingDrop >= pair.trailingStop && changePct > 0) sell(pair, "trailing stop");
  }

  useEffect(() => {
    if (!running) return;
    const interval = setInterval(() => setPairs((prev) => prev.map((p) => ({ ...p, price: randomWalk(p.price) }))), liveMode ? 5000 : 1200);
    return () => clearInterval(interval);
  }, [running, liveMode]);

  useEffect(() => {
    if (!running) return;
    pairs.forEach(evaluate);
  }, [pairs, running]);

  function reset() {
    setRunning(false);
    setPairs(DEFAULT_PAIRS);
    setPositions({});
    setTradingBalance(5000);
    setProfitWallet(0);
    setRealizedPnl(0);
    setLogs(["Simulation reset."]);
  }

  function updatePair(id, field, value) {
    setPairs((prev) => prev.map((p) => (p.id === id ? { ...p, [field]: value } : p)));
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        <motion.div initial={{ opacity: 0, y: -12 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <div className="text-sm text-emerald-300 font-medium">Solana DEX Bot — Jupiter Live Execution Ready</div>
            <h1 className="text-3xl md:text-5xl font-bold tracking-tight">5-Pair Auto Buy/Sell Bot</h1>
            <p className="text-slate-400 mt-2 max-w-2xl">Starts in safe simulation mode. Live mode signs Jupiter swap transactions with your connected wallet. Use tiny sizes until fully tested.</p>
          </div>
          <div className="flex flex-wrap gap-3 items-center">
            <WalletMultiButton className="!rounded-2xl" />
            <Button onClick={() => setRunning(!running)} className="rounded-2xl px-5 py-6 text-base">
              {running ? <Pause className="mr-2 h-5 w-5" /> : <Play className="mr-2 h-5 w-5" />}
              {running ? "Pause" : "Start"}
            </Button>
            <Button variant="secondary" onClick={reset} className="rounded-2xl px-5 py-6 text-base"><RotateCcw className="mr-2 h-5 w-5" /> Reset</Button>
          </div>
        </motion.div>

        <Card className="bg-amber-950/50 border-amber-800 rounded-2xl">
          <CardContent className="p-4 flex gap-3 text-sm text-amber-100">
            <AlertTriangle className="h-5 w-5 shrink-0 text-amber-300" />
            <div>
              <strong>Prototype only:</strong> live execution is disabled. This UI tracks
              positions and PnL from simulated prices, so a live run would place real,
              irreversible mainnet swaps and then manage them against numbers that do not
              reflect actual fills. Entries are random, not a strategy. See
              <code className="mx-1">web-app/README.md</code> for what must be built before
              any live use.
            </div>
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
          <Metric icon={<Wallet />} label="Sim Trading Wallet" value={money(tradingBalance)} />
          <Metric icon={<Send />} label="Sim Profit Wallet" value={money(profitWallet)} />
          <Metric icon={<TrendingUp />} label="Realized PnL" value={money(realizedPnl)} positive={realizedPnl >= 0} />
          <Metric icon={<Activity />} label="Sim Portfolio" value={money(portfolioValue)} />
          <Metric icon={<PlugZap />} label="Wallet SOL" value={`${walletSol.toFixed(4)} SOL`} />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Card className="lg:col-span-2 bg-slate-900/80 border-slate-800 rounded-2xl shadow-xl">
            <CardContent className="p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-xl font-semibold">Live Pairs</h2>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-2 text-sm text-slate-300">
                    <input
                      type="checkbox"
                      checked={liveMode}
                      disabled={!LIVE_MODE_SUPPORTED}
                      onChange={(e) => setLiveMode(e.target.checked)}
                    />{" "}
                    {LIVE_MODE_SUPPORTED ? "Live mode" : "Live mode (disabled - see README)"}
                  </label>
                  <span className={`text-xs px-3 py-1 rounded-full ${running ? "bg-emerald-500/20 text-emerald-300" : "bg-slate-700 text-slate-300"}`}>{running ? "RUNNING" : "PAUSED"}</span>
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-slate-400 border-b border-slate-800">
                    <tr><th className="text-left py-3">Pair</th><th className="text-left py-3">Output Mint</th><th className="text-left py-3">Price</th><th className="text-left py-3">Position</th><th className="text-left py-3">PnL</th><th className="text-right py-3">Action</th></tr>
                  </thead>
                  <tbody>
                    {pairs.map((pair) => {
                      const pos = positions[pair.id];
                      const isOpen = pos?.status === "OPEN";
                      const unrealized = isOpen ? pair.price * pos.tokenAmount - pos.entryUsd : pos?.pnl || 0;
                      const unrealizedPct = isOpen ? ((pair.price - pos.entryPrice) / pos.entryPrice) * 100 : 0;
                      return (
                        <tr key={pair.id} className="border-b border-slate-800/70">
                          <td className="py-4"><div className="font-medium">{pair.name}</div><div className="text-xs text-slate-500">TP {pair.takeProfit}% · SL {pair.stopLoss}% · Trail {pair.trailingStop}%</div></td>
                          <td className="py-4"><input value={pair.outputMint} onChange={(e) => updatePair(pair.id, "outputMint", e.target.value)} className="w-72 bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs font-mono" /></td>
                          <td className="py-4 font-mono">${pair.price.toFixed(6)}</td>
                          <td className="py-4">{isOpen ? <span className="text-emerald-300">OPEN {money(pos.entryUsd)}</span> : <span className="text-slate-500">None</span>}</td>
                          <td className={`py-4 ${unrealized >= 0 ? "text-emerald-300" : "text-red-300"}`}>{isOpen ? `${money(unrealized)} / ${pct(unrealizedPct)}` : pos ? money(pos.pnl) : "—"}</td>
                          <td className="py-4 text-right space-x-2">{!isOpen ? <Button size="sm" variant="secondary" disabled={busyPair === pair.id} onClick={() => buy(pair)} className="rounded-xl">Buy</Button> : <Button size="sm" disabled={busyPair === pair.id} onClick={() => sell(pair, "manual")} className="rounded-xl">Sell</Button>}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <div className="space-y-6">
            <Card className="bg-slate-900/80 border-slate-800 rounded-2xl shadow-xl"><CardContent className="p-5 space-y-4">
              <div className="flex items-center gap-2"><Shield className="h-5 w-5 text-emerald-300" /><h2 className="text-xl font-semibold">Execution Settings</h2></div>
              <TextInput label="RPC URL" value={rpcUrl} setValue={setRpcUrl} />
              <TextInput label="Profit wallet address" value={profitWalletAddress} setValue={setProfitWalletAddress} />
              <Control label="Profit sweep %" value={sweepPct} setValue={setSweepPct} min={0} max={100} suffix="%" />
              <Control label="Minimum sweep" value={minSweep} setValue={setMinSweep} min={0} max={250} suffix="$" />
              <Control label="Daily loss limit" value={dailyLossLimit} setValue={setDailyLossLimit} min={50} max={2000} suffix="$" />
              <div className="text-xs text-slate-500 pt-2">Current live sweep implementation sends a tiny SOL test transfer. Production should swap profits to USDC, calculate exact token balances, then transfer USDC to the profit wallet.</div>
            </CardContent></Card>

            <Card className="bg-slate-900/80 border-slate-800 rounded-2xl shadow-xl"><CardContent className="p-5">
              <h2 className="text-xl font-semibold mb-4">Event Log</h2>
              <div className="space-y-2 max-h-96 overflow-y-auto pr-1">{logs.map((l, i) => <div key={i} className="text-xs bg-slate-950 border border-slate-800 rounded-xl p-3 text-slate-300">{l}</div>)}</div>
            </CardContent></Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function Metric({ icon, label, value, positive }) {
  return <Card className="bg-slate-900/80 border-slate-800 rounded-2xl shadow-xl"><CardContent className="p-5 flex items-center gap-4"><div className="p-3 rounded-2xl bg-slate-800 text-emerald-300">{React.cloneElement(icon, { className: "h-5 w-5" })}</div><div><div className="text-xs uppercase tracking-wide text-slate-500">{label}</div><div className={`text-2xl font-bold ${positive === false ? "text-red-300" : "text-slate-100"}`}>{value}</div></div></CardContent></Card>;
}

function Control({ label, value, setValue, min, max, suffix }) {
  return <div><div className="flex justify-between text-sm mb-2"><span className="text-slate-300">{label}</span><span className="text-emerald-300 font-medium">{suffix === "$" ? `$${value}` : `${value}${suffix}`}</span></div><input type="range" min={min} max={max} value={value} onChange={(e) => setValue(Number(e.target.value))} className="w-full" /></div>;
}

function TextInput({ label, value, setValue }) {
  return <label className="block"><div className="text-sm text-slate-300 mb-2">{label}</div><input value={value} onChange={(e) => setValue(e.target.value)} className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs" /></label>;
}
