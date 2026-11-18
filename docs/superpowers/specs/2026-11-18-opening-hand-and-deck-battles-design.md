# Opening Hand Tester and Deck Battles Design

## Intent

Add two practical testing tools to the existing Decks area: a quick way to draw and inspect test hands from a saved list, and a direct Forge matchup between two saved decks. Reuse the collection app and its existing Forge service so the feature remains deployable with the current server stack.

## Goals

- Let a user select a saved deck, draw a seven-card sample, mulligan, choose London-mulligan bottom cards, and draw additional cards without editing or saving the deck.
- Show card faces in the hand tester using the app’s existing card image data and open-card details.
- Let a user choose two saved Pauper or Standard decks and run a validated 1v1 Forge battle with the existing game count, progress, results, and saved-log behavior.
- Keep Commander simulations as four-player free-for-all with the current three-opponent selection; do not describe a two-player Commander duel as multiplayer Commander evidence.
- Save Forge battle evidence beneath the existing per-format output directories.

## Non-goals and constraints

- No separate PowerShell dashboard or second Forge runner.
- No collection/deck mutation from hand testing.
- No deck-vs-deck Commander duel mode; Commander uses its existing four-player workflow.
- The current simulation worker remains single-job and bounded to 10 games.
- Validate both selected saved decks server-side and require matching formats.

## Approaches

1. **Use the current web app and Forge service (selected).** Implement local browser hand testing and a Pauper/Standard battle dialog that sends both selected decks through the existing validated Forge job flow. This preserves deployment and logs while keeping format rules clear.
2. **Port the old Forge Deck Lab dashboard.** It offers richer simulation reporting but is a separate PowerShell Commander application, so embedding it would duplicate interfaces and complicate the server deployment.

## Proposed interaction

In the Deck editor toolbar, add **Test hand** for a loaded saved deck and **Battle decks** for a saved Pauper or Standard deck. The hand tool opens with seven shuffled cards, controls for **Mulligan**, **Keep hand**, **Draw one**, and **Shuffle / reset**, and requires the user to select cards to bottom after keeping a London-mulligan hand. Commander cards remain outside the shuffled 99-card library. The battle dialog presents Deck A, Deck B, and game count; it displays the format and follows the existing queued/running/completed states and results.

Commander keeps **Run Forge simulation**, which requires three same-format opponents for four-player games. Scanned decks and invalid lists cannot be tested.

## Validation

Add backend tests for same-format 1v1 selection, illegal/missing decks, and Commander route separation. Add browser-side tests or deterministic helper coverage for hand size, multiset quantities, mulligan bottom counts, draw/reset behavior, and commander exclusion. Run the project regression suite, syntax checks, and a Forge smoke battle when two valid saved Pauper or Standard lists are available.

## Builder calls

- Use existing in-app card details and current CSS patterns; no new dependency.
- Treat each copy as a distinct shuffled card, preserving quantities.
- Randomize in the browser for hand testing; hands are for practice and are not persisted or represented as Forge results.
- Keep the current Commander flow distinct from the 1v1 battle flow.
