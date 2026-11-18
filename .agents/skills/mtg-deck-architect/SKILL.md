---
name: mtg-deck-architect
description: Build synergy-first, collection-constrained Commander, Pauper, and Standard decks using card-level Oracle analysis, researched strategy, independent legality checks, and evidence-based Forge testing. Use for deck construction, optimization, matchup analysis, and deck reports; do not use for unrelated card rulings or invented metagame statistics.
---

# MTG Deck Architect

Build only from the latest verified collection inventory. Strategy and functional card interactions outrank generic card ratings, color overlap, typal overlap, and theme. Treat current legality as time-sensitive.

## Route the request

1. Infer or confirm `commander`, `pauper`, or `standard` from the request. Never simulate decks from different formats as sanctioned matchup evidence.
2. Read the matching file in `formats/`. It is JSON-compatible YAML loaded by the shared standard-library engine.
3. For format rules and interpretation, read [references/formats.md](references/formats.md). For Forge execution or result interpretation, also read [references/forge.md](references/forge.md).
4. Use `scripts/architect.py` through the project `.venv`; do not reimplement validation, ownership checks, export, or Forge invocation ad hoc.

## Required workflow

- Confirm `MTG_Collection_Enriched.csv` and `.scryfall_cache/oracle_cards.jsonl.gz` exist and are current enough for the requested run. Refresh enrichment before a new Standard build or whenever legality may have changed.
- Analyze the latest collection independently for each requested format. Identify supported archetypes by count of actual enablers, payoffs, engines, interaction, protection, and finishers; do not infer synergy from shared types or themes.
- Write one precise objective and define primary and independent secondary win conditions before selecting cards. If a requested archetype lacks enough owned support, choose a better supported strategy and explain the limit.
- Read each candidate's complete Oracle text. For every included card, record its role, specific card-to-card links, required board state/resources/timing, mana fit, standalone utility, counter-synergies, and a rejected or stronger owned alternative where applicable. Build a synergy graph with enabler, payoff, engine, finisher, interaction, protection, recovery, unsupported dependency, dead-card, and conflict nodes/edges.
- Start with win conditions and their support, then assemble the mana base, access/consistency, interaction, protection, and recovery. Tune quantities to role, curve, colored sources, expected game length, draw probability, and owned copies; do not use a generic card score as the main selection algorithm.
- For Commander, compare at least three serious eligible candidates when possible using strategy fit, number and quality of owned mechanical links, repeatability, resilience after removal, win-condition contribution, mana burden, and external research. Check full Oracle text, Commander legality, EDHREC or another reputable strategy source, known combos via Commander Spellbook, and their exact requirements. Popularity is a signal only. Do not assume command-zone text works outside its stated zone.
- Validate every card count, legality, commander selection, color identity, ban, ownership quantity, and format deck rule before export. Never waive a rule to meet a deck-count request.
- Export each deck and report beneath `outputs/<format>/`. Keep original and optimized versions.
- If the collection cannot support a requested deck, report the exact shortage. Fixtures may test the engine but must be labeled as fixtures and never presented as owned decks.
- Explain each win condition as combat, deterministic combo, infinite engine plus payoff, or advantage engine; list its exact pieces/resources, rules sequence, assembly path, availability, disruption points, and recovery. Do not claim a combo or loop without verifying costs and repeatability. Do not force a second win condition when the pool lacks one.
- Keep each baseline, changed version, logs, and report. Every replacement needs a concrete synergy, curve, consistency, or observed-game reason; explain interactions gained/lost and revalidate ownership and legality.

## Legality and current data

Scryfall's per-card `legalities` fields are the primary deterministic gate. Pauper legality follows qualifying common printings, not the rarity of the owned printing. Standard legality follows the card's current legality, including reprints and rotation, not the age of the owned printing. Commander bracket classification is a pregame communication aid, not a legality substitute; use the installed Forge Game Changers/bracket data when available and state uncertainty.

## Forge evidence

Read the installed Forge version, its CLI help, AI documentation, and actual simulation/deck format support before running. Validate independently because Forge CLI exposes Commander and generic Constructed modes, not Standard/Pauper legality enforcement.

For every final candidate, play at least 10 successfully completed baseline games and at least 10 more after targeted revision. Failed starts, crashes, timeouts, and games without a completion result do not count. Commander uses four-player pods with varied seats and diverse opponents. Pauper and Standard use only same-format legal opponents, alternating play/draw positions; use BO3 and sideboarding when the installed version supports them. If no verified opponent exists, build a clearly labeled owned-independent fixture and never call it a legal matchup. Do not claim competitive performance from unrelated opponent decks.

Save attempted/completed/failed games, wins/losses, seed, position, opponent, turns, logs, command/config, and limitations per game. Separate observed results from interpretation, distinguish deck weakness from Forge AI errors or unsupported cards, and compare baseline/revised runs under like settings. Ten games are preliminary evidence, not proof. If 10 completions are impossible, report the exact blocker and keep the deck marked untested/incomplete.

## Common commands

```powershell
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py analyze commander
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py build pauper --count 5 --archetype artifact
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py build standard --count 3 --power-target competitive
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py validate outputs\standard\deck.json
```

## Research sources and standards

Use these primary/reputable references as decision aids, recording access date and the specific principle applied:

- [Wizards: My Most Important Deck-Building Rule](https://magic.wizards.com/en/news/feature/my-most-important-deck-building-rule-2018-02-08): choose one coherent strategic through-line; card inclusion must serve it.
- [Wizards: How to Build Like a Pro](https://magic.wizards.com/en/news/feature/how-build-pro-2016-02-02): curve, selection, and consistency are contextual to the deck's objective.
- [EDHREC: How to Choose Your Commander](https://edhrec.com/articles/how-to-choose-your-commander): compare the play pattern and supported archetype, not popularity alone.
- [EDHREC: How to Build a Commander Deck](https://edhrec.com/articles/how-to-build-a-commander-deck): establish credible win conditions and supporting role balance, adjusted to commander and plan.
- [EDHREC digital deckbuilding guide](https://edhrec.com/articles/digital-deckbuilding-the-how-to-guide-to-building-a-commander-deck-using-edhrec-archidekt-and-commander-spellbook): use database patterns as discovery signals and verify card interactions.
- [Commander Spellbook](https://commanderspellbook.com/): check candidate combo pieces and exact stated outcomes; verify rules/costs independently.
- [Forge AI documentation](https://github.com/Card-Forge/forge/blob/master/docs/AI.md): use documented AI/simulation configuration, then verify behavior against the installed version.

The local web UI uses the shared validator for user-created decks; start it with `python collection_web.py`.

## Required report

Write `outputs/<format>/<deck>-report.md` with deck identity and one-sentence plan; complete list, curve, colored sources, role counts, and ownership; card-by-card synergy graph and unsupported/conflicting slots; primary and secondary win conditions with requirements/rules/disruption/recovery; Commander candidate comparison and citations where relevant; 10+ baseline and 10+ revised Forge results with per-game evidence; each changed card and rationale; and a verdict for synergy, consistency, resilience, interaction, win-condition reliability, and observed performance. Label any estimate as analysis, not measured data.

The local web UI uses this same validator for user-created decks; start it with `python collection_web.py`.
