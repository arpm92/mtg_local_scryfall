(function exposeHandTester(root) {
  'use strict';

  const HAND_SIZE = 7;

  function shuffle(cards, random) {
    const result = cards.slice();
    for (let index = result.length - 1; index > 0; index -= 1) {
      const sample = Math.min(Math.max(Number(random()), 0), 0.9999999999999999);
      const target = Math.floor(sample * (index + 1));
      [result[index], result[target]] = [result[target], result[index]];
    }
    return result;
  }

  function createLibrary(deck, random = Math.random) {
    const library = [];
    let copyId = 0;
    Object.entries(deck?.main || {}).forEach(([name, quantity]) => {
      const count = Number(quantity);
      if (!Number.isInteger(count) || count <= 0) return;
      for (let copy = 0; copy < count; copy += 1) {
        copyId += 1;
        library.push({id: `copy-${copyId}`, name});
      }
    });
    return shuffle(library, random);
  }

  function takeOpeningHand(library) {
    return library.splice(Math.max(0, library.length - HAND_SIZE), HAND_SIZE);
  }

  function newState(deck, random = Math.random) {
    const library = createLibrary(deck, random);
    return {
      library,
      hand: takeOpeningHand(library),
      mulligans: 0,
      phase: 'opening',
      bottomSelection: [],
      drawn: 0,
    };
  }

  function mulligan(state, random = Math.random) {
    if (state.phase !== 'opening') throw new Error('Mulligan is only available before keeping a hand.');
    if (state.mulligans >= HAND_SIZE) throw new Error("Can't mulligan past a zero-card hand.");
    state.mulligans += 1;
    state.library = shuffle(state.library.concat(state.hand), random);
    state.hand = takeOpeningHand(state.library);
    state.bottomSelection = [];
    return state;
  }

  function keep(state) {
    if (state.phase !== 'opening') throw new Error('This hand is already kept.');
    state.phase = state.mulligans ? 'bottoming' : 'ready';
    return state;
  }

  function toggleBottom(state, cardId) {
    if (state.phase !== 'bottoming') throw new Error('Keep a hand before choosing cards to bottom.');
    if (!state.hand.some(card => card.id === cardId)) throw new Error('Choose a card from the current hand.');
    const selectedIndex = state.bottomSelection.indexOf(cardId);
    if (selectedIndex !== -1) {
      state.bottomSelection.splice(selectedIndex, 1);
      return state;
    }
    if (state.bottomSelection.length >= state.mulligans) {
      throw new Error(`Select only ${state.mulligans} card${state.mulligans === 1 ? '' : 's'} to bottom.`);
    }
    state.bottomSelection.push(cardId);
    return state;
  }

  function confirmBottom(state) {
    if (state.phase !== 'bottoming') throw new Error('There are no cards waiting to be bottomed.');
    if (state.bottomSelection.length !== state.mulligans) {
      throw new Error(`Select ${state.mulligans} card${state.mulligans === 1 ? '' : 's'} to bottom.`);
    }
    const selected = new Set(state.bottomSelection);
    const bottomed = state.hand.filter(card => selected.has(card.id));
    state.hand = state.hand.filter(card => !selected.has(card.id));
    state.library.unshift(...bottomed);
    state.bottomSelection = [];
    state.phase = 'ready';
    return state;
  }

  function draw(state) {
    if (state.phase !== 'ready') throw new Error('Keep the hand before drawing.');
    if (!state.library.length) return null;
    const card = state.library.pop();
    state.hand.push(card);
    state.drawn += 1;
    return card;
  }

  function reset(state, deck, random = Math.random) {
    return newState(deck, random);
  }

  const api = {HAND_SIZE, createLibrary, newState, mulligan, keep, toggleBottom, confirmBottom, draw, reset};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.MtgHandTester = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
