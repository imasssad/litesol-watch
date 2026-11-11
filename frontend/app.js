// --- token handling ---------------------------------------------------------
// sessionStorage, not localStorage: the token survives a page refresh in this
// tab but is gone when the tab closes, and never reaches another tab or disk
// profile. Every read is wrapped because storage can throw in private mode.
const TOKEN_KEY = 'arbbot.token';

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

function writeToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_KEY, value);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable; the in-memory value below still works */
  }
}

let token = readToken();
let pollTimer = null;

function authHeaders() {
  return token ? { 'X-API-Token': token } : {};
}

// --- helpers ----------------------------------------------------------------
async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// Venue names, reasons and error strings are built from remote API responses
// and exception text. Interpolating them into innerHTML unescaped let a venue
// (or anything that can shape an error message) inject markup here.
function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function show(id, message) {
  const node = document.getElementById(id);
  if (!node) return;
  if (!message) {
    node.hidden = true;
    node.textContent = '';
    return;
  }
  node.hidden = false;
  node.textContent = message;
}

const setError = (m) => show('error', m);

// --- gate -------------------------------------------------------------------
function showGate(message) {
  document.getElementById('gate').hidden = false;
  document.getElementById('app').hidden = true;
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  show('gateError', message || '');
}

function showApp() {
  document.getElementById('gate').hidden = true;
  document.getElementById('app').hidden = false;
  refresh();
  if (!pollTimer) {
    pollTimer = setInterval(() => {
      refresh().catch((e) => setError(`Dashboard refresh failed: ${e.message}`));
    }, 3000);
  }
}

// Verifies a token with no side effect. Every other protected endpoint changes
// state, so none of them can be used merely to test a token.
async function verifyToken(candidate) {
  const res = await fetch('/api/auth/verify', {
    headers: candidate ? { 'X-API-Token': candidate } : {},
  });
  if (res.ok) return { ok: true };
  let detail = '';
  try {
    detail = (await res.json())?.detail ?? '';
  } catch {
    /* body was not JSON */
  }
  return { ok: false, status: res.status, detail };
}

async function attemptUnlock(candidate) {
  if (!candidate) {
    show('gateError', 'Enter the API token from backend/.env on the server.');
    return;
  }
  const button = document.getElementById('unlock');
  button.disabled = true;
  button.textContent = 'Connecting…';
  try {
    const result = await verifyToken(candidate);
    if (result.ok) {
      token = candidate;
      writeToken(candidate);
      show('gateError', '');
      showApp();
      return;
    }
    if (result.status === 503) {
      show('gateError', result.detail || 'Control endpoints are disabled: API_AUTH_TOKEN is not set on the server.');
    } else {
      show('gateError', 'That token was rejected. Check API_AUTH_TOKEN in backend/.env.');
    }
  } catch (e) {
    show('gateError', `Could not reach the bot: ${e.message}`);
  } finally {
    button.disabled = false;
    button.innerHTML = 'Open workspace <span aria-hidden="true">&rarr;</span>';
  }
}

async function probeConnection() {
  const dot = document.getElementById('gateDot');
  try {
    const status = await getJson('/api/status');
    dot.className = 'dot online';
    document.getElementById('gateStatus').textContent = status.live_execution_allowed
      ? 'Bot online · LIVE EXECUTION ENABLED'
      : 'Bot online · paper mode';
  } catch {
    dot.className = 'dot offline';
    document.getElementById('gateStatus').textContent = 'Bot unreachable — is the container running?';
  }
}

