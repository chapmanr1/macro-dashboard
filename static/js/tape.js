// FILE: static/js/tape.js
// Ticker tape across the top of the page. Prior-session data from the existing
// endpoints (/api/market, /api/yields, /api/credit, /api/calendar) — nothing is
// hard-coded. Any item whose data is older than the lead session carries its own
// date, so prior-close data never reads as live. Visibility is a per-device
// setting (stLoad().tickerTape, default on).
(function () {
  const TAPE_REFRESH_MS = 5 * 60 * 1000;   // server caches are 15–60 min; 5 min is plenty
  const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // FRED as_of is a calendar date ("2026-10-28"); parse at noon so no time zone shifts the day.
  const asDate = ymd => (ymd ? new Date(ymd + 'T12:00:00') : null);
  const shortDate = ymd => {
    const d = asDate(ymd);
    return d ? d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
  };
  const num = (v, dp) => Number(v).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const pct = v => {
    if (v == null || !isFinite(v)) return '';
    const cls = v > 0 ? 'up' : v < 0 ? 'dn' : 'flat';
    return ` <span class="${cls}">${v > 0 ? '+' : ''}${num(v, 2)}%</span>`;
  };
  const CRUDE_BENCHMARK = { DCOILWTICO: 'WTI', DCOILBRENTEU: 'Brent' };

  function item(label, value, change, asOf, leadAsOf) {
    const stale = asOf && leadAsOf && asOf !== leadAsOf ? ` <span class="tape-date">(${shortDate(asOf)})</span>` : '';
    return `<span class="tape-item"><span class="tape-lbl">${esc(label)}</span> <span class="tape-val">${value}</span>${change || ''}${stale}</span>`;
  }

  async function getJSON(url) {
    try { return await apiFetch(url, 15000); } catch (e) { return null; }
  }

  async function renderTape() {
    const track = document.getElementById('tapeTrack');
    if (!track) return;
    const [mkt, yld, crd, cal] = await Promise.all([
      getJSON('/api/market'), getJSON('/api/yields'), getJSON('/api/credit'), getJSON('/api/calendar?days=60'),
    ]);
    const parts = [];
    const idx = (mkt && mkt.indices) || [];
    const spx = idx.find(i => i.symbol === '^GSPC');
    const lead = spx && spx.as_of;
    const leadDate = asDate(lead);
    const leadText = leadDate
      ? 'PRIOR CLOSE — ' + leadDate.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase()
      : sessionLabel();
    parts.push(`<span class="tape-item tape-lead">${esc(leadText)}</span>`);

    const NAMES = { '^GSPC': 'S&P 500', '^IXIC': 'Nasdaq', '^DJI': 'Dow' };
    ['^GSPC', '^IXIC', '^DJI'].forEach(sym => {
      const i = idx.find(x => x.symbol === sym);
      if (i && i.price != null) parts.push(item(NAMES[sym], num(i.price, 2), pct(i.pct_change), i.as_of, lead));
    });
    const ys = (yld && yld.yields) || [];
    [['dgs10', '10Y'], ['dgs2', '2Y']].forEach(([id, lbl]) => {
      const y = ys.find(x => x.id === id);
      if (y && y.value != null) parts.push(item(lbl, num(y.value, 2) + '%', '', y.as_of, lead));
    });
    const vix = mkt && mkt.vix;
    if (vix && vix.value != null) parts.push(item('VIX', num(vix.value, 2), pct(vix.pct_change), vix.as_of, lead));
    const hy = ((crd && crd.spreads) || []).find(x => x.id === 'hy_oas');
    if (hy && hy.value != null) parts.push(item('HY OAS', num(hy.value, 0) + 'bp', '', hy.as_of, lead));
    const crude = ((mkt && mkt.commodities) || []).find(c => CRUDE_BENCHMARK[c.symbol]);
    if (crude && crude.price != null) {
      parts.push(item(CRUDE_BENCHMARK[crude.symbol] + ' crude', '$' + num(crude.price, 2), pct(crude.pct_change), crude.as_of, lead));
    }
    const days = (cal && cal.events) || [];
    const fomc = days.find(d => (d.events || []).some(e => /FOMC RATE DECISION/i.test(e.event)));
    if (fomc) parts.push(`<span class="tape-item"><span class="tape-lbl">Next FOMC</span> <span class="tape-val">${esc(shortDate(fomc.date))}</span></span>`);

    if (parts.length === 1) parts.push('<span class="tape-item tape-lbl">Market data unavailable</span>');
    // Two copies side by side; the CSS animation slides one full copy for a seamless loop.
    const run = parts.join('<span class="tape-sep" aria-hidden="true">·</span>');
    track.innerHTML = `<span class="tape-run">${run}<span class="tape-sep" aria-hidden="true">·</span></span>` +
                      `<span class="tape-run" aria-hidden="true">${run}<span class="tape-sep">·</span></span>`;
  }

  // Show/hide from the per-device setting. Exposed for the Settings toggle.
  window.applyTape = function () {
    const on = (typeof stLoad === 'function' ? stLoad().tickerTape : true) !== false;
    const tape = document.getElementById('tape');
    if (tape) tape.hidden = !on;
  };

  applyTape();
  renderTape();
  setInterval(renderTape, TAPE_REFRESH_MS);
})();
