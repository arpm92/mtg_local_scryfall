'use strict';

const assert = require('node:assert/strict');
const {toText, toCsv, filename} = require('../web/deck_export.js');

function testTextExportKeepsCommanderMainAndSideboardSections() {
  const deck = {
    commanders: {'Esika, God of the Tree // The Prismatic Bridge': 1},
    main: {'Sol Ring': 1, 'Forest': 34, "Aunt May, Heroic Ally": 2},
    sideboard: {'Swords to Plowshares': 2},
  };
  assert.equal(toText(deck), [
    'Commander', '1 Esika, God of the Tree // The Prismatic Bridge', '',
    'Deck', '2 Aunt May, Heroic Ally', '34 Forest', '1 Sol Ring', '',
    'Sideboard', '2 Swords to Plowshares', '',
  ].join('\n'));
}

function testTextExportOmitsEmptyCommanderAndSideboard() {
  assert.equal(toText({main: {'Island': 60}, commanders: {}, sideboard: {}}),
    'Deck\n60 Island\n');
}

function testCsvExportPreservesSectionsCountsAndEscapesCardNames() {
  const csv = toCsv({
    commanders: {'The Doctor, the Last of the Time Lords': 1},
    main: {'Forest': 2, 'Blossom-Blessed Angel // Seed Suture': 3},
    sideboard: {'Take Up the Shield': 1},
  });
  assert.equal(csv, [
    'Section,Quantity,Card Name',
    'Commander,1,"The Doctor, the Last of the Time Lords"',
    'Deck,3,Blossom-Blessed Angel // Seed Suture',
    'Deck,2,Forest',
    'Sideboard,1,Take Up the Shield',
    '',
  ].join('\r\n'));
}

function testInvalidCountsAreNotExportedAndFilenameIsSafe() {
  assert.equal(toText({main: {'Forest': 0, 'Island': 'bad'}}), '');
  assert.equal(filename({name: 'Green / White: Deck!'}, 'txt'), 'green-white-deck.txt');
}

testTextExportKeepsCommanderMainAndSideboardSections();
testTextExportOmitsEmptyCommanderAndSideboard();
testCsvExportPreservesSectionsCountsAndEscapesCardNames();
testInvalidCountsAreNotExportedAndFilenameIsSafe();
process.stdout.write('Deck export tests passed.\n');
