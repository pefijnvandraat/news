/* ==========================================================================
   Nieuws — frontend SPA
   Routes: #/front  #/mynews  #/favorites  #/search?q=  #/topic/:slug
           #/category/:slug  #/story/:id  #/saved  #/sources  #/privacy
   ========================================================================== */
'use strict';

const API = {
  async get(path) {
    const r = await fetch(path, { headers: { Accept: 'application/json' } });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return r.json();
  },
  async send(path, method, body) {
    const r = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.text()) || r.statusText);
    return r.status === 204 ? null : r.json();
  },
  post(p, b) { return this.send(p, 'POST', b); },
  del(p) { return this.send(p, 'DELETE'); },
};

const state = {
  meta: null, filters: {}, busy: false, topSignature: null,
  view: 'cards',                       // 'cards' | 'list'
  // An empty sort key means "keep the order the server delivered", which is
  // the page's own ranking: relevance on My News, editorial score on the front
  // page. Sorting only overrides that once you click a column.
  listSort: { key: '', dir: 'desc' },
  listFilters: {},
  listRows: null, listCtx: {}, listRoute: null,
};
const whyCache = new Map();
const $ = (sel, root = document) => root.querySelector(sel);
const main = $('#main');

/* ---------------------------------------------------------------- helpers */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* FastAPI reports errors as {"detail": ...}; the raw JSON is useless in an
   alert, so pull out the human sentence when there is one. */
function apiMessage(err) {
  const raw = String(err?.message ?? err ?? '');
  try {
    const d = JSON.parse(raw).detail;
    if (typeof d === 'string') return d;
    if (d && typeof d.message === 'string') return d.message;
  } catch { /* not JSON: the raw text is the best we have */ }
  return raw;
}

/* The developing-story badge. Defined once so the card, the story page and the
   explanatory note can never drift apart. */
const DEVELOPING = {
  label: 'Ontwikkelt zich',
  tooltip: 'Dit verhaal ontwikkelt zich nog: een uitgever heeft een artikel '
    + 'herzien of er kwam recent nieuwe berichtgeving bij.',
  short: 'Ontwikkelt zich nog',
};

function ago(iso) {
  if (!iso) return 'onbekend';
  const d = new Date(iso);
  if (isNaN(d)) return 'onbekend';
  const m = Math.round((Date.now() - d.getTime()) / 60000);
  if (m < 1) return 'nu';
  if (m < 60) return `${m} min geleden`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} uur geleden`;
  const dd = Math.round(h / 24);
  if (dd === 1) return 'gisteren';
  if (dd < 7) return `${dd} dagen geleden`;
  return d.toLocaleDateString('nl-NL', { day: 'numeric', month: 'short' });
}
function clock(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleString('nl-NL',
    { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

/* Compact relative time for the list view on narrow screens, where
   "46 min geleden" costs more width than the action buttons need. */
function agoShort(iso) {
  if (!iso) return '?';
  const d = new Date(iso);
  if (isNaN(d)) return '?';
  const m = Math.round((Date.now() - d.getTime()) / 60000);
  if (m < 1) return 'nu';
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}u`;
  return `${Math.round(h / 24)}d`;
}

function track(action, story, extra = {}) {
  API.post('/api/interactions', { action, story_id: story, ...extra }).catch(() => {});
}

/* ---------------------------------------------------------------- cards */
function pubBadges(pubs = []) {
  return pubs.map((p) =>
    `<span class="pub" style="color:${esc(p.colour || 'currentColor')}">
       <span class="dot"></span><span style="color:var(--text)">${esc(p.name)}</span></span>`).join('');
}

function storyCard(s, variant = '') {
  if (s.reasons && s.reasons.length) whyCache.set(s.id, s.reasons);
  const multi = s.publisher_count > 1;
  const img = s.image_url
    ? `<div class="thumb"><img loading="lazy" src="${esc(s.image_url)}" alt=""
         onerror="this.parentElement.classList.add('ph');this.remove()"></div>`
    : `<div class="thumb ph">📰</div>`;
  const chips = [
    multi ? `<span class="chip multi">${s.publisher_count} uitgevers</span>`
          : `<span class="chip">1 artikel</span>`,
    s.is_updating
      ? `<span class="chip live" title="${esc(DEVELOPING.tooltip)}">${esc(DEVELOPING.label)}</span>`
      : '',
    s.followed ? `<span class="chip follow" title="Je volgt dit verhaal">🔔 Gevolgd</span>` : '',
    s.updated_since_read
      ? `<span class="chip new" title="Bijgewerkt nadat je het las">Nieuw sinds gelezen</span>` : '',
    s.category_label ? `<span class="chip cat">${esc(s.category_label)}</span>` : '',
    s.discovery ? `<span class="chip disc">Ontdekking</span>` : '',
  ].filter(Boolean).join('');

  const topics = (s.topics || []).slice(0, 3).map((t) =>
    `<span class="chip topic" data-topic="${esc(t.slug)}">${esc(t.label)}</span>`).join('');

  const why = s.reasons && s.reasons.length
    ? `<button class="why" data-why="${esc(s.id)}">✨ ${esc(s.reasons[0].text)}</button>` : '';

  return `
  <article class="card ${variant}" data-story="${esc(s.id)}">
    ${multi ? `<span class="badge-story">Verhaal · ${s.article_count} artikelen</span>` : ''}
    ${img}
    <div class="body">
      <div style="display:flex;gap:6px;flex-wrap:wrap">${chips}</div>
      <h3><a href="#/story/${esc(s.id)}">${esc(s.headline)}</a></h3>
      ${s.summary ? `<p class="sum">${esc(s.summary)}</p>` : ''}
      ${why}
      <div style="display:flex;gap:6px;flex-wrap:wrap">${topics}</div>
      <div class="meta-row">
        <span>${ago(s.last_updated_at)}</span>
        <span class="pubs">${pubBadges(s.publishers)}</span>
      </div>
      <div class="card-actions">
        <button class="mini ${s.saved ? 'on' : ''}" data-act="save" data-id="${esc(s.id)}"
          aria-pressed="${!!s.saved}">${s.saved ? '★ Bewaard' : '☆ Bewaar'}</button>
        ${followButton(s)}
        ${feedbackButtons(s)}
        ${s.read_at
          ? `<button class="mini" data-unread="${esc(s.id)}" title="Terug naar de Voorpagina">↩ Ongelezen</button>`
          : `<button class="mini" data-act="hide" data-id="${esc(s.id)}">✕ Verberg</button>`}
      </div>
    </div>
  </article>`;
}

/* ---------------------------------------------------------------- list view
   A compact, sortable, filterable table. Same data as the cards, minus the
   images, so a lot more stories fit on screen at once. Column filters live in
   a second header row so each control is unambiguously tied to its column. */

const COLUMNS = {
  section:   { label: 'Sectie',     sort: (s) => s.section || '',             filter: 'select' },
  title:     { label: 'Titel',      sort: (s) => (s.headline || '').toLowerCase(), filter: 'text' },
  publishers:{ label: 'Uitgevers',  sort: (s) => s.publisher_count || 0,      filter: 'minpubs', num: true },
  status:    { label: 'Status',     sort: (s) => (s.is_updating ? 1 : 0),     filter: 'status' },
  category:  { label: 'Categorie',  sort: (s) => s.category_label || '',      filter: 'select' },
  updated:   { label: 'Bijgewerkt', sort: (s) => new Date(s.last_updated_at || 0).getTime(), num: true },
  readAt:    { label: 'Gelezen',    sort: (s) => new Date(s.read_at || 0).getTime(), num: true },
  why:       { label: 'Waarom',     sort: (s) => (s.reasons?.[0]?.text || '').toLowerCase(), filter: 'text' },
};

function listColumns(ctx) {
  const cols = [];
  if (ctx.sections) cols.push('section');
  cols.push('title', 'publishers', 'status', 'category', 'updated');
  if (ctx.read) cols.push('readAt');
  if (ctx.why) cols.push('why');
  return cols;
}

function applyListFilters(rows) {
  const f = state.listFilters;
  return rows.filter((s) => {
    if (f.title && !(s.headline || '').toLowerCase().includes(f.title.toLowerCase())) return false;
    if (f.section && s.section !== f.section) return false;
    if (f.category && s.category_label !== f.category) return false;
    if (f.publishers && (s.publisher_count || 0) < Number(f.publishers)) return false;
    if (f.status === 'dev' && !s.is_updating) return false;
    if (f.status === 'stable' && s.is_updating) return false;
    if (f.why && !(s.reasons?.[0]?.text || '').toLowerCase().includes(f.why.toLowerCase())) return false;
    return true;
  });
}

function applyListSort(rows) {
  const { key, dir } = state.listSort;
  if (!key || !COLUMNS[key]) return rows;
  const get = COLUMNS[key].sort;
  const mul = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = get(a); const y = get(b);
    if (x < y) return -1 * mul;
    if (x > y) return 1 * mul;
    return 0;
  });
}

