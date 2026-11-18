#!/usr/bin/env python3
"""Prepare and persist ManaBox CSV imports without editing source exports."""

import csv
import hashlib
import json
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from enrich_mtg_collection import (
    ADDED_COLUMNS,
    NAME_KEYS,
    ORACLE_ID_KEYS,
    QUANTITY_KEYS,
    SCRYFALL_ID_KEYS,
    build_index,
    get_column,
    lookup,
    make_enrichment,
    read_csv,
)


_STORE_LOCK = threading.RLock()
_STORE_VERSION = 1


def prepare_upload(csv_bytes, bulk_path, mode="bulk", deck_name=None, indexes=None):
    """Parse and enrich a ManaBox CSV upload using the local Oracle bulk file.

    Returns original rows with Oracle fields added, an unmatched-row report,
    summary totals, a content hash, and (for a fully matched deck scan) a
    draft ``format: scanned`` deck. This function never writes the source.
    """
    if mode not in ("bulk", "deck"):
        raise ValueError("Choose either a bulk collection scan or a deck scan.")
    if not isinstance(csv_bytes, (bytes, bytearray)) or not csv_bytes:
        raise ValueError("The uploaded ManaBox CSV is empty.")
    deck_name = str(deck_name or "").strip()
    if mode == "deck" and (not deck_name or len(deck_name) > 100):
        raise ValueError("A deck scan needs a name between 1 and 100 characters.")

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as uploaded:
        temp_path = Path(uploaded.name)
        uploaded.write(bytes(csv_bytes))
    try:
        fields, source_rows, delimiter, encoding = read_csv(temp_path)
    except (OSError, csv.Error, UnicodeError, ValueError) as error:
        raise ValueError("Could not read the uploaded ManaBox CSV: {}".format(error)) from error
    finally:
        temp_path.unlink(missing_ok=True)

    if not source_rows:
        raise ValueError("The uploaded ManaBox CSV contains no card rows.")
    name_column = get_column(fields, NAME_KEYS)
    if not name_column:
        raise ValueError("The uploaded ManaBox CSV needs a card name column.")
    oracle_column = get_column(fields, ORACLE_ID_KEYS)
    print_column = get_column(fields, SCRYFALL_ID_KEYS)
    quantity_column = get_column(fields, QUANTITY_KEYS)
    if indexes is None:
        if not bulk_path or not Path(bulk_path).is_file():
            raise ValueError("The local Scryfall Oracle Cards cache is not available.")
        indexes = build_index(Path(bulk_path))

    output_fields = list(fields)
    if "Name" not in output_fields:
        output_fields.append("Name")
    if "Quantity" not in output_fields:
        output_fields.append("Quantity")
    output_fields.extend(field for field in ADDED_COLUMNS if field not in output_fields)

    enriched_rows = []
    unmatched = []
    deck_counts = OrderedDict()
    total_copies = 0
    for row_number, source_row in enumerate(source_rows, start=2):
        row = dict(source_row)
        card_name = str(source_row.get(name_column) or "").strip()
        if not card_name:
            raise ValueError("CSV row {} is missing a card name.".format(row_number))
        raw_quantity = source_row.get(quantity_column) if quantity_column else "1"
        raw_quantity = str(raw_quantity or "").strip() or "1"
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError) as error:
            raise ValueError("CSV row {} quantity must be a positive whole number.".format(row_number)) from error
        if quantity <= 0:
            raise ValueError("CSV row {} quantity must be a positive whole number.".format(row_number))
        row["Name"] = card_name
        row["Quantity"] = str(quantity)
        total_copies += quantity

        card, method, note = lookup(row, name_column, oracle_column, print_column, indexes)
        if card:
            enrichment = make_enrichment(card, method, note)
            row.update({key: value for key, value in enrichment.items() if key not in fields})
            oracle_name = str(card.get("name") or card_name).strip()
            count_key = oracle_name.casefold()
            old = deck_counts.get(count_key)
            deck_counts[count_key] = (old[0], old[1] + quantity) if old else (oracle_name, quantity)
        else:
            row.update({field: "" for field in ADDED_COLUMNS if field not in fields})
            if "Scryfall Match Status" not in fields:
                row["Scryfall Match Status"] = "unmatched"
            if "Scryfall Match Note" not in fields:
                row["Scryfall Match Note"] = note
            unmatched.append({
                "CSV Row": row_number,
                "Card Name": card_name,
                "Scryfall ID": str(source_row.get("Scryfall ID") or ""),
                "Reason": note,
            })
        enriched_rows.append(row)

    deck = None
    if mode == "deck" and not unmatched:
        deck = {
            "name": deck_name,
            "format": "scanned",
            "main": {name: quantity for name, quantity in deck_counts.values()},
        }
    return {
        "mode": mode,
        "deck_name": deck_name if mode == "deck" else "",
        "sha256": hashlib.sha256(bytes(csv_bytes)).hexdigest(),
        "fields": output_fields,
        "delimiter": delimiter,
        "encoding": encoding,
        "rows": enriched_rows,
        "unmatched": unmatched,
        "summary": {
            "rows": len(enriched_rows),
            "copies": total_copies,
            "matched_rows": len(enriched_rows) - len(unmatched),
            "unmatched_rows": len(unmatched),
        },
        "deck": deck,
        "can_create_deck": mode == "deck" and not unmatched,
    }


