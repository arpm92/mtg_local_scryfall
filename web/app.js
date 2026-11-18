const state = { all: [], filtered: [], visible: 60, formats: {}, savedDecks: [], chatMessages: [], chatAvailable: false, deckView: 'grid', deckInfo: null, deck: {main: {}, sideboard: {}, commanders: {}} };
const $ = (selector) => document.querySelector(selector);
const grid = $('#card-grid');
const search = $('#search');
const controls = ['color-filter', 'type-filter', 'set-filter', 'rarity-filter', 'foil-filter', 'sort'].map(id => $(`#${id}`));

function normalize(value) { return String(value ?? '').toLocaleLowerCase(); }
function list(value) { return normalize(value) === 'colorless' ? [] : normalize(value).split(',').map(x => x.trim()).filter(Boolean); }
function tokenize(query) {
  const tokens = []; let current = ''; let quoted = false;
  for (const char of query.trim()) {
    if (char === '"') { quoted = !quoted; continue; }
    if (/\s/.test(char) && !quoted) { if (current) tokens.push(current); current = ''; }
    else current += char;
  }
  if (current) tokens.push(current);
  if (quoted) throw new Error('Missing closing quote.');
  return tokens;
}

function compareNumber(actual, operator, wanted) {
  const n = Number(wanted); if (!Number.isFinite(n)) return false;
  return ({'<': actual < n, '<=': actual <= n, '>': actual > n, '>=': actual >= n, '!=': actual !== n, '=': actual === n, ':': actual === n})[operator];
}
function compareColors(actualValue, operator, wantedValue) {
  const actual = new Set(list(actualValue));
  const wanted = new Set(normalize(wantedValue).replace(/[^wubrg]/g, '').split(''));
  if (normalize(wantedValue) === 'c' || normalize(wantedValue) === 'colorless') return actual.size === 0;
  const contains = [...wanted].every(c => actual.has(c));
  const subset = [...actual].every(c => wanted.has(c));
  if (operator === '=') return contains && subset;
  if (operator === '<' || operator === '<=') return subset;
  if (operator === '>' || operator === '>=') return contains;
  return contains;
}
function isFilter(card, value) {
  const key = normalize(value);
  if (key === 'foil') return card.foils.some(item => !['', 'normal', 'nonfoil'].includes(normalize(item)));
  if (key === 'nonfoil') return card.foils.some(item => ['', 'normal', 'nonfoil'].includes(normalize(item)));
  if (key === 'commander') return normalize(card.commander) === 'legal';
  if (key === 'multicolor') return list(card.colors).length > 1;
  if (key === 'colorless') return list(card.colors).length === 0;
  if (key === 'dfc' || key === 'double-faced') return Boolean(card.backImageUrl) || card.name.includes(' // ');
  return false;
}
function fieldMatch(card, field, operator, wanted) {
  const f = normalize(field); const value = normalize(wanted);
  if (['name', 'n'].includes(f)) return normalize(card.name).includes(value);
  if (['type', 't'].includes(f)) return normalize(card.typeLine).includes(value);
  if (['oracle', 'o'].includes(f)) return normalize(card.oracleText).includes(value);
  if (['set', 's'].includes(f)) return card.setCodes.some(item => normalize(item) === value) || card.setNames.some(item => normalize(item).includes(value));
  if (['rarity', 'r'].includes(f)) return card.rarities.some(item => normalize(item).startsWith(value));
  if (['language', 'lang'].includes(f)) return card.languages.some(item => normalize(item) === value);
  if (['keyword', 'kw'].includes(f)) return normalize(card.keywords).includes(value);
  if (f === 'produces') return list(card.producedMana).includes(value);
  if (['color', 'c'].includes(f)) return compareColors(card.colors, operator, value);
  if (['identity', 'id'].includes(f)) return compareColors(card.colorIdentity, operator, value);
  if (['mv', 'cmc'].includes(f)) return compareNumber(card.manaValue, operator, value);
  if (['quantity', 'qty'].includes(f)) return compareNumber(card.quantity, operator, value);
  if (f === 'is') return isFilter(card, value);
  throw new Error(`Unknown filter “${field}”.`);
}
function compileQuery(query) {
  const terms = tokenize(query).map(token => {
    const match = token.match(/^(-?)([a-z]+)(<=|>=|!=|=|:|<|>)(.*)$/i);
    if (!match) {
      const negative = token.startsWith('-');
      const text = normalize(negative ? token.slice(1) : token);
      return card => negative ? !card._search.includes(text) : card._search.includes(text);
    }
    const [, minus, field, operator, wanted] = match;
    if (!wanted) throw new Error(`Filter “${field}” needs a value.`);
    return card => minus ? !fieldMatch(card, field, operator, wanted) : fieldMatch(card, field, operator, wanted);
  });
  return card => terms.every(term => term(card));
}

