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


def _primer_commander_plan(selected, commander):
    """Return card-text-grounded phase advice for recognized Commander engines."""
    if not commander:
        return {}
    commander_text = str(commander.get("oracle_text") or "")
    all_text = "\n".join(str(card.get("oracle_text") or "") for card, _ in selected)
    names = {str(card.get("name") or "").casefold(): card.get("name") for card, _ in selected}
    named = lambda *values: [names[value.casefold()] for value in values if value.casefold() in names]

    # Villain connive/discard: both the commander text and its deck support must exist.
    if "connive" in commander_text.casefold() and "Villain" in commander_text and "discard" in commander_text.casefold():
        connivers = [card["name"] for card, _ in selected
                     if "connive" in str(card.get("oracle_text") or "").casefold()]
        villain_etb_connivers = [card["name"] for card, _ in selected
                                if "villain" in str(card.get("oracle_text") or "").casefold()
                                and "connive" in str(card.get("oracle_text") or "").casefold()]
        returns = [card["name"] for card, _ in selected
                   if "return target villain" in str(card.get("oracle_text") or "").casefold()]
        discard_payoffs = [card["name"] for card, _ in selected
                           if any(phrase in str(card.get("oracle_text") or "").casefold()
                                  for phrase in ("whenever you discard", "whenever you cycle or discard"))]
        return {
            "primary_objective": "Use Villain arrivals and combat connive to filter cards, grow a threatening attacker, and turn discarded lands into multiplayer life loss.",
            "early_game": "Develop colored mana, then establish a connive or discard outlet. "
                          + ("Tombstone discounts Villain spells and returns a Villain from the graveyard." if named("Tombstone, Career Criminal") else "Prioritize low-cost Villains that start the discard engine."),
            "middle_game": ("Doctor Doom gives a Villain menace and connives at each of your combats; "
                            + ("Prowler, Clawed Thief also connives whenever another Villain enters." if named("Prowler, Clawed Thief") else "use the other listed Villain-entry connivers to add repeatable loots." )
                            + (" Leader, Super-Genius draws before a creature connives, increasing card selection." if named("Leader, Super-Genius") else "")
                            + (" A nonland discarded to connive puts a +1/+1 counter on that creature." if "discarded a nonland card" in all_text.casefold() else "")),
            "late_game": ("Discarding a land makes each opponent lose 2 life; "
                          + ("Faith of the Devoted adds another drain-and-gain trigger when cards are discarded." if named("Faith of the Devoted") else "use the land-discard trigger as reach while recurring Villains.")
                          + ((" Graveyard recovery: " + ", ".join(returns[:3]) + ".") if returns else "")
                          + ((" Additional discard payoffs: " + ", ".join(discard_payoffs[:3]) + ".") if discard_payoffs else "")),
            "engine_heading": "Villain connive and discard engine",
            "engine_details": (["Doctor Doom turns one combat connive each turn into menace plus card filtering; nonland discards also add a +1/+1 counter."]
                               + (["Villain-entry connive sources: " + ", ".join(villain_etb_connivers[:5]) + "."] if villain_etb_connivers else [])
                               + (["Discard-trigger payoffs: " + ", ".join(discard_payoffs[:5]) + "."] if discard_payoffs else [])),
        }

    # Jace's planeswalker package: empower cards and creature-to-hit polymorph effects.
    if "planeswalker" in str(commander.get("type_line") or "").casefold() and "reveal cards from the top" in commander_text.casefold():
        empowers = [card["name"] for card, _ in selected
                    if "empower jace" in str(card.get("oracle_text") or "").casefold()]
        polymorphs = [card["name"] for card, _ in selected
                      if "cards from the top" in str(card.get("oracle_text") or "").casefold()
                      and "reveal" in str(card.get("oracle_text") or "").casefold()
                      and any(word in str(card.get("oracle_text") or "").casefold()
                              for word in ("exile target creature", "exile all creatures", "destroy target creature",
                                           "put target permanent you own on the bottom"))]
        token_makers = [card["name"] for card, _ in selected
                        if "create" in str(card.get("oracle_text") or "").casefold()
                        and "creature token" in str(card.get("oracle_text") or "").casefold()]
        topdeck_setup = [card["name"] for card, _ in selected
                         if "on top of your library" in str(card.get("oracle_text") or "").casefold()]
        return {
            "primary_objective": "Build expendable creatures and empower Jace, then trade a token or other small creature for a high-impact creature or planeswalker from the library.",
            "early_game": ("Use mana rocks to reach Jace and cast noncreature spells that trigger the deck's empower Jace cards." if empowers else "Develop mana and establish Jace before committing expensive threats.")
                          + ((" Empower sources: " + ", ".join(empowers[:4]) + ".") if empowers else ""),
            "middle_game": ("Use Jace's −3 on an expendable creature to reveal into a creature or planeswalker; "
                            + (("token makers such as " + ", ".join(token_makers[:4]) + " supply fodder. ") if token_makers else "protect the few creatures you can afford to convert. ")
                            + (("Other conversion spells in this list: " + ", ".join(polymorphs[:5]) + ".") if polymorphs else "The list has few additional conversion effects, so preserve Jace's activation for a strong hit.")),
            "late_game": (("Use " + ", ".join(topdeck_setup[:3]) + " to put a high-impact creature or planeswalker on top before Jace's −3; "
                           if topdeck_setup else "Keep enough high-impact creatures and planeswalkers in the library before using Jace's −3; ")
                          + (", ".join(polymorphs[:3]) + " can provide additional conversion attempts." if polymorphs else "Jace's −3 remains the main creature-cheat line.")
                          + " Keep enough creature and planeswalker hits in the deck so the conversion effect does not whiff."),
            "engine_heading": "Empower Jace and creature conversion",
            "engine_details": (["Jace's −3 exiles another creature you control, then reveals until a creature or planeswalker is found; token makers are expendable fodder, not the payoff."]
                               + (["Empower sources: " + ", ".join(empowers[:6]) + "."] if empowers else [])
                               + (["Creature conversion effects: " + ", ".join(polymorphs[:6]) + "."] if polymorphs else [])),
        }

    # Dragon-token engine: Lathliss multiplies nontoken Dragon entries and dragon ETB payoffs.
    if "another nontoken dragon you control enters" in commander_text.casefold() and "create a 5/5" in commander_text.casefold():
        dragon_payoffs = [card["name"] for card, _ in selected
                          if "dragon" in str(card.get("oracle_text") or "").casefold()
                          and any(word in str(card.get("oracle_text") or "").casefold()
                                  for word in ("enters", "attacks", "double strike", "extra combat"))]
        dragon_ramp = [card["name"] for card, _ in selected
                       if "dragon" in str(card.get("oracle_text") or "").casefold()
                       and any(word in str(card.get("oracle_text") or "").casefold()
                               for word in ("cost less", "add {", "treasure token"))]
        return {
            "primary_objective": "Accelerate into nontoken Dragons; Lathliss copies each Dragon entry as a 5/5 flyer, turning every threat into a wider board and amplifying Dragon-entry payoffs.",
            "early_game": ("Prioritize mana rocks and Dragon-cost reducers before the expensive threats"
                           + ((" (" + ", ".join(dragon_ramp[:5]) + ").") if dragon_ramp else ".")
                           + (" Dragon's Hoard gains a gold counter per Dragon entry, then can cash a counter in for a card, so deploy it before the engine starts." if named("Dragon's Hoard") else "")),
            "middle_game": ("With Lathliss on the battlefield, each nontoken Dragon creates an extra 5/5 flying Dragon token. "
                            + ("Dragon Tempest grants haste to the flyers and deals damage for every Dragon entry." if named("Dragon Tempest") else "Use the added bodies to build a flying board and keep pressure through removal.")
                            + ((" Other Dragon payoffs here include " + ", ".join(dragon_payoffs[:5]) + ".") if dragon_payoffs else "")),
            "late_game": ("Convert the multiplied Dragon board into lethal attacks; "
                          + ("Dragon Tempest and Scourge of Valkas turn repeated Dragon entries into direct damage." if named("Dragon Tempest") and named("Scourge of Valkas") else
                             ("Dragon Tempest turns each Dragon entry into scalable damage." if named("Dragon Tempest") else "use the deck's listed Dragon-entry payoffs and combat finishers to close."))
                          + (" Terror of Mount Velus grants your creatures double strike for a decisive combat." if named("Terror of Mount Velus") else "")),
            "engine_heading": "Lathliss Dragon-token engine",
            "engine_details": (["Lathliss makes one 5/5 flying Dragon whenever another nontoken Dragon enters; the token does not recursively trigger Lathliss."]
                               + (["Dragon-entry payoffs: " + ", ".join(dragon_payoffs[:6]) + "."] if dragon_payoffs else [])
                               + (["Dragon ramp/reducers: " + ", ".join(dragon_ramp[:6]) + "."] if dragon_ramp else [])),
        }
    return {}


