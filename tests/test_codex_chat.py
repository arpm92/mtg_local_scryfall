import os
import sys
import tempfile
import unittest
import json
import sys
from pathlib import Path
from unittest.mock import patch

from codex_chat import CodexChat

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / ".agents" / "skills" / "mtg-deck-architect" / "scripts"))
import architect


class CodexChatTests(unittest.TestCase):
    def test_default_codex_timeout_allows_longer_collection_comparisons(self):
        with patch.dict(os.environ, {
            "MTG_CODEX_ENABLED": "true", "MTG_CODEX_COMMAND": "codex", "CODEX_HOME": "/tmp/codex-test"
        }, clear=True):
            chat = CodexChat.from_environment()
        self.assertEqual(chat.timeout, 240)

    def test_deck_creation_intent_and_format_clarification(self):
        self.assertTrue(CodexChat.is_deck_creation_request({"message": "Create a new Pauper deck with green creatures."}))
        self.assertTrue(CodexChat.is_deck_creation_request({"message": "I would like a fresh deck based on my collection."}))
        self.assertTrue(CodexChat.is_deck_creation_request({"message": "I want a deck using Black Widow and green counters."}))
        self.assertFalse(CodexChat.is_deck_creation_request({"message": "How do I build a Pauper deck?"}))
        self.assertFalse(CodexChat.is_deck_creation_request({"message": "Can you make my current deck better?"}))
        chat = CodexChat(command="python")
        result = chat.create_deck({"message": "Create a new deck"}, [], [], architect, [])
        self.assertIn("Which format", result["clarification"])

    def test_chat_retrieval_keeps_exact_card_name_outside_relevance_limit(self):
        cards = [
            {"name": "A distraction {:03d}".format(index), "typeLine": "Creature",
             "oracleText": "Black Widow appears in this reminder text.", "oracleId": "d{:03d}".format(index)}
            for index in range(220)
        ]
        cards.append({"name": "Black Widow, Deadly Hunter", "oracleId": "black-widow"})

        relevant = CodexChat._relevant_cards("Do I own Black Widow?", None, cards)

        self.assertIn("Black Widow, Deadly Hunter", [card["name"] for card in relevant])

    def test_chat_context_spells_out_partial_card_name_matches(self):
        chat = CodexChat(command="python")
        captured = {}

        def fake_turn(instructions, prompt, model=None):
            captured["instructions"] = instructions
            captured["prompt"] = prompt
            return "You own Black Widow, Deadly Hunter."

        chat._turn = fake_turn
        cards = [
            {"name": "Black Widow, Deadly Hunter", "quantity": 1, "oracleId": "widow"},
            {"name": "Origin of Black Widow", "quantity": 1, "oracleId": "origin"},
        ]
        chat.ask({"message": "Do I own Black Widow?"}, [], cards)
        context = json.loads(captured["prompt"].split("Use this supplied application context:\n", 1)[1])

        self.assertEqual([card["name"] for card in context["card_name_matches_from_question"]], ["Black Widow, Deadly Hunter"])
        self.assertIn("card_name_matches_from_question", captured["instructions"])

    def test_created_deck_uses_owned_candidates_and_passes_shared_validation(self):
        chat = CodexChat(command="python")
        response = {
            "name": "Green Pauper Fixture", "archetype": "mono-green aggro",
            "colors": ["G"], "main": {"Owned Sprout": 4, "Forest": 56},
            "sideboard": {}, "commanders": {},
            "strategy": "Attack early with efficient creatures.",
            "early_game": "Develop a board.", "middle_game": "Apply pressure.",
            "late_game": "Finish with combat damage.",
            "primary_win_condition": "Win through creature combat.",
            "secondary_win_condition": "Use the sideboard for matchup adjustments.",
        }
        chat._turn = lambda instructions, prompt, model=None: json.dumps(response)
        display_cards = [
            {"name": "Owned Sprout", "quantity": 4, "manaCost": "{G}", "manaValue": 1,
             "colors": ["G"], "colorIdentity": ["G"], "typeLine": "Creature — Plant",
             "oracleText": "When this enters, put a +1/+1 counter on it.", "keywords": [], "commander": False,
             "legalities": {"pauper": "legal"}},
            {"name": "Forest", "quantity": 1, "manaCost": "", "manaValue": 0,
             "colors": [], "colorIdentity": [], "typeLine": "Basic Land — Forest",
             "oracleText": "({T}: Add {G}.)", "keywords": [], "commander": False,
             "legalities": {"pauper": "not_legal"}},
        ]
        engine_cards = [
            {"name": "Owned Sprout", "quantity": 4, "mana_cost": "{G}", "mana_value": 1,
             "color_identity": ["G"], "type_line": "Creature — Plant", "oracle_text": "When this enters, put a +1/+1 counter on it.",
             "legalities": {"pauper": "legal"}},
            {"name": "Forest", "quantity": 1, "mana_cost": "", "mana_value": 0,
             "color_identity": [], "type_line": "Basic Land — Forest", "oracle_text": "({T}: Add {G}.)",
             "legalities": {"pauper": "not_legal"}},
        ]
        result = chat.create_deck(
            {"message": "Create a mono-green Pauper deck focused on +1/+1 counters."},
            [], display_cards, architect, engine_cards,
        )
        self.assertEqual(result["deck"]["main"], {"Owned Sprout": 4, "Forest": 56})
        self.assertTrue(result["deck"]["validation"]["valid"])
        self.assertIn("Created and saved", result["answer"])

    def test_chat_prompt_includes_only_relevant_collection_data_and_saved_deck(self):
        chat = CodexChat(command="python")
        captured = {}

        def fake_turn(instructions, prompt, model=None):
            captured["instructions"] = instructions
            captured["prompt"] = prompt
            return "Consider adding Owned Growth."

        chat._turn = fake_turn
        deck = {"name": "Counter Garden", "format": "commander", "main": {"Owned Growth": 1},
                "commanders": {"Green Leader": 1}}
        cards = [
            {"name": "Owned Growth", "quantity": 2, "oracleText": "Put a counter on a creature.", "oracleId": "g1"},
            {"name": "Unrelated Island", "quantity": 4, "oracleText": "", "oracleId": "u1"},
        ]
        result = chat.ask({"message": "How does Owned Growth help?", "deckName": "Counter Garden"}, [deck], cards)
        self.assertEqual(result, "Consider adding Owned Growth.")
        self.assertIn("Never claim you changed or saved a deck", captured["instructions"])
        self.assertIn("Counter Garden", captured["prompt"])
        self.assertIn("Owned Growth", captured["prompt"])
        self.assertNotIn("Unrelated Island", captured["prompt"])

    def test_standard_comparison_includes_saved_lists_and_combined_copy_shortages(self):
        chat = CodexChat(command="python")
        captured = {}

        def fake_turn(instructions, prompt, model=None):
            captured["prompt"] = prompt
            return "The two Standard lists need six copies of Shared Hero, but you own four."

        chat._turn = fake_turn
        decks = [
            {"name": "Standard Marvel heroes", "format": "standard",
             "main": {"Shared Hero": 4, "Hero Support": 56},
             "sideboard": {"Shared Hero": 1}, "commanders": {}},
            {"name": "Standard Marvel villains", "format": "standard",
             "main": {"Shared Hero": 1, "Villain Support": 59},
             "sideboard": {}, "commanders": {}},
            {"name": "Commander test", "format": "commander",
             "main": {"Other Card": 99}, "sideboard": {}, "commanders": {"Commander Card": 1}},
        ]
        cards = [
            {"name": "Shared Hero", "quantity": 4, "typeLine": "Creature",
             "oracleText": "A shared Standard card.", "oracleId": "shared"},
            {"name": "Hero Support", "quantity": 56, "typeLine": "Creature",
             "oracleText": "Hero support.", "oracleId": "hero"},
            {"name": "Villain Support", "quantity": 59, "typeLine": "Creature",
             "oracleText": "Villain support.", "oracleId": "villain"},
        ]

        answer = chat.ask(
            {"message": "Can you compare the Standard hero and villain decks and check if I can build both?"},
            decks, cards,
        )
        context = json.loads(captured["prompt"].split("Use this supplied application context:\n", 1)[1])
        self.assertEqual(answer, "The two Standard lists need six copies of Shared Hero, but you own four.")
        self.assertEqual({deck["name"] for deck in context["saved_decks"]}, {
            "Standard Marvel heroes", "Standard Marvel villains",
        })
        self.assertEqual(context["combined_collection_demand"][0]["card"], "Shared Hero")
        self.assertEqual(context["combined_collection_demand"][0]["required"], 6)
        self.assertEqual(context["combined_collection_demand"][0]["owned"], 4)
        self.assertEqual(context["combined_collection_demand"][0]["shortage"], 2)

    def test_app_server_streams_completed_agent_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text("{}", encoding="utf-8")
            fake_server = root / "fake_app_server.py"
            fake_server.write_text(
                "import json, sys\n"
                "for line in sys.stdin:\n"
                " m=json.loads(line); method=m.get('method'); rid=m.get('id')\n"
                " if method == 'initialize': print(json.dumps({'id':rid,'result':{}}), flush=True)\n"
                " elif method == 'thread/start': print(json.dumps({'id':rid,'result':{'thread':{'id':'thread-test'}}}), flush=True)\n"
                " elif method == 'turn/start':\n"
                "  print(json.dumps({'id':rid,'result':{'turn':{'id':'turn-test'}}}), flush=True)\n"
                "  print(json.dumps({'method':'item/agentMessage/delta','params':{'delta':'Test reply'}}), flush=True)\n"
                "  print(json.dumps({'method':'turn/completed','params':{'turn':{'id':'turn-test','status':'completed'}}}), flush=True)\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home), "MTG_CODEX_CWD": str(root)}):
                chat = CodexChat(command="{} {}".format(sys.executable, fake_server), timeout=5)
                self.assertTrue(chat.status()["available"])
                self.assertEqual(chat._turn("Instructions", "Question"), "Test reply")

    def test_status_reports_missing_server_side_sign_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CODEX_HOME": tmp}):
                chat = CodexChat(command=sys.executable)
                self.assertFalse(chat.status()["available"])
                self.assertIn("Sign in", chat.status()["message"])


if __name__ == "__main__":
    unittest.main()
