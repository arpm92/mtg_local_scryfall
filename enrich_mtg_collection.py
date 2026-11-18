#!/usr/bin/env python3
"""Enrich a Magic: The Gathering collection CSV with Scryfall Oracle data.

No third-party Python packages required. Python 3.9+.

Quick start (Windows CMD, from the directory containing your collection CSV):
    python enrich_mtg_collection.py "ManaBox_Collection.csv"

Or, if there's exactly one collection CSV in the current directory:
    python enrich_mtg_collection.py

Other options:
    python enrich_mtg_collection.py "my_cards.csv" --refresh
    python enrich_mtg_collection.py "my_cards.csv" --offline
    python enrich_mtg_collection.py --help

First run downloads and caches Scryfall's free Oracle Cards bulk data.
This script supports the current gzipped JSONL bulk format (2026), as well
as the older JSON-array format for backwards compatibility.

Important: Oracle Cards bulk data includes ONE representative printing per
Oracle ID, so most ManaBox "Scryfall ID" (printing IDs) will not appear in
that dataset. This script then matches by card name. It does NOT claim to
verify your exact printing, foil, price or set; these original CSV columns
are preserved without modification. Ambiguous cards are not guessed.
"""

import argparse
import csv
import gzip
import json
import os
import re
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = "https://api.scryfall.com/bulk-data/oracle-cards"
USER_AGENT = "PersonalMTGCollectionEnricher/1.0 (Python local CSV enrichment)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
CACHE_MAX_AGE_DAYS = 7
COLOR_ORDER = "WUBRG"

# Export formats from ManaBox, Moxfield and other trackers.
NAME_KEYS = ("name", "card name", "cardname", "card title")
SCRYFALL_ID_KEYS = ("scryfall id", "scryfallid", "scryfall uuid")
ORACLE_ID_KEYS = ("oracle id", "oracleid")
QUANTITY_KEYS = ("quantity", "qty", "count", "amount")

ADDED_COLUMNS = (
    "Oracle Name",
    "Oracle Text",
    "Mana Cost",
    "Mana Value",
    "Colors",
    "Color Identity",
    "Type Line",
    "Keywords",
    "Produced Mana",
    "Power",
    "Toughness",
    "Loyalty",
    "Scryfall Oracle ID",
    "Scryfall URL",
    "Commander Legality",
    "Scryfall Match Method",
    "Scryfall Match Status",
    "Scryfall Match Note",
)


def normalize_header(s):
    return re.sub(r"[\s_\-]+", " ", str(s or "").strip().casefold())


