# Repository Guidelines

## Project Structure & Module Organization

This repository is a small, dependency-free Python CLI for enriching Magic: The Gathering collection exports with Scryfall Oracle data.

- `enrich_mtg_collection.py`: command-line entry point and all enrichment, matching, caching, and CSV I/O logic.
- `collection_web.py` and `web/`: dependency-free local collection server and browser interface.
- `collection_export_*.csv`: ManaBox-style input data. Treat collection exports as user data, not source code.
- `.scryfall_cache/`: generated bulk-data cache created on first online run.
- `tests/`: `unittest` regression coverage for CSV parsing, matching, layouts, caching, and output preservation.
- `*_enriched.csv` and `*_unmatched.csv`: generated outputs; do not edit them as source files.

If the program grows, move reusable logic into an `mtg_enricher/` package while keeping tests under `tests/`.

## Build, Test, and Development Commands

The project requires Python 3.9+ and no third-party packages.

- `python enrich_mtg_collection.py --help` verifies the CLI and lists supported options.
- `python -m py_compile enrich_mtg_collection.py` performs a quick syntax check.
- `python -m unittest discover -s tests -v` runs the automated test suite.
- `python collection_web.py` serves the enriched collection at `http://127.0.0.1:8000/`.
- `python enrich_mtg_collection.py collection_export_1794927834.csv` enriches a collection, downloading Scryfall bulk data when necessary.
- `python enrich_mtg_collection.py collection_export_1794927834.csv --offline` runs against an existing `.scryfall_cache` without network access.
- `python enrich_mtg_collection.py collection_export_1794927834.csv --refresh` forces a fresh bulk-data download.

Use explicit `--output` and `--unmatched` paths during development to avoid confusing generated files with fixtures.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation. Use `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for module constants, and descriptive names for paths and CSV fields. Keep compatibility with Python 3.9 and prefer the standard library. Preserve the current safety properties: validate inputs before downloading, never overwrite the source CSV, and do not guess ambiguous card matches.

## Testing Guidelines

Tests use the standard-library `unittest` framework. Before submitting changes, run the full suite and the compile check. Cover both matched and unmatched rows when changing matching behavior, and include UTF-8 BOM, alternate delimiters, and offline handling when changing CSV or cache code. Test files live in `tests/` and follow `test_*.py` naming.

## Commit & Pull Request Guidelines

Git history is not included in this working copy, so no established commit convention can be inferred. Use short, imperative subjects such as `Handle duplicate CSV headers`. Pull requests should explain behavior changes, list validation commands, and include sanitized sample input/output when CSV behavior changes. Link relevant issues and call out network, cache-format, or backward-compatibility impacts.
