// End-to-end smoke test of the unlock gate and dashboard, in a real browser.
// Run by CI against an app booted with mock venues in paper mode.
//
// Catches the class of bug unit tests cannot: a broken token gate, a dashboard
// that never renders, JS errors, or horizontal overflow on a phone viewport.

const { chromium } = require('playwright');

const BASE = process.env.SMOKE_BASE_URL || 'http://127.0.0.1:8000';
const TOKEN = process.env.SMOKE_TOKEN || 'ci-smoke-token-0123456789abcdef';

const checks = [];
function check(name, passed, detail = '') {
  checks.push({ name, passed, detail });
  console.log(`${passed ? 'PASS' : 'FAIL'}  ${name}${detail ? ` — ${detail}` : ''}`);
}

// Cards are addressed by data-metric, not by rendered text: CSS uppercases
// the labels, so innerText matching silently misses.
async function metricValue(page, metric) {
  const node = await page.$(`#status .card[data-metric="${metric}"] .value`);
  return node ? (await node.innerText()).trim() : '';
}

(async () => {
  const browser = await chromium.launch();
  const pageErrors = [];

  // --- desktop: gate -> dashboard -----------------------------------------
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', (e) => pageErrors.push(e.message));

  await page.goto(BASE, { waitUntil: 'networkidle' });
  check('gate is shown on load', await page.isVisible('#gate'));
  check('dashboard is hidden before auth', !(await page.isVisible('#app')));

  await page.fill('#token', 'definitely-the-wrong-token');
  await page.click('#unlock');
  await page.waitForTimeout(700);
  check('wrong token keeps the gate closed', await page.isVisible('#gate'));
  check('wrong token shows an error', await page.isVisible('#gateError'));

  await page.fill('#token', TOKEN);
  await page.click('#unlock');
  await page.waitForSelector('#app:not([hidden])', { timeout: 8000 });
  check('correct token opens the dashboard', await page.isVisible('#app'));

  await page.waitForTimeout(3000);
  const cards = await page.$$eval('#status .card', (n) => n.length);
  check('status cards render', cards >= 8, `${cards} cards`);

  const mode = await metricValue(page, 'mode');
  check('paper mode is reported', mode.includes('SAFE'), mode);

  const quoteRows = await page.$$eval('#quotes tbody tr', (n) => n.length);
  check('quotes table populates', quoteRows > 0, `${quoteRows} rows`);

  const oppRows = await page.$$eval('#opportunities tbody tr', (n) => n.length);
  check('opportunities populate', oppRows > 0, `${oppRows} rows`);

  // control endpoints through the UI
  await page.click('button:has-text("Kill Switch")');
  await page.waitForTimeout(400);
  const toastText = await page.$$eval('.toast', (ns) => ns.map((n) => n.textContent).join(' / '));
  check('action shows a confirmation toast', toastText.includes('ENGAGED'), toastText);

  await page.waitForTimeout(1200);
  check('kill switch engages from the UI', (await metricValue(page, 'kill-switch')) === 'ON');
  // A control that cannot apply must read as unavailable, not just do nothing.
  check(
    'engage button disables once engaged',
    await page.isDisabled('button:has-text("Kill Switch")')
  );

  await page.click('button:has-text("Reset Kill")');
  await page.waitForTimeout(1500);
  check('kill switch resets from the UI', (await metricValue(page, 'kill-switch')) === 'OFF');
  check(
    'reset button disables once reset',
    await page.isDisabled('button:has-text("Reset Kill")')
  );

  await page.click('button:has-text("Stop")');
  await page.waitForTimeout(1500);
  check('stop disables itself and re-enables start', 
    (await page.isDisabled('button:has-text("Stop")')) &&
    !(await page.isDisabled('button:has-text("Start")')));
  await page.click('button:has-text("Start")');
  await page.waitForTimeout(1500);

  // token survives a reload, then Lock re-gates
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  check('session token survives reload', await page.isVisible('#app'));

  await page.click('#lock');
  await page.waitForTimeout(600);
  check('lock re-gates the dashboard', await page.isVisible('#gate'));
  await page.close();

  // --- phone: layout must not overflow ------------------------------------
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 } });
  phone.on('pageerror', (e) => pageErrors.push(`phone: ${e.message}`));
  await phone.goto(BASE, { waitUntil: 'networkidle' });
  await phone.fill('#token', TOKEN);
  await phone.click('#unlock');
  await phone.waitForSelector('#app:not([hidden])', { timeout: 8000 });
  await phone.waitForTimeout(2500);
  const overflow = await phone.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  check('no horizontal overflow at 390px', overflow <= 0, `${overflow}px`);
  await phone.close();

  check('no uncaught page errors', pageErrors.length === 0, pageErrors.join('; '));

  await browser.close();

  const failed = checks.filter((c) => !c.passed);
  console.log(`\n${checks.length - failed.length}/${checks.length} checks passed`);
  if (failed.length) process.exit(1);
})().catch((e) => {
  console.error('smoke test crashed:', e);
  process.exit(1);
});
