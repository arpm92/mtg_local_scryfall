#!/usr/bin/env python3
"""Serve a local, Scryfall-style browser for an enriched collection CSV."""

import argparse
import base64
import csv
import hmac
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from enrich_mtg_collection import iter_oracle_cards, read_csv
from collection_allocation import annotate_card_availability, summarize_allocations
from collection_import import CollectionImportStore, prepare_upload
from forge_simulation import ForgeSimulationService, SimulationBusyError


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DEFAULT_COLLECTION = ROOT / "MTG_Collection_Enriched.csv"
DEFAULT_CACHE = ROOT / ".scryfall_cache"
ARCHITECT_PATH = ROOT / ".agents" / "skills" / "mtg-deck-architect" / "scripts" / "architect.py"


def load_architect():
    spec = importlib.util.spec_from_file_location("mtg_deck_architect", ARCHITECT_PATH)
    if not spec or not spec.loader:
        raise ValueError("Deck architect is unavailable: {}".format(ARCHITECT_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def deck_compatible_formats(deck, cards, architect):
    """Return formats in which the deck's cards and structure pass validation."""
    if not isinstance(deck, dict):
        return []
    validator = getattr(architect, "validate_deck", None)
    if not callable(validator):
        return []
    if deck.get("format") == "scanned":
        compatible = []
        for format_name in ("pauper", "standard", "legacy"):
            candidate = dict(deck, format=format_name, sideboard={}, commanders={})
            try:
                if validator(candidate, cards, format_name=format_name, respect_ownership=False).get("valid"):
                    compatible.append(format_name)
            except (KeyError, TypeError, ValueError):
                pass
        by_name = {str(card.get("name") or "").casefold(): card for card in cards}
        commander_candidates = []
        for card_name, count in (deck.get("main") or {}).items():
            card = by_name.get(str(card_name).casefold())
            if count and card and getattr(architect, "is_commander", lambda value: False)(card):
                candidate = dict(deck, format="commander", main=dict(deck.get("main") or {}),
                                 sideboard={}, commanders={str(card_name): 1})
                candidate["main"][card_name] -= 1
                if candidate["main"][card_name] <= 0:
                    del candidate["main"][card_name]
                try:
                    if validator(candidate, cards, format_name="commander", respect_ownership=False).get("valid"):
                        commander_candidates.append(str(card_name))
                except (KeyError, TypeError, ValueError):
                    pass
        if commander_candidates:
            compatible.append("commander")
        deck["format_analysis"] = {
            "commander_candidates": commander_candidates,
            "checked_formats": ["commander", "pauper", "standard", "legacy"],
        }
        return compatible
    has_commander_section = bool(deck.get("commanders"))
    compatible = []
    for format_name in ("commander", "pauper", "standard", "legacy"):
        if (format_name == "commander") != has_commander_section:
            continue
        try:
            result = validator(deck, cards, format_name=format_name, respect_ownership=False)
        except (KeyError, TypeError, ValueError):
            continue
        if result.get("valid"):
            compatible.append(format_name)
    return compatible


def deck_from_submission(submitted):
    """Normalize a deck request shared by validation and save endpoints."""
    format_name = str(submitted.get("format") or "").casefold()
    if format_name not in ("commander", "pauper", "standard", "legacy"):
        raise ValueError("Choose Commander, Pauper, Standard, or Legacy.")
    deck = {
        "name": str(submitted.get("name") or "Untitled Deck").strip()[:100],
        "format": format_name,
        "ownership_mode": ("proposal" if submitted.get("ownership_mode") == "proposal" else "owned"),
        "main": submitted.get("main") or {},
        "sideboard": submitted.get("sideboard") or {},
        "commanders": submitted.get("commanders") or {},
        "colors": submitted.get("colors") or [],
        "archetype": str(submitted.get("archetype") or "Website build")[:100],
        "power_target": str(submitted.get("power_target") or "")[:100],
        "source_scanned_name": str(submitted.get("source_scanned_name") or "").strip()[:100],
    }
    for field in ("report", "forge_evidence", "commander_research",
                  "commander_candidate_comparison", "final_verdict", "analysis_status"):
        value = submitted.get(field)
        if isinstance(value, dict) or (field == "analysis_status" and isinstance(value, str)):
            deck[field] = value
    history = submitted.get("optimization_history")
    if isinstance(history, list):
        deck["optimization_history"] = history
    parent = submitted.get("parent")
    if isinstance(parent, str) and parent.strip():
        deck["parent"] = parent.strip()[:100]
    for section in ("main", "sideboard", "commanders"):
        if not isinstance(deck[section], dict):
            raise ValueError("Deck sections must be card/count objects.")
        deck[section] = {
            str(card): int(count) for card, count in deck[section].items() if int(count) > 0
        }
    return deck


def validate_deck_allocations(deck, cards, existing_decks, architect):
    """Validate format rules and report owned copies reserved in other decks."""
    name_key = re.sub(r"\s+", " ", str(deck.get("name") or "")).strip().casefold()
    format_name = str(deck.get("format") or "").casefold()
    current_deck = next((item for item in existing_decks or ()
                         if re.sub(r"\s+", " ", str(item.get("name") or "")).strip().casefold() == name_key
                         and str(item.get("format") or "").casefold() == format_name), None)
    source_scanned_name = re.sub(r"\s+", " ", str(deck.get("source_scanned_name") or "")).strip().casefold()
    if source_scanned_name:
        current_deck = next((item for item in existing_decks or ()
                             if str(item.get("format") or "").casefold() == "scanned"
                             and re.sub(r"\s+", " ", str(item.get("name") or "")).strip().casefold() == source_scanned_name), None)
    other_decks = [
        item for item in existing_decks or ()
        if item is not current_deck
    ]
    validation = architect.validate_deck(
        deck, cards, format_name=format_name, respect_ownership=False
    )
    available_cards = annotate_card_availability(cards, other_decks)
    inventory = {
        re.sub(r"\s+", " ", str(card.get("name") or "")).strip().casefold(): card
        for card in available_cards
    }
    needed = {}
    for section in ("main", "sideboard", "commanders"):
        for card_name, raw_count in (deck.get(section) or {}).items():
            key = re.sub(r"\s+", " ", str(card_name)).strip().casefold()
            needed[key] = needed.get(key, 0) + max(0, int(raw_count))

    allocation_report = []
    errors = list(validation.get("errors", []))
    for key, count in needed.items():
        card = inventory.get(key)
        if not card or architect.is_basic(card):
            continue
        availability = card.get("availability", {})
        owned = int(availability.get("totalOwned", card.get("quantity", 0)) or 0)
        locations = [
            {"deck": str(item.get("name") or "Unnamed deck"),
             "copies": int(item.get("quantity", 0) or 0)}
            for item in availability.get("reservedByDeck", [])
            if int(item.get("quantity", 0) or 0) > 0
        ]
        reserved_elsewhere = sum(item["copies"] for item in locations)
        available = max(0, owned - reserved_elsewhere)
        shortage = max(0, count - available)
        current_deck_copies = 0
        if current_deck:
            for section in ("main", "sideboard", "commanders"):
                current_deck_copies += sum(
                    max(0, int(raw_count)) for current_name, raw_count
                    in (current_deck.get(section) or {}).items()
                    if re.sub(r"\s+", " ", str(current_name)).strip().casefold() == key
                )
        if locations or shortage:
            row = {
                "card_name": card.get("name") or next(
                    (name for section in ("main", "sideboard", "commanders")
                     for name in (deck.get(section) or {})
                     if re.sub(r"\s+", " ", str(name)).strip().casefold() == key), key
                ),
                "needed": count,
                "owned": owned,
                "reserved_elsewhere": reserved_elsewhere,
                "available": available,
                "shortage": shortage,
                "locations": locations,
            }
            if current_deck_copies:
                row["current_deck_name"] = str(current_deck.get("name") or deck.get("name") or "This deck")
                row["current_deck_copies"] = current_deck_copies
            allocation_report.append(row)
        if shortage and deck.get("ownership_mode") != "proposal":
            held = ", ".join("{} ({})".format(item["deck"], item["copies"]) for item in locations)
            copy_word = "copy" if count == 1 else "copies"
            message = "{} needs {} {}, but only {} are available after other deck reservations.".format(
                card.get("name") or key, count, copy_word, available)
            if held:
                message += " Reserved in: {}.".format(held)
            errors.append(message)
    validation["allocation_report"] = allocation_report
    validation["ownership_mode"] = deck.get("ownership_mode", "owned")
    validation["collection_complete"] = not any(item.get("shortage") for item in allocation_report)
    validation["errors"] = errors
    validation["valid"] = not errors
    return validation


def choose_collection(argument=None):
    if argument:
        path = Path(argument).expanduser().resolve()
        if not path.is_file():
            raise ValueError("Collection CSV not found: {}".format(path))
        return path
    if DEFAULT_COLLECTION.is_file():
        return DEFAULT_COLLECTION
    candidates = sorted(ROOT.glob("*_enriched.csv"))
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("No unique enriched CSV found. Pass one with --csv.")


def choose_bulk_file(cache_dir):
    candidates = [
        cache_dir / "oracle_cards.jsonl.gz",
        cache_dir / "oracle_cards.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def materialize_imported_collection(source_csv, extra_rows, output_dir):
    """Build a separate runtime CSV containing source rows plus saved imports."""
    source_csv = Path(source_csv)
    extra_rows = list(extra_rows or [])
    if not extra_rows:
        return source_csv
    fields, source_rows, _, _ = read_csv(source_csv)
    for row in extra_rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / ".collection_with_imports.csv"
    fd, temp_name = tempfile.mkstemp(prefix=".collection_with_imports.", suffix=".tmp", dir=str(output_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(source_rows)
            writer.writerows(extra_rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)
    return target


def card_images(card):
    image_uris = card.get("image_uris") or {}
    front = image_uris.get("normal") or image_uris.get("large")
    back = None
    faces = card.get("card_faces") or []
    face_images = [
        (face.get("image_uris") or {}).get("normal")
        or (face.get("image_uris") or {}).get("large")
        for face in faces
    ]
    face_images = [url for url in face_images if url]
    if not front and face_images:
        front = face_images[0]
    if len(face_images) > 1:
        back = face_images[1]
    return front or "", back or ""


def printing_image_urls(scryfall_id):
    """Return exact-printing CDN images for a valid Scryfall card UUID."""
    card_id = str(scryfall_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", card_id):
        return "", ""
    prefix = "https://cards.scryfall.io/normal"
    shard = "{}/{}/{}".format(card_id[0], card_id[1], card_id)
    return "{}/front/{}.jpg".format(prefix, shard), "{}/back/{}.jpg".format(prefix, shard)


def printing_scryfall_url(scryfall_id):
    """Return Scryfall's exact-printing page when a valid card UUID is present."""
    card_id = str(scryfall_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", card_id):
        return ""
    return "https://scryfall.com/card/{}".format(card_id)


def related_tokens(card):
    """Return token/emblem relationships without treating other combo pieces as tokens."""
    result = []
    for part in card.get("all_parts") or []:
        type_line = str(part.get("type_line") or "")
        if part.get("component") != "token" and "emblem" not in type_line.casefold():
            continue
        card_id = str(part.get("id") or "")
        if not card_id:
            continue
        result.append({
            "id": card_id,
            "name": part.get("name") or "Token",
            "typeLine": type_line,
            "kind": "emblem" if "emblem" in type_line.casefold() else "token",
            "imageUrl": "https://api.scryfall.com/cards/{}?format=image&version=normal".format(card_id),
        })
    return result


def load_image_index(bulk_path, oracle_ids):
    if not bulk_path or not oracle_ids:
        return {}
    result = {}
    for card in iter_oracle_cards(bulk_path):
        oracle_id = str(card.get("oracle_id") or "").lower()
        if oracle_id in oracle_ids and oracle_id not in result:
            result[oracle_id] = card_images(card)
    return result


def load_oracle_index(bulk_path, oracle_ids):
    if not bulk_path or not oracle_ids:
        return {}
    result = {}
    for card in iter_oracle_cards(bulk_path):
        oracle_id = str(card.get("oracle_id") or "").lower()
        if oracle_id in oracle_ids and oracle_id not in result:
            result[oracle_id] = card
    return result


def number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_collection(csv_path, cache_dir=DEFAULT_CACHE):
    fields, rows, _, _ = read_csv(csv_path)
    if "Name" not in fields and "Oracle Name" not in fields:
        raise ValueError("The collection needs a Name or Oracle Name column.")
    oracle_ids = {
        str(row.get("Scryfall Oracle ID") or "").lower()
        for row in rows if row.get("Scryfall Oracle ID")
    }
    oracle_index = load_oracle_index(choose_bulk_file(cache_dir), oracle_ids)
    cards = []
    for index, row in enumerate(rows):
        oracle_id = str(row.get("Scryfall Oracle ID") or "").lower()
        oracle = oracle_index.get(oracle_id, {})
        front, back = card_images(oracle)
        printing_front, printing_back = printing_image_urls(row.get("Scryfall ID"))
        front = printing_front or front
        back = printing_back or back
        quantity = int(number(row.get("Quantity"), 1))
        cards.append({
            "index": index,
            "oracleId": oracle_id,
            "scryfallId": row.get("Scryfall ID", ""),
            "name": row.get("Oracle Name") or row.get("Name") or "Unknown card",
            "quantity": quantity,
            "setCode": row.get("Set code", ""),
            "setName": row.get("Set name", ""),
            "collectorNumber": row.get("Collector number", ""),
            "rarity": row.get("Rarity", ""),
            "foil": row.get("Foil", ""),
            "language": row.get("Language", ""),
            "manaCost": row.get("Mana Cost", ""),
            "manaValue": number(row.get("Mana Value")),
            "colors": row.get("Colors", ""),
            "colorIdentity": row.get("Color Identity", ""),
            "typeLine": row.get("Type Line", ""),
            "oracleText": row.get("Oracle Text", ""),
            "keywords": row.get("Keywords", ""),
            "producedMana": row.get("Produced Mana", ""),
            "commander": row.get("Commander Legality", ""),
            "legalities": oracle.get("legalities", {}),
            "commanderCandidate": (
                "Legendary Creature" in (row.get("Type Line", "") or "")
                or "can be your commander" in (row.get("Oracle Text", "") or "").lower()
            ),
            "relatedTokens": related_tokens(oracle),
            "scryfallUrl": printing_scryfall_url(row.get("Scryfall ID")) or row.get("Scryfall URL", ""),
            "imageUrl": front,
            "backImageUrl": back,
            "raw": row,
        })
    grouped_cards = group_cards(cards)
    return {
        "source": csv_path.name,
        "fields": fields,
        "cards": grouped_cards,
        "summary": {
            "cards": len(grouped_cards),
            "rows": len(cards),
            "copies": sum(card["quantity"] for card in cards),
            "uniqueNames": len({card["name"] for card in grouped_cards}),
        },
    }


def group_cards(cards):
    """Group collection rows by Oracle identity and retain owned printings."""
    groups = {}
    for card in cards:
        key = card["oracleId"] or "name:" + card["name"].strip().casefold()
        if key not in groups:
            group = dict(card)
            group.update({
                "index": len(groups),
                "quantity": 0,
                "printings": [],
                "setCodes": [],
                "setNames": [],
                "rarities": [],
                "foils": [],
                "languages": [],
            })
            groups[key] = group
        group = groups[key]
        group["quantity"] += card["quantity"]
        printing_key = (
            card["scryfallId"] or card["setCode"], card["collectorNumber"],
            card["foil"], card["language"]
        )
        existing = next((item for item in group["printings"] if item["key"] == printing_key), None)
        if existing:
            existing["quantity"] += card["quantity"]
        else:
            group["printings"].append({
                "key": printing_key,
                "scryfallId": card["scryfallId"],
                "setCode": card["setCode"],
                "setName": card["setName"],
                "collectorNumber": card["collectorNumber"],
                "rarity": card["rarity"],
                "foil": card["foil"],
                "language": card["language"],
                "quantity": card["quantity"],
                "imageUrl": card["imageUrl"],
                "backImageUrl": card["backImageUrl"],
                "scryfallUrl": card["scryfallUrl"],
            })
        for target, value in (
            ("setCodes", card["setCode"]), ("setNames", card["setName"]),
            ("rarities", card["rarity"]), ("foils", card["foil"]),
            ("languages", card["language"]),
        ):
            if value and value not in group[target]:
                group[target].append(value)
    for group in groups.values():
        for printing in group["printings"]:
            printing.pop("key", None)
    return list(groups.values())


def archive_saved_deck(format_name, deck_name, outputs_root=ROOT / "outputs"):
    """Move a saved deck bundle to a recoverable trash directory."""
    if format_name not in ("commander", "pauper", "standard", "legacy"):
        raise ValueError("Unsupported deck format.")
    format_dir = (Path(outputs_root) / format_name).resolve()
    if not format_dir.is_dir():
        raise LookupError("Saved deck not found.")
    deck_path = None
    for path in format_dir.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if value.get("name") == deck_name and value.get("format") == format_name and "main" in value:
            deck_path = path.resolve()
            break
    if not deck_path or deck_path.parent != format_dir:
        raise LookupError("Saved deck not found.")
    slug = deck_path.stem
    bundle = [deck_path, format_dir / (slug + ".dck"), format_dir / (slug + "-report.md")]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    trash_root = (Path(outputs_root) / ".trash").resolve()
    destination = (trash_root / (stamp + "-" + slug)).resolve()
    if trash_root not in destination.parents:
        raise ValueError("Unsafe trash destination.")
    destination.mkdir(parents=True, exist_ok=False)
    moved = []
    for path in bundle:
        path = path.resolve()
        if path.parent == format_dir and path.is_file():
            target = destination / path.name
            shutil.move(str(path), str(target))
            moved.append(str(target))
    return {"name": deck_name, "format": format_name, "trash": str(destination), "moved": moved}


def make_handler(payload, architect=None, engine_cards=None, auth_username="", auth_password="", chat=None,
                 import_store=None, collection_csv=None, bulk_path=None, cache_dir=DEFAULT_CACHE):
    import_store = import_store or CollectionImportStore(ROOT / "outputs" / "collection_imports.json")
    collection_csv = Path(collection_csv or DEFAULT_COLLECTION)
    bulk_path = Path(bulk_path) if bulk_path else choose_bulk_file(cache_dir)
    forge_dir = Path(os.environ.get("MTG_FORGE_DIR") or getattr(architect, "DEFAULT_FORGE", "/forge"))
    simulation_service = ForgeSimulationService(
        architect, engine_cards or [], forge_dir, ROOT / "outputs"
    ) if architect else None
    state = {"payload": payload, "engine_cards": engine_cards, "simulations": simulation_service}
    static_routes = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/hand_tester.js": ("hand_tester.js", "text/javascript; charset=utf-8"),
        "/simulation_stats.js": ("simulation_stats.js", "text/javascript; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    }

    class CollectionHandler(BaseHTTPRequestHandler):
        def refresh_collection(self):
            runtime_csv = materialize_imported_collection(
                collection_csv, import_store.imported_rows(), ROOT / "outputs"
            )
            current_payload = load_collection(runtime_csv, cache_dir)
            current_payload["source"] = collection_csv.name
            current_engine_cards = architect.load_collection(runtime_csv, bulk_path) if architect else []
            state["payload"] = current_payload
            state["engine_cards"] = current_engine_cards
            if simulation_service:
                simulation_service.cards = current_engine_cards

        def current_deck_cards(self, exclude_name=None, exclude_format=None, display=False):
            decks = [deck for deck in self.saved_decks() if not (
                exclude_name and str(deck.get("name") or "").casefold() == str(exclude_name).casefold()
                and (not exclude_format or deck.get("format") == exclude_format)
            )]
            source_cards = state["payload"]["cards"] if display else state["engine_cards"]
            available = annotate_card_availability(source_cards, decks)
            return [dict(card, quantity=card["availability"]["available"]) for card in available]

        def authorized(self):
            if not auth_password:
                return True
            supplied = self.headers.get("Authorization", "")
            try:
                scheme, encoded = supplied.split(" ", 1)
                username, password = base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
            except (ValueError, UnicodeDecodeError):
                username, password, scheme = "", "", ""
            valid = (
                scheme.casefold() == "basic"
                and hmac.compare_digest(username, auth_username)
                and hmac.compare_digest(password, auth_password)
            )
            if not valid:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="MTG Collection", charset="UTF-8"')
                self.send_header("Content-Length", "0")
                self.end_headers()
            return valid

        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def saved_decks(self):
            result = []
            for format_name in ("commander", "pauper", "standard", "legacy"):
                directory = ROOT / "outputs" / format_name
                if not directory.is_dir():
                    continue
                for path in sorted(directory.glob("*.json")):
                    if path.name.endswith("simulation.json") or path.name == "collection-analysis.json":
                        continue
                    try:
                        deck = json.loads(path.read_text(encoding="utf-8"))
                        if all(key in deck for key in ("name", "format", "main")):
                            result.append(deck)
                    except (OSError, ValueError):
                        continue
            result.extend(import_store.scanned_decks())
            return result

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if path != "/healthz" and not self.authorized():
                return
            if path == "/healthz":
                self.send_json(200, {"ok": True})
                return
            if path == "/api/collection":
                current = dict(state["payload"])
                current["cards"] = annotate_card_availability(current.get("cards", []), self.saved_decks())
                self.send_json(200, current)
                return
            if path == "/api/formats":
                values = {}
                for name in ("commander", "pauper", "standard", "legacy"):
                    config = architect.load_format(name)
                    values[name] = {key: config.get(key, 0) for key in (
                        "display_name", "main_size", "sideboard_max", "sideboard_target",
                        "main_min_size", "copy_limit", "commander_min", "commander_max"
                    )}
                self.send_json(200, values)
                return
            if path == "/api/decks":
                decks = self.saved_decks()
                for deck in decks:
                    deck["compatible_formats"] = deck_compatible_formats(
                        deck, state.get("engine_cards", []), architect
                    ) if architect else []
                self.send_json(200, decks)
                return
            if path == "/api/chat/status":
                self.send_json(200, chat.status() if chat else {"available": False, "message": "Codex chat is not configured."})
                return
            if path == "/api/chat/models":
                if not chat:
                    self.send_json(503, {"error": "Codex chat is not configured on this server."})
                    return
                try:
                    self.send_json(200, {"models": chat.model_options()})
                except (RuntimeError, TimeoutError, OSError) as error:
                    self.send_json(502, {"error": "Codex could not load its model list: {}".format(error), "models": []})
                return
            if path == "/api/simulations/config":
                formats = {}
                for name in ("commander", "pauper", "standard", "legacy"):
                    config = architect.load_format(name)
                    formats[name] = {
                        "players": config["simulation"]["players"],
                        "opponents": config["simulation"]["players"] - 1,
                        "gameType": config["simulation"]["forge_game_type"],
                    }
                self.send_json(200, {"available": bool(simulation_service and simulation_service.available()),
                                     "formats": formats, "maxGames": 10})
                return
            if path.startswith("/api/simulations/"):
                job_id = path.rsplit("/", 1)[-1]
                result = simulation_service.get(job_id) if simulation_service else None
                if result is None:
                    self.send_json(404, {"error": "Simulation job not found."})
                else:
                    self.send_json(200, result)
                return
            route = static_routes.get(path)
            if route:
                body = (WEB_ROOT / route[0]).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", route[1])
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404, "Not found")

        def do_POST(self):
            path = urlparse(self.path).path
            if not self.authorized():
                return
            if path not in ("/api/decks", "/api/decks/validate", "/api/decks/analysis",
                            "/api/chat", "/api/import", "/api/simulations"):
                self.send_error(404, "Not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limits = {"/api/chat": 200_000, "/api/decks": 2_000_000,
                          "/api/decks/validate": 2_000_000,
                          "/api/decks/analysis": 2_000_000,
                          "/api/import": 20_000_000, "/api/simulations": 100_000}
                if length <= 0 or length > limits[path]:
                    raise ValueError("Invalid request size.")
                submitted = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == "/api/decks/validate":
                    if not architect:
                        self.send_json(503, {"error": "Deck architect is not configured."})
                        return
                    deck = deck_from_submission(submitted)
                    validation = validate_deck_allocations(
                        deck, state.get("engine_cards", []), self.saved_decks(), architect
                    )
                    self.send_json(200, {"validation": validation})
                    return
                if path == "/api/decks/analysis":
                    if not architect:
                        self.send_json(503, {"error": "Deck architect is not configured."})
                        return
                    name = str(submitted.get("name") or "").strip()
                    format_name = str(submitted.get("format") or "").casefold()
                    deck = next((item for item in self.saved_decks()
                                 if item.get("name") == name and item.get("format") == format_name
                                 and format_name in ("commander", "pauper", "standard", "legacy")), None)
                    if not deck:
                        self.send_json(404, {"error": "Saved deck not found."})
                        return
                    report = submitted.get("report")
                    if not isinstance(report, dict):
                        raise ValueError("A deck report object is required.")
                    deck["report"] = report
                    for field in ("forge_evidence", "commander_research",
                                  "commander_candidate_comparison", "final_verdict", "analysis_status"):
                        value = submitted.get(field)
                        if isinstance(value, dict) or (field == "analysis_status" and isinstance(value, str)):
                            deck[field] = value
                    history = submitted.get("optimization_history")
                    if isinstance(history, list):
                        deck["optimization_history"] = history
                    parent = submitted.get("parent")
                    if isinstance(parent, str) and parent.strip():
                        deck["parent"] = parent.strip()[:100]
                    files = architect.save_bundle(deck, state.get("engine_cards", []), ROOT / "outputs")
                    self.send_json(200, {"deck": deck, "files": files})
                    return
                if path == "/api/simulations":
                    if not simulation_service:
                        self.send_json(503, {"error": "Deck architect is not configured."})
                        return
                    if not simulation_service.available():
                        self.send_json(503, {"error": "Forge is not available. Check the Java runtime and mounted Forge installation."})
                        return
                    format_name = str(submitted.get("format") or "").casefold()
                    if submitted.get("mode") == "battle":
                        if format_name not in ("pauper", "standard", "legacy"):
                            raise ValueError("Deck battles support Pauper, Standard, and Legacy; Commander uses four-player simulations.")
                        saved = self.saved_decks()
                        deck_a_name = str(submitted.get("deckA") or "")
                        deck_b_name = str(submitted.get("deckB") or "")
                        if not deck_a_name or not deck_b_name or deck_a_name.casefold() == deck_b_name.casefold():
                            raise ValueError("Choose two different saved decks for the battle.")
                        deck_a = next((item for item in saved if item.get("name") == deck_a_name
                                       and item.get("format") == format_name), None)
                        deck_b = next((item for item in saved if item.get("name") == deck_b_name
                                       and item.get("format") == format_name), None)
                        if not deck_a or not deck_b:
                            raise ValueError("Choose two saved {} decks.".format(format_name.title()))
                        try:
                            job = simulation_service.start(deck_a, [deck_b], submitted.get("games", 10))
                        except SimulationBusyError as error:
                            self.send_json(409, {"error": str(error)})
                            return
                        self.send_json(202, job)
                        return
                    deck_name = str(submitted.get("deckName") or "")
                    saved = self.saved_decks()
                    deck = next((item for item in saved if item.get("name") == deck_name
                                 and item.get("format") == format_name), None)
                    if not deck:
                        raise ValueError("Choose a saved deck to simulate.")
                    names = submitted.get("opponents")
                    if not isinstance(names, list):
                        raise ValueError("Choose same-format opponent decks.")
                    opponents = []
                    for name in names:
                        found = next((item for item in saved if item.get("name") == str(name)
                                      and item.get("format") == format_name and item is not deck), None)
                        if not found:
                            raise ValueError("Opponent '{}' is not a saved {} deck.".format(name, format_name.title()))
                        opponents.append(found)
                    try:
                        job = simulation_service.start(deck, opponents, submitted.get("games", 10))
                    except SimulationBusyError as error:
                        self.send_json(409, {"error": str(error)})
                        return
                    self.send_json(202, job)
                    return
                if path == "/api/import":
                    encoded = submitted.get("csvBase64")
                    if not isinstance(encoded, str) or len(encoded) > 19_000_000:
                        raise ValueError("Choose a ManaBox CSV smaller than 14 MB.")
                    try:
                        csv_bytes = base64.b64decode(encoded, validate=True)
                    except (ValueError, base64.binascii.Error) as error:
                        raise ValueError("The uploaded CSV data is invalid.") from error
                    prepared = prepare_upload(
                        csv_bytes, bulk_path, submitted.get("mode"), submitted.get("deckName")
                    )
                    if prepared["mode"] == "deck" and not prepared["can_create_deck"]:
                        self.send_json(422, {
                            "error": "Deck scan was not saved because one or more cards could not be matched.",
                            "summary": prepared["summary"], "unmatched": prepared["unmatched"],
                        })
                        return
                    result = import_store.add(prepared)
                    if result["added"]:
                        self.refresh_collection()
                    self.send_json(201 if result["added"] else 200, {
                        "added": result["added"], "mode": prepared["mode"],
                        "summary": prepared["summary"], "unmatched": prepared["unmatched"],
                        "deck": prepared.get("deck"),
                        "message": ("Imported into collection." if prepared["mode"] == "bulk"
                                    else "Deck scan imported and card copies reserved."),
                    })
                    return
                if path == "/api/chat":
                    if not chat:
                        self.send_json(503, {"error": "Codex chat is not configured on this server."})
                        return
                    model = str(submitted.get("model") or "").strip()
                    if model and not any(item.get("id") == model for item in chat.model_options()):
                        raise ValueError("Choose a model from the available Codex model list.")
                    pending_deck_request = (
                        chat.pending_deck_creation_request(submitted)
                        if hasattr(chat, "pending_deck_creation_request") else None
                    )
                    if pending_deck_request:
                        submitted = dict(submitted, message=pending_deck_request)
                    if hasattr(chat, "is_deck_creation_request") and chat.is_deck_creation_request(submitted):
                        created = chat.create_deck(
                            submitted, self.saved_decks(), self.current_deck_cards(display=True), architect,
                            state["engine_cards"], model=model
                        )
                        if created.get("clarification"):
                            self.send_json(200, {"answer": created["clarification"]})
                            return
                        deck = created["deck"]
                        validation = validate_deck_allocations(
                            deck, state["engine_cards"], self.saved_decks(), architect
                        )
                        if any(item.get("shortage") for item in validation.get("allocation_report", [])):
                            deck["ownership_mode"] = "proposal"
                            validation = validate_deck_allocations(
                                deck, state["engine_cards"], self.saved_decks(), architect
                            )
                        deck["validation"] = validation
                        self.send_json(200, {"answer": created["answer"], "deckDraft": deck})
                        return
                    answer = chat.ask(submitted, self.saved_decks(), state["payload"]["cards"], model=model)
                    self.send_json(200, {"answer": answer})
                    return
                deck = deck_from_submission(submitted)
                validation = validate_deck_allocations(
                    deck, state.get("engine_cards", []), self.saved_decks(), architect
                )
                if any(item.get("shortage") for item in validation.get("allocation_report", [])):
                    # An incomplete list can still be saved for planning, but must not reserve
                    # more owned copies or block other decks from using the collection.
                    deck["ownership_mode"] = "proposal"
                    validation = validate_deck_allocations(
                        deck, state.get("engine_cards", []), self.saved_decks(), architect
                    )
                deck["validation"] = validation
                submitted_report = submitted.get("report")
                if isinstance(submitted_report, dict):
                    deck["report"] = submitted_report
                else:
                    if not deck.get("colors"):
                        by_name = {str(card.get("name") or "").casefold(): card
                                   for card in state["engine_cards"]}
                        identity = {
                            color
                            for section in ("main", "commanders")
                            for name in (deck.get(section) or {})
                            for color in by_name.get(str(name).casefold(), {}).get("color_identity", [])
                        }
                        deck["colors"] = [color for color in "WUBRG" if color in identity]
                    deck["report"] = architect.deck_report(deck, state["engine_cards"])
                source_scanned_name = deck.pop("source_scanned_name", "")
                files = architect.save_bundle(deck, state["engine_cards"], ROOT / "outputs")
                if source_scanned_name:
                    import_store.remove_scanned_deck(source_scanned_name)
                self.send_json(201, {"deck": deck, "validation": validation, "files": files})
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except (RuntimeError, TimeoutError, OSError) as error:
                self.send_json(502, {"error": str(error)})
            except Exception:
                traceback.print_exc()
                try:
                    self.send_json(500, {"error": "The request failed unexpectedly. The server logged the error; please retry."})
                except OSError:
                    pass

        def do_DELETE(self):
            if not self.authorized():
                return
            if urlparse(self.path).path != "/api/decks":
                self.send_error(404, "Not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 100_000:
                    raise ValueError("Invalid request size.")
                submitted = json.loads(self.rfile.read(length).decode("utf-8"))
                format_name = str(submitted.get("format") or "").lower()
                result = (import_store.remove_scanned_deck(str(submitted.get("name") or ""))
                          if format_name == "scanned" else archive_saved_deck(format_name, str(submitted.get("name") or "")))
                self.send_json(200, result)
            except LookupError as error:
                self.send_json(404, {"error": str(error)})
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self.send_json(400, {"error": str(error)})

        def log_message(self, format_string, *args):
            print("{} - {}".format(self.address_string(), format_string % args))

    return CollectionHandler


def main():
    parser = argparse.ArgumentParser(description="Browse an enriched MTG collection in a local web interface.")
    parser.add_argument("--csv", help="Enriched CSV path (default: MTG_Collection_Enriched.csv)")
    parser.add_argument("--host", default="127.0.0.1", help="Listening host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Listening port (default: 8000)")
    args = parser.parse_args()
    try:
        csv_path = choose_collection(args.csv)
        import_store = CollectionImportStore(ROOT / "outputs" / "collection_imports.json")
        bulk_path = choose_bulk_file(DEFAULT_CACHE)
        runtime_csv = materialize_imported_collection(
            csv_path, import_store.imported_rows(), ROOT / "outputs"
        )
        payload = load_collection(runtime_csv)
        payload["source"] = csv_path.name
        architect = load_architect()
        engine_cards = architect.load_collection(runtime_csv, bulk_path)
        from codex_chat import CodexChat
        chat = CodexChat.from_environment()
        username = os.environ.get("MTG_WEB_USERNAME", "")
        password = os.environ.get("MTG_WEB_PASSWORD", "")
        server = ThreadingHTTPServer((args.host, args.port), make_handler(
            payload, architect, engine_cards, username, password, chat,
            import_store=import_store, collection_csv=csv_path, bulk_path=bulk_path,
        ))
        print("Loaded {:,} cards from {:,} rows / {:,} copies in {}".format(
            payload["summary"]["cards"], payload["summary"]["rows"],
            payload["summary"]["copies"], csv_path.name
        ))
        print("Open http://{}:{}/ in your browser (Ctrl+C to stop)".format(args.host, args.port))
        server.serve_forever()
    except (ValueError, OSError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
