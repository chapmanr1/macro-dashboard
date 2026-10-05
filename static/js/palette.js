// FILE: static/js/palette.js
// Keyboard navigation + ⌘K / Ctrl+K command palette.
//   1–6            switch tabs (when not typing in a field)
//   ← → Home End   move between tabs when a tab has focus (ARIA tablist pattern)
//   ⌘K / Ctrl+K    open the palette: jump to a tab or panel, or research a ticker
// Relies on globals from the inline script: switchTab(), openResearch().
(function () {
  const TABS = ['overview', 'markets', 'economy', 'credit', 'news', 'journal'];
  const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const isTyping = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const esc = t => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ── Tab keyboard navigation ───────────────────────────────────────────────
  function focusTab(id) {
    switchTab(id);
    const btn = document.querySelector(`.tab-btn[data-tab="${id}"]`);
    if (btn) btn.focus();
  }
  document.querySelector('.tab-nav')?.addEventListener('keydown', e => {
    const cur = TABS.indexOf(document.activeElement?.dataset?.tab);
    if (cur < 0) return;
    const next = { ArrowRight: cur + 1, ArrowLeft: cur - 1, Home: 0, End: TABS.length - 1 }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    focusTab(TABS[(next + TABS.length) % TABS.length]);
  });

  // ── Palette ───────────────────────────────────────────────────────────────
  const root = document.createElement('div');
  root.className = 'cmdk';
  root.hidden = true;
  root.innerHTML = `
    <div class="cmdk-backdrop" data-close></div>
    <div class="cmdk-box" role="dialog" aria-modal="true" aria-label="Command palette">
      <input class="cmdk-input" id="cmdkInput" type="text" autocomplete="off" spellcheck="false"
             role="combobox" aria-expanded="true" aria-controls="cmdkList" aria-autocomplete="list"
             placeholder="Jump to a tab or panel, or type a ticker to research…">
      <ul class="cmdk-list" id="cmdkList" role="listbox"></ul>
      <div class="cmdk-foot"><kbd>↑</kbd><kbd>↓</kbd> move <kbd>↵</kbd> open <kbd>esc</kbd> close</div>
    </div>`;
  document.body.appendChild(root);
  const input = root.querySelector('#cmdkInput');
  const list = root.querySelector('#cmdkList');
  let items = [], shown = [], active = 0, lastFocus = null;

  function collect() {
    const out = TABS.map((t, i) => {
      const btn = document.querySelector(`.tab-btn[data-tab="${t}"]`);
      const label = btn ? btn.childNodes[0].textContent.trim() : t;
      return { kind: 'Tab', label, hint: String(i + 1), run: () => focusTab(t) };
    });
    document.querySelectorAll('.tab-panel [data-widget-id]').forEach(el => {
      const titleEl = el.querySelector('.section-title, .card-title');
      const label = titleEl ? titleEl.textContent.replace(/ⓘ/g, '').trim() : '';
      const tab = el.closest('.tab-panel')?.id.replace('tab-', '');
      if (!label || !tab) return;
      out.push({ kind: 'Panel', label, hint: tab[0].toUpperCase() + tab.slice(1), run: () => jumpTo(tab, el) });
    });
    return out;
  }

  function jumpTo(tab, el) {
    switchTab(tab);
    requestAnimationFrame(() => {
      el.scrollIntoView({ behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start' });
      el.classList.remove('flash-target');
      void el.offsetWidth;
      el.classList.add('flash-target');
    });
  }

  function research(q) {
    if (typeof openResearch === 'function') openResearch();
    const box = document.getElementById('researchSearchInput');
    if (!box) return;
    box.value = q;
    box.focus();
    box.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function score(label, q) {
    const l = label.toLowerCase();
    if (!q) return 1;
    if (l.startsWith(q)) return 3;
    if (l.includes(q)) return 2;
    let i = 0;                                   // subsequence match
    for (const ch of l) if (ch === q[i]) i++;
    return i === q.length ? 1 : 0;
  }

  function render() {
    const q = input.value.trim().toLowerCase();
    shown = items.map(it => ({ it, s: score(it.label, q) })).filter(x => x.s > 0)
                 .sort((a, b) => b.s - a.s).slice(0, 12).map(x => x.it);
    if (q) shown.push({ kind: 'Research', label: `Research “${input.value.trim()}”`, hint: 'Ticker or company',
                        run: () => research(input.value.trim()) });
    active = Math.min(active, Math.max(shown.length - 1, 0));
    list.innerHTML = shown.map((it, i) =>
      `<li id="cmdk-opt-${i}" role="option" aria-selected="${i === active}" class="cmdk-item${i === active ? ' active' : ''}" data-i="${i}">
         <span class="cmdk-kind">${it.kind}</span><span class="cmdk-label">${esc(it.label)}</span><span class="cmdk-hint">${esc(it.hint || '')}</span>
       </li>`).join('') || '<li class="cmdk-empty">No matches</li>';
    input.setAttribute('aria-activedescendant', shown.length ? `cmdk-opt-${active}` : '');
    list.querySelector('.active')?.scrollIntoView({ block: 'nearest' });
  }

  function open() {
    lastFocus = document.activeElement;
    items = collect();
    input.value = '';
    active = 0;
    root.hidden = false;
    render();
    input.focus();
  }
  function close() {
    root.hidden = true;
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }
  function choose(i) {
    const it = shown[i];
    if (!it) return;
    close();
    it.run();
  }

  input.addEventListener('input', () => { active = 0; render(); });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % Math.max(shown.length, 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active - 1 + shown.length) % Math.max(shown.length, 1); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(active); }
    else if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'Tab') { e.preventDefault(); }          // keep focus inside the dialog
  });
  list.addEventListener('mousedown', e => {
    const li = e.target.closest('[data-i]');
    if (li) { e.preventDefault(); choose(+li.dataset.i); }
  });
  root.addEventListener('mousedown', e => { if (e.target.hasAttribute('data-close')) close(); });
  document.getElementById('paletteBtn')?.addEventListener('click', open);

  // ── Global shortcuts ──────────────────────────────────────────────────────
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      root.hidden ? open() : close();
      return;
    }
    if (!root.hidden || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return;
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= TABS.length) { e.preventDefault(); focusTab(TABS[n - 1]); }
  });

  // Show the right modifier on non-Mac keyboards.
  if (!/Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)) {
    const k = document.querySelector('#paletteBtn kbd');
    if (k) k.textContent = 'Ctrl K';
  }
})();