function option(select, value, label = value) {
  const el = document.createElement('option'); el.value = value; el.textContent = label; select.append(el);
}
function unique(field) { return [...new Set(state.all.map(card => card[field]).filter(Boolean))].sort((a,b) => a.localeCompare(b)); }
function uniqueNested(field) { return [...new Set(state.all.flatMap(card => card[field] || []).filter(Boolean))].sort((a,b) => a.localeCompare(b)); }
function populateFilters() {
  [['W','White'],['U','Blue'],['B','Black'],['R','Red'],['G','Green'],['Colorless','Colorless'],['Multicolor','Multicolor']].forEach(x => option($('#color-filter'), ...x));
  const types = ['Creature','Instant','Sorcery','Artifact','Enchantment','Planeswalker','Land','Battle'];
  types.forEach(x => option($('#type-filter'), x, x));
  uniqueNested('setCodes').forEach(code => { const printing = state.all.flatMap(c => c.printings).find(p => p.setCode === code); option($('#set-filter'), code, `${code.toUpperCase()} — ${printing.setName}`); });
  uniqueNested('rarities').forEach(x => option($('#rarity-filter'), x, x[0].toUpperCase() + x.slice(1)));
  uniqueNested('foils').forEach(x => option($('#foil-filter'), x, x || 'Normal'));
}
function applyFilters() {
  let predicate;
  try { predicate = compileQuery(search.value); $('#query-error').textContent = ''; }
  catch (error) { $('#query-error').textContent = error.message; predicate = () => false; }
  const color = $('#color-filter').value;
  const type = normalize($('#type-filter').value);
  const set = normalize($('#set-filter').value);
  const rarity = normalize($('#rarity-filter').value);
  const foil = normalize($('#foil-filter').value);
  state.filtered = state.all.filter(card => {
    if (!predicate(card)) return false;
    if (color === 'Multicolor' && list(card.colors).length < 2) return false;
    if (color && color !== 'Multicolor' && !compareColors(card.colors, ':', color)) return false;
    if (type && !normalize(card.typeLine).includes(type)) return false;
    if (set && !card.setCodes.some(item => normalize(item) === set)) return false;
    if (rarity && !card.rarities.some(item => normalize(item) === rarity)) return false;
    if (foil && !card.foils.some(item => normalize(item) === foil)) return false;
    return true;
  });
  const sort = $('#sort').value;
  if (sort === 'name') state.filtered.sort((a,b) => a.name.localeCompare(b.name) || a.setCode.localeCompare(b.setCode));
  if (sort === 'mv') state.filtered.sort((a,b) => a.manaValue - b.manaValue || a.name.localeCompare(b.name));
  if (sort === 'quantity') state.filtered.sort((a,b) => b.quantity - a.quantity || a.name.localeCompare(b.name));
  if (sort === 'set') state.filtered.sort((a,b) => a.setName.localeCompare(b.setName) || a.name.localeCompare(b.name));
  if (sort === 'original') state.filtered.sort((a,b) => a.index - b.index);
  state.visible = 60; render();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
}
function cardMarkup(card) {
  const media = card.imageUrl
    ? `<img class="card-image" src="${escapeHtml(card.imageUrl)}" alt="${escapeHtml(card.name)}" loading="lazy">`
    : `<div class="card-placeholder"><h2>${escapeHtml(card.name)}</h2><div>${escapeHtml(card.manaCost)}</div><p class="type">${escapeHtml(card.typeLine)}</p><p class="rules">${escapeHtml(card.oracleText)}</p></div>`;
  const setLabel = card.setCodes.length === 1 ? card.setCodes[0] : `${card.setCodes.length} sets`;
  return `<article class="card-tile"><button class="card-button" data-index="${card.index}" type="button">${media}</button>${card.quantity > 1 ? `<span class="quantity-badge">×${card.quantity}</span>` : ''}<div class="card-caption"><strong title="${escapeHtml(card.name)}">${escapeHtml(card.name)}</strong><span>${escapeHtml(setLabel)}</span></div></article>`;
}
function render() {
  const cards = state.filtered.slice(0, state.visible);
  grid.innerHTML = cards.map(cardMarkup).join('');
  $('#result-count').textContent = state.filtered.length.toLocaleString();
  $('#copy-count').textContent = state.filtered.reduce((n,c) => n + c.quantity, 0).toLocaleString();
  $('#empty').hidden = state.filtered.length !== 0;
  $('#show-more').hidden = state.visible >= state.filtered.length;
}
function openCard(index) {
  const card = state.all.find(item => item.index === index); if (!card) return;
  const tags = [card.rarity, card.foil, card.language, `MV ${card.manaValue}`, card.commander === 'legal' ? 'Commander legal' : 'Not Commander legal'].filter(Boolean);
  const art = card.imageUrl ? `<img src="${escapeHtml(card.imageUrl)}" alt="${escapeHtml(card.name)}">` : '<div class="dialog-art-placeholder"></div>';
  const rows = card.printings.map(p => `<tr><td>${p.imageUrl ? `<a class="printing-image-link" href="${escapeHtml(p.imageUrl)}" target="_blank" rel="noopener" title="Open this exact printing"><img class="printing-image" src="${escapeHtml(p.imageUrl)}" alt="${escapeHtml(card.name)} ${escapeHtml(p.setCode)} printing" loading="lazy"></a>` : ''}</td><td>${escapeHtml(p.setCode.toUpperCase())} #${escapeHtml(p.collectorNumber)}</td><td>${escapeHtml(p.rarity)}</td><td>${escapeHtml(p.foil || 'normal')}</td><td>${escapeHtml(p.language)}</td><td>?${p.quantity}</td></tr>`).join('');
  $('#dialog-content').innerHTML = `<div class="dialog-layout">${art}<div class="dialog-copy"><h2>${escapeHtml(card.name)}</h2><p class="dialog-meta">${card.printings.length} owned printing${card.printings.length === 1 ? '' : 's'} · ${card.quantity} copies</p><p>${escapeHtml(card.manaCost)}</p><p class="dialog-type">${escapeHtml(card.typeLine)}</p><p class="dialog-rules">${escapeHtml(card.oracleText) || '<i>No rules text</i>'}</p><div class="dialog-tags">${tags.map(x => `<span>${escapeHtml(x)}</span>`).join('')}</div><h3 class="printing-title">Owned printings</h3><table class="printing-list"><thead><tr><th>Art</th><th>Printing</th><th>Rarity</th><th>Finish</th><th>Lang</th><th>Qty</th></tr></thead><tbody>${rows}</tbody></table>${card.scryfallUrl ? `<p><a class="scryfall-link" href="${escapeHtml(card.scryfallUrl)}" target="_blank" rel="noopener">Open on Scryfall ↗</a></p>` : ''}</div></div>`;
  $('#card-dialog').showModal();
}