function filterCell(key, rows) {
  const f = state.listFilters;
  const uniq = (fn) => [...new Set(rows.map(fn).filter(Boolean))].sort();
  const kind = COLUMNS[key].filter;
  if (kind === 'text') {
    return `<input type="search" data-lf="${key}" value="${esc(f[key] || '')}"
      placeholder="filter…" aria-label="Filter op ${esc(COLUMNS[key].label)}" />`;
  }
  if (kind === 'select') {
    const vals = key === 'section' ? uniq((s) => s.section) : uniq((s) => s.category_label);
    return `<select data-lf="${key}" aria-label="Filter op ${esc(COLUMNS[key].label)}">
      <option value="">alle</option>
      ${vals.map((v) => `<option value="${esc(v)}" ${f[key] === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}
    </select>`;
  }
  if (kind === 'minpubs') {
    return `<select data-lf="publishers" aria-label="Minimaal aantal uitgevers">
      <option value="">alle</option>
      ${[2, 3, 4, 5].map((n) => `<option value="${n}" ${f.publishers == n ? 'selected' : ''}>${n}+</option>`).join('')}
    </select>`;
  }
  if (kind === 'status') {
    return `<select data-lf="status" aria-label="Filter op status">
      <option value="">alle</option>
      <option value="dev" ${f.status === 'dev' ? 'selected' : ''}>ontwikkelt zich</option>
      <option value="stable" ${f.status === 'stable' ? 'selected' : ''}>stabiel</option>
    </select>`;
  }
  return '';
}

function storyTable(rows, ctx = {}) {
  rows.forEach((s) => { if (s.reasons?.length) whyCache.set(s.id, s.reasons); });
  const cols = listColumns(ctx);
  const shown = applyListSort(applyListFilters(rows));
  const { key: sk, dir } = state.listSort;

  const head = cols.map((c) => {
    const active = sk === c ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
    const arrow = sk === c ? (dir === 'asc' ? ' ▲' : ' ▼') : '';
    return `<th${active} class="${COLUMNS[c].num ? 'num' : ''} col-${c}">
      <button class="sortbtn" data-sort="${c}">${esc(COLUMNS[c].label)}${arrow}</button></th>`;
  }).join('') + '<th class="col-actions">Acties</th>';

  const filters = cols.map((c) =>
    `<td class="col-${c}">${filterCell(c, rows)}</td>`).join('') + '<td></td>';

  const body = shown.map((s) => {
    const cells = cols.map((c) => {
      if (c === 'section') return `<td class="col-section"><span class="chip">${esc(s.section || '')}</span></td>`;
      if (c === 'title') {
        return `<td class="col-title"><a href="#/story/${esc(s.id)}">${esc(s.headline)}</a>
          ${(s.topics || []).slice(0, 2).map((t) =>
            `<span class="chip topic tiny" data-topic="${esc(t.slug)}">${esc(t.label)}</span>`).join('')}
          ${s.summary ? `<span class="rowsum">${esc(s.summary)}</span>` : ''}</td>`;
      }
      if (c === 'publishers') {
        return `<td class="num col-publishers" title="${esc((s.publishers || []).map((p) => p.name).join(', '))}">
          <span class="chip multi">${s.publisher_count}</span></td>`;
      }
      if (c === 'status') {
        const bits = [];
        if (s.is_updating) {
          bits.push(`<span class="chip live" title="${esc(DEVELOPING.tooltip)}">${esc(DEVELOPING.label)}</span>`);
        }
        if (s.followed) {
          bits.push('<span class="chip follow" title="Je volgt dit verhaal">🔔</span>');
        }
        if (s.updated_since_read) {
          bits.push('<span class="chip new" title="Bijgewerkt nadat je het las">Nieuw</span>');
        }
        return `<td class="col-status">${bits.join(' ') || '<span class="muted">—</span>'}</td>`;
      }
      if (c === 'category') {
        return `<td class="col-category"><span class="chip cat">${esc(s.category_label || '')}</span></td>`;
      }
      if (c === 'updated') {
        return `<td class="num col-updated" title="${esc(clock(s.last_updated_at))}">
          <span class="t-full">${ago(s.last_updated_at)}</span>
          <span class="t-short">${agoShort(s.last_updated_at)}</span></td>`;
      }
      if (c === 'readAt') {
        return `<td class="num col-readAt" title="${esc(clock(s.read_at))}">
          <span class="t-full">${ago(s.read_at)}</span>
          <span class="t-short">${agoShort(s.read_at)}</span></td>`;
      }
      if (c === 'why') {
        const r = s.reasons?.[0]?.text || '';
        return `<td class="col-why">${r
          ? `<button class="why linkish" data-why="${esc(s.id)}">✨ ${esc(r)}</button>` : ''}</td>`;
      }
      return '<td></td>';
    }).join('');

    return `<tr data-story="${esc(s.id)}">${cells}
      <td class="col-actions"><div class="rowacts">
        <button class="mini ${s.saved ? 'on' : ''}" data-act="save" data-id="${esc(s.id)}"
          aria-pressed="${!!s.saved}" title="Bewaar">${s.saved ? '★' : '☆'}</button>
        <button class="mini ${s.followed ? 'on' : ''}" data-act="follow" data-id="${esc(s.id)}"
          aria-pressed="${!!s.followed}"
          title="${s.followed ? 'Je volgt dit verhaal — klik om te stoppen' : 'Dit volgen: blijft op de Voorpagina, ook na lezen'}">🔔</button>
        <button class="mini ${s.feedback === 'more' ? 'on' : ''}" data-act="more" data-id="${esc(s.id)}"
          aria-pressed="${s.feedback === 'more'}"
          title="${s.feedback === 'more' ? 'Meer zo — klik om ongedaan te maken' : 'Meer zo'}">👍</button>
        <button class="mini ${s.feedback === 'less' ? 'on' : ''}" data-act="less" data-id="${esc(s.id)}"
          aria-pressed="${s.feedback === 'less'}"
          title="${s.feedback === 'less' ? 'Minder zo — klik om ongedaan te maken' : 'Minder zo'}">👎</button>
        ${s.read_at
          ? `<button class="mini" data-unread="${esc(s.id)}" title="Markeer als ongelezen">↩</button>`
          : `<button class="mini" data-act="hide" data-id="${esc(s.id)}" title="Verberg">✕</button>`}
      </div></td></tr>`;
  }).join('');

  const filtered = shown.length !== rows.length;
  const ordering = state.listSort.key
    ? `gesorteerd op ${esc(COLUMNS[state.listSort.key].label.toLowerCase())}`
    : (ctx.why ? 'in volgorde van relevantie' : 'in volgorde van nieuwswaarde');
  return `
    <div class="tablewrap">
      <table class="storytable">
        <thead><tr>${head}</tr><tr class="filterrow">${filters}</tr></thead>
        <tbody>${body}</tbody>
      </table>
      ${shown.length ? '' : `<div class="state"><div class="big">🔍</div>
        <h3>Geen verhalen na filteren</h3><p>Pas de filters in de koprij aan.</p>
        <button data-action="clearfilters">Filters wissen</button></div>`}
    </div>
    <div class="tablefoot">${shown.length} van ${rows.length} verhalen · ${ordering}
      ${filtered || state.listSort.key
        ? '· <button class="linkish" data-action="clearfilters">standaard herstellen</button>' : ''}</div>`;
}

/* Toolbar with the card/list switch, shown on every news page. */
function viewToolbar() {
  return `<div class="viewbar">
    <div class="viewtoggle" role="group" aria-label="Weergave">
      <button class="vt ${state.view === 'cards' ? 'on' : ''}" data-view="cards"
        title="Kaarten met afbeeldingen">▦ Kaarten</button>
      <button class="vt ${state.view === 'list' ? 'on' : ''}" data-view="list"
        title="Compacte lijst zonder afbeeldingen, sorteerbaar en filterbaar">☰ Lijst</button>
    </div>
  </div>`;
}

/* Re-render just the table after a sort/filter change, without refetching. */
function rerenderTable() {
  const host = document.querySelector('#tablehost');
  if (host && state.listRows) {
    host.innerHTML = storyTable(state.listRows, state.listCtx);
  }
}

function tableHost(rows, ctx) {
  state.listRows = rows;
  state.listCtx = ctx;
  return `<div id="tablehost">${storyTable(rows, ctx)}</div>`;
}

/* Sorting and filtering belong to one page's dataset. Carrying them across
   navigation silently hides rows elsewhere, and a stale sort would override
   the ranking the next page was built on. */
function resetListState(routeKey) {
  if (state.listRoute === routeKey) return;
  state.listRoute = routeKey;
  state.listSort = { key: '', dir: 'desc' };
  state.listFilters = {};
}

/* Thumbs up/down are a single three-way state per story: up, down, or
   neither. Rendered from the server's verdict so a reload shows what you
   actually chose, and defined once so the card, the table row and the
   "why" panel cannot disagree. */
function feedbackButtons(s, size = 'mini') {
  const up = s.feedback === 'more';
  const down = s.feedback === 'less';
  const cls = size === 'mini' ? 'mini' : 'btn ghost';
  const label = (full, icon) => (size === 'mini' ? full : icon);
  return `
    <button class="${cls} ${up ? 'on' : ''}" data-act="more" data-id="${esc(s.id)}"
      aria-pressed="${up}" title="${up ? 'Klik om dit ongedaan te maken' : 'Meer verhalen zoals dit'}"
      >${label('👍 Meer zo', '👍 Meer zoals dit')}</button>
    <button class="${cls} ${down ? 'on' : ''}" data-act="less" data-id="${esc(s.id)}"
      aria-pressed="${down}" title="${down ? 'Klik om dit ongedaan te maken' : 'Minder verhalen zoals dit'}"
      >${label('👎 Minder zo', '👎 Minder zoals dit')}</button>`;
}

/* Following keeps a story on the front page after you have read it, so you can
   track something developing without it vanishing the moment you look at it. */
function followButton(s, size = 'mini') {
  const on = !!s.followed;
  const cls = size === 'mini' ? 'mini' : 'btn ghost';
  const label = size === 'mini'
    ? (on ? '🔔 Gevolgd' : '🔔 Dit volgen')
    : (on ? '🔔 Je volgt dit verhaal' : '🔔 Dit volgen');
  return `<button class="${cls} ${on ? 'on' : ''}" data-act="follow" data-id="${esc(s.id)}"
    aria-pressed="${on}" title="${on
      ? 'Je volgt dit verhaal — het blijft op de Voorpagina staan, ook als je het gelezen hebt. Klik om te stoppen.'
      : 'Houd dit verhaal op de Voorpagina, ook nadat je het gelezen hebt'}"
    >${label}</button>`;
}

function section(title, hint, stories, opts = {}) {
  if (!stories || !stories.length) return '';
  const lead = opts.lead && stories.length >= 3;
  const cards = lead
    ? storyCard(stories[0], 'lead') + stories.slice(1).map((s) => storyCard(s)).join('')
    : stories.map((s) => storyCard(s, opts.compact ? 'compact' : '')).join('');
  return `
  <section class="section">
    <div class="section-head">
      <h2>${esc(title)}</h2>${hint ? `<span class="hint">${esc(hint)}</span>` : ''}
      ${opts.more ? `<a class="more" href="${opts.more}">Alles bekijken →</a>` : ''}
    </div>
    <div class="${opts.compact ? 'rail' : (lead ? 'grid lead' : 'grid')}">${cards}</div>
  </section>`;
}

function skeleton(n = 6) {
  return `<div class="grid">${Array.from({ length: n }, () =>
    `<div class="skel"><div class="sk img"></div><div class="sk line"></div>
     <div class="sk line s"></div><div class="sk line s"></div></div>`).join('')}</div>`;
}
function emptyState(icon, title, text, btn) {
  return `<div class="state"><div class="big">${icon}</div><h3>${esc(title)}</h3>
    <p>${esc(text)}</p>${btn ? `<button data-action="${esc(btn.action)}">${esc(btn.label)}</button>` : ''}</div>`;
}
function errorState(msg) {
  return emptyState('⚠️', 'Er ging iets mis', msg || 'Het nieuws kon niet worden geladen.',
    { label: 'Opnieuw proberen', action: 'reload' });
}

/* ---------------------------------------------------------------- views */
async function viewFront() {
  resetListState('front');
  main.innerHTML = skeleton(6);
  try {
    const d = await API.get('/api/frontpage?limit=13');
    if (d.empty) {
      main.innerHTML = d.read_skipped
        ? viewToolbar() + emptyState('✅', 'Je bent helemaal bij',
            `Alle ${d.read_skipped} actuele verhalen heb je al gelezen. `
            + 'Nieuwe berichtgeving verschijnt hier vanzelf.',
            { label: 'Naar gelezen artikelen', action: 'goto-read' })
        : emptyState('📭', 'Nog geen nieuws opgehaald',
            'Klik op verversen om de feeds van de uitgevers op te halen.',
            { label: 'Nieuws ophalen', action: 'refresh' });
      return;
    }
    const readNote = d.read_skipped
      ? `<div class="readnote">✅ ${d.read_skipped} gelezen ${d.read_skipped === 1 ? 'verhaal' : 'verhalen'} verborgen ·
         <a href="#/read">bekijk gelezen artikelen</a></div>` : '';
    const cats = Object.entries(d.categories || {})
      .map(([slug, items]) => section(
        state.meta?.categories.find((c) => c.slug === slug)?.label || slug,
        '', items.slice(0, 3), { more: `#/category/${slug}` })).join('');

    if (state.view === 'list') {
      // Sections become a filterable column. The API already de-duplicates
      // across top/latest/trending/fryslan, but the category blocks can repeat
      // a story, so keep the first section a story appeared in.
      const seen = new Set();
      const rows = [];
      const add = (label, items) => (items || []).forEach((s) => {
        if (seen.has(s.id)) return;
        seen.add(s.id);
        rows.push({ ...s, section: label });
      });
      add('Top', d.top);
      add('Laatste', d.latest);
      add('Trending', d.trending);
      add('Fryslân', d.fryslan);
      Object.entries(d.categories || {}).forEach(([slug, items]) =>
        add(state.meta?.categories.find((c) => c.slug === slug)?.label || slug, items));
      main.innerHTML = viewToolbar() + readNote + tableHost(rows, { sections: true });
      return;
    }

    main.innerHTML = viewToolbar() + readNote +
      section('Topverhalen', 'Gerangschikt op actualiteit, aantal onafhankelijke bronnen en nieuwswaarde',
              d.top, { lead: true }) +
      section('Laatste nieuws', 'Zojuist gepubliceerd of bijgewerkt', d.latest.slice(0, 8), { compact: true }) +
      section('Trending', 'Breed opgepakt door meerdere uitgevers', d.trending.slice(0, 6)) +
      section('Fryslân', 'Regionaal nieuws van Omrop Fryslân', d.fryslan.slice(0, 6),
              { more: '#/category/fryslan' }) +
      cats;
  } catch (e) { main.innerHTML = errorState(e.message); }
}

