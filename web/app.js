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

const state = { meta: null, filters: {}, busy: false, topSignature: null };
const whyCache = new Map();
const $ = (sel, root = document) => root.querySelector(sel);
const main = $('#main');

/* ---------------------------------------------------------------- helpers */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

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
        <button class="mini ${s.saved ? 'on' : ''}" data-act="save" data-id="${esc(s.id)}">
          ${s.saved ? '★ Bewaard' : '☆ Bewaar'}</button>
        <button class="mini" data-act="more" data-id="${esc(s.id)}">👍 Meer zo</button>
        <button class="mini" data-act="less" data-id="${esc(s.id)}">👎 Minder zo</button>
        <button class="mini" data-act="hide" data-id="${esc(s.id)}">✕ Verberg</button>
      </div>
    </div>
  </article>`;
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
  main.innerHTML = skeleton(6);
  try {
    const d = await API.get('/api/frontpage?limit=13');
    if (d.empty) {
      main.innerHTML = emptyState('📭', 'Nog geen nieuws opgehaald',
        'Klik op verversen om de feeds van de uitgevers op te halen.',
        { label: 'Nieuws ophalen', action: 'refresh' });
      return;
    }
    const cats = Object.entries(d.categories || {})
      .map(([slug, items]) => section(
        state.meta?.categories.find((c) => c.slug === slug)?.label || slug,
        '', items.slice(0, 3), { more: `#/category/${slug}` })).join('');

    main.innerHTML =
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
    main.innerHTML = head + (d.stories.length
      ? `<div class="grid">${d.stories.map((s) => storyCard(s)).join('')}</div>`
      : emptyState('🔍', 'Geen verhalen', 'Voeg onderwerpen toe of ververs het nieuws.',
        { label: 'Naar favorieten', action: 'favorites' }));
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
  main.innerHTML = skeleton(6);
  try {
    const d = await API.get(`/api/stories?${qs}`);
    main.innerHTML = `
      <section class="section">
        <div class="section-head"><h2>${esc(title)}</h2>
          <span class="hint">${esc(hint)} · ${d.total} verhalen</span></div>
        ${filterBar()}
        ${d.stories.length
          ? `<div class="grid">${d.stories.map((s) => storyCard(s)).join('')}</div>`
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
      <article>
        <div style="display:flex;gap:7px;flex-wrap:wrap;margin-bottom:8px">
          <span class="chip cat">${esc(s.category_label || '')}</span>
          <span class="chip multi">${s.publisher_count} ${s.publisher_count === 1 ? 'uitgever' : 'uitgevers'} · ${s.article_count} artikelen</span>
          ${s.is_updating ? `<span class="chip live" title="${esc(DEVELOPING.tooltip)}">${esc(DEVELOPING.label)}</span>` : ''}
          ${s.geo_scope === 'fryslan' ? '<span class="chip">Fryslân</span>' : ''}
        </div>
        <h1>${esc(s.headline)}</h1>
        <div class="meta-row" style="margin-bottom:14px">
          <span>Laatste update ${clock(s.last_updated_at)} · ${ago(s.last_updated_at)}</span>
          <span class="pubs">${pubBadges(s.publishers)}</span>
        </div>
        ${s.is_updating ? `<div class="notice" style="border-left-color:var(--accent)">
            <strong>Dit verhaal ${esc(DEVELOPING.short)}.</strong> Een uitgever heeft een artikel
            herzien of er kwam recent nieuwe berichtgeving bij. Controleer de bronlinks
            hieronder voor de actuele stand.</div>` : ''}
        ${s.headline_source_name
          ? `<div style="font-size:12.5px;color:var(--faint);margin-bottom:10px">
               Kopregel overgenomen van ${esc(s.headline_source_name)} als meest neutrale formulering.</div>` : ''}
        ${s.summary ? `<div class="ai-box"><div class="lbl">✨ Automatisch samengevat uit de bronteksten</div>
            <p>${esc(s.summary)}</p></div>` : ''}
        ${dis}

        <div class="card-actions" style="margin-bottom:8px">
          <button class="mini ${s.saved ? 'on' : ''}" data-act="save" data-id="${esc(s.id)}">
            ${s.saved ? '★ Bewaard' : '☆ Bewaar'}</button>
          <button class="mini" data-act="more" data-id="${esc(s.id)}">👍 Meer zo</button>
          <button class="mini" data-act="less" data-id="${esc(s.id)}">👎 Minder zo</button>
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

        <div class="sub">Zo brengen de uitgevers het (${(s.articles || []).length} artikelen)</div>
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

        ${(s.related || []).length ? `<div class="sub">Gerelateerd</div>
          <div class="rail">${s.related.map((r) => storyCard(r, 'compact')).join('')}</div>` : ''}
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
        <form class="addrow" id="addsrc">
          <input name="publisher_name" placeholder="Naam uitgever (bijv. Trouw)" required />
          <input name="url" placeholder="RSS-feed URL (https://…/rss.xml)" required />
          <button type="submit">Bron toevoegen</button>
        </form>
        <div class="notice">We gebruiken alleen officiële, openbaar beschikbare feeds.
          Bronnen achter een inlog, betaalmuur of toestemmingsscherm worden overgeslagen
          en hier als <span class="dot-err">●</span> gemeld.</div>
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
  openSheet('Waarom zie ik dit?', `
    <p style="color:var(--muted);margin-top:0">${card ? esc(card.querySelector('h3').textContent) : ''}</p>
    <ul style="padding-left:18px;line-height:1.8">
      ${data.map((r) => `<li>${esc(r.text)}</li>`).join('') || '<li>Actueel nieuws.</li>'}
    </ul>
    <div class="notice" style="margin-top:18px">Expliciete favorieten wegen altijd zwaarder dan
      afgeleide interesses. Recent gedrag telt zwaarder dan oud gedrag.</div>
    <div style="display:flex;gap:9px;flex-wrap:wrap">
      <button class="btn" data-act="more" data-id="${esc(storyId)}">👍 Meer zoals dit</button>
      <button class="btn ghost" data-act="less" data-id="${esc(storyId)}">👎 Minder zoals dit</button>
      <button class="btn ghost" data-act="hide" data-id="${esc(storyId)}">✕ Verberg dit verhaal</button>
      <a class="btn ghost" href="#/privacy">Beheer personalisatie</a>
    </div>`);
}

/* ---------------------------------------------------------------- router */
const routes = [
  [/^#\/front$/, viewFront],
  [/^#\/mynews$/, viewMyNews],
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
      act.textContent = on ? '★ Bewaard' : '☆ Bewaar';
      track(on ? 'save' : 'unsave', id);
    } else {
      track(a, id);
      act.textContent = { more: '👍 Genoteerd', less: '👎 Genoteerd', hide: '✓ Verborgen' }[a] || '✓';
      act.classList.add('on');
      if (a === 'hide') {
        const card = document.querySelector(`[data-story="${CSS.escape(id)}"]`);
        if (card) { card.style.opacity = '.35'; setTimeout(() => card.remove(), 450); }
        closeSheet();
      }
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
  const srcDel = t.closest('[data-src-del]');
  if (srcDel) { await API.del(`/api/sources/${srcDel.dataset.srcDel}`); viewSources(); return; }

  const action = t.closest('[data-action]');
  if (action) {
    const a = action.dataset.action;
    if (a === 'reload') route();
    if (a === 'refresh') doRefresh();
    if (a === 'favorites') location.hash = '#/favorites';
    if (a === 'reset') {
      if (confirm('Alle geleerde voorkeuren en leesgeschiedenis wissen? Je favorieten blijven staan.')) {
        await API.post('/api/privacy/reset'); viewPrivacy();
      }
    }
    return;
  }

  if (t.closest('#backdrop') || t.closest('#sheet-close')) closeSheet();
});

document.addEventListener('change', (ev) => {
  const f = ev.target.closest('[data-f]');
  if (!f) return;
  state.filters[f.dataset.f] = f.value;
  const qs = Object.entries(state.filters).filter(([, v]) => v)
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  viewList('Gefilterde verhalen', 'Actieve filters', `${qs}&limit=60`);
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
  if (ev.target.id === 'addsrc') {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    try {
      await API.post('/api/sources', Object.fromEntries(fd));
      viewSources();
    } catch (e) { alert('Toevoegen mislukt: ' + e.message); }
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
  await loadMeta();
  route();
  setInterval(loadMeta, 60000);
  const everyMs = Math.max(60, state.meta?.priority_refresh_seconds || 120) * 1000;
  setInterval(pollTopStories, everyMs);
})();
