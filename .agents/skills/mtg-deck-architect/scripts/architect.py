#!/usr/bin/env python3
"""Format-aware MTG collection analysis, deck building, validation and Forge simulation."""

import argparse
import csv
import gzip
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_ROOT.parents[2]
FORMATS_DIR = SKILL_ROOT / "formats"
DEFAULT_COLLECTION = PROJECT_ROOT / "MTG_Collection_Enriched.csv"
DEFAULT_BULK = PROJECT_ROOT / ".scryfall_cache" / "oracle_cards.jsonl.gz"
DEFAULT_FORGE = Path(r"E:\MTG-Forge\forge-installer-2.0.16-SNAPSHOT")
FORGE_JAR = "forge-gui-desktop-2.0.16-SNAPSHOT-jar-with-dependencies.jar"
BASIC_LANDS = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G", "Wastes": "C"}
COLOR_ORDER = "WUBRG"


def load_format(name):
    path = FORMATS_DIR / (str(name).lower() + ".yaml")
    if not path.is_file():
        available = ", ".join(sorted(item.stem for item in FORMATS_DIR.glob("*.yaml")))
        raise ValueError("Unsupported format '{}'. Available: {}".format(name, available))
    return json.loads(path.read_text(encoding="utf-8"))


def iter_bulk(path):
    if path.name.endswith(".jsonl.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    else:
        with path.open("r", encoding="utf-8") as handle:
            yield from json.load(handle)


def _split_colors(value):
    if not value or str(value).casefold() == "colorless":
        return []
    return [color for color in COLOR_ORDER if color in str(value).upper()]


def load_collection(csv_path=DEFAULT_COLLECTION, bulk_path=DEFAULT_BULK):
    csv_path, bulk_path = Path(csv_path), Path(bulk_path)
    if not csv_path.is_file():
        raise ValueError("Enriched collection not found: {}".format(csv_path))
    if not bulk_path.is_file():
        raise ValueError("Scryfall bulk cache not found: {}".format(bulk_path))
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    wanted = {str(row.get("Scryfall Oracle ID") or "").lower() for row in rows}
    oracle = {}
    for card in iter_bulk(bulk_path):
        oid = str(card.get("oracle_id") or "").lower()
        if oid in wanted and oid not in oracle:
            oracle[oid] = card
    grouped = {}
    for row in rows:
        oid = str(row.get("Scryfall Oracle ID") or "").lower()
        source = oracle.get(oid, {})
        name = row.get("Oracle Name") or row.get("Name")
        key = name.casefold()
        if key not in grouped:
            grouped[key] = {
                "name": name, "oracle_id": oid, "quantity": 0,
                "type_line": row.get("Type Line", ""), "oracle_text": row.get("Oracle Text", ""),
                "mana_cost": row.get("Mana Cost", ""), "mana_value": float(row.get("Mana Value") or 0),
                "colors": _split_colors(row.get("Colors")),
                "color_identity": _split_colors(row.get("Color Identity")),
                "keywords": [item.strip() for item in row.get("Keywords", "").split(",") if item.strip()],
                "produced_mana": _split_colors(row.get("Produced Mana")),
                "legalities": source.get("legalities", {}), "layout": source.get("layout", ""),
                "power": source.get("power", ""), "toughness": source.get("toughness", ""),
                "rarity": row.get("Rarity", ""), "printings": [],
            }
        card = grouped[key]
        try:
            qty = int(row.get("Quantity") or 1)
        except ValueError:
            qty = 1
        card["quantity"] += qty
        card["printings"].append({
            "quantity": qty, "set": row.get("Set code", ""),
            "collector": row.get("Collector number", ""), "scryfall_id": row.get("Scryfall ID", ""),
            "rarity": row.get("Rarity", ""), "foil": row.get("Foil", ""),
        })
    return list(grouped.values())


def is_basic(card):
    return card["name"] in BASIC_LANDS and "Basic Land" in card.get("type_line", "")


def is_legal(card, config):
    if is_basic(card):
        return True
    return card.get("legalities", {}).get(config["legality_field"]) == "legal"


def is_commander(card):
    text = card.get("oracle_text", "").casefold()
    return ("Legendary Creature" in card.get("type_line", "") or "can be your commander" in text)


def _allows_extra_copies(card):
    text = card.get("oracle_text", "").casefold()
    return "a deck can have any number of cards named" in text or "a deck can have up to" in text


def _roles(card):
    text = card.get("oracle_text", "").casefold()
    type_line = card.get("type_line", "").casefold()
    roles = []
    patterns = {
        "card advantage": ("draw", "look at the top", "return target card"),
        "interaction": ("destroy target", "exile target", "counter target", "deals ", "-x/-x", "tap target"),
        "protection": ("hexproof", "indestructible", "protection from", "phase out"),
        "ramp/fixing": ("add {", "search your library for a land", "treasure token"),
        "recovery": ("return target", "from your graveyard", "regenerate"),
        "finisher": ("you win the game", "double strike", "trample", "each opponent loses", "can't be blocked"),
    }
    for role, needles in patterns.items():
        if any(needle in text for needle in needles):
            roles.append(role)
    if "land" in type_line:
        roles.append("mana")
    if "creature" in type_line:
        roles.append("threat")
    return roles


def analyze_collection(format_name, cards=None, csv_path=DEFAULT_COLLECTION, bulk_path=DEFAULT_BULK):
    config = load_format(format_name)
    cards = cards or load_collection(csv_path, bulk_path)
    legal = [card for card in cards if is_legal(card, config)]
    colors = Counter(color for card in legal for color in card["color_identity"])
    curve = Counter(int(min(card["mana_value"], 7)) for card in legal if "Land" not in card["type_line"])
    types = Counter()
    for card in legal:
        line = card["type_line"]
        for name in ("Creature", "Instant", "Sorcery", "Artifact", "Enchantment", "Planeswalker", "Land", "Battle"):
            if name in line:
                types[name.lower()] += 1
    roles = Counter(role for card in legal for role in _roles(card))
    shortages = sum(1 for card in legal if not is_basic(card) and card["quantity"] < config["copy_limit"])
    return {
        "format": format_name, "distinct_legal": len(legal),
        "available_copies": sum(card["quantity"] for card in legal),
        "types": dict(types), "colors": dict(colors), "mana_curve": dict(sorted(curve.items())),
        "role_signals": dict(roles), "quantity_shortages_for_max_playset": shortages,
        "commander_candidates": [card["name"] for card in legal if is_commander(card)] if format_name == "commander" else [],
        "constraints": (["Singleton and color identity apply."] if format_name == "commander" else
                        ["Owned quantities and four-copy combined main/sideboard limit apply."]),
    }


def _valid_commander_pair(cards):
    if len(cards) == 1:
        return True
    if len(cards) != 2:
        return False
    texts = [card.get("oracle_text", "").casefold() for card in cards]
    types = [card.get("type_line", "").casefold() for card in cards]
    partner = all("partner" in text for text in texts)
    background = (("choose a background" in texts[0] and "background" in types[1]) or
                  ("choose a background" in texts[1] and "background" in types[0]))
    doctor = (("doctor's companion" in texts[0] and "doctor" in types[1]) or
              ("doctor's companion" in texts[1] and "doctor" in types[0]))
    return partner or background or doctor


def validate_deck(deck, cards, format_name=None, config=None, respect_ownership=True):
    format_name = format_name or deck.get("format")
    config = config or load_format(format_name)
    by_name = {card["name"].casefold(): card for card in cards}
    errors, warnings = [], []
    sections = {section: deck.get(section, {}) for section in ("main", "sideboard", "commanders")}
    main_count = sum(sections["main"].values())
    side_count = sum(sections["sideboard"].values())
    commander_count = sum(sections["commanders"].values())
    if config.get("main_min_size") is not None:
        if main_count < config["main_min_size"]:
            errors.append("Main deck has {} cards; {} requires a minimum of {}.".format(
                main_count, config["display_name"], config["main_min_size"]))
    elif main_count != config["main_size"]:
        errors.append("Main deck has {} cards; {} requires exactly {}.".format(main_count, config["display_name"], config["main_size"]))
    if side_count > config["sideboard_max"]:
        errors.append("Sideboard has {} cards; maximum is {}.".format(side_count, config["sideboard_max"]))
    if format_name == "commander" and not (config["commander_min"] <= commander_count <= config["commander_max"]):
        errors.append("Commander section must contain one valid commander or a valid pair.")
    all_counts = Counter()
    for section in sections.values():
        all_counts.update(section)
    resolved = {}
    for name, count in all_counts.items():
        card = by_name.get(name.casefold())
        if not card:
            errors.append("Unknown card: {}.".format(name))
            continue
        resolved[name] = card
        if not is_legal(card, config):
            errors.append("{} is not currently {}-legal.".format(name, config["display_name"]))
        limit = config["copy_limit"]
        if count > limit and not is_basic(card) and not _allows_extra_copies(card):
            errors.append("{} has {} copies across deck sections; limit is {}.".format(name, count, limit))
        if respect_ownership and not is_basic(card) and count > card["quantity"]:
            errors.append("{} needs {} copies but only {} are owned.".format(name, count, card["quantity"]))
    if format_name == "commander":
        commander_cards = [by_name.get(name.casefold()) for name in sections["commanders"]]
        commander_cards = [card for card in commander_cards if card]
        if any(not is_commander(card) for card in commander_cards):
            errors.append("Commander section contains a card that cannot be a commander.")
        if commander_cards and not _valid_commander_pair(commander_cards):
            errors.append("The selected commanders do not form a supported legal pair.")
        allowed = set(color for card in commander_cards for color in card["color_identity"])
        for name, card in resolved.items():
            if not set(card["color_identity"]).issubset(allowed):
                errors.append("{} has color identity {} outside commander identity {}.".format(
                    name, "".join(card["color_identity"]) or "C", "".join(sorted(allowed)) or "C"))
    if side_count < config.get("sideboard_target", 0):
        warnings.append("Sideboard has {} cards; competitive target is {}.".format(side_count, config["sideboard_target"]))
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "counts": {"main": main_count, "sideboard": side_count, "commanders": commander_count}}