async function viewMyNews() {
  resetListState('mynews');
  main.innerHTML = skeleton(6);
  try {
    const d = await API.get('/api/mynews?limit=40');
    let head = '';
    if (d.cold_start) {
      head = `<div class="notice"><strong>Nog geen persoonlijk profiel.</strong>
        Je ziet nu het belangrijkste algemene nieuws. Kies onderwerpen bij
        <a href="#/favorites" style="color:var(--accent);font-weight:700">Favorieten</a>
        of lees een paar verhalen — Mijn nieuws leert daarvan.</div>`;
    } else {
      head = `<div class="notice">Gepersonaliseerd op basis van
        <strong>${d.has_favourites ? 'je favorieten' : 'je leesgedrag'}</strong>
        ${d.learned_count ? `en <strong>${d.learned_count} geleerde interesses</strong>` : ''}.
        Elk verhaal toont waarom het hier staat.
        <a href="#/privacy" style="color:var(--accent);font-weight:700">Beheer je gegevens →</a></div>`;
    }
    const readNote = d.read_skipped
      ? `<div class="readnote">✅ ${d.read_skipped} gelezen ${d.read_skipped === 1 ? 'verhaal' : 'verhalen'} verborgen ·
         <a href="#/read">bekijk gelezen artikelen</a></div>` : '';
    main.innerHTML = viewToolbar() + head + readNote + (d.stories.length
      ? (state.view === 'list'
          ? tableHost(d.stories, { why: true })
          : `<div class="grid">${d.stories.map((s) => storyCard(s)).join('')}</div>`)
      : emptyState('🔍', 'Geen verhalen', 'Voeg onderwerpen toe of ververs het nieuws.',
        { label: 'Naar favorieten', action: 'favorites' }));
  } catch (e) { main.innerHTML = errorState(e.message); }
}

async function viewRead() {
  resetListState('read');
  main.innerHTML = skeleton(6);
  try {
    const d = await API.get('/api/read');
    const head = `
      <section class="section">
        <div class="section-head"><h2>Gelezen nieuwsartikelen</h2>
          <span class="hint">${d.total} verhalen · recentst gelezen eerst</span></div>
        <div class="notice">
          <label class="switch">
            <input type="checkbox" id="hideread" ${d.hide_read ? 'checked' : ''} />
            <span>Verberg gelezen verhalen op de Voorpagina en Mijn nieuws</span>
          </label>
          <div style="margin-top:8px;font-size:13px">
            Een verhaal dat ná jouw leesmoment is bijgewerkt verschijnt opnieuw —
            dan is het weer nieuw voor je. Verhalen die je <strong>volgt</strong> (🔔)
            blijven altijd staan, ook als je ze gelezen hebt.
          </div>
          ${d.total ? `<div style="margin-top:12px">
            <button class="btn ghost" data-action="unread-all">↩ Alles als ongelezen markeren</button>
          </div>` : ''}
        </div>
      </section>`;

    if (!d.total) {
      main.innerHTML = head + emptyState('📖', 'Nog niets gelezen',
        'Zodra je een verhaal opent, verschijnt het hier en verdwijnt het van de Voorpagina.');
      return;
    }
    main.innerHTML = head + viewToolbar() + (state.view === 'list'
      ? tableHost(d.stories, { read: true })
      : `<div class="grid">${d.stories.map((s) => storyCard(s)).join('')}</div>`);
  } catch (e) { main.innerHTML = errorState(e.message); }
}

