const state = { all: [], filtered: [], visible: 60, formats: {}, savedDecks: [], chatMessages: [], chatAvailable: false, chatModelsLoaded: false, deckView: 'grid', deckInfo: null, reservationDeck: null, validatedDeckFingerprint: null, deckValidation: null, deck: {main: {}, sideboard: {}, commanders: {}} };
const DEFAULT_CHAT_MODEL = 'gpt-5.6-luna';
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
  const scannedCommanderCandidate = isScannedSource() && $('#deck-format').value === 'commander'
    && Boolean(card.commanderCandidate)
    && Boolean(state.deck.main[card.name] || state.deck.commanders[card.name]);
  const commanderControl = scannedCommanderCandidate
    ? `<label class="scanned-commander-toggle"><input id="scanned-commander-toggle" type="checkbox" data-commander-card="${escapeHtml(card.name)}" ${state.deck.commanders[card.name] ? 'checked' : ''}><span>Set as commander for this deck scan</span></label>`
    : '';
  const tags = [card.rarity, card.foil, card.language, `MV ${card.manaValue}`, card.commander === 'legal' ? 'Commander legal' : 'Not Commander legal'].filter(Boolean);
  const availability = card.availability || {totalOwned: card.quantity, reserved: 0, available: card.quantity, shortage: 0, reservedByDeck: []};
  const reservations = availability.reservedByDeck.map(item => `<li><span>${escapeHtml(item.name)}</span><b>${item.quantity} reserved</b></li>`).join('');
  const availabilityView = `<section class="card-availability"><h3>Copy availability</h3><p><strong>${availability.available}</strong> available · ${availability.reserved} reserved · ${availability.totalOwned} owned</p>${availability.shortage ? `<p class="availability-warning">${availability.shortage} more reserved than owned</p>` : ''}${reservations ? `<ul>${reservations}</ul>` : '<p class="muted">No saved deck reserves this card.</p>'}</section>`;
  const art = card.imageUrl ? `<img src="${escapeHtml(card.imageUrl)}" alt="${escapeHtml(card.name)}">` : '<div class="dialog-art-placeholder"></div>';
  const rows = card.printings.map(p => `<tr><td>${p.imageUrl ? `<a class="printing-image-link" href="${escapeHtml(p.scryfallUrl || p.imageUrl)}" target="_blank" rel="noopener" title="Open this exact printing"><img class="printing-image" src="${escapeHtml(p.imageUrl)}" alt="${escapeHtml(card.name)} ${escapeHtml(p.setCode)} printing" loading="lazy"></a>` : ''}</td><td>${escapeHtml(p.setCode.toUpperCase())} #${escapeHtml(p.collectorNumber)}</td><td>${escapeHtml(p.rarity)}</td><td>${escapeHtml(p.foil || 'normal')}</td><td>${escapeHtml(p.language)}</td><td>&times;${p.quantity}</td></tr>`).join('');
  $('#dialog-content').innerHTML = `<div class="dialog-layout">${art}<div class="dialog-copy"><h2>${escapeHtml(card.name)}</h2><p class="dialog-meta">${card.printings.length} owned printing${card.printings.length === 1 ? '' : 's'} · ${card.quantity} copies</p>${commanderControl}<p>${escapeHtml(card.manaCost)}</p><p class="dialog-type">${escapeHtml(card.typeLine)}</p><p class="dialog-rules">${escapeHtml(card.oracleText) || '<i>No rules text</i>'}</p><div class="dialog-tags">${tags.map(x => `<span>${escapeHtml(x)}</span>`).join('')}</div>${availabilityView}<h3 class="printing-title">Owned printings</h3><table class="printing-list"><thead><tr><th>Art</th><th>Printing</th><th>Rarity</th><th>Finish</th><th>Lang</th><th>Qty</th></tr></thead><tbody>${rows}</tbody></table>${card.scryfallUrl ? `<p><a class="scryfall-link" href="${escapeHtml(card.scryfallUrl)}" target="_blank" rel="noopener">Open on Scryfall ↗</a></p>` : ''}</div></div>`;
  $('#card-dialog').showModal();
}

search.addEventListener('input', applyFilters);
controls.forEach(control => control.addEventListener('change', applyFilters));
$('#clear').addEventListener('click', () => { search.value = ''; controls.slice(0,5).forEach(x => x.value = ''); applyFilters(); search.focus(); });
$('#show-more').addEventListener('click', () => { state.visible += 60; render(); });
grid.addEventListener('click', event => { const button = event.target.closest('[data-index]'); if (button) openCard(Number(button.dataset.index)); });
$('#card-dialog .dialog-close').addEventListener('click', () => $('#card-dialog').close());
$('#card-dialog').addEventListener('click', event => { if (event.target === $('#card-dialog')) $('#card-dialog').close(); });
document.addEventListener('keydown', event => { if (event.key === '/' && document.activeElement !== search) { event.preventDefault(); search.focus(); } });

const importDialog = $('#import-dialog');
function setImportMode() { $('#import-deck-name-wrap').hidden = $('#import-mode').value !== 'deck'; }
$('#open-import').addEventListener('click', () => { setImportMode(); $('#import-status').textContent = ''; $('#import-unmatched').innerHTML = ''; importDialog.showModal(); });
$('#close-import').addEventListener('click', () => importDialog.close());
$('#cancel-import').addEventListener('click', () => importDialog.close());
$('#import-mode').addEventListener('change', setImportMode);
async function fileAsBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer()); let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}
$('#import-form').addEventListener('submit', async event => {
  event.preventDefault();
  const file = $('#import-file').files[0]; const mode = $('#import-mode').value;
  const deckName = $('#import-deck-name').value.trim(); const status = $('#import-status');
  $('#import-unmatched').innerHTML = '';
  if (!file) { status.textContent = 'Choose a ManaBox CSV first.'; return; }
  if (file.size > 14 * 1024 * 1024) { status.textContent = 'Choose a CSV smaller than 14 MB.'; return; }
  if (mode === 'deck' && !deckName) { status.textContent = 'Enter a name for this scanned deck.'; return; }
  const submit = $('#submit-import'); submit.disabled = true; submit.textContent = 'Analyzing…';
  status.textContent = 'Reading the CSV and matching cards against the local Scryfall database…';
  try {
    const response = await fetch('/api/import', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mode, deckName, csvBase64: await fileAsBase64(file)})
    });
    const result = await response.json();
    if (!response.ok) {
      status.textContent = result.error || 'The CSV could not be imported.';
      if (result.unmatched?.length) $('#import-unmatched').innerHTML = `<h3>Cards needing review</h3><ul>${result.unmatched.map(item => `<li><b>${escapeHtml(item['Card Name'])}</b> — ${escapeHtml(item.Reason)}</li>`).join('')}</ul>`;
      return;
    }
    const summary = result.summary;
    status.textContent = `${result.added ? result.message : 'This exact CSV was already imported.'} ${summary.rows} rows · ${summary.matched_rows} matched · ${summary.unmatched_rows} unmatched · ${summary.copies} copies.`;
    if (result.unmatched?.length) $('#import-unmatched').innerHTML = `<h3>Unmatched cards</h3><ul>${result.unmatched.map(item => `<li><b>${escapeHtml(item['Card Name'])}</b> — ${escapeHtml(item.Reason)}</li>`).join('')}</ul>`;
    if (result.added) {
      await reloadCollection();
      state.savedDecks = await fetch('/api/decks', {cache: 'no-store'}).then(r => r.json());
      renderSavedDecks(); renderDeckBuilder();
    }
  } catch (error) {
    status.textContent = `Import failed: ${error.message}`;
  } finally {
    submit.disabled = false; submit.textContent = 'Analyze & import';
  }
});
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

async function reloadCollection() {
  const response = await fetch('/api/collection', {cache: 'no-store'});
  if (!response.ok) throw new Error(`Server returned ${response.status}`);
  const data = await response.json();
  state.all = data.cards.map(card => ({...card, _search: normalize([card.name, card.typeLine, card.oracleText, card.setNames.join(' '), card.setCodes.join(' '), card.keywords].join(' '))}));
  $('#source-name').textContent = data.source;
  $('#loading').hidden = true;
  $('#color-filter').innerHTML = '<option value="">Any color</option>';
  $('#type-filter').innerHTML = '<option value="">Any type</option>';
  $('#set-filter').innerHTML = '<option value="">Any set</option>';
  $('#rarity-filter').innerHTML = '<option value="">Any rarity</option>';
  $('#foil-filter').innerHTML = '<option value="">Any finish</option>';
  populateFilters(); applyFilters();
  return data;
}
reloadCollection().catch(error => { $('#loading').textContent = `Could not load collection: ${error.message}`; });