def _score(card, archetype=None, power_target=None):
    roles = _roles(card)
    score = 12 - min(card["mana_value"], 10)
    score += 3 * len(roles)
    score += min(card["quantity"], 4)
    haystack = (card["type_line"] + " " + card["oracle_text"] + " " + " ".join(card["keywords"])).casefold()
    if archetype:
        for term in re.split(r"[\s,+/-]+", archetype.casefold()):
            if term and term in haystack:
                score += 8
    if power_target and str(power_target).casefold() in ("high", "competitive", "optimized"):
        score += 2 if card["mana_value"] <= 3 else 0
    return score


def _choose_colors(cards, requested=None):
    if requested:
        if isinstance(requested, str):
            return [color for color in COLOR_ORDER if color in requested.upper()]
        return [color for color in COLOR_ORDER if color in requested]
    scores = Counter(color for card in cards for color in card["color_identity"] for _ in range(max(1, card["quantity"])))
    return [color for color, _ in scores.most_common(2)] or ["W"]


def _add_basics(main, colors, amount):
    names = [name for name, color in BASIC_LANDS.items() if color in colors]
    names = names or ["Wastes"]
    for index in range(amount):
        name = names[index % len(names)]
        main[name] = main.get(name, 0) + 1


def _build_sixty(config, cards, name, colors, archetype, power_target, constraints):
    legal = [card for card in cards if is_legal(card, config) and set(card["color_identity"]).issubset(colors)]
    nonlands = sorted((card for card in legal if "Land" not in card["type_line"]), key=lambda c: _score(c, archetype, power_target), reverse=True)
    land_target = int((constraints or {}).get("land_count", config["default_land_count"]))
    if archetype and "aggro" in archetype.casefold():
        land_target = min(land_target, 21)
    if archetype and "control" in archetype.casefold():
        land_target = max(land_target, 25)
    main, used = {}, Counter()
    target_spells = config["main_size"] - land_target
    cursor = 0
    while sum(main.values()) < target_spells and nonlands:
        card = nonlands[cursor % len(nonlands)]
        allowed = min(config["copy_limit"], card["quantity"])
        if used[card["name"]] < allowed:
            main[card["name"]] = main.get(card["name"], 0) + 1
            used[card["name"]] += 1
        cursor += 1
        if cursor > len(nonlands) * config["copy_limit"] + 20:
            break
    if sum(main.values()) < target_spells:
        raise ValueError("Collection has only {} usable {} spell copies for the requested colors; {} are required.".format(sum(main.values()), config["display_name"], target_spells))
    owned_lands = sorted((card for card in legal if "Land" in card["type_line"] and not is_basic(card)), key=lambda c: _score(c), reverse=True)
    for card in owned_lands:
        if sum(main.values()) >= config["main_size"]:
            break
        count = min(card["quantity"], config["copy_limit"], config["main_size"] - sum(main.values()))
        main[card["name"]] = count
        used[card["name"]] += count
    _add_basics(main, colors, config["main_size"] - sum(main.values()))
    sideboard = {}
    for card in nonlands:
        available = min(config["copy_limit"], card["quantity"]) - used[card["name"]]
        if available <= 0:
            continue
        add = min(available, config["sideboard_target"] - sum(sideboard.values()))
        if add:
            sideboard[card["name"]] = add
            used[card["name"]] += add
        if sum(sideboard.values()) == config["sideboard_target"]:
            break
    return {"name": name, "format": config["name"], "main": main, "sideboard": sideboard, "commanders": {},
            "colors": sorted(colors), "archetype": archetype or "collection midrange", "power_target": power_target or "best available"}