async function viewFavorites() {
  main.innerHTML = skeleton(3);
  try {
    const [favs, topics] = await Promise.all([
      API.get('/api/favourites'), API.get('/api/topics?limit=80'),
    ]);
    const favSlugs = new Set(favs.favourites.map((f) => f.slug));
    const suggestions = topics.topics.filter((t) => !favSlugs.has(t.slug));

    main.innerHTML = `
      <section class="section">
        <div class="section-head"><h2>Favorieten</h2>
          <span class="hint">Jouw expliciete keuzes wegen het zwaarst in Mijn nieuws</span></div>
        <form class="addrow" id="addfav">
          <input id="favinput" placeholder="Zoek of voeg een onderwerp toe (bijv. Makkum, SC Heerenveen, AI)…"
                 autocomplete="off" list="topiclist" />
          <datalist id="topiclist">${topics.topics.map((t) =>
            `<option value="${esc(t.label)}">`).join('')}</datalist>
          <button type="submit">Toevoegen</button>
        </form>
        ${favs.favourites.length
          ? `<div class="fav-grid">${favs.favourites.map((f) =>
              `<button class="fav on" data-fav-remove="${esc(f.slug)}">
                 <span class="n">${esc(f.label)}</span><span class="c">✕</span></button>`).join('')}</div>`
          : `<div class="notice">Je volgt nog geen onderwerpen. Kies er hieronder een paar — of typ je eigen onderwerp.</div>`}
      </section>

      <section class="section">
        <div class="section-head"><h2>Voorgesteld</h2>
          <span class="hint">Onderwerpen uit het actuele nieuws</span></div>
        <div class="fav-grid">${suggestions.slice(0, 48).map((t) =>
          `<button class="fav" data-fav-add="${esc(t.slug)}" data-label="${esc(t.label)}">
             <span class="n">${esc(t.label)}</span>
             <span class="c">${t.story_count || 0}</span></button>`).join('')}</div>
      </section>

      <section class="section">
        <div class="section-head"><h2>Beheer</h2></div>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <a class="btn ghost" href="#/sources">Bronnen beheren</a>
          <a class="btn ghost" href="#/privacy">Personalisatie & privacy</a>
          <a class="btn ghost" href="#/saved">Bewaarde verhalen</a>
        </div>
      </section>`;
  } catch (e) { main.innerHTML = errorState(e.message); }
}

async function viewList(title, hint, qs) {
  resetListState(`list:${qs}`);
  main.innerHTML = skeleton(6);
  try {
    const d = await API.get(`/api/stories?${qs}`);
    main.innerHTML = viewToolbar() + `
      <section class="section">
        <div class="section-head"><h2>${esc(title)}</h2>
          <span class="hint">${esc(hint)} · ${d.total} verhalen</span></div>
        ${filterBar()}
        ${d.stories.length
          ? (state.view === 'list'
              ? tableHost(d.stories, {})
              : `<div class="grid">${d.stories.map((s) => storyCard(s)).join('')}</div>`)
          : emptyState('🔍', 'Niets gevonden', 'Pas je zoekopdracht of filters aan.')}
      </section>`;
  } catch (e) { main.innerHTML = errorState(e.message); }
}

function filterBar() {
  const m = state.meta || { categories: [], publishers: [] };
  const f = state.filters;
  const opt = (v, l, cur) => `<option value="${esc(v)}" ${cur === v ? 'selected' : ''}>${esc(l)}</option>`;
  return `<div class="filters" id="filterbar">
    <select data-f="category"><option value="">Alle categorieën</option>
      ${m.categories.map((c) => opt(c.slug, c.label, f.category)).join('')}</select>
    <select data-f="publisher"><option value="">Alle uitgevers</option>
      ${m.publishers.map((p) => opt(p.id, p.name, f.publisher)).join('')}</select>
    <select data-f="location"><option value="">Overal</option>
      ${['fryslan', 'nl', 'world'].map((v) => opt(v,
        { fryslan: 'Fryslân', nl: 'Nederland', world: 'Buitenland' }[v], f.location)).join('')}</select>
    <select data-f="hours"><option value="">Elke periode</option>
      ${[['6', 'Laatste 6 uur'], ['24', 'Laatste 24 uur'], ['72', 'Laatste 3 dagen'],
         ['168', 'Laatste week']].map(([v, l]) => opt(v, l, f.hours)).join('')}</select>
  </div>`;
}

async function viewStory(id) {
  main.innerHTML = skeleton(2);
  try {
    const s = await API.get(`/api/stories/${encodeURIComponent(id)}`);
    track('open', s.id);
    const dis = (s.disagreements || []).length
      ? `<div class="warn-box"><div class="lbl">Bronnen verschillen</div>
          ${s.disagreements.map((d) => `<div>• ${esc(d.detail)}</div>`).join('')}
          <div style="margin-top:6px;color:var(--muted)">Deze cijfers zijn niet bevestigd; vergelijk de originele artikelen.</div>
         </div>` : '';

    main.innerHTML = `
      <article class="story">
        <header class="storyhead">
          <div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:8px">
            <span class="chip cat">${esc(s.category_label || '')}</span>
            <span class="chip multi">${s.publisher_count} ${s.publisher_count === 1 ? 'uitgever' : 'uitgevers'} · ${s.article_count} artikelen</span>
            ${s.is_updating ? `<span class="chip live" title="${esc(DEVELOPING.tooltip)}">${esc(DEVELOPING.label)}</span>` : ''}
            ${s.followed ? '<span class="chip follow" title="Je volgt dit verhaal">🔔 Gevolgd</span>' : ''}
            ${s.geo_scope === 'fryslan' ? '<span class="chip">Fryslân</span>' : ''}
          </div>
          <h1>${esc(s.headline)}</h1>
          <div class="meta-row">
            <span>Laatste update ${clock(s.last_updated_at)} · ${ago(s.last_updated_at)}</span>
            <span class="pubs">${pubBadges(s.publishers)}</span>
          </div>
        </header>

        <div class="storycols">
        <div class="storycol-main">
        ${s.is_updating ? `<div class="notice" style="border-left-color:var(--accent)">
            <strong>Dit verhaal ${esc(DEVELOPING.short)}.</strong> Een uitgever heeft een artikel
            herzien of er kwam recent nieuwe berichtgeving bij. Controleer de bronlinks
            hiernaast voor de actuele stand.</div>` : ''}
        ${s.headline_source_name
          ? `<div style="font-size:12.5px;color:var(--faint);margin-bottom:10px">
               Kopregel overgenomen van ${esc(s.headline_source_name)} als meest neutrale formulering.</div>` : ''}
        ${s.summary ? `<div class="ai-box"><div class="lbl">✨ Automatisch samengevat uit de bronteksten</div>
            <p>${esc(s.summary)}</p></div>` : ''}
        ${dis}

        <div class="card-actions" style="margin-bottom:8px">
          <button class="mini ${s.saved ? 'on' : ''}" data-act="save" data-id="${esc(s.id)}"
            aria-pressed="${!!s.saved}">${s.saved ? '★ Bewaard' : '☆ Bewaar'}</button>
          ${followButton(s)}
          ${feedbackButtons(s)}
        </div>

        ${(s.topics || []).length ? `<div class="sub">Onderwerpen & entiteiten</div>
          <div style="display:flex;gap:7px;flex-wrap:wrap">
            ${s.topics.map((t) => `<span class="chip topic" data-topic="${esc(t.slug)}">${esc(t.label)}</span>`).join('')}
            ${(s.entities || []).slice(0, 8).map((e) => `<span class="chip">${esc(e)}</span>`).join('')}
          </div>` : ''}

        <div class="sub">Tijdlijn van publicaties</div>
        <div class="timeline">${(s.timeline || []).map((t) =>
          `<div class="tl"><div class="t">${clock(t.at)} · ${esc(t.publisher)}${t.revised ? ' · bijgewerkt' : ''}</div>
            <a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.title)}</a></div>`).join('')}</div>
        </div>

        <div class="storycol-side">
        <div class="sub" style="margin-top:0">Zo brengen de uitgevers het (${(s.articles || []).length} artikelen)</div>
        <div class="src-list">${(s.articles || []).map((a) => `
          <div class="src">
            ${a.image_url ? `<img class="sthumb" loading="lazy" src="${esc(a.image_url)}" alt=""
                 onerror="this.remove()">` : ''}
            <div class="sbody">
              <div style="display:flex;gap:7px;align-items:center;margin-bottom:5px;flex-wrap:wrap">
                <span class="pub" style="color:${esc(a.publisher.colour || 'currentColor')}">
                  <span class="dot"></span><span style="color:var(--text)">${esc(a.publisher.name)}</span></span>
                <span style="font-size:12px;color:var(--faint)">${clock(a.updated_at || a.published_at)}</span>
                ${a.revision > 1 ? '<span class="chip">herzien</span>' : ''}
              </div>
              <h4><a href="${esc(a.url)}" target="_blank" rel="noopener"
                     data-article="${esc(a.id)}" data-pub="${esc(a.publisher.id)}"
                     data-story="${esc(s.id)}">${esc(a.title)}</a></h4>
              ${a.description ? `<p class="sdesc">${esc(a.description)}</p>` : ''}
              <a href="${esc(a.url)}" target="_blank" rel="noopener"
                 data-article="${esc(a.id)}" data-pub="${esc(a.publisher.id)}" data-story="${esc(s.id)}"
                 style="font-size:12.5px;color:var(--accent);font-weight:650">Lees bij ${esc(a.publisher.name)} →</a>
            </div>
          </div>`).join('')}</div>
        </div>
        </div>

        ${(s.related || []).length ? `<div class="related-wrap"><div class="sub">Gerelateerd</div>
          <div class="grid">${s.related.map((r) => storyCard(r)).join('')}</div></div>` : ''}
      </article>`;
    window.scrollTo(0, 0);
  } catch (e) { main.innerHTML = errorState(e.message); }
}