def normalize_name(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    for old, new in (("’", "'"), ("‘", "'"), ("—", "-"), ("–", "-")):
        s = s.replace(old, new)
    s = re.sub(r"\s*//\s*", " // ", s)
    return " ".join(s.casefold().split())


def get_column(fieldnames, aliases):
    by_key = {normalize_header(field): field for field in fieldnames}
    return next((by_key[k] for k in aliases if k in by_key), None)


def choose_input_path(argument):
    if argument:
        path = Path(argument).expanduser().resolve()
        if not path.is_file():
            raise ValueError("Collection CSV not found: {}".format(path))
        return path
    candidates = sorted(
        p for p in Path.cwd().glob("*.csv")
        if not p.stem.lower().endswith(("_enriched", "_unmatched"))
    )
    if len(candidates) == 1:
        return candidates[0].resolve()
    if not candidates:
        raise ValueError("No CSV found in this folder. Specify the filename, e.g. python enrich_mtg_collection.py \"collection.csv\"")
    raise ValueError("Multiple CSV files found. Specify one: " + ", ".join(p.name for p in candidates))


def read_csv(path):
    """Read CSV with common Windows encodings and delimiters."""
    last_error = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                sample = handle.read(32768)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    delimiter = dialect.delimiter
                except csv.Error:
                    delimiter = "," if sample.count(",") >= sample.count(";") else ";"
                reader = csv.DictReader(handle, delimiter=delimiter)
                if not reader.fieldnames:
                    raise ValueError("This CSV has no header row.")
                fields = list(reader.fieldnames)
                normalized_fields = [normalize_header(field) for field in fields]
                if (any(not field for field in normalized_fields)
                        or len(normalized_fields) != len(set(normalized_fields))):
                    raise ValueError("The CSV has blank or duplicate column headers.")
                rows = list(reader)
                for i, row in enumerate(rows, start=2):
                    if None in row:
                        raise ValueError("CSV row {} has more values than its header (check delimiter/quoting).".format(i))
                    if any(value is None for value in row.values()):
                        raise ValueError("CSV row {} has fewer values than its header (check delimiter/quoting).".format(i))
                return fields, rows, delimiter, encoding
        except UnicodeDecodeError as error:
            last_error = error
    raise ValueError("Could not decode CSV: {}".format(last_error))


def http_json(url):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def is_allowed_download_url(url):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    return parsed.scheme == "https" and (
        host == "scryfall.io" or host.endswith(".scryfall.io")
        or host == "scryfall.com" or host.endswith(".scryfall.com")
    )


def download_to_cache(url, destination):
    if not is_allowed_download_url(url):
        raise ValueError("Unexpected Scryfall bulk download URL: {}".format(url))
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="scryfall_", suffix=".part", dir=str(destination.parent))
    os.close(fd)
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=90) as response, open(temp_name, "wb") as output:
            downloaded = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)
                downloaded += len(block)
        if downloaded == 0:
            raise ValueError("Scryfall returned an empty bulk download.")
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def cached_files(cache_dir):
    options = [cache_dir / "oracle_cards.jsonl.gz", cache_dir / "oracle_cards.json"]
    return sorted((p for p in options if p.is_file() and p.stat().st_size > 0),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def ensure_bulk_data(cache_dir, offline=False, refresh=False):
    cached = cached_files(cache_dir)
    if cached and (offline or (
        not refresh and datetime.now(timezone.utc) - datetime.fromtimestamp(
            cached[0].stat().st_mtime, timezone.utc
        ) < timedelta(days=CACHE_MAX_AGE_DAYS)
    )):
        print("Using cached Scryfall data: {}".format(cached[0]))
        return cached[0]
    if offline:
        raise ValueError("No cached Scryfall data found. Run without --offline once to download it.")

    print("Checking Scryfall Oracle Cards bulk data...")
    try:
        manifest = http_json(API_URL)
        # Scryfall migrated to gzip-compressed JSONL in 2026. Support both formats.
        uri = manifest.get("jsonl_download_uri") or manifest.get("download_uri")
        if not uri:
            raise ValueError("The Scryfall bulk manifest contains no download URL.")
        is_jsonl = bool(manifest.get("jsonl_download_uri")) or ".jsonl" in urllib.parse.urlparse(uri).path
        target = cache_dir / ("oracle_cards.jsonl.gz" if is_jsonl else "oracle_cards.json")
        print("Downloading Oracle Cards database (first run may take a little time)...")
        download_to_cache(uri, target)
        print("Saved Scryfall data to {}".format(target))
        return target
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as error:
        if cached:
            print("WARNING: Could not refresh data ({}). Using cached copy.".format(error), file=sys.stderr)
            return cached[0]
        raise ValueError("Could not download Scryfall data: {}".format(error)) from error


def iter_oracle_cards(path):
    if path.name.endswith(".jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if line.strip():
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError("Invalid Scryfall data at line {}: {}".format(line_number, error)) from error
    else:
        # Older Scryfall bulk downloads were a single JSON array.
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError("Unexpected legacy Scryfall bulk file format.")
        yield from data


def card_aliases(card):
    names = [card.get("name"), card.get("printed_name")]
    for face in card.get("card_faces") or []:
        names.extend([face.get("name"), face.get("printed_name")])
    return {normalize_name(n) for n in names if n}


def build_index(source):
    by_oracle_id = {}
    by_print_id = {}
    by_name = defaultdict(dict)  # normalized name -> {oracle_id: card}
    by_alias = defaultdict(dict)  # face/printed names are lower-priority fallbacks
    count = 0
    for card in iter_oracle_cards(source):
        oracle_id = card.get("oracle_id")
        if not oracle_id or not card.get("name"):
            continue
        count += 1
        by_oracle_id[oracle_id.lower()] = card
        if card.get("id"):
            by_print_id[card["id"].lower()] = card
        exact_name = normalize_name(card["name"])
        by_name[exact_name][oracle_id.lower()] = card
        for alias in card_aliases(card) - {exact_name}:
            by_alias[alias][oracle_id.lower()] = card
    if not count:
        raise ValueError("No Oracle cards were found in the downloaded file.")
    # Exact card names must beat face aliases. For example, Scryfall includes
    # both the playable "Forest" card and an art-series "Forest // Forest"
    # object whose face is also named "Forest".
    for alias, cards in by_alias.items():
        if alias not in by_name:
            by_name[alias].update(cards)
    print("Indexed {:,} unique Oracle cards.".format(count))
    return by_oracle_id, by_print_id, by_name


def lookup(row, name_col, oracle_col, scryfall_col, indexes):
    by_oracle_id, by_print_id, by_name = indexes
    card_name = str(row.get(name_col) or "").strip() if name_col else ""
    oracle_id = str(row.get(oracle_col) or "").strip().lower() if oracle_col else ""
    print_id = str(row.get(scryfall_col) or "").strip().lower() if scryfall_col else ""

    if oracle_id and oracle_id in by_oracle_id:
        return by_oracle_id[oracle_id], "Oracle ID", ""
    if print_id and print_id in by_print_id:
        return by_print_id[print_id], "representative Scryfall ID", ""
    if card_name:
        matches = by_name.get(normalize_name(card_name), {})
        if len(matches) == 1:
            note = ("The collection's printing-specific Scryfall ID is not in the Oracle Cards "
                    "bulk dataset; matched card rules by name, not exact printing.") if print_id else ""
            return next(iter(matches.values())), "exact card/face name", note
        if len(matches) > 1:
            return None, "", "Ambiguous name: {} different Oracle cards match. Review manually.".format(len(matches))
    if not card_name:
        return None, "", "Missing card name and no matching ID in the Oracle Cards bulk dataset."
    return None, "", "Name not found in Oracle Cards bulk dataset. Check spelling or language."


def join_colors(value, empty="Colorless"):
    if not value:
        return empty
    return ",".join(c for c in COLOR_ORDER if c in value) or empty


def card_field(card, key):
    value = card.get(key)
    if value not in (None, ""):
        return str(value)
    faces = card.get("card_faces") or []
    pairs = [(face.get("name", "Face"), str(face.get(key))) for face in faces
             if face.get(key) is not None and face.get(key) != ""]
    if not pairs:
        return ""
    if key == "oracle_text":
        return "\n\n".join("{}:\n{}".format(name, content) for name, content in pairs)
    return " // ".join(content for name, content in pairs)


def make_enrichment(card, method, note):
    colors = card.get("colors")
    if colors is None:
        colors = list({color for face in card.get("card_faces", []) for color in face.get("colors", [])})
    legalities = card.get("legalities") or {}
    produced_mana = card.get("produced_mana")
    if produced_mana is None:
        produced_mana = {
            color
            for face in card.get("card_faces", [])
            for color in (face.get("produced_mana") or [])
        }
    return {
        "Oracle Name": card.get("name", ""),
        "Oracle Text": card_field(card, "oracle_text"),
        "Mana Cost": card_field(card, "mana_cost"),
        "Mana Value": card.get("cmc", ""),
        "Colors": join_colors(colors),
        "Color Identity": join_colors(card.get("color_identity")),
        "Type Line": card_field(card, "type_line"),
        "Keywords": ", ".join(card.get("keywords") or []),
        "Produced Mana": ",".join(c for c in "WUBRGC" if c in produced_mana),
        "Power": card_field(card, "power"),
        "Toughness": card_field(card, "toughness"),
        "Loyalty": card_field(card, "loyalty"),
        "Scryfall Oracle ID": card.get("oracle_id", ""),
        "Scryfall URL": card.get("scryfall_uri", ""),
        "Commander Legality": legalities.get("commander", "unknown"),
        "Scryfall Match Method": method,
        "Scryfall Match Status": "matched",
        "Scryfall Match Note": note,
    }


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)


def enrich_collection(input_path, output_path, unmatched_path, indexes):
    fields, original_rows, delimiter, encoding = read_csv(input_path)
    name_col = get_column(fields, NAME_KEYS)
    oracle_col = get_column(fields, ORACLE_ID_KEYS)
    print_col = get_column(fields, SCRYFALL_ID_KEYS)
    quantity_col = get_column(fields, QUANTITY_KEYS)
    if not any((name_col, oracle_col, print_col)):
        raise ValueError(
            "Could not identify a card column. Expected 'Name', 'Card Name', "
            "'Oracle ID' or 'Scryfall ID'. Found: " + ", ".join(fields)
        )
    if not original_rows:
        raise ValueError("The input CSV contains no card rows.")
    print("Read {:,} rows ({}, '{}' delimiter).".format(len(original_rows), encoding, delimiter))
    enriched_rows = []
    unmatched_rows = []
    matched = 0
    total_copies = 0
    for source_row, row in enumerate(original_rows, 2):
        card, method, note = lookup(row, name_col, oracle_col, print_col, indexes)
        result = dict(row)
        if card:
            result.update(make_enrichment(card, method, note))
            matched += 1
        else:
            result.update({field: "" for field in ADDED_COLUMNS})
            result["Scryfall Match Status"] = "unmatched"
            result["Scryfall Match Note"] = note
            unmatched_rows.append({
                "CSV Row": source_row,
                "Card Name": row.get(name_col, "") if name_col else "",
                "Scryfall ID": row.get(print_col, "") if print_col else "",
                "Reason": note,
            })
        enriched_rows.append(result)
        if quantity_col:
            try:
                total_copies += int(str(row.get(quantity_col, "")).strip())
            except (ValueError, TypeError):
                pass
    if input_path.resolve() in (output_path.resolve(), unmatched_path.resolve()):
        raise ValueError("Output filename must differ from the input CSV; your original data will not be overwritten.")
    if output_path.resolve() == unmatched_path.resolve():
        raise ValueError("Enriched and unmatched report filenames must be different.")
    output_fields = fields + [f for f in ADDED_COLUMNS if f not in fields]
    write_csv(output_path, output_fields, enriched_rows)
    write_csv(unmatched_path, ["CSV Row", "Card Name", "Scryfall ID", "Reason"], unmatched_rows)

    print("\nFinished:")
    print("  Matched:   {:,} / {:,} collection rows".format(matched, len(original_rows)))
    print("  Unmatched: {:,} rows".format(len(unmatched_rows)))
    if quantity_col:
        print("  Copies:    {:,} (sum of quantity column)".format(total_copies))
    print("  Enriched:  {}".format(output_path))
    print("  Review:    {}".format(unmatched_path))
    if unmatched_rows:
        print("  NOTE: Review the unmatched report; those cards have not been guessed.")


def main():
    parser = argparse.ArgumentParser(description="Enrich a ManaBox or other MTG collection CSV using Scryfall Oracle text.")
    parser.add_argument("csv_file", nargs="?", help="Input CSV (auto-detected if exactly one suitable CSV in the current folder)")
    parser.add_argument("--output", help="Output enriched CSV path (default: INPUT_enriched.csv)")
    parser.add_argument("--unmatched", help="Unmatched report CSV path (default: INPUT_unmatched.csv)")
    parser.add_argument("--cache-dir", help="Bulk database cache folder (default: .scryfall_cache next to this script)")
    parser.add_argument("--refresh", action="store_true", help="Force a new Scryfall bulk download")
    parser.add_argument("--offline", action="store_true", help="Use previously downloaded Scryfall bulk data only")
    args = parser.parse_args()
    try:
        if args.refresh and args.offline:
            raise ValueError("--refresh and --offline cannot be used together.")
        input_path = choose_input_path(args.csv_file)
        cache_dir = Path(args.cache_dir).expanduser().resolve() if args.cache_dir else Path(__file__).resolve().parent / ".scryfall_cache"
        output_path = Path(args.output).expanduser().resolve() if args.output else input_path.with_name(input_path.stem + "_enriched.csv")
        unmatched_path = Path(args.unmatched).expanduser().resolve() if args.unmatched else input_path.with_name(input_path.stem + "_unmatched.csv")
        # Validate the input before initiating a potentially large download.
        fields, rows, _, _ = read_csv(input_path)
        if not rows:
            raise ValueError("The input CSV is empty.")
        if not any((get_column(fields, NAME_KEYS), get_column(fields, ORACLE_ID_KEYS), get_column(fields, SCRYFALL_ID_KEYS))):
            raise ValueError("Input CSV lacks Name, Card Name, Oracle ID or Scryfall ID columns: " + ", ".join(fields))
        bulk_path = ensure_bulk_data(cache_dir, offline=args.offline, refresh=args.refresh)
        indexes = build_index(bulk_path)
        enrich_collection(input_path, output_path, unmatched_path, indexes)
    except (ValueError, OSError, csv.Error, json.JSONDecodeError, EOFError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