def _build_commander(config, cards, name, colors, archetype, power_target, constraints, excluded_commanders):
    candidates = [card for card in cards if is_legal(card, config) and is_commander(card) and card["name"] not in excluded_commanders]
    requested_commander = (constraints or {}).get("commander")
    if requested_commander:
        candidates = [card for card in candidates if card["name"].casefold() == str(requested_commander).casefold()]
    if colors:
        candidates = [card for card in candidates if set(card["color_identity"]) == set(colors)] or [card for card in candidates if set(card["color_identity"]).issubset(colors)]
    if not candidates:
        raise ValueError("No owned legal commander supports the requested colors.")
    commander = max(candidates, key=lambda c: _score(c, archetype, power_target) + len(c["color_identity"]) * 3)
    identity = set(commander["color_identity"])
    legal = [card for card in cards if card["name"] != commander["name"] and is_legal(card, config)
             and set(card["color_identity"]).issubset(identity)]
    land_target = int((constraints or {}).get("land_count", config["default_land_count"]))
    nonlands = sorted((card for card in legal if "Land" not in card["type_line"]), key=lambda c: _score(c, archetype, power_target), reverse=True)
    main = {card["name"]: 1 for card in nonlands[:config["main_size"] - land_target]}
    lands = sorted((card for card in legal if "Land" in card["type_line"] and not is_basic(card)), key=lambda c: _score(c), reverse=True)
    for card in lands:
        if sum(main.values()) >= config["main_size"]:
            break
        main[card["name"]] = 1
    _add_basics(main, identity, config["main_size"] - sum(main.values()))
    return {"name": name, "format": "commander", "main": main, "sideboard": {},
            "commanders": {commander["name"]: 1}, "colors": sorted(identity),
            "archetype": archetype or "multiplayer value", "power_target": power_target or "Bracket 2-3"}


