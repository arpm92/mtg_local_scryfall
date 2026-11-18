const assert = require('node:assert/strict');
const handTester = require('../web/hand_tester.js');

function makeDeck() {
  return {
    main: { Forest: 4, "Llanowar Elves": 4, "Giant Growth": 4, "Forest Keeper": 48 },
    commanders: { "The Green Commander": 1 },
  };
}

function fixedRandom() {
  return () => 0.25;
}

function testInitialHandPreservesCopiesAndExcludesCommander() {
  const deck = makeDeck();
  const state = handTester.newState(deck, fixedRandom());
  const names = [...state.hand, ...state.library].map(card => card.name);
  assert.equal(state.hand.length, 7);
  assert.equal(state.library.length, 53);
  assert.equal(names.length, 60);
  assert.equal(names.filter(name => name === 'Forest').length, 4);
  assert.equal(names.includes('The Green Commander'), false);
  assert.equal(new Set([...state.hand, ...state.library].map(card => card.id)).size, 60);
}

function testLondonMulliganRequiresOneBottomPerMulligan() {
  const state = handTester.newState(makeDeck(), fixedRandom());
  handTester.mulligan(state, fixedRandom());
  assert.equal(state.mulligans, 1);
  assert.equal(state.hand.length, 7);
  handTester.keep(state);
  assert.equal(state.phase, 'bottoming');
  assert.throws(() => handTester.confirmBottom(state), /select 1 card/i);
  handTester.toggleBottom(state, state.hand[0].id);
  handTester.confirmBottom(state);
  assert.equal(state.phase, 'ready');
  assert.equal(state.hand.length, 6);
  assert.equal(state.library.length, 54);
  assert.equal(state.bottomSelection.length, 0);
}

function testMultipleMulligansRequireMatchingBottomCountAndDraw() {
  const state = handTester.newState(makeDeck(), fixedRandom());
  handTester.mulligan(state, fixedRandom());
  handTester.mulligan(state, fixedRandom());
  handTester.keep(state);
  assert.equal(state.mulligans, 2);
  handTester.toggleBottom(state, state.hand[0].id);
  handTester.toggleBottom(state, state.hand[1].id);
  handTester.confirmBottom(state);
  assert.equal(state.hand.length, 5);
  const drawn = handTester.draw(state);
  assert.ok(drawn);
  assert.equal(state.hand.length, 6);
  assert.equal(state.drawn, 1);
}

function testCannotMulliganPastASevenCardHand() {
  const state = handTester.newState(makeDeck(), fixedRandom());
  for (let count = 0; count < 7; count += 1) handTester.mulligan(state, fixedRandom());
  assert.equal(state.mulligans, 7);
  assert.throws(() => handTester.mulligan(state, fixedRandom()), /zero-card hand/i);
  assert.equal(state.mulligans, 7);
}

function testResetRestoresSevenCardHandAndFullLibrary() {
  const deck = makeDeck();
  const state = handTester.newState(deck, fixedRandom());
  handTester.keep(state);
  handTester.draw(state);
  const resetState = handTester.reset(state, deck, fixedRandom());
  assert.equal(resetState.hand.length, 7);
  assert.equal(resetState.library.length, 53);
  assert.equal(resetState.mulligans, 0);
  assert.equal(resetState.drawn, 0);
  assert.equal(resetState.phase, 'opening');
}

function testSmallDeckCanKeepAndStopsDrawingWhenLibraryIsEmpty() {
  const state = handTester.newState({ main: { Island: 2 } }, fixedRandom());
  assert.equal(state.hand.length, 2);
  handTester.keep(state);
  assert.equal(state.phase, 'ready');
  assert.equal(handTester.draw(state), null);
  assert.equal(state.hand.length, 2);
}

testInitialHandPreservesCopiesAndExcludesCommander();
testLondonMulliganRequiresOneBottomPerMulligan();
testMultipleMulligansRequireMatchingBottomCountAndDraw();
testCannotMulliganPastASevenCardHand();
testResetRestoresSevenCardHandAndFullLibrary();
testSmallDeckCanKeepAndStopsDrawingWhenLibraryIsEmpty();
process.stdout.write('6 hand tester tests passed.\n');
