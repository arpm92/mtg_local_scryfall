# MTG Collection Enricher

This standard-library Python CLI adds Scryfall Oracle rules data to a ManaBox collection CSV while preserving every original row and column. It writes a separate enriched collection and an unmatched-card review report; it never overwrites the input file.

## Requirements and setup

Python 3.9 or newer is required. The project has no external dependencies. Create the local environment once:

```powershell
python -m venv .venv
```

Activate it in Windows CMD:

```cmd
.venv\Scripts\activate.bat
```

Or in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activation is optional if commands use `.\.venv\Scripts\python.exe` explicitly.

## Process a ManaBox collection

Export the collection as CSV, place it in this directory, and pass its filename explicitly:

```powershell
.\.venv\Scripts\python.exe enrich_mtg_collection.py "collection_export.csv"
```

By default, results are saved beside the input as `collection_export_enriched.csv` and `collection_export_unmatched.csv`. Choose exact paths with `--output` and `--unmatched`:

```powershell
.\.venv\Scripts\python.exe enrich_mtg_collection.py "collection_export.csv" --output "MTG_Collection_Enriched.csv" --unmatched "MTG_Collection_Unmatched.csv"
```

The first online run downloads Scryfall's Oracle Cards bulk data into `.scryfall_cache`. A fresh cache is reused for seven days. Force an update with `--refresh`, or prohibit network access and require an existing cache with `--offline`:

```powershell
.\.venv\Scripts\python.exe enrich_mtg_collection.py "collection_export.csv" --refresh
.\.venv\Scripts\python.exe enrich_mtg_collection.py "collection_export.csv" --offline
```

Review the unmatched report after every run. Ambiguous names are intentionally not guessed. Run the automated suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Browse and filter the collection

Start the local collection browser after generating `MTG_Collection_Enriched.csv`:

```powershell
.\.venv\Scripts\python.exe collection_web.py
```

Open `http://127.0.0.1:8000/` and stop the server with `Ctrl+C`. Use `--csv PATH` to load another enriched export or `--port 8080` to select another port. The interface searches only your owned cards and supports Scryfall-style filters such as `t:creature c:u mv<=3`, `o:"draw a card"`, `set:mh3`, `is:foil`, `qty>1`, and exclusions such as `-c:r`.

Duplicate collection rows are grouped by Scryfall Oracle identity; quantities are summed and each owned printing remains visible in the card detail dialog.

Use **Import ManaBox CSV** to analyze an additional export against the locally cached Scryfall Oracle dataset. A **Bulk scan** adds its copies to your displayed collection. A **Deck scan** asks for a deck name, adds its copies, and reserves them for a read-only, unclassified scanned deck; unmatched or ambiguous rows prevent saving a deck scan. The import ledger is stored at `outputs/collection_imports.json`, and the source CSV is never changed. Card details show owned, reserved, and available quantities by saved deck. Removing a scanned deck releases its reservation while keeping the scanned cards in your collection.

The `Decks` tab is an owned-card deck builder. Choose Commander, Pauper, or Standard, add cards to the appropriate section, then select **Validate & save**. Saved decks can be viewed as image cards or a compact list; the visual board groups cards by Commander, creatures, artifacts, enchantments, instants, sorceries, lands, and other card types. Select any card image to open its uncropped full details. A short deck primer summarizes the main plan, early/mid/late game, and winning conditions. Scryfall token and emblem relationships are collected automatically into a display-only **Tokens & Emblems** section with their creating cards; accessories never count toward deck size or ownership. The delete button beside a saved deck asks for confirmation and moves its JSON, report, and Forge file to `outputs/.trash/` for recovery. The server enforces current Scryfall legality, exact main-deck size, copy and ownership limits, Commander identity, and sideboard limits. Valid decks are saved as JSON, a strategy report, and a Forge `.dck` file under `outputs/<format>/`.

The same tab has a **Deck assistant** panel. When configured, it uses Codex app-server with the server's ChatGPT sign-in and can answer questions about the collection or a saved deck. Replies render common Markdown (headings, lists, tables, links, and code). The **Codex model** selector uses the model catalog reported by app-server; an empty selection uses Codex's default. Requested deck changes are returned as proposals; chat-created decks are validated and saved, then can be simulated from **Decks → Run Forge simulation**. Sign-in data stays in the server-side Codex home and is never sent to the browser. The chat path is optional; the rest of the web app works when Codex is disconnected.

## Format-aware deck architect

The reusable engine and Codex skill live in `.agents/skills/mtg-deck-architect/`. Analyze or build from the command line with:

```powershell
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py analyze standard
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py build pauper --count 1 --power-target optimized --output-root outputs
.\.venv\Scripts\python.exe .agents\skills\mtg-deck-architect\scripts\architect.py build commander --colors G --commander "Tigra, Feline Fury" --archetype "lifegain +1/+1 counters" --output-root outputs
```

Format rules are configured independently in `formats/commander.yaml`, `formats/pauper.yaml`, and `formats/standard.yaml` beneath the skill. Refresh the Scryfall cache before a time-sensitive Standard build. The server simulation runner validates the selected deck and same-format opponents first, then runs Commander as a four-player pod or Pauper/Standard as 1v1. It rotates seat order across up to 10 games and saves result lines and logs under `outputs/<format>/simulations/`. Forge's CLI does not enforce Standard or Pauper bans/legality; the app's independent validator does. Forge AI results are preliminary evidence, and the CLI does not provide a verified sideboard plan for BO3.

## Deploy on a home server with Portainer

The container setup is in `Dockerfile`, `compose.yaml`, and [deploy/README.md](deploy/README.md). It serves the collection UI on port 8015, mounts the enriched CSV and Scryfall cache read-only, and persists deck changes under the host `outputs/` directory. Copy `.env.portainer.example` to a private `.env.portainer`, set the host paths, and bind only to the server's LAN address. The image installs Java 17 and downloads a pinned Forge release with a SHA-256 check. Codex app-server uses the ChatGPT account signed into its persistent, ignored `.codex-home` directory. The internal web app has no login.
