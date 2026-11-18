# Format behavior

Read the selected `formats/<name>.yaml` first. These notes explain decisions that do not fit cleanly into configuration.

## Commander

- Exactly 99 main-deck cards plus one commander, or a rules-supported partner/background/Doctor pair totaling 100.
- Enforce color identity and singleton by English card name. Basic lands and explicit card-text exceptions bypass singleton.
- Ordinary multiplayer starts at 40 life; commander damage and command-zone rules are Forge game-engine concerns.
- Use current `legalities.commander`; refresh data when bans may have changed.
- Brackets 1–5 describe experience and intent. Game Changers, tutors, extra turns, mass land denial, and combo speed inform classification, but the banned list always controls legality.
- Prefer four-player free-for-all evidence and disclose sample size, timeouts, AI warnings, and seat configuration.

## Pauper

- Build exactly 60 main cards and preferably 15 sideboard cards. The combined main/sideboard copy limit is four except basic lands and explicit card-text exceptions.
- `legalities.pauper == legal` captures the rule that any qualifying common printing makes all printings of that card legal. Never infer Pauper legality from the owned row's rarity.
- Assess aggro, tempo, control, midrange, artifact, graveyard, and combo signals from the actual owned pool. Report missing playsets as ownership shortages.
- Use only Pauper-validated opponents. Treat best-of-three/sideboard evidence as unsupported if Forge does not visibly perform sideboarding.

## Standard

- Build exactly 60 main cards and preferably 15 sideboard cards; enforce a combined four-copy limit.
- Refresh Scryfall before each new build. `legalities.standard` handles rotation, bans, and currently legal reprints; do not infer legality from the owned set code.
- Optional metagame analysis requires current, cited tournament data. Never invent shares, win rates, or matchup percentages.
- Use only independently validated Standard opponents.

## Ownership

Nonbasic cards may not exceed the summed collection quantity across main, sideboard, and commander sections. Basic lands are unlimited only when `basic_lands_unlimited` is enabled. Upgrade suggestions must be separate from an owned deck and clearly labeled as purchases.
