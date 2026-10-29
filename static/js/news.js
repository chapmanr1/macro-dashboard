// FILE: static/js/news.js
// News section: On Watch rail + ONE ranked feed (pinned top 3 by impact, then the flow).
// Quiet-day states are computed at render time from the release calendar and the
// stories' own timestamps — no toggle. Stories are never re-dated, padded, or shown
// as new when they are carried over. Uses helpers from the inline dashboard script.
(function () {
  const TOPICS = ['Fed & Rates', 'Inflation', 'Credit', 'Energy', 'Growth & Jobs', 'Global', 'Markets'];
  const RANK = ['TOP', '2ND', '3RD'];
  let _news = null, _ctx = {}, _filter = 'All';

  // ── Eastern-time helpers ──
  const etParts = d => {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
      .formatToParts(d).map(x => [x.type, x.value]));
    return { key: `${p.year}-${p.month}-${p.day}`, mins: (+p.hour % 24) * 60 + (+p.minute) };
  };
  // The most recent completed US session close (4:00 PM ET, weekdays; holidays not modeled).
  function lastClose(now = new Date()) {
    const n = etParts(now);
    const d = new Date(n.key + 'T12:00:00');
    const weekday = x => x.getDay() !== 0 && x.getDay() !== 6;
    if (!(weekday(d) && n.mins >= 16 * 60)) {
      do { d.setDate(d.getDate() - 1); } while (!weekday(d));
    }
    const key = d.toLocaleDateString('en-CA');
    return { key, mins: 16 * 60 };
  }
  // Absolute instant for an Eastern wall-clock time (handles EST/EDT).
  function etInstant(key, mins) {
    const [y, m, d] = key.split('-').map(Number);
    const guess = Date.UTC(y, m - 1, d, Math.floor(mins / 60), mins % 60);
    const off = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', timeZoneName: 'shortOffset' })
      .formatToParts(new Date(guess)).find(x => x.type === 'timeZoneName').value;   // e.g. "GMT-4"
    const h = parseInt((/GMT([+-]\d+)/.exec(off) || [0, '-5'])[1], 10);
    return new Date(guess - h * 3600000);
  }
  const after = (a, b) => a.key > b.key || (a.key === b.key && a.mins > b.mins);
  const storyTime = a => (a.publishedAt ? new Date(a.publishedAt) : null);
  const dayLabel = key => {
    const today = etParts(new Date()).key;
    const d = new Date(key + 'T12:00:00');
    return key === today
      ? 'Today — ' + d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })
      : d.toLocaleDateString('en-US', { weekday: 'long' }) + ' — ' + d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  };
  const timeCell = a => {
    const t = storyTime(a);
    return t ? `<span class="nf-age">${relAge(t)}</span><span class="nf-clock">${etTime(t)}</span>`
             : '<span class="nf-age">Time n/a</span>';
  };
  const safeUrl = u => (/^https?:\/\//.test(u || '') ? _xe(u) : '#');
  const topicTag = t => `<span class="tag nf-topic">${_xe(t || 'Markets')}</span>`;

  // ── "Why it matters": factual, mechanical, from the dashboard's own data ──
  function whyLine(topic) {
    const c = _ctx;
    const hy = ((c.credit && c.credit.spreads) || []).find(x => x.id === 'hy_oas');
    const trig = id => ((c.thesis && c.thesis.triggers) || []).find(t => t.id === id);
    const mac = id => ((c.macro && c.macro.series) || []).find(x => x.id === id);
    const y = id => ((c.yields && c.yields.yields) || []).find(x => x.id === id);
    const nextEv = re => {
      const now = etParts(new Date());
      for (const d of ((c.calendar && c.calendar.events) || [])) for (const ev of (d.events || [])) {
        if (re.test(ev.event) && (d.date > now.key || (d.date === now.key && (_evMinutes(ev.time) ?? 0) > now.mins))) return `${_titleCase(ev.event)} ${_ymdLabel(d.date, { month: 'short', day: 'numeric' })}`;
      }
      return null;
    };
    const val = s => (s ? (s.display ?? (s.current != null ? _num(s.current, s.decimals ?? 2) + (s.suffix || '') : null)) : null);
    switch (topic) {
      case 'Credit': {
        const t = trig('hy_spreads');
        if (!hy || hy.value == null) return null;
        return t ? `HY OAS ${_num(hy.value, 0)}bp — ${_num(Math.abs(hy.value - t.threshold), 0)}bp ${hy.value >= t.threshold ? 'above' : 'below'} the ${t.threshold}bp thesis trigger.`
                 : `HY OAS ${_num(hy.value, 0)}bp.`;
      }
      case 'Fed & Rates': {
        const ff = val(mac('fed_funds')), t10 = y('dgs10'), t2 = y('dgs2'), f = nextEv(/FOMC RATE DECISION/);
        return [ff && `Fed target ${ff}`, t10 && `10Y ${_num(t10.value, 2)}%`, t2 && `2Y ${_num(t2.value, 2)}%`, f && `next FOMC ${f.replace(/^FOMC Rate Decision /, '')}`].filter(Boolean).join(' · ') + '.';
      }
      case 'Inflation': {
        const p = mac('pce'), t = trig('core_pce'), n = nextEv(/CPI RELEASE|CORE PCE/);
        if (!p) return null;
        return `Core PCE ${val(p)}${t ? ` vs the ${t.threshold}% thesis trigger` : ''}${n ? `; next print: ${n}` : ''}.`;
      }
      case 'Energy': {
        const cr = ((c.market && c.market.commodities) || []).find(x => x.symbol === 'DCOILWTICO');
        return cr && cr.price != null ? `WTI $${_num(cr.price, 2)} (${cr.pct_change > 0 ? '+' : ''}${_num(cr.pct_change, 2)}%) at the prior close.` : null;
      }
      case 'Growth & Jobs': {
        const g = mac('gdp'), u = mac('unemployment'), t = trig('gdp_growth');
        return g ? `GDP ${val(g)}${t ? ` vs the ${t.threshold}% thesis trigger` : ''}${u ? `; unemployment ${val(u)}` : ''}.` : null;
      }
      case 'Global': {
        const usd = ((c.market && c.market.currencies) || []).find(x => x.symbol === 'DTWEXBGS');
        return usd && usd.price != null ? `USD broad index ${_num(usd.price, 2)}${c.market.dollar_signal ? ` (${_titleCase(c.market.dollar_signal)})` : ''}.` : null;
      }
      default: {
        const spx = ((c.market && c.market.indices) || []).find(i => i.symbol === '^GSPC');
        const vix = c.market && c.market.vix;
        return spx && spx.price != null ? `S&P 500 ${_num(spx.price, 2)} (${spx.pct_change > 0 ? '+' : ''}${_num(spx.pct_change, 2)}%)${vix && vix.value != null ? `; VIX ${_num(vix.value, 2)} (${_titleCase(vix.label || '')})` : ''}.` : null;
      }
    }
  }

  // ── Data releases from today's calendar (printed ones only), as feed items at release time ──
  function releaseItems() {
    const now = etParts(new Date());
    const today = ((_ctx.calendar && _ctx.calendar.events) || []).find(d => d.date === now.key);
    if (!today) return { items: [], scheduled: false };
    const items = (today.events || []).filter(ev => ev.actual != null).map(ev => {
      const mins = _evMinutes(ev.time) ?? 0;
      return { kind: 'release', topic: 'Markets', ev, etKey: now.key, etMins: mins, sortTime: etInstant(now.key, mins) };
    });
    return { items, scheduled: (today.events || []).length > 0 };
  }

  function releaseRow(r) {
    const ev = r.ev;
    const vs = { UP: 'Higher than the prior print', DOWN: 'Lower than the prior print', UNCH: 'Unchanged from the prior print' }[ev.vs_prior] || 'Released';
    return `<div class="nf-item nf-release">
      <div class="nf-when"><span class="nf-age">${_evTimeLabel(ev.time)}</span></div>
      <div class="nf-main"><div class="nf-tags"><span class="tag accent">Data release</span></div>
        <div class="nf-head">${_xe(_titleCase(ev.event))}: ${_xe(ev.actual)} <span class="flat">vs expected — · prior ${_xe(ev.prior || '—')}</span></div>
        <div class="nf-meta">${_xe(ev.note || 'Official release')} · ${_evTimeLabel(ev.time)} · ${vs} (consensus forecasts have no free source).</div></div></div>`;
  }

  function storyRow(a, opts = {}) {
    const watch = (a.watchlist || []).length > 0;
    return `<a class="nf-item${watch ? ' nf-watch' : ''}${opts.pinned ? ' nf-pinned' : ''}" href="${safeUrl(a.url)}" target="_blank" rel="noopener noreferrer">
      <div class="nf-when">${opts.rank ? `<span class="nf-rank">${opts.carried ? 'CARRIED' : opts.rank}</span>` : ''}${timeCell(a)}</div>
      <div class="nf-main">
        <div class="nf-tags">${topicTag(a.topic)}${watch ? '<span class="tag accent">Watchlist</span>' : ''}</div>
        <div class="nf-head">${_xe(a.title || a.headline || '')}</div>
        <div class="nf-meta">${_xe(a.source || '')}${opts.pinned ? ` · <span class="nf-impact"><span class="nf-impact-bar"><span style="width:${Math.min(100, a.impact ?? 0)}%"></span></span>Impact ${a.impact ?? 0}</span>` : ''}</div>
        ${opts.why ? `<div class="nf-why">${_xe(opts.why)}</div>` : ''}
      </div></a>`;
  }

  function render() {
    const el = document.getElementById('newsContent');
    if (!el || !_news) return;
    let arts = typeof filterArticlesBySettings === 'function' ? filterArticlesBySettings(_news.articles || []) : (_news.articles || []);
    const close = lastClose();
    const fresh = a => { const t = storyTime(a); return t ? after(etParts(t), close) : false; };
    const anyFresh = arts.some(fresh);
    const rel = releaseItems();

    // Header stamp: true time since the last successful pull
    const st = document.getElementById('newsTimestamp');
    const nSrc = Object.keys(_news.source_counts || {}).length;
    if (st) st.textContent = _news.last_success_at
      ? `Updated ${relAge(parseServerTime(_news.last_success_at))} · ${nSrc} source${nSrc === 1 ? '' : 's'}${_news.status === 'stale' ? ' · feeds failing' : ''}`
      : 'No successful update yet';

    // Filtered view: one flat list, no pinned heading, no date dividers
    if (_filter !== 'All') {
      const list = arts.filter(a => (a.topic || 'Markets') === _filter);
      el.innerHTML = list.length ? list.map(a => storyRow(a)).join('') : `<div class="panel-note">No ${_xe(_filter)} stories in the current set.</div>`;
      renderFilterMenu(arts);
      return;
    }

    // Pinned top 3 by impact (dated stories only)
    const pinned = [...arts].filter(a => a.publishedAt).sort((a, b) => (b.impact ?? 0) - (a.impact ?? 0)).slice(0, 3);
    const pinnedSet = new Set(pinned);
    let html = `<div class="nf-section">Market Driving — pinned top 3${anyFresh ? '' : ' · carried (nothing new since the prior close)'}</div>`;
    html += pinned.map((a, i) => storyRow(a, { pinned: true, rank: RANK[i], carried: !fresh(a), why: whyLine(a.topic) })).join('');

    // Flow: remaining stories + printed releases, newest first, under true date dividers
    const flow = arts.filter(a => !pinnedSet.has(a)).map(a => ({ kind: 'story', a, sortTime: storyTime(a) }))
      .concat(rel.items).sort((x, y) => (y.sortTime || 0) - (x.sortTime || 0));
    html += '<div class="nf-section">Latest</div>';
    const todayKey = etParts(new Date()).key;
    let lastKey = null;
    if (!rel.scheduled) {
      const nxt = typeof _upcomingEvents === 'function' && _lastCalendar ? _upcomingEvents()[0] : null;
      html += `<div class="nf-divider">${_xe(dayLabel(todayKey))}</div>`; lastKey = todayKey;
      html += `<div class="nf-item nf-release"><div class="nf-when"><span class="nf-age">Today</span></div><div class="nf-main"><div class="nf-tags"><span class="tag">Data</span></div>
        <div class="nf-head">No economic data scheduled today</div><div class="nf-meta">${nxt ? `Next on the calendar: ${_xe(_titleCase(nxt.ev.event))}, ${_ymdLabel(nxt.date)} ${_evTimeLabel(nxt.ev.time)}.` : 'Calendar unavailable.'}</div></div></div>`;
    }
    flow.forEach(it => {
      const key = it.kind === 'release' ? it.etKey : (it.sortTime ? etParts(it.sortTime).key : 'undated');
      if (key !== lastKey) { html += `<div class="nf-divider">${key === 'undated' ? 'Undated' : _xe(dayLabel(key))}</div>`; lastKey = key; }
      html += it.kind === 'release' ? releaseRow(it) : storyRow(it.a);
    });
    el.innerHTML = arts.length || rel.items.length ? html : '<div class="panel-note">No stories available from the feeds right now.</div>';
    renderFilterMenu(arts);
  }

  // ── Filter menu: topics with live counts + per-source counts for the current set ──
  function renderFilterMenu(arts) {
    const menu = document.getElementById('nfMenu');
    const btn = document.getElementById('nfFilterBtn');
    if (!menu || !btn) return;
    btn.textContent = 'Filter: ' + _filter;
    const counts = Object.fromEntries(TOPICS.map(t => [t, 0]));
    arts.forEach(a => { counts[a.topic || 'Markets'] = (counts[a.topic || 'Markets'] || 0) + 1; });
    const set = _filter === 'All' ? arts : arts.filter(a => (a.topic || 'Markets') === _filter);
    const src = {};
    set.forEach(a => { src[a.source] = (src[a.source] || 0) + 1; });
    menu.innerHTML = `<button class="nf-opt${_filter === 'All' ? ' on' : ''}" data-topic="All">All<span>${arts.length}</span></button>` +
      TOPICS.map(t => `<button class="nf-opt${_filter === t ? ' on' : ''}" data-topic="${_xe(t)}"${counts[t] ? '' : ' disabled'}>${_xe(t)}<span>${counts[t]}</span></button>`).join('') +
      `<div class="nf-sources">${Object.entries(src).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${_xe(k)} ${v}`).join(' · ') || 'No sources'}</div>`;
  }
  function closeMenu() {
    const m = document.getElementById('nfMenu'), b = document.getElementById('nfFilterBtn');
    if (m) m.hidden = true; if (b) b.setAttribute('aria-expanded', 'false');
  }
  document.addEventListener('click', e => {
    const btn = e.target.closest('#nfFilterBtn');
    const opt = e.target.closest('#nfMenu .nf-opt');
    const menu = document.getElementById('nfMenu');
    if (btn) { const open = menu.hidden; menu.hidden = !open; btn.setAttribute('aria-expanded', String(open)); return; }
    if (opt && !opt.disabled) { _filter = opt.dataset.topic; closeMenu(); render(); return; }
    if (menu && !menu.hidden && !e.target.closest('#nfMenu')) closeMenu();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });

  // ── On Watch rail ──
  function renderWatch() {
    const el = document.getElementById('newsWatchBody');
    if (!el || !_news) return;
    const today = etParts(new Date()).key;
    const flagged = (_news.articles || []).filter(a => (a.watchlist || []).length && a.publishedAt && etParts(new Date(a.publishedAt)).key === today);
    const checkpoints = new Set((_news.checkpoint_events || []).map(x => x.toUpperCase()));
    let next = null;
    for (const d of ((_ctx.calendar && _ctx.calendar.events) || [])) {
      const ev = (d.events || []).find(x => checkpoints.has(String(x.event).toUpperCase()));
      if (ev && d.date >= today) { next = { d, ev }; break; }
    }
    el.innerHTML = `<div class="ow-tags">${(_news.watchlist || []).map(t => `<span class="tag accent tag-plain">${_xe(t)}</span>`).join('')}</div>
      <p class="panel-note ow-line">${flagged.length ? `${flagged.length} flagged ${flagged.length === 1 ? 'story' : 'stories'} today.` : 'No flagged stories today.'}</p>
      <table class="tbl tbl-kv"><tr><td class="lbl">Next checkpoint</td><td class="val">${next ? `${_xe(_titleCase(next.ev.event))}<span class="row-sub">${_ymdLabel(next.d.date)} · ${_evTimeLabel(next.ev.time)}</span>` : 'None on the calendar'}</td></tr></table>`;
  }

  // ── Load ──
  window.fetchNews = async function () {
    setStatus('loading');
    const retry = () => window.fetchNews();
    const [n, cal, crd, yl, mk, mc, th] = await Promise.allSettled([
      apiFetch('/api/news'), apiFetch('/api/calendar?days=60'), apiFetch('/api/credit'), apiFetch('/api/yields'),
      apiFetch('/api/market'), apiFetch('/api/macro'), apiFetch('/api/thesis'),
    ]);
    const v = r => (r.status === 'fulfilled' ? r.value : null);
    _ctx = { calendar: v(cal), credit: v(crd), yields: v(yl), market: v(mk), macro: v(mc), thesis: v(th) };
    if (_ctx.calendar && typeof _lastCalendar !== 'undefined' && !_lastCalendar) _lastCalendar = _ctx.calendar;
    if (n.status === 'fulfilled') {
      _news = n.value;
      _lastHeadlines = (_news.articles || []).slice(0, 10);
      if (typeof renderTopStories === 'function') renderTopStories(_news.articles || []);
      render();
      renderWatch();
      setStatus('ok');
    } else {
      panelState('newsContent', 'failed', { msg: `News unavailable (${_xe(n.reason.message || 'timeout')}).`, retry });
      panelState('newsWatchBody', 'failed', { msg: 'News unavailable.', retry });
      setStatus('error');
    }
    startRefreshCountdown();
  };
})();