async function viewSources() {
  main.innerHTML = skeleton(2);
  try {
    const d = await API.get('/api/sources');
    main.innerHTML = `
      <section class="section">
        <div class="section-head"><h2>Bronnen</h2>
          <span class="hint">Voeg zelf uitgevers of feeds toe</span></div>
        <form class="addrow" id="probesrc">
          <input name="url" id="probeurl" placeholder="Plak een site- of feed-URL, bijv. tweakers.net"
                 autocomplete="off" required />
          <button type="submit">Controleren</button>
        </form>
        <div id="proberesult"></div>
        <div class="notice">We gebruiken alleen officiële, openbaar beschikbare feeds.
          Plak gerust het adres van de website — we zoeken de feed er zelf bij.
          Staat een pagina achter een toestemmingsscherm, dan wordt dat
          <strong>niet omzeild</strong>; we kijken dan of er een open feed naast staat.</div>
        <table class="src-table">
          <thead><tr><th></th><th>Uitgever</th><th>Feed</th><th>Laatste check</th><th>Items</th><th></th></tr></thead>
          <tbody>${d.sources.map((s) => `
            <tr>
              <td class="${s.last_status === 'error' ? 'dot-err' : 'dot-ok'}">●</td>
              <td><strong>${esc(s.publisher_name)}</strong><br><span style="color:var(--faint)">${esc(s.name)}</span></td>
              <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis">
                <a href="${esc(s.url)}" target="_blank" rel="noopener" style="color:var(--muted)">${esc(s.url)}</a>
                ${s.last_error ? `<br><span style="color:var(--accent);font-size:12px">${esc(s.last_error)}</span>` : ''}</td>
              <td>${s.last_fetch_at ? ago(s.last_fetch_at) : '—'}</td>
              <td>${s.last_item_count || 0}</td>
              <td>${s.user_added ? `<button class="mini" data-src-del="${esc(s.id)}">Verwijder</button>` : ''}</td>
            </tr>`).join('')}</tbody>
        </table>
      </section>`;
  } catch (e) { main.innerHTML = errorState(e.message); }
}

/* Render what the probe found: either a single valid feed, or alternatives to
   confirm. Nothing is ever added without an explicit click. */
function renderProbe(d) {
  const box = document.querySelector('#proberesult');
  if (!box) return;
  const icon = { feed: '✅', gated: '🔒', html: '🔎', error: '⚠️' }[d.status] || '🔎';
  const tone = d.status === 'feed' ? 'ok' : d.status === 'error' ? 'err' : 'warn';

  const card = (c) => `
    <div class="cand${c.already_added ? ' dim' : ''}">
      <div class="cand-body">
        <strong>${esc(c.title)}</strong>
        <span class="cand-url">${esc(c.url)}</span>
        <span class="cand-meta">${c.item_count} artikelen · ${c.has_images
          ? 'met afbeeldingen' : 'zonder afbeeldingen'}</span>
        ${c.sample ? `<span class="cand-sample">Nieuwste: “${esc(c.sample)}”</span>` : ''}
      </div>
      ${c.already_added
        ? '<span class="chip">Al toegevoegd</span>'
        : `<button class="btn" data-add-feed="${esc(c.url)}"
             data-title="${esc(c.title)}">Toevoegen</button>`}
    </div>`;

  box.innerHTML = `
    <div class="probe probe-${tone}">
      <div class="probe-msg">${icon} ${esc(d.message)}</div>
      ${(d.candidates || []).length
        ? `<div class="cand-list">${d.candidates.map(card).join('')}</div>` : ''}
    </div>`;
}

async function viewPrivacy() {
  main.innerHTML = skeleton(2);
  try {
    const d = await API.get('/api/privacy');
    main.innerHTML = `
      <section class="section">
        <div class="section-head"><h2>Personalisatie & privacy</h2>
          <span class="hint">Alles blijft lokaal op deze machine</span></div>
        <div class="notice">${esc(d.storage)} Er worden alleen onderwerp-, uitgever- en
          verhaal-ID's opgeslagen. Er worden geen gevoelige persoonskenmerken afgeleid.</div>

        <div class="sub">Expliciete favorieten (zwaarst wegend)</div>
        <div style="display:flex;gap:7px;flex-wrap:wrap">${d.favourites.length
          ? d.favourites.map((f) => `<span class="chip cat">${esc(f.label)}</span>`).join('')
          : '<span style="color:var(--muted)">Nog geen favorieten gekozen.</span>'}</div>

        <div class="sub">Geleerde interesses (afgeleid uit je leesgedrag)</div>
        ${d.learned_interests.length
          ? `<div class="fav-grid">${d.learned_interests.map((t) =>
              `<button class="fav" data-forget="${esc(t.slug)}">
                 <span class="n">${esc(t.label)}</span>
                 <span class="c">${Math.round(t.score * 100)}% · vergeet ✕</span></button>`).join('')}</div>`
          : '<div style="color:var(--muted)">Nog niets geleerd — lees een paar verhalen.</div>'}

        <div class="sub">Verborgen verhalen${d.hidden_total ? ` (${d.hidden_total})` : ''}</div>        ${d.hidden_total ? `
          <p style="color:var(--muted);font-size:14px;margin:0 0 12px">
            Deze verhalen zijn uit je feeds gefilterd. Zet ze terug om ze weer te zien.</p>
          <div class="hidden-list">
            ${d.hidden_stories.map((s) => `
              <div class="hidden-row">
                <div class="hr-body">
                  <a href="#/story/${esc(s.id)}">${esc(s.headline)}</a>
                  <span class="hr-meta">${esc(s.category || '')} · verborgen ${ago(s.hidden_at)}</span>
                </div>
                <button class="mini" data-unhide="${esc(s.id)}">↩ Weer tonen</button>
              </div>`).join('')}
          </div>
          ${d.hidden_total > d.hidden_stories.length ? `
            <p style="color:var(--faint);font-size:12.5px;margin-top:10px">
              ${d.hidden_total - d.hidden_stories.length} verborgen verhaal(en) bestaan niet meer
              — die zijn uit het nieuwsarchief verdwenen.</p>` : ''}
          <div style="margin-top:12px">
            <button class="btn ghost" data-action="unhide-all">↩ Alles weer tonen</button>
          </div>`
          : '<div style="color:var(--muted)">Je hebt niets verborgen.</div>'}

        <div class="sub">Gevolgde verhalen${d.followed_total ? ` (${d.followed_total})` : ''}</div>
        ${d.followed_total ? `
          <p style="color:var(--muted);font-size:14px;margin:0 0 12px">
            Deze blijven op de Voorpagina staan, ook nadat je ze gelezen hebt.</p>
          <div class="hidden-list">
            ${d.followed_stories.map((s) => `
              <div class="hidden-row">
                <div class="hr-body">
                  <a href="#/story/${esc(s.id)}">${esc(s.headline)}</a>
                  <span class="hr-meta">${esc(s.category || '')} · bijgewerkt ${ago(s.last_updated_at)}</span>
                </div>
                <button class="mini on" data-act="follow" data-id="${esc(s.id)}"
                  aria-pressed="true" title="Klik om niet meer te volgen">🔔 Gevolgd</button>
              </div>`).join('')}
          </div>`
          : '<div style="color:var(--muted)">Je volgt nog geen verhalen. Gebruik 🔔 Dit volgen op een verhaal.</div>'}

        <div class="sub">Expliciete feedback op verhalen</div>
        <div style="color:var(--muted);font-size:14px">
          👍 ${d.feedback_counts?.more || 0} keer "meer zo" ·
          👎 ${d.feedback_counts?.less || 0} keer "minder zo".
          Klik een actieve knop op een verhaal opnieuw om je keuze in te trekken.
        </div>

        <div class="sub">Opgeslagen signalen</div>
        <div style="color:var(--muted);font-size:14px">
          ${Object.entries(d.interaction_counts || {}).map(([k, v]) =>
            `${esc(k)}: <strong>${v}</strong>`).join(' · ') || 'Nog geen interacties.'}
          (totaal ${d.total_interactions})
        </div>

        <div style="margin-top:22px;display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn danger" data-action="reset">Geleerde voorkeuren wissen</button>
          <a class="btn ghost" href="#/favorites">Favorieten beheren</a>
        </div>
      </section>`;
  } catch (e) { main.innerHTML = errorState(e.message); }
}

/* ---------------------------------------------------------------- ticker */
/* The strip under the address bar. Two jobs: surface the current top
   headlines without a click, and show the quotes the reader picked.

   Built as a transform animation over a duplicated track rather than CSS
   `marquee` or a scroll timer, because that keeps it on the compositor and
   lets a single `animation-play-state` pause it on hover. */
const ticker = {
  paused: false, stories: [], stocks: [], timer: null,
  hoverPause: false,
};

