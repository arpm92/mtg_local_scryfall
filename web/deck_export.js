(function (root) {
  'use strict';

  function entries(value) {
    return Object.entries(value || {})
      .map(([name, rawCount]) => [String(name).trim(), Number(rawCount)])
      .filter(([name, count]) => name && Number.isInteger(count) && count > 0)
      .sort(([a], [b]) => a.localeCompare(b));
  }

  function deckSections(deck) {
    const sections = [];
    if (Object.keys(deck.commanders || {}).length) sections.push(['Commander', entries(deck.commanders)]);
    sections.push(['Deck', entries(deck.main)]);
    if (Object.keys(deck.sideboard || {}).length) sections.push(['Sideboard', entries(deck.sideboard)]);
    return sections;
  }

  function toText(deck) {
    const sections = deckSections(deck).filter(([, cards]) => cards.length);
    return sections.length ? sections
      .map(([title, cards]) => `${title}\n${cards.map(([name, count]) => `${count} ${name}`).join('\n')}`)
      .join('\n\n') + '\n' : '';
  }

  function csvCell(value) {
    const text = String(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCsv(deck) {
    const rows = [['Section', 'Quantity', 'Card Name']];
    for (const [section, cards] of deckSections(deck)) {
      for (const [name, count] of cards) rows.push([section, count, name]);
    }
    return rows.map(row => row.map(csvCell).join(',')).join('\r\n') + '\r\n';
  }

  function filename(deck, extension) {
    const safe = String(deck.name || 'deck').normalize('NFKD')
      .replace(/[^\w -]/g, '').trim().replace(/[\s-]+/g, '-').toLowerCase() || 'deck';
    return `${safe}.${extension}`;
  }

  const api = {toText, toCsv, filename};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MtgDeckExport = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