def deck_report(deck, cards, config=None):
    config = config or load_format(deck["format"])
    by_name = {card["name"]: card for card in cards}
    selected = [(by_name[name], count) for section in ("main", "commanders") for name, count in deck.get(section, {}).items() if name in by_name]
    roles = defaultdict(list)
    curve = Counter()
    for card, count in selected:
        if "Land" not in card["type_line"]:
            curve[int(min(float(card.get("mana_value") or 0), 7))] += count
        for role in _roles(card):
            roles[role].append(card["name"])
    large_threats = [card["name"] for card, _ in selected
                     if str(card.get("power", "")).isdigit() and float(card["power"]) >= 5]
    wins = list(dict.fromkeys(roles.get("finisher", []) + large_threats))
    commander_cards = [card for card, _ in selected if card["name"] in deck.get("commanders", {})]
    specific_plan = _primer_commander_plan(selected, commander_cards[0] if len(commander_cards) == 1 else None)
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
    report.update(specific_plan)
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
    unowned_csv = export_unowned_csv(deck, cards, output_root / (slug + "-unowned.csv"))
    files = {"json": str(json_path), "forge": str(dck_path), "report": str(report_path)}
    if unowned_csv:
        files["unowned_csv"] = str(unowned_csv)
    return files


def export_unowned_csv(deck, cards, target):
    """Write one purchase row per card with a shortfall, enriched from Scryfall metadata."""
    shortages = [item for item in (deck.get("validation") or {}).get("allocation_report", [])
                 if int(item.get("shortage", 0) or 0) > 0]
    if not shortages:
        shortages = [{
            "card_name": item.get("card"), "needed": item.get("deck_count", 0),
            "owned": item.get("owned", 0), "reserved_elsewhere": 0,
            "available": item.get("owned", 0), "shortage": item.get("to_buy", 0),
        } for item in (deck.get("ownership_summary") or {}).get("cards_to_buy", [])
            if int(item.get("to_buy", 0) or 0) > 0]
    if not shortages:
        return None
    by_name = {str(card.get("name") or "").casefold(): card for card in cards or []}
    by_name.update({str(card.get("name") or "").casefold(): card
                    for card in deck.get("card_catalog") or []})
    fields = ["Cardmarket import line", "Card", "Sections", "Needed", "Owned",
              "Reserved elsewhere", "Available", "Missing copies", "Card type",
              "Mana cost", "Mana value", "Oracle text", "Scryfall URL"]
    with Path(target).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for shortage in shortages:
            name = str(shortage.get("card_name") or "")
            card = by_name.get(name.casefold(), {})
            sections = [section.title() for section in ("main", "sideboard", "commanders")
                        if any(str(card_name).casefold() == name.casefold()
                               for card_name in (deck.get(section) or {}))]
            printings = card.get("printings") or []
            scryfall_url = card.get("scryfall_uri") or ""
            if not scryfall_url and printings and printings[0].get("scryfall_id"):
                scryfall_url = "https://scryfall.com/card/{}".format(printings[0]["scryfall_id"])
            missing = int(shortage.get("shortage", 0) or 0)
            writer.writerow({
                "Cardmarket import line": "{} {}".format(missing, name),
                "Card": name, "Sections": ", ".join(sections),
                "Needed": shortage.get("needed", 0), "Owned": shortage.get("owned", 0),
                "Reserved elsewhere": shortage.get("reserved_elsewhere", 0),
                "Available": shortage.get("available", 0), "Missing copies": missing,
                "Card type": card.get("type_line", ""), "Mana cost": card.get("mana_cost", ""),
                "Mana value": card.get("mana_value", ""), "Oracle text": card.get("oracle_text", ""),
                "Scryfall URL": scryfall_url,
            })
    return Path(target)


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