// --- dashboard --------------------------------------------------------------
async function post(path) {
  try {
    const res = await fetch(path, { method: 'POST', headers: authHeaders() });
    if (res.status === 401 || res.status === 503) {
      token = '';
      writeToken('');
      showGate('Your token is no longer accepted. Paste it again.');
      return;
    }
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
  // data-metric is a stable hook for tests and scripts: the visible label is
  // uppercased by CSS, so matching on rendered text is unreliable.
  const metric = label.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  return (
    `<div class="card ${esc(cls)}" data-metric="${esc(metric)}">` +
    `<div class="label">${esc(label)}</div>` +
    `<div class="value">${esc(value)}</div></div>`
  );
}

function table(el, rows, cols) {
  const node = document.getElementById(el);
  if (!node) return;

  const counter = document.getElementById(`${el}Count`);
  if (counter) counter.textContent = rows && rows.length ? `${rows.length}` : '0';

  if (!rows || rows.length === 0) {
    node.innerHTML = `<tbody><tr><td class="empty" colspan="${cols.length}">No data yet</td></tr></tbody>`;
    return;
  }
  const head =
    '<thead><tr>' + cols.map((c) => `<th>${esc(c.label)}</th>`).join('') + '</tr></thead>';
  const body = rows
    .map((r) => {
      const cells = cols
        .map((c) => {
          const value = c.render ? c.render(r[c.key], r) : esc(r[c.key]);
          const cls = typeof c.cls === 'function' ? c.cls(r[c.key], r) : c.cls || '';
          // data-label drives the stacked card layout under 680px, where a
          // horizontal scrollbar would be unusable.
          return `<td class="${esc(cls)}" data-label="${esc(c.label)}">${value}</td>`;
        })
        .join('');
      return `<tr>${cells}</tr>`;
    })
    .join('');
  node.innerHTML = `${head}<tbody>${body}</tbody>`;
}

function signed(v) {
  if (v === null || v === undefined || v === '') return '';
  return Number(v) >= 0 ? 'num pos' : 'num neg';
}

// Full ISO timestamps are unreadable in a dense table; keep date only when
// the row is not from today.
function shortTime(v) {
  if (!v) return '';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour12: false });
  return sameDay ? time : `${d.toLocaleDateString()} ${time}`;
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
    // Without this, the first failed poll threw out of the interval callback
    // and the dashboard froze on stale numbers with no indication why.
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
      card('Running', status.running ? 'Yes' : 'No', status.running ? 'goodCard' : ''),
      card('Kill Switch', status.kill_switch ? 'ON' : 'OFF', status.kill_switch ? 'dangerCard' : ''),
      card('Pair', status.pair),
      card('Min Spread', `${status.min_net_spread_bps} bps`),
      card('Trade Size', `$${status.trade_size_usd}`),
      card(
        'Daily PnL',
        money(status.daily_pnl_usd),
        status.daily_pnl_usd < 0 ? 'dangerCard' : status.daily_pnl_usd > 0 ? 'goodCard' : ''
      ),
      card('Failures', status.failed_trade_count, status.failed_trade_count > 0 ? 'dangerCard' : ''),
      card('Trades', status.trade_count),
    ].join('');
  }

  const healthNode = document.getElementById('health');
  if (healthNode) healthNode.textContent = JSON.stringify(health, null, 2);

  show(
    'killReason',
    status.kill_switch && status.kill_switch_reason
      ? `Kill switch reason: ${status.kill_switch_reason}`
      : ''
  );

  show(
    'rateLimit',
    status.rate_limited
      ? 'A venue is rate limiting this client. The scanner is backing off and will retry — this is not counted as a trading failure.'
      : ''
  );

  table('quotes', quotes, [
    { key: 'venue', label: 'Venue' },
    { key: 'pair', label: 'Pair' },
    { key: 'bid', label: 'Bid', cls: 'num' },
    { key: 'ask', label: 'Ask', cls: 'num' },
    { key: 'source', label: 'Source' },
    { key: 'ts', label: 'Time', cls: 'ts', render: (v) => esc(shortTime(v)) },
  ]);

  table('opportunities', opportunities, [
    { key: 'ts', label: 'Time', cls: 'ts', render: (v) => esc(shortTime(v)) },
    { key: 'direction', label: 'Direction' },
    { key: 'buy_price', label: 'Buy', cls: 'num' },
    { key: 'sell_price', label: 'Sell', cls: 'num' },
    { key: 'net_spread_bps', label: 'Net bps', cls: signed },
    { key: 'expected_profit_usd', label: 'Expected $', cls: signed, render: money },
    {
      key: 'executable',
      label: 'Executable',
      render: (v) => `<span class="badge ${v ? 'good' : 'bad'}">${esc(v)}</span>`,
    },
    { key: 'reason', label: 'Reason', cls: 'reason' },
  ]);

  table('trades', trades, [
    { key: 'ts', label: 'Time', cls: 'ts', render: (v) => esc(shortTime(v)) },
    { key: 'mode', label: 'Mode' },
    { key: 'direction', label: 'Direction' },
    { key: 'trade_size_usd', label: 'Size $', cls: 'num' },
    { key: 'expected_profit_usd', label: 'Expected $', cls: signed, render: money },
    { key: 'realized_profit_usd', label: 'Realized $', cls: signed, render: money },
    {
      key: 'status',
      label: 'Status',
      render: (v) =>
        `<span class="badge ${String(v).includes('filled') ? 'good' : 'bad'}">${esc(v)}</span>`,
    },
    { key: 'error', label: 'Error', cls: 'reason' },
  ]);
}

// --- wiring -----------------------------------------------------------------
document.getElementById('unlock').addEventListener('click', () => {
  attemptUnlock(document.getElementById('token').value.trim());
});

document.getElementById('token').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') attemptUnlock(e.target.value.trim());
});

document.getElementById('toggleToken').addEventListener('click', () => {
  const input = document.getElementById('token');
  const hidden = input.type === 'password';
  input.type = hidden ? 'text' : 'password';
  document.getElementById('toggleToken').textContent = hidden ? 'Hide' : 'Show';
});

document.getElementById('lock').addEventListener('click', () => {
  token = '';
  writeToken('');
  document.getElementById('token').value = '';
  showGate('');
  probeConnection();
});

// On load: reuse a token already verified in this tab, otherwise show the gate.
(async () => {
  probeConnection();
  setInterval(probeConnection, 10000);
  if (token && (await verifyToken(token)).ok) showApp();
  else {
    token = '';
    writeToken('');
    showGate('');
  }
})();