function tickerStockChip(q) {
  if (!q.ok) {
    return `<span class="tk-stock tk-stock-bad" title="Koers tijdelijk niet beschikbaar">`
      + `<b>${esc(q.label || q.symbol)}</b> <span class="tk-na">n.b.</span></span>`;
  }
  const pct = q.change_pct;
  const dir = pct == null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
  const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : '▬';
  // Dutch notation: comma as decimal separator.
  const price = q.price == null ? '—'
    : q.price.toLocaleString('nl-NL', { maximumFractionDigits: q.price < 10 ? 4 : 2 });
  const pctTxt = pct == null ? '' : `${pct > 0 ? '+' : ''}${pct.toLocaleString('nl-NL', {
    minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  const cur = q.currency === 'EUR' ? '€' : q.currency === 'USD' ? '$' : esc(q.currency || '');
  return `<span class="tk-stock tk-${dir}" title="${esc(q.name || q.symbol)}${
    q.exchange ? ' · ' + esc(q.exchange) : ''}">`
    + `<b>${esc(q.label || q.symbol)}</b> <span class="tk-price">${cur}${price}</span> `
    + `<span class="tk-delta">${arrow} ${pctTxt}</span></span>`;
}

function tickerSegment() {
  return ticker.stories.map((s) =>
    `<a class="tk-story" href="#/story/${encodeURIComponent(s.id)}"`
    + ` data-ticker-story="${esc(s.id)}">`
    + (s.publisher_count > 2 ? `<span class="tk-count">${s.publisher_count}×</span>` : '')
    + `<span>${esc(s.headline)}</span></a>`
  ).join('<span class="tk-sep" aria-hidden="true">•</span>');
}

function paintTicker() {
  const bar = $('#ticker');
  const track = $('#ticker-track');
  const strip = $('#ticker-stocks-strip');
  if (!bar || !track) return;
  if (!ticker.stories.length && !ticker.stocks.length) { bar.hidden = true; return; }
  bar.hidden = false;

  // Quotes are pinned, not scrolled. A price you have to wait a full lap to
  // see is useless - the reason to pick a stock is to glance at it.
  if (strip) {
    strip.innerHTML = ticker.stocks.map(tickerStockChip).join('');
    strip.hidden = !ticker.stocks.length;
    // The scrollbar is hidden for looks, so without this the extra chips are
    // simply invisible. A fade on the edge is the only cue that there is more.
    requestAnimationFrame(() => {
      strip.classList.toggle('is-scrollable', strip.scrollWidth > strip.clientWidth + 2);
    });
  }

  const segment = tickerSegment();
  // The track holds the same content twice; the animation shifts it by exactly
  // half its width, so the hand-off is invisible and the loop never "jumps".
  track.innerHTML = `<div class="tk-seg">${segment}</div>`
    + `<div class="tk-seg" aria-hidden="true">${segment}</div>`;

  // Duration from content width keeps the reading speed constant whether there
  // are three headlines or thirty. ~95px/s is brisk enough not to feel stuck
  // but still comfortably readable.
  requestAnimationFrame(() => {
    const w = track.firstElementChild?.getBoundingClientRect().width || 0;
    track.style.setProperty('--tk-duration', `${Math.max(18, Math.round(w / 95))}s`);
  });
  applyTickerPause();
}

function applyTickerPause() {
  const track = $('#ticker-track');
  const btn = $('#ticker-pause');
  if (!track) return;
  const stop = ticker.paused || ticker.hoverPause;
  track.style.animationPlayState = stop ? 'paused' : 'running';
  if (btn) {
    btn.textContent = ticker.paused ? '▶' : '⏸';
    btn.setAttribute('aria-pressed', String(ticker.paused));
    btn.title = ticker.paused ? 'Ticker hervatten' : 'Ticker pauzeren';
    btn.setAttribute('aria-label', btn.title);
  }
}

async function loadTicker() {
  try {
    const d = await API.get('/api/ticker');
    ticker.stories = d.stories || [];
    ticker.stocks = d.stocks || [];
    paintTicker();
  } catch { /* a ticker is decoration: never let it surface an error banner */ }
}