def build_decks(format, count=1, colors=None, archetype=None, power_target=None, constraints=None,
                cards=None, csv_path=DEFAULT_COLLECTION, bulk_path=DEFAULT_BULK):
    config = load_format(format)
    cards = cards or load_collection(csv_path, bulk_path)
    legal = [card for card in cards if is_legal(card, config)]
    chosen_colors = _choose_colors(legal, colors)
    decks, excluded = [], set()
    for index in range(int(count)):
        title = "{} {} {}".format(config["display_name"], archetype or "Collection", index + 1)
        if format == "commander":
            deck = _build_commander(config, cards, title, chosen_colors if colors else None, archetype, power_target, constraints, excluded)
            excluded.update(deck["commanders"])
        else:
            if colors or len(chosen_colors) == 1 or index % 3 == 0:
                variation = chosen_colors
            else:
                variation = [chosen_colors[(index - 1) % len(chosen_colors)]]
            deck = _build_sixty(config, cards, title, set(variation), archetype, power_target, constraints)
        validation = validate_deck(deck, cards, format, config)
        deck["validation"] = validation
        deck["report"] = deck_report(deck, cards, config)
        decks.append(deck)
    return decks


def deck_report(deck, cards, config=None):
    config = config or load_format(deck["format"])
    by_name = {card["name"]: card for card in cards}
    selected = [(by_name[name], count) for section in ("main", "commanders") for name, count in deck.get(section, {}).items() if name in by_name]
    roles = defaultdict(list)
    curve = Counter()
    for card, count in selected:
        if "Land" not in card["type_line"]:
            curve[int(min(card["mana_value"], 7))] += count
        for role in _roles(card):
            roles[role].append(card["name"])
    large_threats = [card["name"] for card, _ in selected
                     if str(card.get("power", "")).isdigit() and float(card["power"]) >= 5]
    wins = list(dict.fromkeys(roles.get("finisher", []) + large_threats))
    limitations = []
    if len(wins) < 2:
        limitations.append("The owned pool does not expose two clearly distinct heuristic win conditions; review manually.")
    report = {
        "identity": "".join(deck.get("colors", [])) or "Colorless", "archetype": deck.get("archetype"),
        "primary_objective": "Develop mana, establish the deck's synergy engine, interact efficiently, and convert card advantage into a finish.",
        "early_game": "Develop mana and low-cost enablers while preserving interaction.",
        "middle_game": "Assemble value engines, protect key pieces, and disrupt opposing engines.",
        "late_game": "Use resilient threats and accumulated advantage to close the game.",
        "synergy_engine": (roles.get("card advantage", []) + roles.get("ramp/fixing", []))[:8],
        "win_conditions": wins[:6], "interaction": roles.get("interaction", [])[:12],
        "protection": roles.get("protection", [])[:8], "recovery": roles.get("recovery", [])[:8],
        "mana_curve": dict(sorted(curve.items())),
        "land_count": sum(count for name, count in deck["main"].items() if name in by_name and "Land" in by_name[name]["type_line"]),
        "weaknesses": ["Card evaluation is collection-relative and should be reviewed against the intended metagame."] + limitations,
        "sideboard_strategy": ("Use interaction packages according to opponent speed, graveyard use, artifacts/enchantments, and threat profile."
                               if config["sideboard_max"] else "Commander has no conventional sideboard."),
    }
    if deck["format"] == "commander":
        report["commander_bracket"] = classify_commander_bracket(deck)
    return report


