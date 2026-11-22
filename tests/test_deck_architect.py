import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ENGINE_PATH = Path(__file__).resolve().parents[1] / ".agents" / "skills" / "mtg-deck-architect" / "scripts" / "architect.py"
SPEC = importlib.util.spec_from_file_location("deck_architect_engine", ENGINE_PATH)
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def make_card(name, quantity=4, legal=True, colors=None, type_line="Creature — Test", text="", mv=2):
    colors = colors or []
    return {
        "name": name, "oracle_id": name.lower(), "quantity": quantity,
        "type_line": type_line, "oracle_text": text, "mana_cost": "", "mana_value": mv,
        "colors": colors, "color_identity": colors, "keywords": [], "produced_mana": [],
        "legalities": {key: "legal" if legal else "not_legal" for key in ("commander", "pauper", "standard", "legacy")},
        "layout": "normal", "power": "2", "toughness": "2", "rarity": "common",
        "printings": [{"set": "TST", "collector": "1", "quantity": quantity}],
    }


class DeckArchitectTests(unittest.TestCase):
    def test_configs_load_independently(self):
        self.assertEqual(engine.load_format("commander")["total_size"], 100)
        self.assertEqual(engine.load_format("pauper")["legality_field"], "pauper")
        self.assertEqual(engine.load_format("standard")["simulation"]["forge_game_type"], "constructed")

    def test_legacy_allows_sixty_or_more_cards_and_checks_legacy_bans(self):
        legal_spell = make_card("Legacy Spell", quantity=4)
        basic = make_card("Island", quantity=1, type_line="Basic Land — Island")
        banned = make_card("Banned Card", quantity=1)
        banned["legalities"]["legacy"] = "not_legal"
        config = engine.load_format("legacy")
        deck = {"format": "legacy", "main": {"Legacy Spell": 4, "Island": 57},
                "sideboard": {}, "commanders": {}}
        self.assertTrue(engine.validate_deck(deck, [legal_spell, basic], respect_ownership=True)["valid"])
        deck["main"]["Banned Card"] = 1
        self.assertFalse(engine.validate_deck(deck, [legal_spell, basic, banned], config=config)["valid"])

    def test_legacy_rejects_fewer_than_sixty_main_deck_cards(self):
        card = make_card("Legacy Spell", quantity=4)
        island = make_card("Island", quantity=1, type_line="Basic Land — Island")
        deck = {"format": "legacy", "main": {"Legacy Spell": 4, "Island": 55},
                "sideboard": {}, "commanders": {}}
        result = engine.validate_deck(deck, [card, island])
        self.assertFalse(result["valid"])
        self.assertTrue(any("minimum of 60" in error for error in result["errors"]))

    def test_pauper_uses_legality_not_owned_rarity(self):
        card = make_card("Common Somewhere")
        card["rarity"] = "rare"
        self.assertTrue(engine.is_legal(card, engine.load_format("pauper")))

    def test_sixty_card_validation_enforces_combined_copy_limit_and_ownership(self):
        cards = [make_card("Spell", quantity=3), make_card("Island", quantity=1, type_line="Basic Land — Island")]
        deck = {"format": "pauper", "main": {"Spell": 4, "Island": 56}, "sideboard": {"Spell": 1}, "commanders": {}}
        result = engine.validate_deck(deck, cards, respect_ownership=True)
        self.assertFalse(result["valid"])
        self.assertTrue(any("limit is 4" in error for error in result["errors"]))
        self.assertTrue(any("only 3 are owned" in error for error in result["errors"]))

    def test_commander_color_identity_and_size(self):
        commander = make_card("Blue Leader", quantity=1, colors=["U"], type_line="Legendary Creature — Wizard")
        red = make_card("Red Spell", quantity=1, colors=["R"], type_line="Instant")
        island = make_card("Island", quantity=1, type_line="Basic Land — Island")
        deck = {"format": "commander", "main": {"Island": 98, "Red Spell": 1}, "sideboard": {}, "commanders": {"Blue Leader": 1}}
        result = engine.validate_deck(deck, [commander, red, island])
        self.assertFalse(result["valid"])
        self.assertTrue(any("outside commander identity" in error for error in result["errors"]))

    def test_forge_export_sections(self):
        cards = [make_card("Spell"), make_card("Island", type_line="Basic Land — Island")]
        deck = {"name": "Test", "format": "standard", "main": {"Spell": 4, "Island": 56},
                "sideboard": {"Spell": 1}, "commanders": {}}
        with tempfile.TemporaryDirectory() as temp:
            path = engine.export_forge(deck, Path(temp) / "test.dck", cards)
            text = path.read_text(encoding="utf-8")
        self.assertIn("[Main]", text)
        self.assertIn("[Sideboard]", text)

    def test_forge_export_uses_front_face_name_for_multiface_layouts(self):
        card = make_card("Blossom-Blessed Angel // Seed Suture", type_line="Creature — Angel // Sorcery")
        card["layout"] = "transform"
        deck = {"name": "Prepared Angels", "format": "legacy",
                "main": {card["name"]: 4}, "sideboard": {}, "commanders": {}}

        with tempfile.TemporaryDirectory() as temp:
            path = engine.export_forge(deck, Path(temp) / "angels.dck", [card])
            text = path.read_text(encoding="utf-8")

        self.assertIn("4 Blossom-Blessed Angel|TST|[1]", text)
        self.assertNotIn("Blossom-Blessed Angel // Seed Suture", text)

    def test_deck_report_shows_forge_limitations_and_evidence(self):
        text = engine.render_report({
            "name": "Legacy Test", "format": "legacy", "main": {"Island": 60},
            "sideboard": {}, "commanders": {}, "report": {}, "validation": {"valid": True},
            "forge_evidence": {
                "status": "unsupported", "attempted_games": 10, "completed_games": 0,
                "unsupported_cards": ["Example Card"], "note": "These games do not count."
            },
        })
        self.assertIn("Attempted/completed games: 10/0", text)
        self.assertIn("Unsupported cards: Example Card", text)
        self.assertIn("These games do not count.", text)

    def test_deck_report_includes_synergy_engine_details(self):
        text = engine.render_report({
            "name": "Connive Tempo", "format": "legacy", "main": {"Island": 60},
            "sideboard": {}, "commanders": {}, "validation": {"valid": True},
            "report": {
                "engine_heading": "Connive engine · 11 direct sources",
                "engine_details": ["Red Room Recruit connives on entry."],
            },
        })
        self.assertIn("## Connive engine · 11 direct sources", text)
        self.assertIn("Red Room Recruit connives on entry.", text)

    def test_doctor_doom_primer_explains_villain_connive_and_land_discard_engine(self):
        commander = make_card(
            "Doctor Doom, King of Latveria", quantity=1,
            type_line="Legendary Creature — Human Villain",
            text="Whenever you discard one or more land cards, each opponent loses 2 life. "
                 "At the beginning of combat on your turn, target Villain you control gains menace "
                 "until end of turn. It connives.",
        )
        prowler = make_card(
            "Prowler, Clawed Thief", quantity=1,
            type_line="Legendary Creature — Villain",
            text="Whenever another Villain you control enters, Prowler connives.",
        )
        tombstone = make_card(
            "Tombstone, Career Criminal", quantity=1,
            type_line="Legendary Creature — Villain",
            text="When Tombstone enters, return target Villain card from your graveyard to your hand. "
                 "Villain spells you cast cost {1} less to cast.",
        )
        deck = {"name": "Doom", "format": "commander", "colors": ["U", "B", "R"],
                "main": {prowler["name"]: 1, tombstone["name"]: 1},
                "commanders": {commander["name"]: 1}}

        report = engine.deck_report(deck, [commander, prowler, tombstone])

        self.assertIn("connive", report["early_game"].casefold())
        self.assertIn("Prowler, Clawed Thief", report["middle_game"])
        self.assertIn("land", report["late_game"].casefold())
        self.assertNotEqual(report["early_game"], "Develop mana and low-cost enablers while preserving interaction.")

    def test_jace_primer_explains_empower_and_polymorph_lines(self):
        commander = make_card(
            "Jace, Multiverse Architect", quantity=1,
            type_line="Legendary Planeswalker — Jace",
            text="+1: Draw two cards, then put a card from your hand on the bottom of your library. "
                 "−3: Exile another target creature you control. Reveal cards from the top of your library "
                 "until you reveal a creature or planeswalker card. Put that card onto the battlefield.",
        )
        plan = make_card("Plan for All Outcomes", quantity=1, type_line="Enchantment",
                         text="Whenever you cast your first noncreature spell each turn, empower Jace 1.")
        token = make_card("Secure the Wastes", quantity=1, type_line="Instant",
                          text="Create X 1/1 white Warrior creature tokens.")
        polymorph = make_card("Polymorph", quantity=1, type_line="Sorcery",
                              text="Destroy target creature. Its controller reveals cards from the top of their library until they reveal a creature card.")
        deck = {"name": "Jace", "format": "commander", "colors": ["W", "U", "B", "R"],
                "main": {plan["name"]: 1, token["name"]: 1, polymorph["name"]: 1},
                "commanders": {commander["name"]: 1}}

        report = engine.deck_report(deck, [commander, plan, token, polymorph])

        self.assertIn("empower jace", report["early_game"].casefold())
        self.assertIn("Secure the Wastes", report["middle_game"])
        self.assertIn("Polymorph", report["late_game"])

    def test_lathliss_primer_explains_dragon_token_and_entry_damage_synergy(self):
        commander = make_card(
            "Lathliss, Dragon Queen", quantity=1,
            type_line="Legendary Creature — Dragon",
            text="Flying. Whenever another nontoken Dragon you control enters, create a 5/5 red Dragon creature token with flying.",
        )
        tempest = make_card(
            "Dragon Tempest", quantity=1, type_line="Enchantment",
            text="Whenever a Dragon you control enters, it deals X damage to any target, where X is the number of Dragons you control.",
        )
        dragon = make_card("Atsushi, the Blazing Sky", quantity=1,
                           type_line="Legendary Creature — Dragon", text="Flying")
        hoard = make_card("Dragon's Hoard", quantity=1, type_line="Artifact",
                          text="Whenever a Dragon you control enters, put a gold counter on Dragon's Hoard.")
        deck = {"name": "Dragons", "format": "commander", "colors": ["R"],
                "main": {tempest["name"]: 1, dragon["name"]: 1, hoard["name"]: 1},
                "commanders": {commander["name"]: 1}}

        report = engine.deck_report(deck, [commander, tempest, dragon, hoard])

        self.assertIn("Dragon's Hoard", report["early_game"])
        self.assertIn("5/5 flying Dragon", report["middle_game"])
        self.assertIn("Dragon Tempest", report["late_game"])

    def test_rendered_report_includes_validation_packages_curve_and_sideboard_plan(self):
        text = engine.render_report({
            "name": "Primer Test", "format": "legacy",
            "main": {"Island": 60}, "sideboard": {}, "commanders": {},
            "validation": {"valid": True, "counts": {"main": 60}},
            "report": {
                "primary_objective": "Win through tempo.", "interaction": ["Unsummon"],
                "protection": ["Kid Loki"], "recovery": ["Twinned Vision"],
                "mana_curve": {"1": 4, "2": 12}, "land_count": 20,
                "sideboard_strategy": "Bring counters against large spells.",
            },
        })

        self.assertIn("## Deck check", text)
        self.assertIn("## Card roles", text)
        self.assertIn("Unsummon", text)
        self.assertIn("1 mana: 4", text)
        self.assertIn("Bring counters against large spells.", text)

    def test_forge_runner_refuses_wrong_player_count_before_launch(self):
        value = engine.run_forge_simulation(["one.dck"], "pauper")
        self.assertEqual(value["status"], "unsupported")
        self.assertIn("requires 2", value["reason"])

    def test_commander_bracket_counts_game_changers_conservatively(self):
        with tempfile.TemporaryDirectory() as temp:
            lists = Path(temp) / "res" / "lists"
            lists.mkdir(parents=True)
            (lists / "gamechangers.txt").write_text("Power Card\n", encoding="utf-8")
            deck = {"main": {"Power Card": 1}, "commanders": {"Leader": 1}}
            result = engine.classify_commander_bracket(deck, temp)
        self.assertEqual(result["advisory_bracket"], 3)
        self.assertEqual(result["game_changers"], ["power card"])

    def test_optimizer_preserves_parent_and_validation(self):
        cards = [make_card("Spell", quantity=4), make_card("Island", type_line="Basic Land â€” Island")]
        deck = {"name": "Original", "format": "standard", "main": {"Spell": 4, "Island": 56},
                "sideboard": {}, "commanders": {}, "colors": ["U"]}
        value = engine.optimize_deck(deck, cards)
        self.assertEqual(value["parent"], "Original")
        self.assertTrue(value["validation"]["valid"])
        self.assertEqual(value["main"], deck["main"])


if __name__ == "__main__":
    unittest.main()
