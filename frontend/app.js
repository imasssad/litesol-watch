async function getJson(path) {
  const res = await fetch(path);
  return res.json();
}

function authHeaders() {
  const token = document.getElementById('token')?.value;
  return token ? { 'X-API-Token': token } : {};
}

async function post(path) {
  const res = await fetch(path, { method: 'POST', headers: authHeaders() });
  if (!res.ok) alert(`Request failed: ${res.status}`);
  await refresh();
}

function card(label, value, cls='') {
  return `<div class="card ${cls}"><div class="label">${label}</div><div class="value">${value}</div></div>`;
}

function table(el, rows, cols) {
  const node = document.getElementById(el);
  if (!rows || rows.length === 0) {
    node.innerHTML = '<tr><td>No data yet</td></tr>';
    return;
  }
  node.innerHTML = '<thead><tr>' + cols.map(c => `<th>${c.label}</th>`).join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + cols.map(c => `<td>${c.render ? c.render(r[c.key], r) : (r[c.key] ?? '')}</td>`).join('') + '</tr>').join('') +
    '</tbody>';
}

function money(v) { return v === null || v === undefined ? '' : `$${Number(v).toFixed(4)}`; }

async function refresh() {
  const [status, health, quotes, opportunities, trades] = await Promise.all([
    getJson('/api/status'),
    getJson('/api/health'),
    getJson('/api/quotes'),
    getJson('/api/opportunities'),
    getJson('/api/trades'),
  ]);

  document.getElementById('status').innerHTML = [
    card('Mode', status.live_execution_allowed ? 'LIVE EXECUTION' : 'SAFE / PAPER', status.live_execution_allowed ? 'dangerCard' : ''),
    card('Running', status.running ? 'Yes' : 'No'),
    card('Kill Switch', status.kill_switch ? 'ON' : 'OFF', status.kill_switch ? 'dangerCard' : ''),
    card('Pair', status.pair),
    card('Min Spread', `${status.min_net_spread_bps} bps`),
    card('Trade Size', `$${status.trade_size_usd}`),
    card('Daily PnL', money(status.daily_pnl_usd)),
    card('Failures', status.failed_trade_count),
  ].join('');

  document.getElementById('health').textContent = JSON.stringify(health, null, 2);

  table('quotes', quotes, [
    { key: 'venue', label: 'Venue' },
    { key: 'pair', label: 'Pair' },
    { key: 'bid', label: 'Bid' },
    { key: 'ask', label: 'Ask' },
    { key: 'source', label: 'Source' },
    { key: 'ts', label: 'Time' },
  ]);

  table('opportunities', opportunities, [
    { key: 'ts', label: 'Time' },
    { key: 'direction', label: 'Direction' },
    { key: 'buy_price', label: 'Buy' },
    { key: 'sell_price', label: 'Sell' },
    { key: 'net_spread_bps', label: 'Net bps' },
    { key: 'expected_profit_usd', label: 'Expected $', render: money },
    { key: 'executable', label: 'Executable', render: v => `<span class="badge ${v ? 'good' : 'bad'}">${v}</span>` },
    { key: 'reason', label: 'Reason' },
  ]);

  table('trades', trades, [
    { key: 'ts', label: 'Time' },
    { key: 'mode', label: 'Mode' },
    { key: 'direction', label: 'Direction' },
    { key: 'trade_size_usd', label: 'Size $' },
    { key: 'expected_profit_usd', label: 'Expected $', render: money },
    { key: 'realized_profit_usd', label: 'Realized $', render: money },
    { key: 'status', label: 'Status' },
    { key: 'error', label: 'Error' },
  ]);
}

refresh();
setInterval(refresh, 3000);