def classify_commander_bracket(deck, forge_dir=DEFAULT_FORGE):
    """Conservative advisory classification; never labels a deck Exhibition or cEDH automatically."""
    path = Path(forge_dir) / "res" / "lists" / "gamechangers.txt"
    game_changers = set()
    if path.is_file():
        game_changers = {line.strip().casefold() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()}
    names = set(name.casefold() for section in ("main", "commanders") for name in deck.get(section, {}))
    found = sorted(name for name in names if name in game_changers)
    bracket = 4 if len(found) > 3 else (3 if found else 2)
    return {"advisory_bracket": bracket, "game_changers": found,
            "note": "Manual review is still required for tutors, early combos, extra turns, mass land denial, and table intent."}


def _forge_line(name, count, by_name):
    card = by_name.get(name.casefold())
    forge_name = name
    if card and card.get("layout") not in (None, "", "normal") and " // " in name:
        forge_name = name.split(" // ", 1)[0]
    if card and card.get("printings"):
        printing = card["printings"][0]
        suffix = "|{}".format(printing["set"]) if printing["set"] else ""
        if printing["collector"]:
            suffix += "|[{}]".format(printing["collector"])
        return "{} {}{}".format(count, forge_name, suffix)
    return "{} {}".format(count, forge_name)


def export_forge(deck, path, cards):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    by_name = {card["name"].casefold(): card for card in cards}
    lines = ["[metadata]", "Name={}".format(deck["name"]), "[Main]"]
    lines.extend(_forge_line(name, count, by_name) for name, count in sorted(deck["main"].items()))
    if deck.get("sideboard"):
        lines.append("[Sideboard]")
        lines.extend(_forge_line(name, count, by_name) for name, count in sorted(deck["sideboard"].items()))
    if deck.get("commanders"):
        lines.append("[Commander]")
        lines.extend(_forge_line(name, count, by_name) for name, count in sorted(deck["commanders"].items()))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_forge_simulation(deck_paths, format_name, forge_dir=DEFAULT_FORGE, games=1, best_of=None,
                         seed=42, timeout_seconds=180):
    config = load_format(format_name)
    forge_dir = Path(forge_dir)
    jar = forge_dir / FORGE_JAR
    if not jar.is_file():
        candidates = sorted(forge_dir.glob("forge-gui-desktop-*-jar-with-dependencies.jar"))
        jar = candidates[0] if len(candidates) == 1 else jar
    if not jar.is_file():
        return {"status": "unsupported", "reason": "Forge JAR not found: {}".format(jar), "command": []}
    deck_paths = [Path(path).resolve() for path in deck_paths]
    required = config["simulation"]["players"]
    if len(deck_paths) != required:
        return {"status": "unsupported", "reason": "{} simulation requires {} deck paths; received {}.".format(config["display_name"], required, len(deck_paths)), "command": []}
    deck_dir = deck_paths[0].parent
    if any(path.parent != deck_dir for path in deck_paths):
        return {"status": "unsupported", "reason": "Forge CLI requires smoke-test decks in one directory.", "command": []}
    command = ["java", "-Xmx2g", "-Dfile.encoding=UTF-8", "-jar", str(jar), "sim", "-d"]
    command.extend(path.name for path in deck_paths)
    command.extend(["-D", str(deck_dir), "-f", config["simulation"]["forge_game_type"], "-s", str(seed), "-c", str(timeout_seconds), "-q"])
    match_games = best_of or 0
    command.extend(["-m", str(match_games)] if match_games else ["-n", str(games)])
    try:
        result = subprocess.run(command, cwd=forge_dir, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout_seconds * max(1, games) + 60)
    except subprocess.TimeoutExpired as error:
        return {"status": "timeout", "command": command, "stdout": error.stdout or "", "stderr": error.stderr or ""}
    output = result.stdout + "\n" + result.stderr
    result_lines = re.findall(r"Game Result:.*", output)
    normal = [line for line in result_lines if "status=NORMAL_WIN" in line or "has won" in line]
    return {"status": "completed" if result.returncode == 0 and result_lines else "failed",
            "exit_code": result.returncode, "command": command, "game_results": result_lines,
            "completed_games": len(normal), "stdout": result.stdout, "stderr": result.stderr}


