// FILE: static/js/panel.js
// Shared helpers for the workspace layout: panel load states, regime colors,
// and Eastern-time labels. Loaded before the inline dashboard script.

// Regime colors: use regimeColor() in the inline script — it reads the
// --regime-* tokens, which tokens.css maps onto the four regime families.

/** Human regime name: "STAGFLATION_RISK" -> "Stagflation Risk". */
function regimeName(label) {
  return String(label || '—').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, c => c.toUpperCase());
}

// ── Eastern time ──
const _ET = 'America/New_York';

/** "9:42 AM ET" for a Date / ISO string / epoch ms. */
function etTime(d, withLabel = true) {
  const dt = d instanceof Date ? d : new Date(d);
  if (isNaN(dt)) return '—';
  const t = dt.toLocaleTimeString('en-US', { timeZone: _ET, hour: 'numeric', minute: '2-digit' });
  return withLabel ? t + ' ET' : t;
}

/** "Tue, Oct 28" in Eastern time. */
function etDate(d, opts = { weekday: 'short', month: 'short', day: 'numeric' }) {
  const dt = d instanceof Date ? d : new Date(d);
  return isNaN(dt) ? '—' : dt.toLocaleDateString('en-US', { timeZone: _ET, ...opts });
}

/** YYYY-MM-DD of a date in Eastern time (for comparing calendar days). */
function etDayKey(d) {
  const dt = d instanceof Date ? d : new Date(d);
  return dt.toLocaleDateString('en-CA', { timeZone: _ET });
}

/** The prior completed US trading session (weekends skipped; holidays not modeled). */
function priorSessionDate(now = new Date()) {
  const d = new Date(now.toLocaleString('en-US', { timeZone: _ET }));
  d.setDate(d.getDate() - 1);
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
  return d;
}

/** "PRIOR CLOSE — TUE, OCT 28" */
function sessionLabel(now = new Date()) {
  const d = priorSessionDate(now);
  const s = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
  return 'PRIOR CLOSE — ' + s.toUpperCase();
}

/** "12m ago" / "3h ago" / "2d ago" — always the true age. */
function relAge(d, now = Date.now()) {
  const ms = now - (d instanceof Date ? d.getTime() : new Date(d).getTime());
  if (!isFinite(ms)) return '—';
  const m = Math.max(0, Math.round(ms / 60000));
  if (m < 60) return m + 'm ago';
  const h = Math.round(m / 60);
  return h < 48 ? h + 'h ago' : Math.round(h / 24) + 'd ago';
}

// ── Panel load states: loading (skeleton) → loaded | failed (message + Retry) ──
/**
 * Set a panel body's state. `el` is the body element or its id.
 * state: 'loading' | 'failed'. For 'loaded', just write the content yourself.
 * opts.retry: function to call from the Retry button; opts.msg: failure text.
 */
function panelState(el, state, opts = {}) {
  const node = typeof el === 'string' ? document.getElementById(el) : el;
  if (!node) return;
  if (state === 'loading') {
    const rows = opts.rows || 4;
    node.innerHTML = '<div class="panel-skel">' +
      Array.from({ length: rows }, (_, i) => `<div class="skel" style="width:${92 - (i % 3) * 14}%"></div>`).join('') +
      '</div>';
  } else if (state === 'failed') {
    const msg = opts.msg || 'Couldn’t load this panel.';
    node.innerHTML = `<div class="panel-fail"><span>${msg}</span>` +
      (opts.retry ? '<button class="panel-retry" type="button">Retry</button>' : '') + '</div>';
    if (opts.retry) node.querySelector('.panel-retry').addEventListener('click', () => {
      panelState(node, 'loading', opts);
      opts.retry();
    });
  }
}
