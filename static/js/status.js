// FILE: static/js/status.js
// Header status dots — one per data source, from GET /api/status (which only
// reads recorded request outcomes; it never calls the sources itself).
//   green = latest request succeeded recently
//   amber = latest request failed but a recent success exists, or data is getting old
//   red   = failing with no recent success
//   grey  = not used yet in this server session
(function () {
  const ORDER = ['fred', 'twelvedata', 'fmp', 'rss', 'anthropic', 'fed_web', 'cftc'];
  const SHORT = { fred: 'FRED', twelvedata: 'TD', fmp: 'FMP', rss: 'RSS', anthropic: 'AI', fed_web: 'FED', cftc: 'COT' };
  // How old a last success can be before the dot turns amber (minutes).
  const STALE_MIN = { fred: 120, twelvedata: 30, fmp: 24 * 60, rss: 60, anthropic: 24 * 60, fed_web: 26 * 60, cftc: 26 * 60 };

  const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const minsAgo = iso => iso ? (Date.now() - new Date(iso).getTime()) / 60000 : Infinity;
  function ago(iso) {
    const m = minsAgo(iso);
    if (!isFinite(m)) return 'never';
    if (m < 1) return 'just now';
    if (m < 60) return `${Math.round(m)} min ago`;
    if (m < 1440) return `${Math.round(m / 60)} h ago`;
    return `${Math.round(m / 1440)} d ago`;
  }
  function level(key, s) {
    if (s.state === 'idle') return 'idle';
    const fresh = minsAgo(s.last_success) <= (STALE_MIN[key] || 120);
    if (s.state === 'error') return fresh ? 'warn' : 'error';
    return fresh ? 'ok' : 'warn';
  }
  function describe(key, s, lvl) {
    const parts = [s.label];
    if (lvl === 'idle') parts.push('not used yet this session');
    else {
      parts.push(`last success ${ago(s.last_success)}`);
      if (s.latency_ms != null) parts.push(`${s.latency_ms.toLocaleString('en-US')} ms`);
      if (s.state === 'error') parts.push(`last error ${ago(s.last_error)}: ${s.error || 'failed'}`);
    }
    return parts.join(' · ');
  }

  function render(data) {
    const box = document.getElementById('srcStatus');
    if (!box || !data || !data.sources) return;
    box.innerHTML = ORDER.filter(k => data.sources[k]).map(k => {
      const s = data.sources[k], lvl = level(k, s), tip = esc(describe(k, s, lvl));
      return `<span class="src-dot" data-level="${lvl}" tabindex="0" role="img" aria-label="${tip}" data-tip="${tip}">` +
             `<i aria-hidden="true"></i><span class="src-name">${SHORT[k] || k}</span></span>`;
    }).join('');
    const worst = ['error', 'warn'].find(l => box.querySelector(`[data-level="${l}"]`));
    box.dataset.worst = worst || 'ok';
  }

  async function refreshSourceStatus() {
    try {
      const res = await fetch('/api/status', { cache: 'no-store' });
      if (res.ok) render(await res.json());
    } catch (e) { /* header dots are best-effort; panels show their own errors */ }
  }

  window.refreshSourceStatus = refreshSourceStatus;
  refreshSourceStatus();
  setInterval(refreshSourceStatus, 60000);
})();