/* ------------------------------------------------------- stock picker */
function stockRow(q) {
  const pct = q.change_pct;
  const sign = pct == null ? '' : pct > 0 ? '+' : '';
  const dir = pct == null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat';
  return `<li class="stk">
    <div class="stk-body">
      <strong>${esc(q.name || q.label || q.symbol)}</strong>
      <span class="stk-sym">${esc(q.symbol)}${q.exchange ? ' · ' + esc(q.exchange) : ''}</span>
    </div>
    ${q.ok ? `<span class="stk-quote tk-${dir}">${
      q.currency === 'EUR' ? '€' : q.currency === 'USD' ? '$' : esc(q.currency || '')}${
      q.price.toLocaleString('nl-NL', { maximumFractionDigits: q.price < 10 ? 4 : 2 })}
      <small>${pct == null ? '' : sign + pct.toLocaleString('nl-NL', {
        minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%'}</small></span>`
      : '<span class="stk-quote tk-flat">niet beschikbaar</span>'}
    <button class="btn ghost small" data-stock-del="${esc(q.symbol)}">Verwijderen</button>
  </li>`;
}

/* Keeps the "In de ticker 2/12" counter honest while rows are added or
   removed in place. */
function bumpStockCount(delta) {
  const el = $('#stk-count');
  if (!el) return;
  const [n, max] = el.textContent.split('/').map((v) => parseInt(v, 10));
  if (Number.isNaN(n)) return;
  el.textContent = `${Math.max(0, n + delta)}/${max}`;
}

async function openStockPicker() {
  openSheet('Koersen in de ticker', '<p class="muted">Laden…</p>');
  let d;
  try { d = await API.get('/api/stocks'); }
  catch (e) { $('#sheet-body').innerHTML = `<p class="err">Laden mislukt: ${esc(e.message)}</p>`; return; }

  const list = d.quotes.length
    ? `<ul class="stk-list" id="stk-watchlist">${d.quotes.map(stockRow).join('')}</ul>`
    : `<ul class="stk-list" id="stk-watchlist"></ul>
       <p class="muted">Nog geen fondsen gekozen. Zoek hierboven op bedrijfsnaam
       (bijv. <em>Heineken</em>) of op ticker (<em>ASML.AS</em>).</p>`;

  $('#sheet-body').innerHTML = `
    <form id="stocksearch" class="stk-search" autocomplete="off">
      <input id="stockq" type="search" placeholder="Bedrijfsnaam of ticker, bijv. Heineken"
             aria-label="Zoek een fonds" />
      <button class="btn" type="submit">Zoeken</button>
    </form>
    <div id="stockresults"></div>
    <h3 class="stk-head">In de ticker
      <span class="muted" id="stk-count">${d.quotes.length}/${d.max}</span></h3>
    ${list}
    <p class="muted small">Koersen komen van Yahoo Finance en lopen maximaal een
    minuut achter. Geen advies — alleen ter informatie.</p>`;
  $('#stockq')?.focus();
}

async function runStockSearch(term) {
  const box = $('#stockresults');
  if (!box) return;
  box.innerHTML = '<p class="muted">Zoeken…</p>';
  let r;
  try { r = await API.get(`/api/stocks/search?q=${encodeURIComponent(term)}`); }
  catch (e) { box.innerHTML = `<p class="err">Zoeken mislukt: ${esc(e.message)}</p>`; return; }

  if (!r.results.length) {
    box.innerHTML = `<p class="muted">Niets gevonden voor “${esc(term)}”. Probeer de
      beursticker, bijvoorbeeld <em>HEIA.AS</em>.</p>`;
    return;
  }
  box.innerHTML = `<ul class="stk-list">${r.results.map((s) => `
    <li class="stk">
      <div class="stk-body">
        <strong>${esc(s.name)}</strong>
        <span class="stk-sym">${esc(s.symbol)}${s.exchange ? ' · ' + esc(s.exchange) : ''}</span>
      </div>
      <button class="btn small" data-stock-add="${esc(s.symbol)}"
              data-stock-name="${esc(s.name)}">Toevoegen</button>
    </li>`).join('')}</ul>`;
}

/* ---------------------------------------------------------------- sheet */
function openSheet(title, html) {
  $('#sheet-title').textContent = title;
  $('#sheet-body').innerHTML = html;
  $('#sheet').hidden = false;
  $('#backdrop').hidden = false;
}
function closeSheet() { $('#sheet').hidden = true; $('#backdrop').hidden = true; }

function showWhy(storyId) {
  const card = document.querySelector(`[data-story="${CSS.escape(storyId)}"]`);
  const data = whyCache.get(storyId) || [];
  const title = card?.querySelector('h3, .col-title a')?.textContent?.trim() || '';
  // Mirror whatever the on-page buttons currently show, so the panel never
  // contradicts the card it was opened from.
  const active = document.querySelector(
    `[data-id="${CSS.escape(storyId)}"][data-act="more"].on`) ? 'more'
    : document.querySelector(`[data-id="${CSS.escape(storyId)}"][data-act="less"].on`) ? 'less' : null;
  openSheet('Waarom zie ik dit?', `
    <p style="color:var(--muted);margin-top:0">${esc(title)}</p>
    <ul style="padding-left:18px;line-height:1.8">
      ${data.map((r) => `<li>${esc(r.text)}</li>`).join('') || '<li>Actueel nieuws.</li>'}
    </ul>
    <div class="notice" style="margin-top:18px">Expliciete favorieten wegen altijd zwaarder dan
      afgeleide interesses. Recent gedrag telt zwaarder dan oud gedrag.
      Klik een actieve knop opnieuw om je keuze in te trekken.</div>
    <div style="display:flex;gap:9px;flex-wrap:wrap">
      ${feedbackButtons({ id: storyId, feedback: active }, 'btn')}
      <button class="btn ghost" data-act="hide" data-id="${esc(storyId)}">✕ Verberg dit verhaal</button>
      <a class="btn ghost" href="#/privacy">Beheer personalisatie</a>
    </div>`);
}

/* ---------------------------------------------------------------- router */
const routes = [
  [/^#\/front$/, viewFront],
  [/^#\/mynews$/, viewMyNews],
  [/^#\/read$/, viewRead],
  [/^#\/favorites$/, viewFavorites],
  [/^#\/sources$/, viewSources],
  [/^#\/privacy$/, viewPrivacy],
  [/^#\/saved$/, () => viewList('Bewaard', 'Verhalen die je hebt bewaard', 'saved=true&limit=60')],
  [/^#\/topic\/(.+)$/, (m) => viewList(decodeURIComponent(m[1]).replace(/-/g, ' '),
    'Alle verhalen over dit onderwerp', `topic=${encodeURIComponent(m[1])}&limit=60`)],
  [/^#\/category\/(.+)$/, (m) => viewList(
    state.meta?.categories.find((c) => c.slug === m[1])?.label || m[1],
    'Categorie', `category=${encodeURIComponent(m[1])}&limit=60`)],
  [/^#\/story\/(.+)$/, (m) => viewStory(m[1])],
  [/^#\/search$/, () => {
    const q = new URLSearchParams(location.hash.split('?')[1] || '').get('q') || '';
    $('#search').value = q;
    return viewList(`Zoeken: “${q}”`, 'Resultaten', `q=${encodeURIComponent(q)}&limit=60`);
  }],
];

function route() {
  const hash = location.hash || '#/front';
  const path = hash.split('?')[0];
  document.querySelectorAll('.mainnav a').forEach((a) =>
    a.classList.toggle('active', a.getAttribute('href') === path));
  $('#mainnav').classList.remove('open');
  closeSheet();
  for (const [re, fn] of routes) {
    const m = (hash.startsWith('#/search') ? '#/search' : path).match(re);
    if (m) { fn(m); return; }
  }
  viewFront();
}

/* ---------------------------------------------------------------- events */
document.addEventListener('click', async (ev) => {
  const t = ev.target;

  const viewBtn = t.closest('[data-view]');
  if (viewBtn) {
    ev.preventDefault();
    if (state.view !== viewBtn.dataset.view) {
      state.view = viewBtn.dataset.view;
      localStorage.setItem('nieuws-view', state.view);
      route();
    }
    return;
  }

  const sortBtn = t.closest('[data-sort]');
  if (sortBtn) {
    ev.preventDefault();
    const key = sortBtn.dataset.sort;
    const cur = state.listSort;
    // First click on a new column sorts descending for numbers and dates
    // (newest / most sources first) and ascending for text.
    state.listSort = cur.key === key
      ? { key, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
      : { key, dir: COLUMNS[key]?.num ? 'desc' : 'asc' };
    rerenderTable();
    return;
  }

  const why = t.closest('[data-why]');
  if (why) { ev.preventDefault(); showWhy(why.dataset.why); return; }

  const topic = t.closest('[data-topic]');
  if (topic) { location.hash = `#/topic/${encodeURIComponent(topic.dataset.topic)}`; return; }

  const act = t.closest('[data-act]');
  if (act) {
    ev.preventDefault();
    const { act: a, id } = act.dataset;

    if (a === 'save') {
      const on = act.classList.toggle('on');
      act.setAttribute('aria-pressed', String(on));
      if (act.closest('.rowacts')) act.textContent = on ? '★' : '☆';
      else act.textContent = on ? '★ Bewaard' : '☆ Bewaar';
      track(on ? 'save' : 'unsave', id);
      return;
    }

    if (a === 'follow') {
      // Toggle, and keep every copy of this story on screen in step.
      const next = !act.classList.contains('on');
      document.querySelectorAll(`[data-id="${CSS.escape(id)}"][data-act="follow"]`)
        .forEach((btn) => {
          btn.classList.toggle('on', next);
          btn.setAttribute('aria-pressed', String(next));
          const big = !btn.classList.contains('mini');
          btn.textContent = next
            ? (big ? '🔔 Je volgt dit verhaal' : '🔔 Gevolgd')
            : '🔔 Dit volgen';
          btn.title = next
            ? 'Je volgt dit verhaal — het blijft op de Voorpagina staan, ook als je het gelezen hebt. Klik om te stoppen.'
            : 'Houd dit verhaal op de Voorpagina, ook nadat je het gelezen hebt';
        });
      track(next ? 'follow' : 'unfollow', id);
      return;
    }

    if (a === 'more' || a === 'less') {
      // One three-way state: clicking the active button withdraws it,
      // clicking the other replaces it. Every copy of these buttons for this
      // story is updated, because the same story can be on screen twice (a
      // card and the why-panel, or a row and a related item).
      const wasOn = act.classList.contains('on');
      const next = wasOn ? null : a;
      document.querySelectorAll(`[data-id="${CSS.escape(id)}"]`).forEach((btn) => {
        const kind = btn.dataset.act;
        if (kind !== 'more' && kind !== 'less') return;
        const on = kind === next;
        btn.classList.toggle('on', on);
        btn.setAttribute('aria-pressed', String(on));
      });
      track(next || 'feedback_clear', id);
      return;
    }

    track(a, id);
    act.classList.add('on');
    if (a === 'hide') {
      if (!act.closest('.rowacts')) act.textContent = '✓ Verborgen';
      const card = document.querySelector(`[data-story="${CSS.escape(id)}"]`);
      if (card) { card.style.opacity = '.35'; setTimeout(() => card.remove(), 450); }
      closeSheet();
    }
    return;
  }

  const artLink = t.closest('[data-article]');
  if (artLink) {
    track('open_article', artLink.dataset.story,
      { article_id: artLink.dataset.article, publisher_id: artLink.dataset.pub });
    return;
  }

  const favAdd = t.closest('[data-fav-add]');
  if (favAdd) {
    await API.post('/api/favourites', { slug: favAdd.dataset.favAdd, label: favAdd.dataset.label });
    viewFavorites(); return;
  }
  const favRm = t.closest('[data-fav-remove]');
  if (favRm) {
    await API.del(`/api/favourites/${encodeURIComponent(favRm.dataset.favRemove)}`);
    viewFavorites(); return;
  }
  const forget = t.closest('[data-forget]');
  if (forget) {
    await API.post('/api/privacy/forget', { slug: forget.dataset.forget });
    viewPrivacy(); return;
  }
  const unhide = t.closest('[data-unhide]');
  if (unhide) {
    ev.preventDefault();
    await API.post('/api/privacy/unhide', { story_id: unhide.dataset.unhide });
    viewPrivacy(); return;
  }
  const unread = t.closest('[data-unread]');
  if (unread) {
    ev.preventDefault();
    const row = document.querySelector(`[data-story="${CSS.escape(unread.dataset.unread)}"]`);
    await API.post('/api/read/unread', { story_id: unread.dataset.unread });
    if (row) { row.style.opacity = '.35'; setTimeout(() => row.remove(), 400); }
    return;
  }
  const srcDel = t.closest('[data-src-del]');
  if (srcDel) { await API.del(`/api/sources/${srcDel.dataset.srcDel}`); viewSources(); return; }

  const stockAdd = t.closest('[data-stock-add]');
  if (stockAdd) {
    ev.preventDefault();
    stockAdd.disabled = true;
    const was = stockAdd.textContent;
    stockAdd.textContent = 'Bezig…';
    try {
      const res = await API.post('/api/stocks', {
        symbol: stockAdd.dataset.stockAdd, name: stockAdd.dataset.stockName,
      });
      stockAdd.textContent = res.already ? '✓ Staat er al' : '✓ Toegevoegd';
      // Append straight to the list instead of re-rendering the sheet: a full
      // refetch costs a couple of seconds, during which the reader sees no
      // change and clicks again.
      if (res.quote) {
        $('#stk-watchlist')?.insertAdjacentHTML('beforeend', stockRow(
          { ...res.quote, name: stockAdd.dataset.stockName }));
        bumpStockCount(+1);
        loadTicker();
      }
    } catch (e) {
      stockAdd.disabled = false;
      stockAdd.textContent = was;
      alert('Toevoegen mislukt: ' + apiMessage(e));
    }
    return;
  }

  const stockDel = t.closest('[data-stock-del]');
  if (stockDel) {
    ev.preventDefault();
    stockDel.disabled = true;
    const row = stockDel.closest('.stk');
    try {
      await API.del(`/api/stocks/${encodeURIComponent(stockDel.dataset.stockDel)}`);
      row?.remove();
      bumpStockCount(-1);
      loadTicker();
    } catch (e) {
      stockDel.disabled = false;
      alert('Verwijderen mislukt: ' + apiMessage(e));
    }
    return;
  }

  const addFeed = t.closest('[data-add-feed]');
  if (addFeed) {
    ev.preventDefault();
    const url = addFeed.dataset.addFeed;
    // The feed title is the publisher name by default; let the user adjust it,
    // because "TR: homepage" is a feed name, not a publisher name.
    const suggested = (addFeed.dataset.title || '').split(/[:|–—]/)[0].trim();
    const name = prompt('Onder welke naam wil je deze uitgever opslaan?', suggested);
    if (name === null) return;
    addFeed.disabled = true;
    addFeed.textContent = 'Toevoegen…';
    try {
      // verified: the probe already fetched and parsed this exact URL.
      await API.post('/api/sources', {
        url, publisher_name: name.trim() || suggested,
        name: addFeed.dataset.title, verified: true,
      });
      viewSources();
    } catch (e) {
      addFeed.disabled = false;
      addFeed.textContent = 'Toevoegen';
      alert('Toevoegen mislukt: ' + e.message);
    }
    return;
  }

  const action = t.closest('[data-action]');
  if (action) {
    const a = action.dataset.action;
    if (a === 'reload') route();
    if (a === 'refresh') doRefresh();
    if (a === 'favorites') location.hash = '#/favorites';
    if (a === 'goto-read') location.hash = '#/read';
    if (a === 'clearfilters') {
      state.listFilters = {};
      state.listSort = { key: '', dir: 'desc' };
      rerenderTable();
    }
    if (a === 'reset') {
      if (confirm('Alle geleerde voorkeuren en leesgeschiedenis wissen? Je favorieten blijven staan.')) {
        await API.post('/api/privacy/reset'); viewPrivacy();
      }
    }
    if (a === 'unhide-all') {
      await API.post('/api/privacy/unhide', {});
      viewPrivacy();
    }
    if (a === 'unread-all') {
      if (confirm('Alle gelezen verhalen weer als ongelezen markeren?')) {
        await API.post('/api/read/unread', {});
        viewRead();
      }
    }
    return;
  }

  if (t.closest('#backdrop') || t.closest('#sheet-close')) closeSheet();
});

document.addEventListener('change', async (ev) => {
  if (ev.target.id === 'hideread') {
    await API.post('/api/settings/hide-read', { enabled: ev.target.checked });
    viewRead();
    return;
  }

  const lf = ev.target.closest('[data-lf]');
  if (lf) {
    const key = lf.dataset.lf;
    const val = lf.value.trim();
    if (val) state.listFilters[key] = val; else delete state.listFilters[key];
    rerenderTable();
    return;
  }

  const f = ev.target.closest('[data-f]');
  if (!f) return;
  state.filters[f.dataset.f] = f.value;
  const qs = Object.entries(state.filters).filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  viewList('Gefilterde verhalen', 'Actieve filters', `${qs}&limit=60`);
});

/* Text filters should narrow as you type, not only on blur. */
document.addEventListener('input', (ev) => {
  const lf = ev.target.closest('input[data-lf]');
  if (!lf) return;
  const key = lf.dataset.lf;
  const val = lf.value.trim();
  if (val) state.listFilters[key] = val; else delete state.listFilters[key];
  clearTimeout(state.lfTimer);
  state.lfTimer = setTimeout(() => {
    const active = document.activeElement?.dataset?.lf;
    const caret = document.activeElement?.selectionStart;
    rerenderTable();
    if (active) {
      const el = document.querySelector(`input[data-lf="${active}"]`);
      if (el) { el.focus(); try { el.setSelectionRange(caret, caret); } catch { /* non-text input */ } }
    }
  }, 180);
});

document.addEventListener('submit', async (ev) => {
  if (ev.target.id === 'searchform') {
    ev.preventDefault();
    const q = $('#search').value.trim();
    if (q) location.hash = `#/search?q=${encodeURIComponent(q)}`;
  }
  if (ev.target.id === 'addfav') {
    ev.preventDefault();
    const v = $('#favinput').value.trim();
    if (!v) return;
    await API.post('/api/favourites', { label: v });
    $('#favinput').value = '';
    viewFavorites();
  }
  if (ev.target.id === 'stocksearch') {
    ev.preventDefault();
    runStockSearch($('#stockq').value.trim());
    return;
  }

  if (ev.target.id === 'probesrc') {
    ev.preventDefault();
    const input = document.querySelector('#probeurl');
    const btn = ev.target.querySelector('button');
    const url = input.value.trim();
    if (!url) return;
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = 'Bezig…';
    document.querySelector('#proberesult').innerHTML =
      '<div class="probe probe-warn"><div class="probe-msg">🔎 Bezig met controleren van de URL '
      + 'en zoeken naar feeds… dit duurt ongeveer 10 seconden.</div></div>';
    try {
      renderProbe(await API.post('/api/sources/probe', { url }));
    } catch (e) {
      document.querySelector('#proberesult').innerHTML =
        `<div class="probe probe-err"><div class="probe-msg">⚠️ Controle mislukt: ${esc(e.message)}</div></div>`;
    } finally { btn.disabled = false; btn.textContent = was; }
  }
});

$('#burger').addEventListener('click', () => {
  const n = $('#mainnav');
  n.classList.toggle('open');
  $('#burger').setAttribute('aria-expanded', n.classList.contains('open'));
});

$('#theme').addEventListener('click', () => {
  const cur = document.documentElement.dataset.theme;
  const next = cur === 'dark' ? 'light' : cur === 'light' ? 'auto' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('nieuws-theme', next);
});

async function doRefresh() {
  if (state.busy) return;
  state.busy = true;
  $('#refresh').classList.add('spin');
  try { await API.post('/api/refresh?scope=full'); await loadMeta(); route(); }
  catch (e) { console.warn(e); }
  finally { state.busy = false; $('#refresh').classList.remove('spin'); }
}
$('#refresh').addEventListener('click', doRefresh);

/* Keep the front page honest while it sits open: ask the server for a priority
   sweep (top-story feeds only) and repaint if anything actually moved. */
async function pollTopStories() {
  if (state.busy || document.hidden) return;
  const path = (location.hash || '#/front').split('?')[0];
  if (path !== '#/front' && path !== '#/mynews') return;
  try {
    await API.post('/api/refresh?scope=priority');
    await loadMeta();
    const fresh = await API.get('/api/frontpage?limit=13');
    const signature = fresh.top.map((s) => `${s.id}:${s.last_updated_at}`).join('|');
    if (signature !== state.topSignature) {
      state.topSignature = signature;
      if (path === '#/front') route();
    }
  } catch { /* transient: the next tick will retry */ }
}

async function loadMeta() {
  try {
    state.meta = await API.get('/api/meta');
    const strip = $('#statusstrip');
    if (state.meta.degraded?.length) {
      strip.hidden = false;
      strip.innerHTML = `⚠ Tijdelijk niet beschikbaar: ${state.meta.degraded.map(esc).join(', ')}
        — de overige bronnen werken gewoon. <a href="#/sources" style="color:inherit;text-decoration:underline">Details</a>`;
    } else { strip.hidden = true; }
    renderFreshness();
  } catch { /* meta is optional for rendering */ }
}

/* Freshness indicator: shows when the top stories were last re-checked, which
   happens far more often than the full sweep over every feed. */
function renderFreshness() {
  const el = $('#freshness');
  if (!el || !state.meta) return;
  const s = state.meta.status || {};
  const last = s.last_priority_run || s.last_run;
  const every = Math.round((state.meta.priority_refresh_seconds || 120) / 60);
  el.textContent = last ? `Top bijgewerkt ${ago(last)}` : 'Nog niet bijgewerkt';
  el.title = `Topverhalen worden elke ${every} min opnieuw gecontroleerd `
    + `(${state.meta.priority_source_count || 0} prioriteitsbronnen). `
    + `Volledige ronde over alle ${state.meta.sources?.length || 0} feeds elke `
    + `${Math.round((state.meta.refresh_seconds || 600) / 60)} min.`
    + (s.last_run ? ` Laatste volledige ronde: ${clock(s.last_run)}.` : '');
}

window.addEventListener('hashchange', route);

(async function boot() {
  const saved = localStorage.getItem('nieuws-theme');
  if (saved) document.documentElement.dataset.theme = saved;
  const savedView = localStorage.getItem('nieuws-view');
  if (savedView === 'list' || savedView === 'cards') state.view = savedView;
  ticker.paused = localStorage.getItem('nieuws-ticker') === 'paused';
  wireTicker();
  await loadMeta();
  route();
  loadTicker();
  setInterval(loadMeta, 60000);
  const everyMs = Math.max(60, state.meta?.priority_refresh_seconds || 120) * 1000;
  setInterval(pollTopStories, everyMs);
  // Quotes move faster than headlines, but the server caches for a minute, so
  // polling more often than that would only burn requests.
  setInterval(loadTicker, 60000);
})();

function wireTicker() {
  const bar = $('#ticker');
  if (!bar) return;
  $('#ticker-pause')?.addEventListener('click', () => {
    ticker.paused = !ticker.paused;
    localStorage.setItem('nieuws-ticker', ticker.paused ? 'paused' : 'running');
    applyTickerPause();
  });
  $('#ticker-stocks')?.addEventListener('click', openStockPicker);
  // Hold still while the reader is aiming at a headline, otherwise the link
  // slides out from under the cursor.
  const vp = $('#ticker-viewport');
  vp?.addEventListener('mouseenter', () => { ticker.hoverPause = true; applyTickerPause(); });
  vp?.addEventListener('mouseleave', () => { ticker.hoverPause = false; applyTickerPause(); });
  vp?.addEventListener('focusin', () => { ticker.hoverPause = true; applyTickerPause(); });
  vp?.addEventListener('focusout', () => { ticker.hoverPause = false; applyTickerPause(); });
}
