# Unowned Cards in Deck Proposals

## Intent

Show cards included in a saved purchase proposal even when they are absent from the owned collection. Use the local Scryfall Oracle cache for card art, Oracle text, and format legality; never add proposal cards to collection quantities or reserve them.

## Approaches

1. Add proposal cards to the collection CSV as zero-copy rows. Rejected because the CSV is the owned inventory source and this blurs inventory semantics.
2. Keep a separate per-deck Scryfall card catalog (chosen). The deck view can display complete Scryfall details and ownership status while collection filtering and quantities remain unchanged.

## Behavior

- Resolve unknown deck names from the local Scryfall bulk cache and attach only the deck's referenced card records.
- Validate legality against owned cards plus those Scryfall records; validate ownership separately against the actual inventory and deck reservations.
- Display card images and Oracle text. Show a `NOT OWNED` ribbon for zero copies and a `SHORT n` ribbon when some copies are owned but insufficient.
- Export a separate `<deck>-unowned.csv` with needed/owned/reserved/short copies, card type, mana information, Oracle text, and Scryfall link.
- Keep the feature dependency-free and preserve all source collection data.

## Constraints

Use the cached official Scryfall Oracle Cards bulk file, do not download on normal app requests, and do not claim ownership or simulate a proposal as if its missing cards were available.
