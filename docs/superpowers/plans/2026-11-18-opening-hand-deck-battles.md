# Opening Hand Tester and Deck Battles Implementation Plan

> **For agentic workers:** Use the native implementation path and complete each task in order. Steps use checkbox syntax for tracking.

**Goal:** Add local opening-hand/London-mulligan testing and direct 1v1 Pauper/Standard Forge battles to the existing Decks interface.

**Architecture:** Keep test hands entirely in the browser using a small deterministic, testable card-zone helper. Extend the existing simulations endpoint to resolve two saved decks and pass them through the current format validator and Forge background service; retain the current four-player Commander flow.

**Tech Stack:** Python standard library, existing Forge runner, HTML/CSS/vanilla JavaScript, Node built-in `assert` for the hand-state helper.

**Spec:** `docs/superpowers/specs/2026-11-18-opening-hand-and-deck-battles-design.md`

## Global Constraints

- No new runtime dependencies.
- Hand testing never saves or edits a deck.
- Pauper and Standard battles contain exactly two same-format saved decks.
- Commander remains a four-player game with three different saved opponents.
- Keep one Forge job at a time and the current 1–10 game bound.

## Review Focus

- Selecting a missing, scanned, invalid, or mismatched-format deck must not start a job; test endpoint resolution and validator rejection.
- Duplicate card quantities must remain independent shuffle entries; test multiset construction and draws.
- London mulligan bottoms exactly one card per mulligan and does not include commanders in the library; test the helper's state transitions.
- Battle setup must not route Commander to two-player play; test the explicit format rejection and preserve existing 4-player selection behavior.
- A reset or new hand must restore the complete original list and clear mulligan/draw state; test reset and deck exhaustion behavior.

---

### Task 1: Add explicit saved-deck 1v1 battles to the simulation API

**Files:**
- Modify: `collection_web.py`
- Modify: `forge_simulation.py` only if a battle-specific method is useful; prefer reusing `start(deck_a, [deck_b], games)`.
- Test: `tests/test_collection_web.py`

**Interfaces:**
- Consumes: current `ForgeSimulationService.start(deck, opponents, games)` and `architect.validate_deck`.
- Produces: `POST /api/simulations` request `{mode:"battle", format, deckA, deckB, games}`; existing responses remain unchanged.

- [x] Add endpoint tests for valid same-format Pauper/Standard deck pairs returning a queued job, missing deck and mismatched format rejection, and Commander battle rejection.
- [x] Run the focused endpoint tests and confirm they fail before implementation.
- [x] Resolve both deck names only from `saved_decks()`, reject Commander and scanned formats, and pass `[deck_b]` to the existing service with deck A as primary.
- [x] Run the focused endpoint tests and confirm the current Commander simulation route still uses three opponents.

### Task 2: Implement the client-side hand and mulligan model

**Files:**
- Create: `web/hand_tester.js`
- Test: `tests/test_hand_tester.js`

**Interfaces:**
- `MtgHandTester.createLibrary(deck)` returns a shuffled array containing each main-deck copy; Commander cards are excluded.
- `MtgHandTester.newState(deck, random?)` returns `{library, hand, mulligans, phase, bottomSelection, drawn}` with an initial seven-card hand.
- `MtgHandTester.mulligan(state, random?)`, `keep(state)`, `toggleBottom(state, cardId)`, `confirmBottom(state)`, `draw(state)`, and `reset(state, deck, random?)` update that state while enforcing London-mulligan counts.
- The module is available as `window.MtgHandTester` and can be required by Node tests without dependencies.

- [x] Add Node built-in assertions for duplicate quantities, seven-card initial hands, commander exclusion, mulligan/redraw, exact bottom count, draw, reset, exhausted libraries, and preventing mulligans beyond a zero-card hand.
- [x] Run `node tests/test_hand_tester.js` and confirm the missing module fails.
- [x] Implement the pure helper, keeping card identity unique per copy so identical names can be selected separately.
- [x] Run the helper test file and confirm every assertion passes (6 tests).

### Task 3: Add the opening-hand dialog and deck toolbar action

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`

**Interfaces:**
- **Test hand** opens for a loaded saved, non-scanned deck.
- Render hand cards from existing collection card image/details data; clicking a card opens the existing card detail dialog.
- Controls are **Mulligan**, **Keep hand**, **Confirm bottom cards**, **Draw one**, and **Shuffle / reset**; status text explains required bottom count.

- [x] Add the dialog, hand area, actions, and accessible status region; include `hand_tester.js` before `app.js`.
- [x] Wire the loaded deck to helper state and render card faces, copy-aware selection, mulligan count, library count, and phase-appropriate controls.
- [x] Keep the opening-hand state ephemeral and clear it when switching decks or closing the dialog.
- [x] Apply responsive layout rules so full card faces remain visible without cropping on desktop and mobile widths.

### Task 4: Add a two-deck Pauper/Standard battle flow

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`
- Modify: `collection_web.py` (if integration changes are needed)

**Interfaces:**
- Add **Battle decks** action and dialog with Deck A, Deck B, game count, status, and results.
- Send `{mode:"battle", format, deckA, deckB, games}` to `POST /api/simulations`.
- Reuse existing job polling and render winner lines plus evidence/log paths.

- [x] Populate both selectors from same-format saved Pauper/Standard decks and prevent selecting the same deck twice.
- [x] Show the format and selected pair before starting; disable start when Forge is unavailable or fewer than two eligible decks exist.
- [x] Poll the returned job through `/api/simulations/{id}` and show queued, running, completed, partial, timeout, and failed states.
- [x] Leave **Run Forge simulation** as the separate four-player Commander flow and retain its current 1v1 use for selected-deck simulations if applicable.

### Task 5: Document the tools and run regression validation

**Files:**
- Modify: `README.md`
- Validate: `tests/test_collection_web.py`, `tests/test_hand_tester.js`, project test suite and syntax checks.

- [x] Document hand testing behavior, London mulligan handling, direct Pauper/Standard battles, Commander four-player behavior, and saved evidence paths.
- [x] Run focused Python and Node tests, then the full documented regression suite and syntax checks. Full suite remains at the same five unrelated Codex chat failures present in the pre-change baseline.
- [x] Run a Forge smoke battle with two saved, valid, same-format 60-card decks; one completed Standard game and saved logs.
- [x] Review the diff for accidental changes to saved deck files, collection data, generated outputs, and Commander simulation behavior.