search.addEventListener('input', applyFilters);
controls.forEach(control => control.addEventListener('change', applyFilters));
$('#clear').addEventListener('click', () => { search.value = ''; controls.slice(0,5).forEach(x => x.value = ''); applyFilters(); search.focus(); });
$('#show-more').addEventListener('click', () => { state.visible += 60; render(); });
grid.addEventListener('click', event => { const button = event.target.closest('[data-index]'); if (button) openCard(Number(button.dataset.index)); });
$('.dialog-close').addEventListener('click', () => $('#card-dialog').close());
$('#card-dialog').addEventListener('click', event => { if (event.target === $('#card-dialog')) $('#card-dialog').close(); });
document.addEventListener('keydown', event => { if (event.key === '/' && document.activeElement !== search) { event.preventDefault(); search.focus(); } });
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => {
  const tab = button.dataset.tab;
  document.querySelectorAll('[data-tab]').forEach(item => {
    const active = item === button;
    item.classList.toggle('active', active);
    item.setAttribute('aria-selected', String(active));
  });
  $('#collection-view').hidden = tab !== 'collection';
  $('#decks-view').hidden = tab !== 'decks';
  $('#chat-view').hidden = tab !== 'chat';
  $('#header-stats').hidden = tab !== 'collection';
}));

