async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function authHeaders() {
  const token = document.getElementById('token')?.value;
  return token ? { 'X-API-Token': token } : {};
}

// Venue names, reasons and error strings are built from remote API responses and
// exception text. Interpolating them into innerHTML unescaped let a venue (or
// anything that can shape an error message) inject markup into the dashboard.
function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function setError(message) {
  const node = document.getElementById('error');
  if (!node) return;
  if (!message) {
    node.hidden = true;
    node.textContent = '';
    return;
  }
  node.hidden = false;
  node.textContent = message;
}

async function post(path) {
  try {
    const res = await fetch(path, { method: 'POST', headers: authHeaders() });
    if (!res.ok) {
      let detail = '';
      try {
        detail = (await res.json())?.detail ?? '';
      } catch {
        /* body was not JSON */
      }
      setError(`${path} failed: ${res.status}${detail ? ` - ${detail}` : ''}`);
    } else {
      setError('');
    }
  } catch (e) {
    setError(`${path} failed: ${e.message}`);
  }
  await refresh();
}

function card(label, value, cls = '') {
  return `<div class="card ${esc(cls)}"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`;
}

function table(el, rows, cols) {
  const node = document.getElementById(el);
  if (!node) return;
  if (!rows || rows.length === 0) {
    node.innerHTML = '<tr><td>No data yet</td></tr>';
    return;
  }
  const head =
    '<thead><tr>' + cols.map((c) => `<th>${esc(c.label)}</th>`).join('') + '</tr></thead>';
  const body = rows
    .map((r) => {
      const cells = cols
        .map((c) => `<td>${c.render ? c.render(r[c.key], r) : esc(r[c.key])}</td>`)
        .join('');
      return `<tr>${cells}</tr>`;
    })
    .join('');
  node.innerHTML = `${head}<tbody>${body}</tbody>`;
}

function money(v) {
  return v === null || v === undefined ? '' : `$${Number(v).toFixed(4)}`;
}

async function refresh() {
  let status, health, quotes, opportunities, trades;
  try {
    [status, health, quotes, opportunities, trades] = await Promise.all([
      getJson('/api/status'),
      getJson('/api/health'),
      getJson('/api/quotes'),
      getJson('/api/opportunities'),
      getJson('/api/trades'),
    ]);
  } catch (e) {
    // Without this the first failed poll threw out of the interval callback and
    // the dashboard froze on stale numbers with no indication why.
    setError(`Dashboard refresh failed: ${e.message}`);
    return;
  }
  setError('');

  const statusNode = document.getElementById('status');
  if (statusNode) {
    statusNode.innerHTML = [
      card(
        'Mode',
        status.live_execution_allowed ? 'LIVE EXECUTION' : 'SAFE / PAPER',
        status.live_execution_allowed ? 'dangerCard' : ''
      ),
      card('Running', status.running ? 'Yes' : 'No'),
      card('Kill Switch', status.kill_switch ? 'ON' : 'OFF', status.kill_switch ? 'dangerCard' : ''),
      card(
        'Auth',
        status.auth_configured ? 'Configured' : 'NOT SET',
        status.auth_configured ? '' : 'dangerCard'
      ),
      card('Pair', status.pair),
      card('Min Spread', `${status.min_net_spread_bps} bps`),
      card('Trade Size', `$${status.trade_size_usd}`),
      card('Daily PnL', money(status.daily_pnl_usd)),
      card('Failures', status.failed_trade_count),
    ].join('');
  }

  const healthNode = document.getElementById('health');
  if (healthNode) healthNode.textContent = JSON.stringify(health, null, 2);

  const killReason = document.getElementById('killReason');
  if (killReason) {
    if (status.kill_switch && status.kill_switch_reason) {
      killReason.hidden = false;
      killReason.textContent = `Kill switch reason: ${status.kill_switch_reason}`;
    } else {
      killReason.hidden = true;
      killReason.textContent = '';
    }
  }

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
    {
      key: 'executable',
      label: 'Executable',
      render: (v) => `<span class="badge ${v ? 'good' : 'bad'}">${esc(v)}</span>`,
    },
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
setInterval(() => {
  refresh().catch((e) => setError(`Dashboard refresh failed: ${e.message}`));
}, 3000);