function deckTotal(section) { return Object.values(state.deck[section]).reduce((sum, count) => sum + count, 0); }
function ownedInDeck(name) { return ['main', 'sideboard', 'commanders'].reduce((sum, section) => sum + (state.deck[section][name] || 0), 0); }
function isScannedSource() { return state.deckInfo?.format === 'scanned'; }
function legalForFormat(card, format) { return card.legalities?.[format] === 'legal' || /^Basic Land/.test(card.typeLine); }
function availableToCurrentDeck(card) {
  const owned = Number(card.availability?.totalOwned ?? card.quantity) || 0;
  let reserved = Number(card.availability?.reserved || 0);
  if (state.reservationDeck) {
    const own = (card.availability?.reservedByDeck || []).find(item => item.name === state.reservationDeck.name);
    reserved -= Number(own?.quantity || 0);
  }
  return Math.max(0, owned - reserved);
}
function maxCopies(card, format) { const available = availableToCurrentDeck(card); return format === 'commander' ? Math.min(1, available) : Math.min(4, available); }
function addToDeck(name, section) {
  const card = state.all.find(item => item.name === name); const format = $('#deck-format').value;
  if (!card || !legalForFormat(card, format)) return;
  if (section === 'commanders' && (!card.commanderCandidate || format !== 'commander')) return;
  if (ownedInDeck(name) >= maxCopies(card, format)) return;
  clearDeckValidation();
  state.deckInfo = null;
  state.deck[section][name] = (state.deck[section][name] || 0) + 1; renderDeckBuilder();
}
function removeFromDeck(name, section) {
  if (!state.deck[section][name]) return;
  clearDeckValidation();
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
  const actions = state.deckInfo?.format === 'scanned' ? '' : `<span class="deck-card-actions"><button type="button" data-remove="${section}" data-card="${escapeHtml(name)}" aria-label="Remove ${escapeHtml(name)}">−</button><button type="button" data-add="${section}" data-card="${escapeHtml(name)}" aria-label="Add another ${escapeHtml(name)}">+</button></span>`;
  return `<article class="deck-visual-card">
    <span class="deck-qty">${count}×</span>
    <button class="deck-card-art" type="button" ${preview} aria-label="View ${escapeHtml(name)}">${art}</button>
    ${actions}
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
let deckValidationPanelOpen = true;
let deckPrimerPanelOpen = true;

function renderDeckPrimer() {
  const report = state.deckInfo?.report;
  if (!report) {
    $('#deck-primer').innerHTML = Object.keys(state.deck.main).length ? '<p>Save this deck to generate its strategy and winning-condition summary. Validation issues stay visible, but won’t block saving.</p>' : '';
    return;
  }
  const priorPanel = $('#deck-primer details');
  if (priorPanel) deckPrimerPanelOpen = priorPanel.open;
  const wins = report.win_conditions || [];
  const engineDetails = report.engine_details || [];
  const packageRows = [
    ['Interaction', report.interaction], ['Protection', report.protection], ['Recovery', report.recovery],
  ].filter(([, cards]) => Array.isArray(cards) && cards.length);
  const packageMarkup = packageRows.length ? `<div class="primer-packages">${packageRows.map(([heading, cards]) =>
    `<div><strong>${heading}</strong><span>${cards.map(escapeHtml).join(' · ')}</span></div>`).join('')}</div>` : '';
  const weaknesses = report.weaknesses || [];
  const weaknessMarkup = weaknesses.length
    ? `<div class="primer-weaknesses"><strong>Known weaknesses</strong><ul>${weaknesses.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul></div>` : '';
  const estimate = report.connive_opening_estimate;
  const estimateMarkup = estimate ? `<div class="primer-estimate"><strong>Opening hand estimate</strong><p>${Number(estimate.probability * 100).toFixed(1)}% chance to see at least one of ${Number(estimate.source_copies)} connive sources in ${Number(estimate.opening_hand_size)} cards; ${Number(estimate.early_probability * 100).toFixed(1)}% for ${Number(estimate.early_source_copies)} early sources.</p><small>Raw draw math only; it does not account for mana, sequencing, or mulligans.</small></div>` : '';
  const curve = report.mana_curve && typeof report.mana_curve === 'object'
    ? Object.entries(report.mana_curve).map(([mv, count]) => `${mv} mana: ${count}`).join(' · ') : '';
  const curveMarkup = curve ? `<p class="primer-curve"><strong>Mana curve:</strong> ${escapeHtml(curve)} · ${Number(report.land_count || 0)} lands</p>` : '';
  $('#deck-primer').innerHTML = `<details class="primer-collapsible" ${deckPrimerPanelOpen ? 'open' : ''}><summary class="primer-summary"><div class="deck-primer-header"><div><span class="primer-label">Deck primer</span><h2>${escapeHtml(state.deckInfo.archetype || 'Deck strategy')}</h2><p class="primer-summary-objective">${escapeHtml(report.primary_objective || '')}</p></div><span class="primer-identity"><span class="primer-label">${escapeHtml(report.identity || '')}</span><span class="deck-collapse-indicator" aria-hidden="true"></span></span></div></summary><div class="primer-content">
    <p>${escapeHtml(report.primary_objective || '')}</p>
    <div class="primer-path"><div><strong>Early game</strong>${escapeHtml(report.early_game || '')}</div><div><strong>Mid game</strong>${escapeHtml(report.middle_game || '')}</div><div><strong>Late game</strong>${escapeHtml(report.late_game || '')}</div></div>
    ${engineDetails.length ? `<div class="primer-engine"><strong>${escapeHtml(report.engine_heading || 'Synergy engine')}</strong><ul>${engineDetails.map(detail => `<li>${escapeHtml(detail)}</li>`).join('')}</ul></div>` : ''}
    <p><strong>Winning conditions</strong></p><div class="primer-wins">${wins.length ? wins.map(name => `<span>${escapeHtml(name)}</span>`).join('') : '<span>No distinct finishers detected</span>'}</div>
    ${packageMarkup}${estimateMarkup}${curveMarkup}${weaknessMarkup}
    ${report.sideboard_strategy ? `<p class="primer-sideboard"><strong>Sideboard:</strong> ${escapeHtml(report.sideboard_strategy)}</p>` : ''}</div></details>`;
  $('#deck-primer details').addEventListener('toggle', event => { deckPrimerPanelOpen = event.currentTarget.open; });
}
function renderDeckFormatAnalysis() {
  const section = $('#deck-format-analysis');
  const entries = ['main', 'sideboard', 'commanders'].flatMap(key => Object.entries(state.deck[key] || {}));
  if (!state.deckInfo || !entries.length) { section.hidden = true; section.innerHTML = ''; return; }
  const labels = [['alchemy','Alchemy'],['pioneer','Pioneer'],['historic','Historic'],['modern','Modern'],['brawl','Brawl'],['legacy','Legacy'],['vintage','Vintage'],['timeless','Timeless'],['commander','Commander'],['pauper','Pauper'],['oathbreaker','Oathbreaker'],['penny','Penny']];
  const result = labels.map(([key,label]) => {
    let legal = 0, notLegal = 0, unknown = 0;
    for (const [name,count] of entries) {
      const card = state.all.find(item => item.name === name);
      const status = card?.legalities?.[key];
      if (status === 'legal' || status === 'restricted') legal += count;
      else if (status === 'not_legal' || status === 'banned') notLegal += count;
      else unknown += count;
    }
    const status = notLegal ? `${notLegal} not legal` : unknown ? `${unknown} unknown` : 'All cards legal';
    return `<button class="format-analysis-row" type="button" data-legality-format="${key}" data-legality-label="${label}" aria-label="Show cards not legal in ${label}"><b>${label}</b><span>${legal}/${entries.reduce((sum, [,count]) => sum + count, 0)} legal · ${escapeHtml(status)}</span></button>`;
  }).join('');
  const compatible = state.deckInfo.compatible_formats || [];
  const commanderCandidates = state.deckInfo.format_analysis?.commander_candidates || [];
  const wholeDeck = compatible.length
    ? `<p class="format-analysis-note">Full deck rules and legality pass: ${compatible.map(name => name[0].toUpperCase() + name.slice(1)).join(', ')}.</p>`
    : '<p class="format-analysis-note">Full deck validation is available for Commander, Pauper, Standard, and Legacy. Select a format and choose a commander (if needed), then use Validate deck.</p>';
  const commanderNote = commanderCandidates.length ? `<p class="format-analysis-note">Commander scan candidates that pass full deck validation: ${commanderCandidates.map(escapeHtml).join(', ')}.</p>` : '';
  section.innerHTML = `<h2>Card legality by format</h2><p>Per-card Scryfall legality for the cards in this deck; deck-size and other rules are checked separately.</p><div class="format-analysis-grid">${result}</div><p class="format-analysis-note">Pauper legality is card-level: a card qualifies if it has a common printing, even when the copy you own is an uncommon or rare printing.</p>${wholeDeck}${commanderNote}`;
  section.hidden = false;
}
function showFormatLegality(formatKey, formatLabel) {
  const placements = ['main', 'sideboard', 'commanders'].flatMap(section =>
    Object.entries(state.deck[section] || {}).map(([name, quantity]) => ({section, name, quantity}))
  );
  const lookup = new Map(state.all.map(card => [card.name, card]));
  const notLegal = [];
  const unknown = [];
  for (const item of placements) {
    const card = lookup.get(item.name);
    const legality = card?.legalities?.[formatKey] || 'unknown';
    const row = {...item, legality};
    if (legality === 'not_legal' || legality === 'banned') notLegal.push(row);
    else if (legality !== 'legal' && legality !== 'restricted') unknown.push(row);
  }
  const zoneName = {main: 'Main deck', sideboard: 'Sideboard', commanders: 'Commander'};
  const renderRows = rows => rows.map(item => `<tr><th scope="row">${escapeHtml(item.name)}</th><td>${escapeHtml(zoneName[item.section])} · ×${Number(item.quantity)}</td><td>${item.legality === 'banned' ? 'Banned' : item.legality === 'not_legal' ? 'Not legal' : 'Unknown'}</td></tr>`).join('');
  const content = [];
  if (notLegal.length) content.push(`<h3>Not legal in ${escapeHtml(formatLabel)} (${notLegal.length})</h3><div class="legality-table-wrap"><table class="legality-table"><thead><tr><th>Card</th><th>Deck section and copies</th><th>Status</th></tr></thead><tbody>${renderRows(notLegal)}</tbody></table></div>`);
  if (unknown.length) content.push(`<h3>Legality unknown (${unknown.length})</h3><div class="legality-table-wrap"><table class="legality-table"><thead><tr><th>Card</th><th>Deck section and copies</th><th>Status</th></tr></thead><tbody>${renderRows(unknown)}</tbody></table></div>`);
  $('#legality-dialog-title').textContent = `Cards not legal in ${formatLabel}`;
  $('#legality-dialog-note').textContent = 'This is a per-card Scryfall legality check. Deck size, copy limits, and other format rules are evaluated separately.';
  $('#legality-dialog-cards').innerHTML = content.join('') || `<p class="legality-empty">All ${placements.length} card entries are marked legal for ${escapeHtml(formatLabel)}.</p>`;
  $('#legality-dialog').showModal();
}
function renderDeckBuilder() {
  const format = $('#deck-format').value; const scanned = isScannedSource();
  $('.deck-workspace').classList.toggle('deck-workspace-scanned', scanned);
  const config = state.formats[format] || {main_size: 0, sideboard_target: 0, commander_min: 0};
  $('#deck-name').readOnly = false; $('#deck-format').disabled = false;
  $('#save-deck').disabled = !state.validatedDeckFingerprint
    || state.validatedDeckFingerprint !== deckFingerprint(deckPayload());
  $('#save-deck').textContent = state.deckValidation && (!state.deckValidation.valid || state.deckValidation.collection_complete === false)
    ? 'Save as draft' : 'Save deck';
  $('#simulate-deck').disabled = !state.deckInfo || scanned || format === 'scanned' || !state.simulationAvailable;
  $('#test-hand').disabled = !state.deckInfo || scanned || format === 'scanned';
  updateBattleButton();
  document.querySelector('.available-cards').hidden = scanned;
  $('#commander-section').hidden = format !== 'commander'; $('#sideboard-section').hidden = format === 'commander' || format === 'scanned';
  $('#scanned-commander-picker-wrap').hidden = !(scanned && format === 'commander');
  $('#scanned-commander-hint').hidden = !(scanned && format === 'commander');
  if (scanned) {
    if (!state.validatedDeckFingerprint) $('#deck-status').textContent = `Scanned deck · choose a format to validate. Its ${deckTotal('main') + deckTotal('commanders')} card copies remain reserved.`;
    renderScannedCommanderPicker();
  }
  $('#commander-total').textContent = `${deckTotal('commanders')} / ${config.commander_min || 0}`;
  $('#main-total').textContent = config.main_min_size
    ? `${deckTotal('main')} / ${config.main_min_size}+ minimum`
    : `${deckTotal('main')} / ${config.main_size}`;
  $('#sideboard-total').textContent = `${deckTotal('sideboard')} / ${config.sideboard_target}`;
  const selected = Object.entries(state.deck.main).map(([name,count]) => ({card: state.all.find(item => item.name === name), count}));
  const lands = selected.reduce((sum,item) => sum + (item.card?.typeLine.includes('Land') ? item.count : 0), 0);
  const creatures = selected.reduce((sum,item) => sum + (item.card?.typeLine.includes('Creature') ? item.count : 0), 0);
  const spells = selected.filter(item => item.card && !item.card.typeLine.includes('Land'));
  const spellCount = spells.reduce((sum,item) => sum + item.count, 0);
  const averageMv = spellCount ? spells.reduce((sum,item) => sum + item.card.manaValue * item.count, 0) / spellCount : 0;
  $('#deck-summary').innerHTML = `<span><strong>${deckTotal('main') + deckTotal('commanders')}</strong> deck cards</span><span><strong>${Object.keys(state.deck.main).length}</strong> unique main</span><span><strong>${creatures}</strong> creatures</span><span><strong>${lands}</strong> lands</span><span><strong>${averageMv.toFixed(2)}</strong> avg. mana value</span>`;
  renderDeckFormatAnalysis();
  renderDeckPrimer();
  const query = normalize($('#deck-search').value);
  const available = scanned ? [] : state.all.filter(card => legalForFormat(card, format) && (!query || card._search.includes(query))).slice(0, 150);
  $('#deck-card-list').innerHTML = available.map(card => {
    const remaining = Math.max(0, maxCopies(card, format) - ownedInDeck(card.name));
    const commanderButton = format === 'commander' && card.commanderCandidate ? `<button data-add="commanders" data-card="${escapeHtml(card.name)}" ${remaining ? '' : 'disabled'} title="Add commander">C</button>` : '';
    const thumb = card.imageUrl ? `<button class="deck-card-art" type="button" data-preview-index="${card.index}" aria-label="View ${escapeHtml(card.name)}"><img class="deck-card-thumb" src="${escapeHtml(card.imageUrl)}" alt="" loading="lazy"></button>` : '<span class="deck-card-thumb"></span>';
    return `<div class="deck-card-row">${thumb}<span><b>${escapeHtml(card.name)}</b><small>${escapeHtml(card.typeLine)} · ${remaining}/${availableToCurrentDeck(card)} available</small></span><span class="deck-adds">${commanderButton}<button data-add="main" data-card="${escapeHtml(card.name)}" ${remaining ? '' : 'disabled'} title="Add to main">+</button>${format !== 'commander' ? `<button data-add="sideboard" data-card="${escapeHtml(card.name)}" ${remaining ? '' : 'disabled'} title="Add to sideboard">S</button>` : ''}</span></div>`;
  }).join('') || '<p class="muted">No owned legal cards match.</p>';
  document.querySelectorAll('[data-deck-section]').forEach(container => {
    const section = container.dataset.deckSection;
    renderDeckSection(container, section);
  });
  renderTokens();
}
function clearDeckValidation() {
  state.validatedDeckFingerprint = null;
  state.deckValidation = null;
  $('#save-deck').disabled = true;
  $('#deck-validation').hidden = true;
  $('#deck-validation').innerHTML = '';
}
function deckPayload() {
  const metadataFields = ['report', 'forge_evidence', 'optimization_history', 'commander_research',
    'commander_candidate_comparison', 'final_verdict', 'analysis_status', 'parent'];
  const sameSection = section => {
    const normalize = value => Object.entries(value || {}).sort(([left], [right]) => left.localeCompare(right));
    return JSON.stringify(normalize(state.deckInfo?.[section])) === JSON.stringify(normalize(state.deck?.[section]));
  };
  const preserveAnalysis = state.deckInfo?.format === $('#deck-format').value
    && ['main', 'sideboard', 'commanders'].every(sameSection);
  const metadata = preserveAnalysis
    ? Object.fromEntries(metadataFields.filter(field => Object.prototype.hasOwnProperty.call(state.deckInfo, field))
      .map(field => [field, state.deckInfo[field]])) : {};
  return {...state.deck, name: $('#deck-name').value.trim() || 'Untitled Deck', format: $('#deck-format').value,
    ...metadata,
    ...(state.deckInfo?.ownership_mode === 'proposal' ? {ownership_mode: 'proposal'} : {}),
    ...(isScannedSource() ? {source_scanned_name: state.deckInfo.name} : {})};
}
function deckFingerprint(payload) {
  const sortedSection = section => Object.fromEntries(Object.entries(payload[section] || {}).sort(([a], [b]) => a.localeCompare(b)));
  return JSON.stringify({name: payload.name, format: payload.format, source_scanned_name: payload.source_scanned_name || '', main: sortedSection('main'),
    sideboard: sortedSection('sideboard'), commanders: sortedSection('commanders')});
}
function renderDeckValidation(validation) {
  const priorPanel = $('#deck-validation details');
  if (priorPanel) deckValidationPanelOpen = priorPanel.open;
  const report = validation.allocation_report || [];
  const errors = validation.errors || [];
  const rows = new Map();
  const cardKey = value => String(value).toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  const locationMarkup = (item) => {
    const locations = [...(item.locations || [])];
    if (item.current_deck_copies) locations.unshift({deck: `${item.current_deck_name} · this deck`, copies: item.current_deck_copies});
    return locations.length
      ? locations.map(location => `<span class="validation-location">${escapeHtml(location.deck)} <b>×${location.copies}</b></span>`).join('')
      : '<span class="validation-empty">No deck location recorded</span>';
  };
  for (const item of report) {
    const reserved = Number(item.reserved_elsewhere || 0);
    const note = item.shortage
      ? `${item.needed} needed · ${item.owned} owned · ${item.available} available · ${item.shortage} short`
      : `${item.needed} needed · ${item.owned} owned · ${item.available} available · ${reserved} reserved elsewhere`;
    const proposalGap = validation.ownership_mode === 'proposal' && Boolean(item.shortage);
    rows.set(cardKey(item.card_name), {cardName: item.card_name, notes: [note], locations: locationMarkup(item), hasError: Boolean(item.shortage) && !proposalGap, hasWarning: proposalGap});
  }
  for (const error of errors) {
    const allocation = report.find(item => error.startsWith(`${item.card_name} needs `));
    if (allocation) continue;
    const unknown = error.match(/^Unknown card: (.+)\.$/);
    const illegal = error.match(/^(.+?) is not currently (.+)-legal\.$/);
    const copyLimit = error.match(/^(.+?) has (\d+) copies across deck sections; limit is (\d+)\.$/);
    const cardName = unknown?.[1] || illegal?.[1] || copyLimit?.[1] || 'Deck rules';
    const where = unknown ? 'Not found in the owned collection'
      : illegal ? 'Owned collection · format legality'
      : copyLimit ? 'This deck · copy limit'
      : 'Deck structure · format rules';
    const key = cardKey(cardName);
    const existing = rows.get(key);
    if (existing) { existing.notes.push(error); existing.hasError = true; }
    else rows.set(key, {cardName, notes: [error], locations: `<span class="validation-location">${escapeHtml(where)}</span>`, hasError: true});
  }
  const rowMarkup = rows.size ? [...rows.values()].map(item =>
    `<tr class="${item.hasError ? 'validation-row-error' : item.hasWarning ? 'validation-row-warning' : ''}"><th scope="row">${escapeHtml(item.cardName)}</th><td>${item.notes.map(note => `<span class="validation-note">${escapeHtml(note)}</span>`).join('')}</td><td>${item.locations}</td></tr>`
  ).join('')
    : `<tr><td colspan="3" class="validation-no-issues">${validation.valid ? 'No validation issues found.' : 'No card-specific details are available.'}</td></tr>`;
  const issueCount = errors.length;
  const summary = validation.valid && validation.collection_complete === false
    ? 'Format, deck size, and legality pass. Quantity gaps remain; this proposal reserves no collection copies.'
    : validation.valid
      ? 'Format, card counts, legality, and collection availability pass.'
      : `${issueCount} format or availability issue${issueCount === 1 ? '' : 's'}. You can still save this deck as a draft and generate its strategy summary.`;
  const table = `<div class="validation-table-wrap"><table class="validation-results-table"><thead><tr><th scope="col">Card</th><th scope="col">Note</th><th scope="col">Where found</th></tr></thead><tbody>${rowMarkup}</tbody></table></div>`;
  const resultLabel = validation.valid && validation.collection_complete === false ? 'Format passed · quantity gaps'
    : validation.valid ? 'Passed' : `${issueCount} issues`;
  const resultClass = !validation.valid ? 'has-errors'
    : validation.collection_complete === false ? 'has-warnings' : 'is-valid';
  $('#deck-validation').innerHTML = `<details class="validation-collapsible" ${deckValidationPanelOpen ? 'open' : ''}><summary class="validation-heading"><div><p class="eyebrow">Deck check</p><h3>Validation results</h3></div><span class="validation-count ${resultClass}">${resultLabel}</span><span class="deck-collapse-indicator" aria-hidden="true"></span></summary><div class="validation-content"><p class="deck-validation-summary">${escapeHtml(summary)}</p>${table}</div></details>`;
  $('#deck-validation details').addEventListener('toggle', event => { deckValidationPanelOpen = event.currentTarget.open; });
  $('#deck-validation').hidden = false;
}
function renderScannedCommanderPicker() {
  const select = $('#scanned-commander');
  const current = Object.keys(state.deck.commanders)[0] || '';
  const choices = Object.keys(state.deck.main).filter(name => state.all.some(card => card.name === name && card.commanderCandidate));
  select.innerHTML = '<option value="">Choose a commander</option>' + choices.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
  select.value = choices.includes(current) ? current : '';
}
function chooseScannedCommander(name) {
  const previous = Object.keys(state.deck.commanders)[0];
  if (previous) {
    state.deck.main[previous] = (state.deck.main[previous] || 0) + state.deck.commanders[previous];
    delete state.deck.commanders[previous];
  }
  if (name && state.deck.main[name] > 0) {
    state.deck.main[name] -= 1;
    if (!state.deck.main[name]) delete state.deck.main[name];
    state.deck.commanders[name] = 1;
  }
  clearDeckValidation();
  renderDeckBuilder();
}
function onDeckFormatChange() {
  if (!isScannedSource()) { newDeck(); return; }
  if ($('#deck-format').value !== 'commander' && Object.keys(state.deck.commanders).length) {
    for (const [name, count] of Object.entries(state.deck.commanders)) state.deck.main[name] = (state.deck.main[name] || 0) + count;
    state.deck.commanders = {};
  }
  clearDeckValidation();
  renderDeckBuilder();
}
function newDeck() { closeHandTester(); clearDeckValidation(); state.deckInfo = null; state.reservationDeck = null; if ($('#deck-format').value === 'scanned') $('#deck-format').value = 'commander'; state.deck = {main: {}, sideboard: {}, commanders: {}}; $('#deck-name').value = 'New Deck'; $('#deck-status').textContent = ''; renderDeckBuilder(); }
function createNewDeck() {
  newDeck();
  $('#deck-status').textContent = 'Choose a format, add cards, then validate and save your deck.';
  $('#deck-editor').scrollIntoView({behavior: 'smooth', block: 'start'});
  $('#deck-name').focus();
}
function loadSavedDeck(deck) { closeHandTester(); clearDeckValidation(); state.deckInfo = deck; state.reservationDeck = {name: deck.name, format: deck.format}; state.deck = {main: {...deck.main}, sideboard: {...(deck.sideboard || {})}, commanders: {...(deck.commanders || {})}}; $('#deck-name').value = deck.name; $('#deck-format').value = deck.format; $('#chat-deck').value = deck.name; renderDeckBuilder(); if (deck.format !== 'scanned') checkSavedDeck(deck); }
async function checkSavedDeck(deck) {
  const status = $('#deck-status');
  status.className = 'deck-status';
  status.textContent = 'Checking current format rules and card availability...';
  try {
    const response = await fetch('/api/decks/validate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: deck.name, format: deck.format, ownership_mode: deck.ownership_mode,
        main: deck.main,
        sideboard: deck.sideboard || {}, commanders: deck.commanders || {}}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not check this saved deck.');
    if (state.deckInfo?.name !== deck.name || state.deckInfo?.format !== deck.format) return;
    state.deckValidation = result.validation || {};
    renderDeckValidation(state.deckValidation);
    status.classList.add(state.deckValidation.valid && state.deckValidation.collection_complete !== false ? 'success' : 'error');
    status.textContent = state.deckValidation.valid && state.deckValidation.collection_complete === false
      ? 'Format is valid, but collection quantities are short. This proposal does not reserve your copies.'
      : state.deckValidation.valid
        ? 'Saved deck check passed: format rules and current card availability are valid.'
      : 'Saved deck check found issues. Review the results table below.';
  } catch (error) {
    if (state.deckInfo?.name !== deck.name || state.deckInfo?.format !== deck.format) return;
    status.classList.add('error');
    status.textContent = `Could not refresh this deck check: ${error.message}`;
  }
}
function renderSavedDecks() {
  $('#deck-count').textContent = `${state.savedDecks.length} saved deck${state.savedDecks.length === 1 ? '' : 's'}`;
  const formats = [['commander', 'Commander'], ['pauper', 'Pauper'], ['standard', 'Standard'], ['legacy', 'Legacy']];
  const row = (deck, index) => {
    const checked = deck.format !== 'scanned' && deck.validation?.valid;
    const validation = deck.format === 'scanned' ? 'Choose format to validate'
      : deck.ownership_mode === 'proposal' ? 'Quantity proposal'
        : checked ? 'Validation passed' : deck.validation ? 'Validation needs review' : 'Validation not recorded';
    const primer = deck.report?.primary_objective ? 'Primer available' : 'Primer pending';
    const formatText = deck.format === 'scanned'
      ? `Scan · fits: ${(deck.compatible_formats || []).map(name => name[0].toUpperCase() + name.slice(1)).join(', ') || 'choose a format to validate'}`
      : `Saved as ${escapeHtml(deck.format)}`;
    return `<div class="saved-deck-row"><button class="saved-deck" data-saved="${index}">${escapeHtml(deck.name)}<small>${formatText} · ${validation} · ${primer}</small></button><button class="delete-deck" type="button" data-delete-deck="${index}" aria-label="Delete ${escapeHtml(deck.name)}" title="Delete deck">×</button></div>`;
  };
  const sections = formats.map(([format, label]) => {
    const matches = state.savedDecks.map((deck, index) => ({deck, index}))
      .filter(({deck}) => (deck.compatible_formats || [deck.format]).includes(format));
    return `<details class="saved-deck-format-section" open><summary>${label}<span>${matches.length}</span></summary>${matches.map(({deck, index}) => row(deck, index)).join('') || '<p class="muted">No compatible saved decks.</p>'}</details>`;
  });
  const scanned = state.savedDecks.map((deck, index) => ({deck, index})).filter(({deck}) => deck.format === 'scanned' && !(deck.compatible_formats || []).length);
  if (scanned.length) sections.push(`<details class="saved-deck-format-section"><summary>Scanned / unclassified<span>${scanned.length}</span></summary>${scanned.map(({deck, index}) => row(deck, index)).join('')}</details>`);
  const unclassified = state.savedDecks.map((deck, index) => ({deck, index}))
    .filter(({deck}) => deck.format !== 'scanned' && !(deck.compatible_formats || [deck.format]).length);
  if (unclassified.length) sections.push(`<details class="saved-deck-format-section"><summary>Needs legality review<span>${unclassified.length}</span></summary>${unclassified.map(({deck, index}) => row(deck, index)).join('')}</details>`);
  $('#saved-decks').innerHTML = sections.join('') || '<p class="muted">No saved decks yet.</p>';
  const picker = $('#chat-deck'); const selected = picker.value;
  picker.innerHTML = '<option value="">Collection / general question</option>' + state.savedDecks.map(deck => `<option value="${escapeHtml(deck.name)}">${escapeHtml(deck.name)} (${escapeHtml(deck.format)})</option>`).join('');
  if (state.savedDecks.some(deck => deck.name === selected)) picker.value = selected;
  updateBattleButton();
}
async function deleteDeck(index) {
  const deck = state.savedDecks[index]; if (!deck) return;
  const prompt = deck.format === 'scanned'
    ? `Remove the reservation for “${deck.name}”? Its scanned cards will remain in your collection.`
    : `Delete “${deck.name}”? Its files will be moved to outputs/.trash and can be recovered.`;
  if (!window.confirm(prompt)) return;
  const response = await fetch('/api/decks', {method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: deck.name, format: deck.format})});
  const result = await response.json();
  if (!response.ok) { $('#deck-status').className = 'deck-status error'; $('#deck-status').textContent = result.error || 'Could not delete deck.'; return; }
  if (state.deckInfo?.name === deck.name && state.deckInfo?.format === deck.format) newDeck();
  await reloadCollection();
  state.savedDecks = await fetch('/api/decks').then(r => r.json()); renderSavedDecks(); renderDeckBuilder();
  $('#deck-status').className = 'deck-status success';
  $('#deck-status').textContent = deck.format === 'scanned'
    ? `Removed the reservation for “${deck.name}”. Its cards remain in your collection.`
    : `Deleted “${deck.name}”. Files moved to outputs/.trash.`;
}
async function validateDeck() {
  clearDeckValidation();
  const status = $('#deck-status'); status.className = 'deck-status'; status.textContent = 'Checking format rules and card reservations...';
  const payload = deckPayload();
  const fingerprint = deckFingerprint(payload);
  try {
    const response = await fetch('/api/decks/validate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not validate this deck.');
    const validation = result.validation || {};
    state.deckValidation = validation;
    state.validatedDeckFingerprint = fingerprint;
    renderDeckValidation(validation);
    if (!validation.valid) {
      status.classList.add('error'); status.textContent = 'Validation found issues. Review the table, then save as a draft to keep the deck and generate its strategy summary.';
      renderDeckBuilder();
      return;
    }
    status.classList.add(validation.collection_complete === false ? 'error' : 'success');
    status.textContent = validation.collection_complete === false
      ? 'Format passed with collection quantity gaps. This can be saved as a proposal without reserving copies.'
      : 'Validation passed. Save the deck when you are ready.';
    renderDeckBuilder();
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
}
async function saveDeck() {
  const status = $('#deck-status'); status.className = 'deck-status';
  const payload = deckPayload();
  if (!state.validatedDeckFingerprint || state.validatedDeckFingerprint !== deckFingerprint(payload)) {
    status.classList.add('error'); status.textContent = 'Validate the current deck before saving.'; return;
  }
  status.textContent = 'Saving deck...';
  try {
    const response = await fetch('/api/decks', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const result = await response.json();
    if (!response.ok) {
      if (result.validation) { state.deckValidation = result.validation; renderDeckValidation(result.validation); }
      state.validatedDeckFingerprint = null; $('#save-deck').disabled = true;
      throw new Error((result.validation?.errors || [result.error]).join(' '));
    }
    state.deckInfo = result.deck; state.reservationDeck = {name: result.deck.name, format: result.deck.format};
    state.deckValidation = result.validation || result.deck.validation || state.deckValidation;
    state.validatedDeckFingerprint = null;
    await reloadCollection(); renderDeckBuilder();
    if (state.deckValidation) renderDeckValidation(state.deckValidation);
    const incomplete = state.deckValidation && (!state.deckValidation.valid || state.deckValidation.collection_complete === false);
    status.classList.add('success');
    status.textContent = incomplete
      ? `Saved as a draft and exported to outputs/${payload.format}. Review the validation table; the strategy and winning conditions are below.`
      : `Saved and exported to outputs/${payload.format}. Strategy and winning conditions are below.`;
    state.savedDecks = await fetch('/api/decks').then(r => r.json()); renderSavedDecks();
  } catch (error) { status.classList.add('error'); status.textContent = error.message; }
}
$('#deck-card-list').addEventListener('click', event => { const button = event.target.closest('[data-add]'); if (button) addToDeck(button.dataset.card, button.dataset.add); });
$('.deck-list-panel').addEventListener('click', event => { const add = event.target.closest('[data-add]'); const remove = event.target.closest('[data-remove]'); if (add) addToDeck(add.dataset.card, add.dataset.add); if (remove) removeFromDeck(remove.dataset.card, remove.dataset.remove); });
$('#decks-view').addEventListener('click', event => {
  const preview = event.target.closest('[data-preview-index]');
  if (preview) openCard(Number(preview.dataset.previewIndex));
});
$('#dialog-content').addEventListener('change', event => {
  const toggle = event.target.closest('#scanned-commander-toggle');
  if (!toggle) return;
  chooseScannedCommander(toggle.checked ? toggle.dataset.commanderCard : '');
});
$('#deck-format-analysis').addEventListener('click', event => {
  const row = event.target.closest('[data-legality-format]');
  if (row) showFormatLegality(row.dataset.legalityFormat, row.dataset.legalityLabel);
});
const legalityDialog = $('#legality-dialog');
$('#close-legality').addEventListener('click', () => legalityDialog.close());
$('#close-legality-footer').addEventListener('click', () => legalityDialog.close());
legalityDialog.addEventListener('click', event => { if (event.target === legalityDialog) legalityDialog.close(); });
document.querySelectorAll('[data-deck-view]').forEach(button => button.addEventListener('click', () => { state.deckView = button.dataset.deckView; document.querySelectorAll('[data-deck-view]').forEach(item => { const active = item === button; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); }); renderDeckBuilder(); }));
$('#deck-search').addEventListener('input', renderDeckBuilder); $('#deck-format').addEventListener('change', onDeckFormatChange); $('#scanned-commander').addEventListener('change', event => chooseScannedCommander(event.target.value)); $('#deck-name').addEventListener('input', clearDeckValidation); $('#new-deck').addEventListener('click', newDeck); $('#create-deck').addEventListener('click', createNewDeck); $('#validate-deck').addEventListener('click', validateDeck); $('#save-deck').addEventListener('click', saveDeck);

const simulationDialog = $('#simulation-dialog');
const handDialog = $('#hand-dialog');
const battleDialog = $('#battle-dialog');

function closeHandTester() {
  if (handDialog.open) handDialog.close();
  state.handTestDeck = null;
  state.handTestState = null;
}
function openHandTester() {
  const deck = state.deckInfo;
  if (!deck || deck.format === 'scanned') return;
  state.handTestDeck = deck;
  state.handTestState = window.MtgHandTester.newState(deck);
  $('#hand-deck-label').textContent = `${deck.name} · ${deck.format[0].toUpperCase()}${deck.format.slice(1)}`;
  renderHandTester();
  handDialog.showModal();
}
function renderHandTester() {
  const handState = state.handTestState;
  if (!handState) return;
  const bottoming = handState.phase === 'bottoming';
  const selected = new Set(handState.bottomSelection);
  $('#hand-cards').innerHTML = handState.hand.map(copy => {
    const card = state.all.find(item => item.name === copy.name);
    const art = card?.imageUrl
      ? `<img src="${escapeHtml(card.imageUrl)}" alt="${escapeHtml(copy.name)}" loading="lazy">`
      : `<span class="hand-art-fallback">${escapeHtml(copy.name)}</span>`;
    const preview = card && Number.isInteger(card.index)
      ? `<button class="hand-card-art" type="button" data-preview-index="${card.index}" aria-label="View ${escapeHtml(copy.name)}">${art}</button>`
      : `<div class="hand-card-art">${art}</div>`;
    const isSelected = selected.has(copy.id);
    return `<article class="hand-card ${isSelected ? 'bottom-selected' : ''}">${preview}<span class="hand-card-name" title="${escapeHtml(copy.name)}">${escapeHtml(copy.name)}</span>${bottoming ? `<button class="hand-bottom-toggle" type="button" data-hand-bottom="${escapeHtml(copy.id)}" aria-pressed="${isSelected}">${isSelected ? 'Selected to bottom' : 'Select to bottom'}</button>` : ''}</article>`;
  }).join('') || '<p class="muted">This deck has no cards in its main section.</p>';

  if (bottoming) {
    const count = handState.mulligans;
    $('#hand-status').textContent = `Choose exactly ${count} card${count === 1 ? '' : 's'} to put on the bottom. ${handState.bottomSelection.length} of ${count} selected.`;
  } else if (handState.phase === 'opening') {
    $('#hand-status').textContent = `Opening hand: ${handState.hand.length} card${handState.hand.length === 1 ? '' : 's'} · ${handState.mulligans} mulligan${handState.mulligans === 1 ? '' : 's'} taken. Keep or mulligan.`;
  } else {
    $('#hand-status').textContent = `Hand kept · ${handState.library.length} cards in library · ${handState.drawn} extra card${handState.drawn === 1 ? '' : 's'} drawn.`;
  }
  $('#hand-mulligan').hidden = handState.phase !== 'opening';
  $('#hand-keep').hidden = handState.phase !== 'opening';
  $('#hand-confirm-bottom').hidden = !bottoming;
  $('#hand-confirm-bottom').disabled = bottoming && handState.bottomSelection.length !== handState.mulligans;
  $('#hand-draw').hidden = handState.phase !== 'ready';
  $('#hand-draw').disabled = handState.phase === 'ready' && handState.library.length === 0;
}
function withHandState(action) {
  try {
    action(state.handTestState);
    renderHandTester();
  } catch (error) {
    $('#hand-status').textContent = error.message;
  }
}
$('#test-hand').addEventListener('click', openHandTester);
$('#close-hand').addEventListener('click', closeHandTester);
$('#hand-dialog').addEventListener('close', () => { state.handTestDeck = null; state.handTestState = null; });
$('#hand-dialog').addEventListener('click', event => {
  const preview = event.target.closest('[data-preview-index]');
  const bottom = event.target.closest('[data-hand-bottom]');
  if (preview) openCard(Number(preview.dataset.previewIndex));
  else if (bottom) withHandState(handState => window.MtgHandTester.toggleBottom(handState, bottom.dataset.handBottom));
});
$('#hand-mulligan').addEventListener('click', () => withHandState(handState => window.MtgHandTester.mulligan(handState)));
$('#hand-keep').addEventListener('click', () => withHandState(handState => window.MtgHandTester.keep(handState)));
$('#hand-confirm-bottom').addEventListener('click', () => withHandState(handState => window.MtgHandTester.confirmBottom(handState)));
$('#hand-draw').addEventListener('click', () => withHandState(handState => window.MtgHandTester.draw(handState)));
$('#hand-reset').addEventListener('click', () => {
  if (state.handTestDeck) state.handTestState = window.MtgHandTester.reset(state.handTestState, state.handTestDeck);
  renderHandTester();
});

function updateBattleButton() {
  const availableDecks = state.savedDecks.filter(deck => ['pauper', 'standard', 'legacy'].includes(deck.format));
  $('#battle-decks').disabled = !state.simulationAvailable || availableDecks.length < 2;
  $('#battle-decks').title = !state.simulationAvailable
    ? 'Forge runtime or installation is unavailable on the server.'
    : availableDecks.length < 2 ? 'Save at least two Pauper or Standard decks to start a battle.' : '';
}
function updateBattleStart() {
  const format = $('#battle-format').value;
  const choices = state.savedDecks.filter(deck => deck.format === format);
  const deckA = $('#battle-deck-a').value;
  const deckB = $('#battle-deck-b').value;
  const games = Number($('#battle-games').value);
  $('#start-battle').disabled = !state.simulationAvailable || choices.length < 2 || !deckA || !deckB ||
    deckA === deckB || !Number.isInteger(games) || games < 1 || games > 10;
}
function setBattlePending(pending) {
  $('#battle-format').disabled = pending;
  $('#battle-deck-a').disabled = pending;
  $('#battle-deck-b').disabled = pending;
  $('#battle-games').disabled = pending;
}
function forgeStatisticsMarkup(job) {
  const summary = window.MtgSimulationStats?.summarize(job);
  if (!summary || !summary.completed || !summary.players.length) return '';
  const players = summary.players.map(player => {
    const rate = player.winRate === null ? '—' : `${player.winRate.toFixed(1)}%`;
    return `<article class="forge-stat-card"><h4>${escapeHtml(player.name)}</h4><strong class="forge-stat-record">${player.wins}–${player.losses}${player.draws ? `–${player.draws}` : ''}</strong><span>W–L${player.draws ? '–D' : ''}</span><dl><div><dt>Wins</dt><dd>${player.wins}</dd></div><div><dt>Losses</dt><dd>${player.losses}</dd></div>${player.draws ? `<div><dt>Draws</dt><dd>${player.draws}</dd></div>` : ''}<div><dt>Win rate</dt><dd>${rate}</dd></div></dl></article>`;
  }).join('');
  const unresolved = summary.unresolved
    ? `<p class="forge-stats-note">${summary.unresolved} completed game${summary.unresolved === 1 ? '' : 's'} had no recognized winner and are excluded from losses.</p>` : '';
  return `<section class="forge-statistics" aria-label="Forge game statistics"><h3>Game statistics</h3><p class="forge-stats-summary">${summary.completed} completed of ${summary.attempted} attempted</p><div class="forge-stat-grid">${players}</div>${unresolved}</section>`;
}
function populateBattleDecks() {
  const choices = state.savedDecks.filter(deck => deck.format === $('#battle-format').value);
  const fill = (select, preferred, excluded = '') => {
    select.innerHTML = '<option value="">Choose a saved deck</option>' + choices
      .filter(deck => deck.name !== excluded)
      .map(deck => `<option value="${escapeHtml(deck.name)}">${escapeHtml(deck.name)}</option>`).join('');
    if (choices.some(deck => deck.name === preferred && deck.name !== excluded)) select.value = preferred;
    else select.value = choices.find(deck => deck.name !== excluded)?.name || '';
  };
  const oldA = $('#battle-deck-a').value;
  const oldB = $('#battle-deck-b').value;
  fill($('#battle-deck-a'), oldA);
  fill($('#battle-deck-b'), oldB, $('#battle-deck-a').value);
  if (!$('#battle-deck-b').value) fill($('#battle-deck-b'), '', $('#battle-deck-a').value);
  updateBattleStart();
}
function openBattleDialog() {
  if (!state.simulationAvailable) return;
  setBattlePending(false);
  const currentFormat = ['pauper', 'standard', 'legacy'].includes(state.deckInfo?.format) ? state.deckInfo.format : '';
  const defaultFormat = currentFormat || ['standard', 'pauper', 'legacy'].find(format =>
    state.savedDecks.filter(deck => deck.format === format).length > 1) || 'pauper';
  $('#battle-format').value = defaultFormat;
  $('#battle-games').value = '10';
  $('#battle-status').textContent = '';
  $('#battle-results').innerHTML = '';
  $('#start-battle').textContent = 'Start battle';
  populateBattleDecks();
  if (currentFormat && state.savedDecks.some(deck => deck.format === currentFormat && deck.name === state.deckInfo.name)) {
    $('#battle-deck-a').value = state.deckInfo.name;
    populateBattleDecks();
  }
  battleDialog.showModal();
}
function showBattleJob(job) {
  const complete = ['completed', 'partial', 'failed', 'timeout', 'interrupted', 'unsupported'].includes(job.status);
  const status = $('#battle-status');
  status.className = `simulation-status ${complete && job.status !== 'completed' ? 'error' : complete ? 'success' : ''}`;
  if (job.status === 'queued' || job.status === 'running') {
    setBattlePending(true);
    status.textContent = job.status === 'queued' ? 'Battle queued…' : `Forge is running (${job.completed_games || 0}/${job.games_requested} games reported)…`;
    $('#start-battle').disabled = true;
    $('#start-battle').textContent = 'Running…';
    return false;
  }
  setBattlePending(false);
  $('#start-battle').textContent = 'Run again';
  status.textContent = job.status === 'completed'
    ? `Completed ${job.completed_games} of ${job.games_requested} games. Forge results are preliminary AI evidence.`
    : `${job.message || `Battle ${job.status}.`} Completed ${job.completed_games || 0} of ${job.games_requested} games.`;
  const lines = (job.game_results || []).slice(0, 20);
  $('#battle-results').innerHTML = `${forgeStatisticsMarkup(job)}${lines.length ? `<h3>Forge result lines</h3><ul>${lines.map(line => `<li><code>${escapeHtml(line)}</code></li>`).join('')}</ul>` : ''}<p class="muted">Evidence saved under <code>outputs/${escapeHtml(job.output_directory || '')}</code>${job.stdout_log ? ` · Logs: <code>${escapeHtml(job.stdout_log)}</code> and <code>${escapeHtml(job.stderr_log)}</code>` : ''}</p>`;
  updateBattleStart();
  return true;
}
async function startBattle(event) {
  event.preventDefault();
  updateBattleStart();
  if ($('#start-battle').disabled) return;
  const format = $('#battle-format').value;
  const body = {
    mode: 'battle', format, deckA: $('#battle-deck-a').value,
    deckB: $('#battle-deck-b').value, games: Number($('#battle-games').value),
  };
  const status = $('#battle-status');
  status.className = 'simulation-status';
  status.textContent = 'Starting Forge battle…';
  $('#start-battle').disabled = true;
  setBattlePending(true);
  try {
    const response = await fetch('/api/simulations', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    let job = await response.json();
    if (!response.ok) throw new Error(job.error || `Server returned ${response.status}`);
    showBattleJob(job);
    while (['queued', 'running'].includes(job.status)) {
      await new Promise(resolve => setTimeout(resolve, 2500));
      const result = await fetch(`/api/simulations/${encodeURIComponent(job.id)}`, {cache: 'no-store'});
      job = await result.json();
      if (!result.ok) throw new Error(job.error || 'Could not read battle status.');
      showBattleJob(job);
    }
  } catch (error) {
    setBattlePending(false);
    status.className = 'simulation-status error';
    status.textContent = error.message;
    updateBattleStart();
    $('#start-battle').textContent = 'Start battle';
  }
}
$('#battle-decks').addEventListener('click', openBattleDialog);
$('#battle-format').addEventListener('change', populateBattleDecks);
$('#battle-deck-a').addEventListener('change', () => { populateBattleDecks(); });
$('#battle-deck-b').addEventListener('change', updateBattleStart);
$('#battle-games').addEventListener('input', updateBattleStart);
$('#battle-form').addEventListener('submit', startBattle);
$('#close-battle').addEventListener('click', () => battleDialog.close());
$('#cancel-battle').addEventListener('click', () => battleDialog.close());

function updateSimulationStart() {
  const required = Number(simulationDialog.dataset.required || 0);
  const selected = $('#simulation-opponents').querySelectorAll('input[type="checkbox"]:checked').length;
  $('#start-simulation').disabled = !state.simulationAvailable || selected !== required;
}
function openSimulationDialog() {
  const deck = state.deckInfo;
  if (!deck || deck.format === 'scanned') return;
  const formatConfig = state.simulationFormats?.[deck.format];
  const required = Number(formatConfig?.opponents || 0);
  simulationDialog.dataset.required = String(required);
  $('#simulation-games').value = '10';
  $('#simulation-status').textContent = '';
  $('#simulation-results').innerHTML = '';
  $('#start-simulation').textContent = 'Start simulation';
  const choices = state.savedDecks.filter(item => item.format === deck.format && item.name !== deck.name);
  $('#simulation-help').textContent = deck.format === 'commander'
    ? `Forge will play a four-player Commander game with ${required} other saved Commander decks. Choose exactly ${required} opponents. Results are preliminary AI evidence.`
    : `Forge will play 1v1 ${deck.format[0].toUpperCase() + deck.format.slice(1)} games. Choose one saved ${deck.format} opponent. Forge does not enforce that format’s legality; decks are checked by the app first.`;
  const rows = choices.map(item => `<label class="simulation-opponent"><input type="checkbox" value="${escapeHtml(item.name)}"><span><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.archetype || item.format)}</small></span></label>`).join('');
  const shortage = choices.length < required ? `<p class="availability-warning">This format needs ${required} saved opponent deck${required === 1 ? '' : 's'}; only ${choices.length} are available.</p>` : '';
  $('#simulation-opponents').innerHTML = choices.length ? `${shortage}<div class="simulation-opponent-list">${rows}</div>` : `${shortage}<p class="muted">No saved opponent decks in this format.</p>`;
  $('#simulation-opponents').querySelectorAll('input[type="checkbox"]').forEach(box => box.addEventListener('change', () => {
    const checked = [...$('#simulation-opponents').querySelectorAll('input[type="checkbox"]:checked')];
    if (checked.length > required) box.checked = false;
    updateSimulationStart();
  }));
  updateSimulationStart();
  simulationDialog.showModal();
}
function showSimulationJob(job) {
  const complete = ['completed','partial','failed','timeout','interrupted','unsupported'].includes(job.status);
  $('#simulation-status').className = `simulation-status ${complete && job.status !== 'completed' ? 'error' : complete ? 'success' : ''}`;
  if (job.status === 'queued' || job.status === 'running') {
    $('#simulation-status').textContent = job.status === 'queued' ? 'Simulation queued…' : `Forge is running (${job.completed_games || 0}/${job.games_requested} games reported)…`;
    $('#start-simulation').disabled = true; $('#start-simulation').textContent = 'Running…';
    return false;
  }
  $('#start-simulation').disabled = false; $('#start-simulation').textContent = 'Run again';
  $('#simulation-status').textContent = job.status === 'completed'
    ? `Completed ${job.completed_games} of ${job.games_requested} requested games. Forge results are preliminary AI evidence.`
    : `${job.message || `Simulation ${job.status}.`} Completed ${job.completed_games || 0} of ${job.games_requested} games.`;
  const results = (job.game_results || []).slice(0, 20);
  $('#simulation-results').innerHTML = `${forgeStatisticsMarkup(job)}${results.length ? `<h3>Forge result lines</h3><ul>${results.map(line => `<li><code>${escapeHtml(line)}</code></li>`).join('')}</ul>` : ''}<p class="muted">Evidence saved under <code>outputs/${escapeHtml(job.output_directory || '')}</code>${job.stdout_log ? ` · Logs: <code>${escapeHtml(job.stdout_log)}</code> and <code>${escapeHtml(job.stderr_log)}</code>` : ''}</p>`;
  return true;
}
async function startSimulation(event) {
  event.preventDefault();
  const deck = state.deckInfo; if (!deck) return;
  const checked = [...$('#simulation-opponents').querySelectorAll('input[type="checkbox"]:checked')];
  const opponents = checked.map(item => item.value);
  const status = $('#simulation-status'); status.className = 'simulation-status'; status.textContent = 'Starting Forge…';
  $('#start-simulation').disabled = true;
  try {
    const response = await fetch('/api/simulations', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deckName:deck.name,format:deck.format,opponents,games:Number($('#simulation-games').value)})});
    let job = await response.json();
    if (!response.ok) throw new Error(job.error || `Server returned ${response.status}`);
    showSimulationJob(job);
    while (['queued','running'].includes(job.status)) {
      await new Promise(resolve => setTimeout(resolve, 2500));
      const result = await fetch(`/api/simulations/${encodeURIComponent(job.id)}`, {cache:'no-store'});
      job = await result.json();
      if (!result.ok) throw new Error(job.error || 'Could not read simulation status.');
      showSimulationJob(job);
    }
  } catch (error) {
    status.className = 'simulation-status error'; status.textContent = error.message;
    $('#start-simulation').disabled = false; $('#start-simulation').textContent = 'Start simulation';
  }
}
$('#simulate-deck').addEventListener('click', openSimulationDialog);
$('#close-simulation').addEventListener('click', () => simulationDialog.close());
$('#cancel-simulation').addEventListener('click', () => simulationDialog.close());
$('#simulation-form').addEventListener('submit', startSimulation);
$('#saved-decks').addEventListener('click', event => { const remove = event.target.closest('[data-delete-deck]'); const button = event.target.closest('[data-saved]'); if (remove) deleteDeck(Number(remove.dataset.deleteDeck)); else if (button) loadSavedDeck(state.savedDecks[Number(button.dataset.saved)]); });
function renderChat() {
  const log = $('#chat-messages');
  const draftStatus = deck => !deck.validation ? 'Draft'
    : !deck.validation.valid ? 'Needs format review'
      : deck.validation.collection_complete === false ? 'Format passed · quantity proposal'
        : 'Validated';
  log.innerHTML = state.chatMessages.map((item, index) => `<article class="chat-message ${item.role === 'user' ? 'user' : 'assistant'}"><strong>${item.role === 'user' ? 'You' : 'Codex'}</strong><div class="chat-message-body">${item.role === 'assistant' ? renderMarkdown(item.content) : `<p>${escapeHtml(item.content).replace(/\n/g, '<br>')}</p>`}</div>${item.deckDraft ? `<p class="chat-draft-summary">${draftStatus(item.deckDraft)} ${escapeHtml(item.deckDraft.format)} draft · ${Object.values(item.deckDraft.main || {}).reduce((sum, count) => sum + Number(count || 0), 0)} main deck cards</p><button class="chat-save-deck" type="button" data-chat-save-deck="${index}" ${item.savingDeck ? 'disabled' : ''}>${item.savingDeck ? 'Saving…' : 'Save deck to Decks'}</button>${item.saveError ? `<p class="chat-save-error" role="alert">${escapeHtml(item.saveError)}</p>` : ''}` : ''}${item.savedDeck ? `<button class="chat-open-deck" type="button" data-chat-open-deck="${escapeHtml(item.savedDeck.name)}" data-chat-open-format="${escapeHtml(item.savedDeck.format)}">Open saved deck in Decks</button>` : ''}</article>`).join('');
  log.scrollTop = log.scrollHeight;
}
async function saveChatDeck(index) {
  const item = state.chatMessages[index];
  if (!item?.deckDraft || item.savingDeck) return;
  item.savingDeck = true; item.saveError = '';
  renderChat();
  try {
    const response = await fetch('/api/decks', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(item.deckDraft)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The deck could not be saved.');
    item.savedDeck = result.deck;
    item.deckDraft = null;
    item.content += '\n\nSaved to Decks.';
    state.savedDecks = [...state.savedDecks.filter(deck => deck.name !== result.deck.name || deck.format !== result.deck.format), result.deck];
    state.deckInfo = result.deck;
    state.reservationDeck = {name: result.deck.name, format: result.deck.format};
    state.deck = {main:{...result.deck.main},sideboard:{...(result.deck.sideboard || {})},commanders:{...(result.deck.commanders || {})}};
    $('#deck-name').value = result.deck.name;
    $('#deck-format').value = result.deck.format;
    $('#chat-deck').value = result.deck.name;
    try { await reloadCollection(); } catch { /* The saved deck remains available even if collection refresh fails. */ }
    try { state.savedDecks = await fetch('/api/decks', {cache: 'no-store'}).then(r => r.json()); } catch { /* Keep the successful save response in local state. */ }
    renderSavedDecks();
    renderDeckBuilder();
  } catch (error) {
    item.saveError = item.savedDeck ? `Deck saved, but the page could not fully refresh: ${error.message}` : error.message;
  } finally {
    item.savingDeck = false;
    renderChat();
  }
}
$('#chat-messages').addEventListener('click', event => {
  const save = event.target.closest('[data-chat-save-deck]');
  if (save) { saveChatDeck(Number(save.dataset.chatSaveDeck)); return; }
  const button = event.target.closest('[data-chat-open-deck]');
  if (!button) return;
  const deck = state.savedDecks.find(item => item.name === button.dataset.chatOpenDeck && item.format === button.dataset.chatOpenFormat);
  if (!deck) return;
  loadSavedDeck(deck);
  $('[data-tab="decks"]').click();
});
function inlineMarkdown(value) {
  const protectedHtml = [];
  let text = escapeHtml(value).replace(/`([^`]+)`/g, (_, code) => {
    const index = protectedHtml.push(`<code>${code}</code>`) - 1;
    return `\u0000${index}\u0000`;
  });
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/gi, (match, label, href) => {
    const url = href.replace(/&amp;/g, '&');
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) return label;
      return `<a href="${escapeHtml(parsed.href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    } catch { return label; }
  });
  text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_\n]+)__/g, '<strong>$1</strong>')
    .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/(^|[^_])_([^_\n]+)_/g, '$1<em>$2</em>');
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => protectedHtml[Number(index)] || '');
}
function renderMarkdown(markdown) {
  const lines = String(markdown ?? '').replace(/\r\n?/g, '\n').split('\n');
  const blocks = []; let paragraph = []; let list = null;
  const flushParagraph = () => { if (paragraph.length) { blocks.push(`<p>${paragraph.map(inlineMarkdown).join('<br>')}</p>`); paragraph = []; } };
  const flushList = () => { if (list) { blocks.push(`<${list.type}>${list.items.map(item => `<li>${inlineMarkdown(item)}</li>`).join('')}</${list.type}>`); list = null; } };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]; const trimmed = line.trim();
    if (!trimmed) { flushParagraph(); flushList(); continue; }
    if (/^```/.test(trimmed)) {
      flushParagraph(); flushList(); const code = []; const language = trimmed.slice(3).trim();
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      blocks.push(`<pre><code${language ? ` class="language-${escapeHtml(language)}"` : ''}>${escapeHtml(code.join('\n'))}</code></pre>`); continue;
    }
    if (i + 1 < lines.length && trimmed.includes('|') && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      flushParagraph(); flushList();
      const cells = value => value.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
      const headings = cells(trimmed); i += 2; const rows = [];
      while (i < lines.length && lines[i].trim().includes('|')) { rows.push(cells(lines[i])); i += 1; }
      i -= 1;
      blocks.push(`<div class="chat-table-wrap"><table><thead><tr>${headings.map(cell => `<th>${inlineMarkdown(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${headings.map((_, index) => `<td>${inlineMarkdown(row[index] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`); continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { flushParagraph(); flushList(); const level = heading[1].length; blocks.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`); continue; }
    if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { flushParagraph(); flushList(); blocks.push('<hr>'); continue; }
    if (/^>\s?/.test(trimmed)) { flushParagraph(); flushList(); const quote = []; while (i < lines.length && /^\s*>/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, '')); i -= 1; blocks.push(`<blockquote>${quote.map(inlineMarkdown).join('<br>')}</blockquote>`); continue; }
    const unordered = trimmed.match(/^[-+*]\s+(.+)$/); const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) { flushParagraph(); const type = ordered ? 'ol' : 'ul'; if (list && list.type !== type) flushList(); if (!list) list = {type, items: []}; list.items.push((unordered || ordered)[1]); continue; }
    flushList(); paragraph.push(trimmed);
  }
  flushParagraph(); flushList();
  return blocks.join('');
}
async function loadChatModels() {
  const response = await fetch('/api/chat/models', {cache: 'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Could not load Codex models.');
  const select = $('#chat-model'); const saved = localStorage.getItem('mtg-chat-model');
  const models = data.models || [];
  const preferred = models.some(model => model.id === DEFAULT_CHAT_MODEL)
    ? DEFAULT_CHAT_MODEL : (models.find(model => model.isDefault)?.id || '');
  select.innerHTML = '<option value="">Codex default</option>' + models.map(model => `<option value="${escapeHtml(model.id)}">${escapeHtml(model.name)}${model.isDefault ? ' · default' : ''}</option>`).join('');
  const selected = saved === null || (saved && !models.some(model => model.id === saved)) ? preferred : saved;
  select.value = selected;
  if (saved !== selected) localStorage.setItem('mtg-chat-model', selected);
  state.chatModelsLoaded = true;
}
$('#chat-model').addEventListener('change', () => localStorage.setItem('mtg-chat-model', $('#chat-model').value));
async function refreshChatStatus() {
  try {
    const response = await fetch('/api/chat/status', {cache: 'no-store'});
    const result = await response.json();
    state.chatAvailable = Boolean(result.available);
    $('#chat-input').disabled = !state.chatAvailable;
    $('#chat-send').disabled = !state.chatAvailable;
    $('#chat-model').disabled = !state.chatAvailable;
    $('#chat-connection').textContent = state.chatAvailable ? 'Connected' : result.message;
    $('#chat-connection').classList.toggle('connected', state.chatAvailable);
    if (state.chatAvailable && !state.chatModelsLoaded) {
      try { await loadChatModels(); }
      catch { $('#chat-model').innerHTML = '<option value="">Codex default (model list unavailable)</option>'; state.chatModelsLoaded = true; }
    }
    if (!state.chatAvailable) $('#chat-messages').innerHTML = `<p class="muted">${escapeHtml(result.message)} The chat is prepared but needs the server-side Codex app-server and ChatGPT-plan credentials.</p>`;
  } catch {
    state.chatAvailable = false;
    $('#chat-input').disabled = true;
    $('#chat-send').disabled = true;
    $('#chat-model').disabled = true;
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
    const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message, history: state.chatMessages.slice(0, -1), deckName: $('#chat-deck').value, model: $('#chat-model').value})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Server returned ${response.status}`);
    state.chatMessages.push({role: 'assistant', content: result.answer, deckDraft: result.deckDraft || null}); renderChat();
  } catch (error) {
    state.chatMessages.push({role: 'assistant', content: `I couldn't complete that request: ${error.message}`}); renderChat();
  } finally { send.disabled = false; send.textContent = 'Send'; input.focus(); }
});
Promise.all([fetch('/api/formats').then(r => r.json()), fetch('/api/decks').then(r => r.json()), fetch('/api/simulations/config').then(r => r.json())]).then(([formats, decks, simulations]) => { state.formats = formats; state.savedDecks = decks; state.simulationAvailable = Boolean(simulations.available); state.simulationFormats = simulations.formats || {}; renderSavedDecks(); renderDeckBuilder(); if (!state.simulationAvailable) $('#simulate-deck').title = 'Forge runtime or installation is unavailable on the server.'; }).catch(error => { $('#deck-status').textContent = `Deck builder unavailable: ${error.message}`; });
