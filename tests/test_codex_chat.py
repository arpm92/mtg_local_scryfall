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
    def test_format_reply_continues_pending_deck_creation_request(self):
        request = {
            "message": "Standard",
            "history": [
                {"role": "user", "content": "Can you create an angels-deamon deck with 60 cards?"},
                {"role": "assistant", "content": "Which format should I use: Commander, Pauper, or Standard?"},
            ],
        }

        resolved = CodexChat.pending_deck_creation_request(request)

        self.assertEqual(resolved, "Can you create an angels-deamon deck with 60 cards? Standard")

    def test_make_deck_followup_restores_the_prior_specific_request(self):
        request = {
            "message": "make the deck",
            "history": [
                {"role": "user", "content": "Create an Angel-focused Standard deck from my collection."},
                {"role": "assistant", "content": "This attempt did not return a validated Standard deck draft."},
            ],
        }

        resolved = CodexChat.pending_deck_creation_request(request)

        self.assertEqual(resolved, "Create an Angel-focused Standard deck from my collection.")

    def test_format_reply_skips_generic_followup_and_restores_original_deck_request(self):
        request = {
            "message": "Standard",
            "history": [
                {"role": "user", "content": "Create an Angel-focused Standard deck from my collection."},
                {"role": "assistant", "content": "This attempt did not return a validated Standard deck draft."},
                {"role": "user", "content": "make the deck"},
                {"role": "assistant", "content": "Which format should I use: Commander, Pauper, or Standard?"},
            ],
        }

        resolved = CodexChat.pending_deck_creation_request(request)

        self.assertEqual(resolved, "Create an Angel-focused Standard deck from my collection. Standard")

    def test_affirmative_reply_accepts_previous_deck_offer(self):
        request = {
            "message": "Yes, please.",
            "history": [
                {"role": "user", "content": "Can you make an angels-deamon deck?"},
                {"role": "assistant", "content": "I can build an Angel-focused Standard deck instead if you want."},
            ],
        }

        resolved = CodexChat.pending_deck_creation_request(request)

        self.assertEqual(resolved, "Create an Angel-focused Standard deck from my available collection.")

    def test_type_context_uses_prior_turn_and_reports_owned_names_and_copies(self):
        chat = CodexChat(command="python")
        captured = {}

        def fake_turn(instructions, prompt, model=None):
            captured["instructions"] = instructions
            captured["prompt"] = prompt
            return "The collection has Angel cards, but no Demons."

        chat._turn = fake_turn
        cards = [
            {"name": "Angel One", "quantity": 3, "typeLine": "Creature — Angel",
             "legalities": {"standard": "legal"}, "oracleId": "angel-one",
             "availability": {"available": 1}},
            {"name": "Angel Two", "quantity": 2, "typeLine": "Creature — Angel Cleric",
             "legalities": {"standard": "not_legal"}, "oracleId": "angel-two"},
        ]
        history = [
            {"role": "user", "content": "Can you make an angels-deamon deck?"},
            {"role": "assistant", "content": "Which format should I use?"},
        ]

        chat.ask({"message": "Standard", "history": history}, [], cards)
        context = json.loads(captured["prompt"].split("Use this supplied application context:\n", 1)[1])

        self.assertEqual(context["owned_card_type_summary"]["angel"]["distinct_cards"], 2)
        self.assertEqual(context["owned_card_type_summary"]["angel"]["owned_copies"], 5)
        self.assertEqual(context["owned_card_type_summary"]["angel"]["available_copies"], 3)
        self.assertEqual(context["owned_card_type_summary"]["angel"]["standard_legal_distinct"], 1)
        self.assertEqual(context["owned_card_type_summary"]["angel"]["standard_legal_copies"], 3)
        self.assertEqual(context["owned_card_type_summary"]["angel"]["standard_legal_available_copies"], 1)
        self.assertEqual(context["owned_card_type_summary"]["demon"]["distinct_cards"], 0)
        self.assertIn("Use owned_card_type_summary for creature-type questions", captured["instructions"])
        self.assertEqual({card["name"] for card in context["owned_cards_matching_question_or_deck"]}, {"Angel One", "Angel Two"})

    def test_deck_creation_does_not_silently_drop_unavailable_requested_tribe(self):
        chat = CodexChat(command="python")
        chat._turn = lambda *args, **kwargs: self.fail("Unsupported tribal request must stop before deck generation.")
        cards = [
            {"name": "Angel One", "quantity": 3, "typeLine": "Creature — Angel",
             "legalities": {"standard": "legal"}, "oracleId": "a1"},
            {"name": "Angel Two", "quantity": 2, "typeLine": "Creature — Angel Cleric",
             "legalities": {"standard": "legal"}, "oracleId": "a2"},
            {"name": "Old Angel", "quantity": 1, "typeLine": "Creature — Angel",
             "legalities": {"standard": "not_legal"}, "oracleId": "a3"},
        ]

        result = chat.create_deck(
            {"message": "Create an angels-deamon Standard deck."}, [], cards, architect, cards
        )

        self.assertNotIn("deck", result)
        self.assertIn("0 available Standard-legal Demon copies", result["clarification"])
        self.assertIn("6 Angel copies across 3 distinct names", result["clarification"])
        self.assertIn("5 Standard-legal Angel copies across 2 names", result["clarification"])
        self.assertIn("No deck was created or saved", result["clarification"])

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
        self.assertIn("Deck draft ready", result["answer"])
        self.assertNotIn("saved", result["answer"].casefold())

    def test_create_deck_canonicalizes_a_unique_split_card_face_name(self):
        chat = CodexChat(command="python")
        full_name = "Blossom-Blessed Angel // Seed Suture"
        response = {
            "name": "Angel Fixture", "archetype": "Angel-focused",
            "colors": ["W"], "main": [
                {"name": "Blossom-Blessed Angel", "count": 4},
                {"name": "Plains", "count": 56},
            ],
            "sideboard": {}, "commanders": {},
            "strategy": "Build a flying board and win through combat damage.",
            "early_game": "Develop mana and prepare the board.",
            "middle_game": "Deploy flying threats.",
            "late_game": "Finish with repeated attacks.",
            "primary_win_condition": "Deal lethal combat damage with flying creatures.",
            "secondary_win_condition": "Use the prepared spell to strengthen a creature.",
        }
        captured = {}

        def fake_turn(instructions, prompt, model=None):
            captured["prompt"] = prompt
            return json.dumps(response)

        chat._turn = fake_turn
        display_cards = [
            {"name": full_name, "quantity": 4, "manaCost": "{3}{W}", "manaValue": 4,
             "colors": ["W"], "colorIdentity": ["W", "G"],
             "typeLine": "Creature — Angel Cleric // Sorcery",
             "oracleText": "Flying, vigilance. Put a +1/+1 counter on target creature. You gain 1 life.",
             "keywords": ["Flying", "Vigilance"], "legalities": {"standard": "legal"}},
            {"name": "Plains", "quantity": 0, "manaCost": "", "manaValue": 0,
             "colors": [], "colorIdentity": [], "typeLine": "Basic Land — Plains",
             "oracleText": "({T}: Add {W}.)", "keywords": [], "legalities": {"standard": "not_legal"}},
        ]
        engine_cards = [
            {"name": full_name, "quantity": 4, "mana_cost": "{3}{W}", "mana_value": 4,
             "color_identity": ["W", "G"], "type_line": "Creature — Angel Cleric // Sorcery",
             "oracle_text": "Flying, vigilance. Put a +1/+1 counter on target creature. You gain 1 life.",
             "legalities": {"standard": "legal"}},
            {"name": "Plains", "quantity": 0, "mana_cost": "", "mana_value": 0,
             "color_identity": [], "type_line": "Basic Land — Plains",
             "oracle_text": "({T}: Add {W}.)", "legalities": {"standard": "not_legal"}},
        ]

        result = chat.create_deck(
            {"message": "Create an Angel-focused Standard deck from my collection."},
            [], display_cards, architect, engine_cards,
        )

        self.assertIn(full_name, captured["prompt"])
        self.assertEqual(result["deck"]["main"], {full_name: 4, "Plains": 56})
        self.assertTrue(result["deck"]["validation"]["valid"])
        self.assertEqual(result["deck"]["validation"]["counts"]["main"], 60)

    def test_deck_generation_resolves_a_unique_split_card_face_name(self):
        card_name = "Blossom-Blessed Angel // Seed Suture"
        plan = {"name": "Angel Fixture", "main": {"Blossom-Blessed Angel": 1}}

        deck, _ = CodexChat._deck_from_plan(
            plan, "standard", {card_name.casefold(): {"name": card_name}}, []
        )

        self.assertEqual(deck["main"], {card_name: 1})

    def test_deck_generation_rejects_ambiguous_split_card_face_name(self):
        first = "Shared Angel // Daybreak"
        second = "Shared Angel // Nightfall"
        plan = {"name": "Ambiguous Angel Fixture", "main": {"Shared Angel": 1}}
        candidates = {first.casefold(): {"name": first}, second.casefold(): {"name": second}}

        with self.assertRaisesRegex(ValueError, "ambiguous face name"):
            CodexChat._deck_from_plan(plan, "standard", candidates, [])

    def test_deck_generation_accepts_card_entry_lists_and_aggregates_counts(self):
        plan = {
            "name": "List Format Fixture",
            "main": [
                {"name": "Owned Sprout", "count": 2},
                {"card_name": "Owned Sprout", "quantity": 2},
                {"name": "Forest", "count": 56},
            ],
        }
        candidates = {
            "owned sprout": {"name": "Owned Sprout"},
            "forest": {"name": "Forest"},
        }

        deck, _ = CodexChat._deck_from_plan(plan, "standard", candidates, [])

        self.assertEqual(deck["main"], {"Owned Sprout": 4, "Forest": 56})

    def test_deck_generation_accepts_repeated_card_names_as_single_copies(self):
        plan = {"name": "Repeated Name Fixture", "main": ["Owned Sprout", "Owned Sprout"]}
        candidates = {"owned sprout": {"name": "Owned Sprout"}}

        deck, _ = CodexChat._deck_from_plan(plan, "standard", candidates, [])

        self.assertEqual(deck["main"], {"Owned Sprout": 2})

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
        self.assertIn("Do not claim a draft was generated or saved unless the app response confirms it", captured["instructions"])
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
