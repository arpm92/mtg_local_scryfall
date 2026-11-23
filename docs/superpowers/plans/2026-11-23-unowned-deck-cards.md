# Unowned Deck Cards Implementation Plan

> **For agentic workers:** Execute natively in this session. Keep each test small and use `.venv`.

**Goal:** Display Scryfall details and explicit ownership gaps for saved purchase proposals, with a separate missing-cards CSV.

**Architecture:** Store per-deck Scryfall metadata resolved from the local bulk cache. Use it for deck legality and presentation while using collection rows alone for owned/reserved counts. Render missing cards with a corner status ribbon and Oracle details.

**Tech Stack:** Python standard library, existing `unittest` suite, vanilla JavaScript/CSS, local Scryfall JSONL cache.

**Spec:** `docs/superpowers/specs/2026-11-23-unowned-deck-cards-design.md`

## Review Focus

- Unknown but legal proposal cards: resolvable from bulk and grouped under the chosen format.
- Quantity shortages: visible without reserving or fabricating owned copies.
- Scryfall images/text: rendered with HTML escaping and a safe text fallback.
- CSV sections: combine main and sideboard quantities, exclude sufficient basics, preserve UTF-8 text.

### Task 1: Resolve and validate proposal card records

- [ ] Add failing tests for Scryfall-known unowned cards passing format legality, while ownership gaps remain visible.
- [ ] Add cache lookup and deck catalog handling; keep inventory quantities unchanged.
- [ ] Verify targeted backend tests.

### Task 2: Render unowned cards and export the missing list

- [ ] Add failing tests for corner ribbons, Oracle details, and the unowned CSV contents.
- [ ] Update deck cards to use Scryfall catalog fallbacks, show `NOT OWNED` / `SHORT n`, and expose Oracle text.
- [ ] Generate the proposal's UTF-8 CSV in the saved deck bundle.
- [ ] Run the full test suite and validate 60/15 counts and ownership figures.

### Task 3: Update the server copy

- [ ] Push/deploy the implementation through the existing Portainer stack after local verification.
- [ ] Re-save the Pauper proposal through the app, then verify it appears under Pauper with Scryfall details and its missing-cards CSV.
