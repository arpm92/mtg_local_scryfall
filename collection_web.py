#!/usr/bin/env python3
"""Serve a local, Scryfall-style browser for an enriched collection CSV."""

import argparse
import base64
import hmac
import importlib.util
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from enrich_mtg_collection import iter_oracle_cards, read_csv


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
    """Return exact-printing image endpoints for a valid Scryfall card UUID."""
    card_id = str(scryfall_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", card_id):
        return "", ""
    base = "https://api.scryfall.com/cards/{}?format=image&version=normal".format(card_id)
    return base, base + "&face=back"


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
            "scryfallUrl": row.get("Scryfall URL", ""),
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
    if format_name not in ("commander", "pauper", "standard"):
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


def make_handler(payload, architect=None, engine_cards=None, auth_username="", auth_password="", chat=None):
    api_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    static_routes = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    }

    class CollectionHandler(BaseHTTPRequestHandler):
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
            for format_name in ("commander", "pauper", "standard"):
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
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(api_body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(api_body)
                return
            if path == "/api/formats":
                values = {}
                for name in ("commander", "pauper", "standard"):
                    config = architect.load_format(name)
                    values[name] = {key: config.get(key, 0) for key in (
                        "display_name", "main_size", "sideboard_max", "sideboard_target",
                        "copy_limit", "commander_min", "commander_max"
                    )}
                self.send_json(200, values)
                return
            if path == "/api/decks":
                self.send_json(200, self.saved_decks())
                return
            if path == "/api/chat/status":
                self.send_json(200, chat.status() if chat else {"available": False, "message": "Codex chat is not configured."})
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
            if path not in ("/api/decks", "/api/chat"):
                self.send_error(404, "Not found")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > (200_000 if path == "/api/chat" else 2_000_000):
                    raise ValueError("Invalid request size.")
                submitted = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == "/api/chat":
                    if not chat:
                        self.send_json(503, {"error": "Codex chat is not configured on this server."})
                        return
                    if hasattr(chat, "is_deck_creation_request") and chat.is_deck_creation_request(submitted):
                        created = chat.create_deck(submitted, self.saved_decks(), payload["cards"], architect, engine_cards)
                        if created.get("clarification"):
                            self.send_json(200, {"answer": created["clarification"]})
                            return
                        deck = created["deck"]
                        validation = architect.validate_deck(deck, engine_cards, deck["format"])
                        if not validation["valid"]:
                            self.send_json(422, {"error": "Generated deck failed validation; it was not saved.", "validation": validation})
                            return
                        deck["validation"] = validation
                        files = architect.save_bundle(deck, engine_cards, ROOT / "outputs")
                        self.send_json(201, {"answer": created["answer"], "deck": deck, "files": files})
                        return
                    answer = chat.ask(submitted, self.saved_decks(), payload["cards"])
                    self.send_json(200, {"answer": answer})
                    return
                format_name = str(submitted.get("format") or "").lower()
                if format_name not in ("commander", "pauper", "standard"):
                    raise ValueError("Choose Commander, Pauper, or Standard.")
                name = str(submitted.get("name") or "Untitled Deck").strip()[:100]
                deck = {
                    "name": name, "format": format_name,
                    "main": submitted.get("main") or {},
                    "sideboard": submitted.get("sideboard") or {},
                    "commanders": submitted.get("commanders") or {},
                    "archetype": str(submitted.get("archetype") or "Website build")[:100],
                    "power_target": str(submitted.get("power_target") or "")[:100],
                }
                for section in ("main", "sideboard", "commanders"):
                    if not isinstance(deck[section], dict):
                        raise ValueError("Deck sections must be card/count objects.")
                    deck[section] = {str(card): int(count) for card, count in deck[section].items() if int(count) > 0}
                validation = architect.validate_deck(deck, engine_cards)
                deck["validation"] = validation
                deck["report"] = architect.deck_report(deck, engine_cards)
                if not validation["valid"]:
                    self.send_json(400, {"error": "Deck is not valid.", "validation": validation})
                    return
                files = architect.save_bundle(deck, engine_cards, ROOT / "outputs")
                self.send_json(201, {"deck": deck, "files": files})
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except (RuntimeError, TimeoutError, OSError) as error:
                self.send_json(502, {"error": str(error)})

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
                result = archive_saved_deck(
                    str(submitted.get("format") or "").lower(),
                    str(submitted.get("name") or ""),
                )
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
        payload = load_collection(csv_path)
        architect = load_architect()
        engine_cards = architect.load_collection(csv_path, choose_bulk_file(DEFAULT_CACHE))
        from codex_chat import CodexChat
        chat = CodexChat.from_environment()
        username = os.environ.get("MTG_WEB_USERNAME", "")
        password = os.environ.get("MTG_WEB_PASSWORD", "")
        server = ThreadingHTTPServer((args.host, args.port), make_handler(
            payload, architect, engine_cards, username, password, chat
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