def optimize_deck(deck, cards, version=2):
    """Create a preserved version with one conservative, validation-safe static improvement."""
    config = load_format(deck["format"])
    candidate = json.loads(json.dumps(deck))
    candidate["name"] = "{} v{}".format(deck["name"], version)
    candidate["parent"] = deck["name"]
    candidate["optimization_objectives"] = config["metrics"]
    by_name = {card["name"]: card for card in cards}
    colors = set(candidate.get("colors") or COLOR_ORDER)
    used = Counter()
    for section in ("main", "sideboard", "commanders"):
        used.update(candidate.get(section, {}))
    replaceable = [by_name[name] for name in candidate["main"] if name in by_name and "Land" not in by_name[name]["type_line"]]
    available = [card for card in cards if is_legal(card, config) and "Land" not in card["type_line"]
                 and set(card["color_identity"]).issubset(colors) and card["name"] not in candidate.get("commanders", {})
                 and (is_basic(card) or used[card["name"]] < min(config["copy_limit"], card["quantity"]))]
    changes = []
    if replaceable and available:
        old = min(replaceable, key=lambda card: _score(card, candidate.get("archetype"), candidate.get("power_target")))
        new = max(available, key=lambda card: _score(card, candidate.get("archetype"), candidate.get("power_target")))
        old_score = _score(old, candidate.get("archetype"), candidate.get("power_target"))
        new_score = _score(new, candidate.get("archetype"), candidate.get("power_target"))
        if new["name"] != old["name"] and new_score > old_score:
            candidate["main"][old["name"]] -= 1
            if not candidate["main"][old["name"]]:
                del candidate["main"][old["name"]]
            candidate["main"][new["name"]] = candidate["main"].get(new["name"], 0) + 1
            check = validate_deck(candidate, cards, config=config)
            if check["valid"]:
                changes.append({"remove": old["name"], "add": new["name"], "score_delta": new_score - old_score})
            else:
                candidate["main"] = json.loads(json.dumps(deck["main"]))
    candidate["validation"] = validate_deck(candidate, cards, config=config)
    candidate["report"] = deck_report(candidate, cards, config)
    candidate["optimization_history"] = [{
        "iteration": version, "stage": "static-review", "changes": changes,
        "validation": "pass" if candidate["validation"]["valid"] else "fail",
        "note": ("A heuristic substitution was validated but still needs comparative Forge evidence."
                 if changes else "No beneficial validation-safe substitution was found; do not claim an improvement.")
    }]
    return candidate