class CollectionImportStore:
    """Atomic JSON ledger for imports; repeated identical uploads are ignored."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        if not self.path.exists():
            return {"version": _STORE_VERSION, "imports": []}
        try:
            content = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("The saved collection import ledger is invalid.") from error
        if (not isinstance(content, dict) or content.get("version") != _STORE_VERSION
                or not isinstance(content.get("imports"), list)):
            raise ValueError("The saved collection import ledger has an unsupported format.")
        return content

    @staticmethod
    def _key(prepared):
        if prepared.get("mode") not in ("bulk", "deck") or not prepared.get("sha256"):
            raise ValueError("Import data is missing its mode or content hash.")
        material = "\0".join((
            prepared["mode"], str(prepared.get("deck_name") or "").casefold(), prepared["sha256"]
        ))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def add(self, prepared):
        """Save a prepared upload once; return ``added=False`` on re-upload."""
        if prepared.get("mode") == "deck" and not prepared.get("deck"):
            raise ValueError("A deck scan must fully match before it can be saved.")
        if not isinstance(prepared.get("rows"), list) or not prepared["rows"]:
            raise ValueError("Import data contains no collection rows.")
        import_key = self._key(prepared)
        with _STORE_LOCK:
            ledger = self._load()
            if any(item.get("key") == import_key for item in ledger["imports"]):
                return {"added": False, "key": import_key}
            if prepared.get("mode") == "deck":
                requested_name = str(prepared.get("deck_name") or "").casefold()
                if any(
                    str((item.get("deck") or {}).get("name") or "").casefold() == requested_name
                    for item in ledger["imports"]
                ):
                    raise ValueError("A scanned deck with this name already exists.")
            record = {
                "key": import_key,
                "mode": prepared["mode"],
                "deck_name": prepared.get("deck_name") or "",
                "sha256": prepared["sha256"],
                "summary": prepared.get("summary") or {},
                "rows": prepared["rows"],
                "deck": prepared.get("deck"),
            }
            ledger["imports"].append(record)
            self._write(ledger)
        return {"added": True, "key": import_key}

    def _write(self, ledger):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                json.dump(ledger, output, ensure_ascii=False, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)

    def imported_rows(self):
        with _STORE_LOCK:
            return [row for entry in self._load()["imports"] for row in entry.get("rows", [])]

    def scanned_decks(self):
        with _STORE_LOCK:
            return [entry["deck"] for entry in self._load()["imports"]
                    if entry.get("mode") == "deck" and isinstance(entry.get("deck"), dict)]

    def remove_scanned_deck(self, deck_name):
        """Remove a scan's reservation while keeping its cards in the collection."""
        wanted = str(deck_name or "").strip().casefold()
        with _STORE_LOCK:
            ledger = self._load()
            for entry in ledger["imports"]:
                deck = entry.get("deck")
                if isinstance(deck, dict) and str(deck.get("name") or "").casefold() == wanted:
                    entry["deck"] = None
                    self._write(ledger)
                    return {"name": deck.get("name"), "format": "scanned", "removed": True}
        raise LookupError("Scanned deck not found.")
