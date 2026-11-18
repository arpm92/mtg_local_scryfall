# Forge integration

## Verified installation

- Forge desktop: `E:\MTG-Forge\forge-installer-2.0.16-SNAPSHOT`
- Observed version: `Forge v.2.0.16-SNAPSHOT-10.16`
- Java: 17.0.20.1
- JAR: `forge-gui-desktop-2.0.16-SNAPSHOT-jar-with-dependencies.jar`
- The Forge installation directory must be the process working directory.

## Verified CLI

```text
java -Xmx2g -Dfile.encoding=UTF-8 -jar <jar> sim -d <decks...> -D <deck-dir> -f <type> -n 1 -s 42 -c 180 -q
```

`-f commander` creates Commander rules and `RegisteredPlayer.forCommander`. Pauper and Standard must use `-f constructed`; Forge's `GameType` has no Standard/Pauper simulation types. `-m 3` runs a match instead of `-n`, but automatic AI sideboarding has not been independently proven by this skill. The CLI accepts AI profiles, RNG seeds, tournaments, and a simulation clock.

The installed `AutomatedTestDeck` harness is Commander-specific and supports four-player pods, random precons, seat rotation, balanced sampling, watchdogs, raw logs, diagnostics, and confidence intervals. Reuse it for serious Commander experiments after copying validated decks into a location it can resolve. Do not use its Commander summaries for 60-card formats.

## Evidence rules

- Keep raw stdout/stderr and machine-readable `Game Result` lines.
- A normal result must have one winner. Timeouts, draws, engine errors, invalid results, and process failures are not losses.
- One-game runs are smoke tests only.
- For Commander, do not turn 1v1 evidence into multiplayer power claims.
- For constructed formats, do not compare across formats and do not call a match sanctioned-format evidence until both decks pass the independent validator.

## Known limitations

- Forge CLI does not enforce Standard rotation/bans or Pauper rarity/bans by format name.
- The generic simulation CLI does not provide an observable sideboard-plan API.
- Forge AI strength and card support vary; win rate is one metric among consistency, curve, interaction, resilience, diagnostics, and human review.
- Commander bracket calculation exists in the Forge source and reads `res/lists/gamechangers.txt` plus combo data, but the Python engine's classification remains advisory.