def save_bundle(deck, cards, output_root=None):
    output_root = Path(output_root or PROJECT_ROOT / "outputs") / deck["format"]
    output_root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", deck["name"].casefold()).strip("-") or "deck"
    json_path = output_root / (slug + ".json")
    dck_path = output_root / (slug + ".dck")
    json_path.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
    export_forge(deck, dck_path, cards)
    report_path = output_root / (slug + "-report.md")
    report_path.write_text(render_report(deck), encoding="utf-8")
    return {"json": str(json_path), "forge": str(dck_path), "report": str(report_path)}


def render_report(deck):
    report, validation = deck.get("report", {}), deck.get("validation", {})
    counts = validation.get("counts", {})
    check_status = "PASS" if validation.get("valid") else "FAIL"
    if validation.get("collection_complete") is False and validation.get("valid"):
        check_status = "FORMAT PASS — PROPOSAL HAS QUANTITY GAPS"
    sections = ["# {}".format(deck["name"]), "", "- Format: {}".format(deck["format"].title()),
                "- Archetype: {}".format(deck.get("archetype", "")),
                "- Validation: {}".format(check_status), "",
                "## Deck check", "",
                "- Main / sideboard / commanders: {} / {} / {}".format(
                    counts.get("main", sum(deck.get("main", {}).values())),
                    counts.get("sideboard", sum(deck.get("sideboard", {}).values())),
                    counts.get("commanders", sum(deck.get("commanders", {}).values()))),
                "- Collection quantities: {}".format(
                    "complete" if validation.get("collection_complete", True) else "short; this proposal reserves no owned copies"), ""]
    if validation.get("errors"):
        sections.extend(["- Issues: {}".format("; ".join(validation["errors"])), ""])
    sections.extend(["## Strategy", "", report.get("primary_objective", ""), "",
                     "- Early: {}".format(report.get("early_game", "")),
                     "- Mid: {}".format(report.get("middle_game", "")),
                     "- Late: {}".format(report.get("late_game", "")), "",
                     "### Win conditions", ""])
    sections.extend("- {}".format(item) for item in report.get("win_conditions", []))
    if not report.get("win_conditions"):
        sections.append("- No distinct heuristic finishers identified.")
    sections.extend(["", "### Known weaknesses", ""])
    sections.extend("- {}".format(item) for item in report.get("weaknesses", []))
    if not report.get("weaknesses"):
        sections.append("- None recorded.")
    role_sections = (("Interaction", "interaction"), ("Protection", "protection"), ("Recovery", "recovery"))
    if any(report.get(key) for _, key in role_sections):
        sections.extend(["", "## Card roles", ""])
        for heading, key in role_sections:
            if report.get(key):
                sections.append("- **{}:** {}".format(heading, ", ".join(report[key])))
    if report.get("mana_curve"):
        sections.extend(["", "## Mana", "", "- Lands: {}".format(report.get("land_count", "not recorded")),
                         "- Nonland curve: {}".format(" · ".join(
                             "{} mana: {}".format(value, count)
                             for value, count in report["mana_curve"].items())), ""])
    for heading, key in (("Commander", "commanders"), ("Main", "main"), ("Sideboard", "sideboard")):
        if deck.get(key):
            sections.extend(["### {}".format(heading), ""])
            sections.extend("{} {}".format(count, name) for name, count in sorted(deck[key].items()))
            sections.append("")
    engine_details = report.get("engine_details", [])
    if engine_details:
        sections.extend(["## {}".format(report.get("engine_heading", "Synergy engine")), ""])
        sections.extend("- {}".format(detail) for detail in engine_details)
        sections.append("")
    if report.get("sideboard_strategy"):
        sections.extend(["## Sideboard plan", "", report["sideboard_strategy"], ""])
    evidence = deck.get("forge_evidence")
    if evidence:
        sections.extend(["## Forge evidence", "", "- Status: {}".format(evidence.get("status", "not run"))])
        if evidence.get("opponent"):
            sections.append("- Opponent: {}".format(evidence["opponent"]))
        if evidence.get("simulation_id"):
            sections.append("- Simulation record: `outputs/{}/simulations/{}/simulation.json`".format(
                deck.get("format", ""), evidence["simulation_id"]))
        if evidence.get("attempted_games") is not None:
            sections.append("- Attempted/completed games: {}/{}".format(
                evidence["attempted_games"], evidence.get("completed_games", 0)))
        if evidence.get("wins") is not None:
            sections.append("- Wins/losses: {}/{}".format(evidence["wins"], evidence.get("losses", 0)))
        if evidence.get("unsupported_cards"):
            sections.append("- Unsupported cards: {}".format(", ".join(evidence["unsupported_cards"])))
        if evidence.get("note"):
            sections.extend(["", evidence["note"]])
    return "\n".join(sections) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=str(DEFAULT_COLLECTION))
    parser.add_argument("--bulk", default=str(DEFAULT_BULK))
    sub = parser.add_subparsers(dest="command", required=True)
    analyze = sub.add_parser("analyze")
    format_choices = tuple(sorted(item.stem for item in FORMATS_DIR.glob("*.yaml")))
    analyze.add_argument("format", choices=format_choices)
    build = sub.add_parser("build")
    build.add_argument("format", choices=format_choices)
    build.add_argument("--count", type=int, default=1)
    build.add_argument("--colors")
    build.add_argument("--archetype")
    build.add_argument("--power-target")
    build.add_argument("--output-root")
    build.add_argument("--commander")
    validate = sub.add_parser("validate")
    validate.add_argument("deck_json")
    simulate = sub.add_parser("simulate")
    simulate.add_argument("format", choices=format_choices)
    simulate.add_argument("decks", nargs="+")
    simulate.add_argument("--games", type=int, default=1)
    simulate.add_argument("--best-of", type=int)
    optimize = sub.add_parser("optimize")
    optimize.add_argument("deck_json")
    optimize.add_argument("--output-root")
    args = parser.parse_args(argv)
    cards = load_collection(args.collection, args.bulk)
    if args.command == "analyze":
        value = analyze_collection(args.format, cards=cards)
    elif args.command == "build":
        constraints = {"commander": args.commander} if args.commander else None
        decks = build_decks(args.format, args.count, args.colors, args.archetype, args.power_target, constraints=constraints, cards=cards)
        value = [{"deck": deck, "files": save_bundle(deck, cards, args.output_root)} for deck in decks]
    elif args.command == "validate":
        deck = json.loads(Path(args.deck_json).read_text(encoding="utf-8"))
        value = validate_deck(deck, cards)
    elif args.command == "simulate":
        value = run_forge_simulation(args.decks, args.format, games=args.games, best_of=args.best_of)
    else:
        deck = json.loads(Path(args.deck_json).read_text(encoding="utf-8"))
        optimized = optimize_deck(deck, cards)
        value = {"deck": optimized, "files": save_bundle(optimized, cards, args.output_root)}
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if not isinstance(value, dict) or value.get("status") != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