fetch('/api/collection').then(response => {
  if (!response.ok) throw new Error(`Server returned ${response.status}`);
  return response.json();
}).then(data => {
  state.all = data.cards.map(card => ({...card, _search: normalize([card.name, card.typeLine, card.oracleText, card.setNames.join(' '), card.setCodes.join(' '), card.keywords].join(' '))}));
  $('#source-name').textContent = data.source;
  $('#loading').hidden = true;
  populateFilters(); applyFilters();
}).catch(error => { $('#loading').textContent = `Could not load collection: ${error.message}`; });

function deckTotal(section) { return Object.values(state.deck[section]).reduce((sum, count) => sum + count, 0); }
function ownedInDeck(name) { return ['main', 'sideboard', 'commanders'].reduce((sum, section) => sum + (state.deck[section][name] || 0), 0); }
function legalForFormat(card, format) { return card.legalities?.[format] === 'legal' || /^Basic Land/.test(card.typeLine); }
function maxCopies(card, format) { return format === 'commander' ? 1 : Math.min(4, card.quantity); }
function addToDeck(name, section) {
  const card = state.all.find(item => item.name === name); const format = $('#deck-format').value;
  if (!card || !legalForFormat(card, format)) return;
  if (section === 'commanders' && (!card.commanderCandidate || format !== 'commander')) return;
  if (ownedInDeck(name) >= maxCopies(card, format)) return;
  state.deckInfo = null;
  state.deck[section][name] = (state.deck[section][name] || 0) + 1; renderDeckBuilder();
}
function removeFromDeck(name, section) {
  if (!state.deck[section][name]) return;
  state.deckInfo = null;
  state.deck[section][name] -= 1; if (!state.deck[section][name]) delete state.deck[section][name]; renderDeckBuilder();
}
function deckCategory(card) {
  const type = card?.typeLine || '';
  return ['Land', 'Creature', 'Planeswalker', 'Battle', 'Artifact', 'Enchantment', 'Instant', 'Sorcery'].find(item => type.includes(item)) || 'Other';
}
function deckVisualCard(name, count, section) {
  const card = state.all.find(item => item.name === name);
  const art = card?.imageUrl
    ? `<img src="${escapeHtml(card.imageUrl)}" alt="${escapeHtml(name)}" loading="lazy">`
    : `<span class="deck-art-fallback">${escapeHtml(name)}</span>`;
  const preview = card ? `data-preview-index="${card.index}"` : '';
  return `<article class="deck-visual-card">
    <span class="deck-qty">${count}×</span>
    <button class="deck-card-art" type="button" ${preview} aria-label="View ${escapeHtml(name)}">${art}</button>
    <span class="deck-card-actions"><button type="button" data-remove="${section}" data-card="${escapeHtml(name)}" aria-label="Remove ${escapeHtml(name)}">−</button><button type="button" data-add="${section}" data-card="${escapeHtml(name)}" aria-label="Add another ${escapeHtml(name)}">+</button></span>
    <span class="deck-visual-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
  </article>`;
}
function renderDeckSection(container, section) {
  const entries = Object.entries(state.deck[section]).map(([name, count]) => ({name, count, card: state.all.find(item => item.name === name)}));
  if (!entries.length) { container.innerHTML = '<p class="muted">No cards in this section.</p>'; return; }
  const groups = new Map();
  entries.sort((a,b) => (a.card?.manaValue || 0) - (b.card?.manaValue || 0) || a.name.localeCompare(b.name)).forEach(entry => {
    const category = section === 'commanders' ? 'Commander' : deckCategory(entry.card);
    if (!groups.has(category)) groups.set(category, []); groups.get(category).push(entry);
  });
  const order = ['Commander','Creature','Planeswalker','Battle','Artifact','Enchantment','Instant','Sorcery','Land','Other'];
  container.innerHTML = [...groups.entries()].sort((a,b) => order.indexOf(a[0]) - order.indexOf(b[0])).map(([category, cards]) => {
    const total = cards.reduce((sum, entry) => sum + entry.count, 0);
    return `<section class="deck-category"><h3 class="deck-category-heading"><span>${category}</span><span>${total} card${total === 1 ? '' : 's'}</span></h3><div class="deck-visual-grid ${state.deckView === 'list' ? 'deck-list-view' : ''}">${cards.map(entry => deckVisualCard(entry.name, entry.count, section)).join('')}</div></section>`;
  }).join('');
}
function requiredTokens() {
  const tokens = new Map();
  for (const section of ['commanders', 'main', 'sideboard']) {
    for (const name of Object.keys(state.deck[section])) {
      const card = state.all.find(item => item.name === name);
      for (const token of card?.relatedTokens || []) {
        if (!tokens.has(token.id)) tokens.set(token.id, {...token, createdBy: new Set()});
        tokens.get(token.id).createdBy.add(name);
      }
    }
  }
  return [...tokens.values()].sort((a,b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name));
}
function renderTokens() {
  const tokens = requiredTokens();
  $('#token-total').textContent = `${tokens.length} unique`;
  $('#deck-tokens').innerHTML = tokens.length ? `<div class="deck-visual-grid">${tokens.map(token => `<article class="deck-visual-card token-card">
    <span class="deck-qty">${escapeHtml(token.kind)}</span>
    <div class="deck-card-art"><img src="${escapeHtml(token.imageUrl)}" alt="${escapeHtml(token.name)}" loading="lazy"></div>
    <span class="deck-visual-name" title="${escapeHtml(token.name)}">${escapeHtml(token.name)}</span>
    <span class="token-source">Created by ${[...token.createdBy].map(escapeHtml).join(', ')}</span>
  </article>`).join('')}</div>` : '<p class="muted">No tokens or emblems are referenced by the current deck.</p>';
}
function renderDeckPrimer() {
  const report = state.deckInfo?.report;
  if (!report) {
    $('#deck-primer').innerHTML = Object.keys(state.deck.main).length ? '<p>Validate and save this deck to generate its strategy and winning-condition summary.</p>' : '';
    return;
  }
  const wins = report.win_conditions || [];
  $('#deck-primer').innerHTML = `<div class="deck-primer-header"><div><span class="primer-label">Deck primer</span><h2>${escapeHtml(state.deckInfo.archetype || 'Deck strategy')}</h2></div><span class="primer-label">${escapeHtml(report.identity || '')}</span></div>
    <p>${escapeHtml(report.primary_objective || '')}</p>
    <div class="primer-path"><div><strong>Early game</strong>${escapeHtml(report.early_game || '')}</div><div><strong>Mid game</strong>${escapeHtml(report.middle_game || '')}</div><div><strong>Late game</strong>${escapeHtml(report.late_game || '')}</div></div>
    <p><strong>Winning conditions</strong></p><div class="primer-wins">${wins.length ? wins.map(name => `<span>${escapeHtml(name)}</span>`).join('') : '<span>No distinct finishers detected</span>'}</div>`;
}
function renderDeckBuilder() {
  const format = $('#deck-format').value; const config = state.formats[format]; if (!config) return;
  $('#commander-section').hidden = format !== 'commander'; $('#sideboard-section').hidden = format === 'commander';
  $('#commander-total').textContent = `${deckTotal('commanders')} / ${config.commander_min || 0}`;
  $('#main-total').textContent = `${deckTotal('main')} / ${config.main_size}`;
  $('#sideboard-total').textContent = `${deckTotal('sideboard')} / ${config.sideboard_target}`;
  const selected = Object.entries(state.deck.main).map(([name,count]) => ({card: state.all.find(item => item.name === name), count}));
  const lands = selected.reduce((sum,item) => sum + (item.card?.typeLine.includes('Land') ? item.count : 0), 0);
  const creatures = selected.reduce((sum,item) => sum + (item.card?.typeLine.includes('Creature') ? item.count : 0), 0);
  const spells = selected.filter(item => item.card && !item.card.typeLine.includes('Land'));
  const spellCount = spells.reduce((sum,item) => sum + item.count, 0);
  const averageMv = spellCount ? spells.reduce((sum,item) => sum + item.card.manaValue * item.count, 0) / spellCount : 0;
  $('#deck-summary').innerHTML = `<span><strong>${deckTotal('main') + deckTotal('commanders')}</strong> deck cards</span><span><strong>${Object.keys(state.deck.main).length}</strong> unique main</span><span><strong>${creatures}</strong> creatures</span><span><strong>${lands}</strong> lands</span><span><strong>${averageMv.toFixed(2)}</strong> avg. mana value</span>`;
  renderDeckPrimer();
  const query = normalize($('#deck-search').value);
  const available = state.all.filter(card => legalForFormat(card, format) && (!query || card._search.includes(query))).slice(0, 150);
  $('#deck-card-list').innerHTML = available.map(card => {
    const remaining = Math.max(0, maxCopies(card, format) - ownedInDeck(card.name));
    const commanderButton = format === 'commander' && card.commanderCandidate ? `<button data-add="commanders" data-card="${escapeHtml(card.name)}" ${remaining ? '' : 'disabled'} title="Add commander">C</button>` : '';
    const thumb = card.imageUrl ? `<button class="deck-card-art" type="button" data-preview-index="${card.index}" aria-label="View ${escapeHtml(card.name)}"><img class="deck-card-thumb" src="${escapeHtml(card.imageUrl)}" alt="" loading="lazy"></button>` : '<span class="deck-card-thumb"></span>';
    return `<div class="deck-card-row">${thumb}<span><b>${escapeHtml(card.name)}</b><small>${escapeHtml(card.typeLine)} · ${remaining}/${card.quantity} available</small></span><span class="deck-adds">${commanderButton}<button data-add="main" data-card="${escapeHtml(card.name)}" ${remaining ? '' : 'disabled'} title="Add to main">+</button>${format !== 'commander' ? `<button data-add="sideboard" data-card="${escapeHtml(card.name)}" ${remaining ? '' : 'disabled'} title="Add to sideboard">S</button>` : ''}</span></div>`;
  }).join('') || '<p class="muted">No owned legal cards match.</p>';
  document.querySelectorAll('[data-deck-section]').forEach(container => {
    const section = container.dataset.deckSection;
    renderDeckSection(container, section);
  });
  renderTokens();
}
function newDeck() { state.deckInfo = null; state.deck = {main: {}, sideboard: {}, commanders: {}}; $('#deck-name').value = 'New Deck'; $('#deck-status').textContent = ''; renderDeckBuilder(); }
function loadSavedDeck(deck) { state.deckInfo = deck; state.deck = {main: {...deck.main}, sideboard: {...(deck.sideboard || {})}, commanders: {...(deck.commanders || {})}}; $('#deck-name').value = deck.name; $('#deck-format').value = deck.format; $('#chat-deck').value = deck.name; renderDeckBuilder(); }
function renderSavedDecks() {
  $('#deck-count').textContent = `${state.savedDecks.length} saved deck${state.savedDecks.length === 1 ? '' : 's'}`;
  $('#saved-decks').innerHTML = state.savedDecks.map((deck,index) => `<div class="saved-deck-row"><button class="saved-deck" data-saved="${index}">${escapeHtml(deck.name)}<small>${escapeHtml(deck.format)}</small></button><button class="delete-deck" type="button" data-delete-deck="${index}" aria-label="Delete ${escapeHtml(deck.name)}" title="Delete deck">×</button></div>`).join('') || '<p class="muted">No saved decks yet.</p>';
  const picker = $('#chat-deck'); const selected = picker.value;
  picker.innerHTML = '<option value="">Collection / general question</option>' + state.savedDecks.map(deck => `<option value="${escapeHtml(deck.name)}">${escapeHtml(deck.name)} (${escapeHtml(deck.format)})</option>`).join('');
  if (state.savedDecks.some(deck => deck.name === selected)) picker.value = selected;
}
async function deleteDeck(index) {
  const deck = state.savedDecks[index]; if (!deck) return;
  if (!window.confirm(`Delete “${deck.name}”? Its files will be moved to outputs/.trash and can be recovered.`)) return;
  const response = await fetch('/api/decks', {method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: deck.name, format: deck.format})});
  const result = await response.json();
  if (!response.ok) { $('#deck-status').className = 'deck-status error'; $('#deck-status').textContent = result.error || 'Could not delete deck.'; return; }
  if (state.deckInfo?.name === deck.name && state.deckInfo?.format === deck.format) newDeck();
  state.savedDecks = await fetch('/api/decks').then(r => r.json()); renderSavedDecks();
  $('#deck-status').className = 'deck-status success'; $('#deck-status').textContent = `Deleted “${deck.name}”. Files moved to outputs/.trash.`;
}
async function saveDeck() {
  const status = $('#deck-status'); status.className = 'deck-status'; status.textContent = 'Validating...';
  const payload = {...state.deck, name: $('#deck-name').value.trim() || 'Untitled Deck', format: $('#deck-format').value};
  try {
    const response = await fetch('/api/decks', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw new Error((result.validation?.errors || [result.error]).join(' '));
    state.deckInfo = result.deck; renderDeckBuilder(); status.classList.add('success'); status.textContent = `Saved and exported to outputs/${payload.format}.`;
    state.savedDecks = await fetch('/api/decks').then(r => r.json()); renderSavedDecks();
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
}
$('#deck-card-list').addEventListener('click', event => { const button = event.target.closest('[data-add]'); if (button) addToDeck(button.dataset.card, button.dataset.add); });
$('.deck-list-panel').addEventListener('click', event => { const add = event.target.closest('[data-add]'); const remove = event.target.closest('[data-remove]'); if (add) addToDeck(add.dataset.card, add.dataset.add); if (remove) removeFromDeck(remove.dataset.card, remove.dataset.remove); });
$('#decks-view').addEventListener('click', event => { const preview = event.target.closest('[data-preview-index]'); if (preview) openCard(Number(preview.dataset.previewIndex)); });
document.querySelectorAll('[data-deck-view]').forEach(button => button.addEventListener('click', () => { state.deckView = button.dataset.deckView; document.querySelectorAll('[data-deck-view]').forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); }); renderDeckBuilder(); }));
$('#deck-search').addEventListener('input', renderDeckBuilder); $('#deck-format').addEventListener('change', newDeck); $('#new-deck').addEventListener('click', newDeck); $('#save-deck').addEventListener('click', saveDeck);
$('#saved-decks').addEventListener('click', event => { const remove = event.target.closest('[data-delete-deck]'); const button = event.target.closest('[data-saved]'); if (remove) deleteDeck(Number(remove.dataset.deleteDeck)); else if (button) loadSavedDeck(state.savedDecks[Number(button.dataset.saved)]); });
function renderChat() {
  const log = $('#chat-messages');
  log.innerHTML = state.chatMessages.map(item => `<div class="chat-message ${item.role === 'user' ? 'user' : 'assistant'}"><strong>${item.role === 'user' ? 'You' : 'Codex'}</strong><p></p></div>`).join('');
  [...log.querySelectorAll('.chat-message p')].forEach((node, index) => { node.textContent = state.chatMessages[index].content; });
  log.scrollTop = log.scrollHeight;
}
async function refreshChatStatus() {
  try {
    const response = await fetch('/api/chat/status', {cache: 'no-store'});
    const result = await response.json();
    state.chatAvailable = Boolean(result.available);
    $('#chat-input').disabled = !state.chatAvailable;
    $('#chat-send').disabled = !state.chatAvailable;
    $('#chat-connection').textContent = state.chatAvailable ? 'Connected' : result.message;
    $('#chat-connection').classList.toggle('connected', state.chatAvailable);
    if (!state.chatAvailable) $('#chat-messages').innerHTML = `<p class="muted">${escapeHtml(result.message)} The chat is prepared but needs the server-side Codex app-server and ChatGPT-plan credentials.</p>`;
  } catch {
    state.chatAvailable = false;
    $('#chat-input').disabled = true;
    $('#chat-send').disabled = true;
    $('#chat-connection').textContent = 'Unavailable; retrying';
  }
}
refreshChatStatus();
window.setInterval(() => { if (!state.chatAvailable) refreshChatStatus(); }, 10000);
$('#chat-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('#chat-input'); const message = input.value.trim();
  if (!message || !state.chatAvailable) return;
  state.chatMessages.push({role: 'user', content: message});
  input.value = ''; renderChat();
  const send = $('#chat-send'); send.disabled = true; send.textContent = 'Thinking…';
  try {
    const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message, history: state.chatMessages.slice(0, -1), deckName: $('#chat-deck').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Server returned ${response.status}`);
    if (result.deck) {
      state.savedDecks = await fetch('/api/decks', {cache: 'no-store'}).then(r => r.json());
      renderSavedDecks();
      $('#chat-deck').value = result.deck.name;
    }
    state.chatMessages.push({role: 'assistant', content: result.answer}); renderChat();
  } catch (error) {
    state.chatMessages.push({role: 'assistant', content: `I couldn't complete that request: ${error.message}`}); renderChat();
  } finally { send.disabled = false; send.textContent = 'Send'; input.focus(); }
});
Promise.all([fetch('/api/formats').then(r => r.json()), fetch('/api/decks').then(r => r.json())]).then(([formats, decks]) => { state.formats = formats; state.savedDecks = decks; renderSavedDecks(); renderDeckBuilder(); }).catch(error => { $('#deck-status').textContent = `Deck builder unavailable: ${error.message}`; });
