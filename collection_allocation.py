"""Compute how saved decks reserve copies from the owned collection."""

import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping


SECTIONS = ("main", "sideboard", "commanders")


def _card_key(value):
    """Normalize ManaBox and decklist names for case- and spacing-safe lookup."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _positive_count(value):
    if isinstance(value, bool):
        return 0
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(count, 0)


def summarize_allocations(cards, decks):
    """Return owned and reserved counts indexed by normalized card name.

    Deck section counts are added together per card and per deck. Malformed
    sections and non-positive counts are ignored so one bad record cannot
    crash collection browsing. A reservation shortage is reported explicitly
    and never represented as negative availability.
    """
    owned = defaultdict(int)
    for card in cards or ():
        if not isinstance(card, Mapping):
            continue
        key = _card_key(card.get("name"))
        if key:
            owned[key] += _positive_count(card.get("quantity", 0))

    reservations = defaultdict(lambda: defaultdict(int))
    for deck in decks or ():
        if not isinstance(deck, Mapping):
            continue
        if deck.get("ownership_mode") == "proposal":
            continue
        deck_name = str(deck.get("name") or "Unnamed deck")
        for section in SECTIONS:
            entries = deck.get(section) or {}
            if not isinstance(entries, Mapping):
                continue
            for name, raw_count in entries.items():
                key = _card_key(name)
                count = _positive_count(raw_count)
                if key and count:
                    reservations[key][deck_name] += count

    result = {}
    for key in set(owned) | set(reservations):
        total_owned = owned.get(key, 0)
        by_deck = reservations.get(key, {})
        total_reserved = sum(by_deck.values())
        result[key] = {
            "totalOwned": total_owned,
            "reserved": total_reserved,
            "available": max(0, total_owned - total_reserved),
            "shortage": max(0, total_reserved - total_owned),
            "reservedByDeck": [
                {"name": name, "quantity": quantity}
                for name, quantity in by_deck.items()
            ],
        }
    return result


def annotate_card_availability(cards, decks):
    """Return copied card objects annotated with name-level allocation data.

    Repeated printings of the same Oracle card share one availability summary,
    because saved decklists identify cards by name and do not select a physical
    printing. The input objects are never mutated.
    """
    summaries = summarize_allocations(cards, decks)
    result = []
    for card in cards or ():
        if not isinstance(card, Mapping):
            continue
        copied = dict(card)
        key = _card_key(card.get("name"))
        copied["availability"] = summaries.get(key, {
            "totalOwned": 0,
            "reserved": 0,
            "available": 0,
            "shortage": 0,
            "reservedByDeck": [],
        })
        result.append(copied)
    return result
