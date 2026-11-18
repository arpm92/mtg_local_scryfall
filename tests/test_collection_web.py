import csv
import base64
import json
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import collection_web as web


class CollectionWebTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_card_dialog_close_handler_is_scoped_to_its_button(self):
        app_script = (Path(web.ROOT) / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("$('#card-dialog .dialog-close').addEventListener('click'", app_script)

    def test_load_collection_and_cached_images(self):
        csv_path = self.root / "collection.csv"
        fields = ["Name", "Quantity", "Set code", "Set name", "Scryfall Oracle ID",
                  "Oracle Name", "Mana Value", "Colors", "Color Identity", "Type Line", "Scryfall ID"]
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({"Name": "Test Card", "Quantity": "3", "Set code": "TST",
                             "Set name": "Test Set", "Scryfall Oracle ID": "oracle-test", "Scryfall ID": "11111111-1111-4111-8111-111111111111",
                             "Oracle Name": "Test Card", "Mana Value": "2.0", "Colors": "U",
                             "Color Identity": "U", "Type Line": "Creature — Wizard"})
            writer.writerow({"Name": "Test Card", "Quantity": "2", "Set code": "ALT",
                             "Set name": "Alternate Set", "Scryfall Oracle ID": "oracle-test", "Scryfall ID": "22222222-2222-4222-8222-222222222222",
                             "Oracle Name": "Test Card", "Mana Value": "2.0", "Colors": "U",
                             "Color Identity": "U", "Type Line": "Creature — Wizard"})
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "oracle_cards.json").write_text(json.dumps([{
            "name": "Test Card", "oracle_id": "oracle-test",
            "image_uris": {"normal": "https://cards.scryfall.io/test.jpg"},
            "legalities": {"standard": "legal", "pauper": "not_legal"},
            "all_parts": [
                {"id": "token-id", "component": "token", "name": "Wizard", "type_line": "Token Creature — Wizard"},
                {"id": "other-id", "component": "combo_piece", "name": "Other Card", "type_line": "Creature — Wizard"},
            ],
        }]), encoding="utf-8")
        payload = web.load_collection(csv_path, cache)
        self.assertEqual(payload["summary"], {"cards": 1, "rows": 2, "copies": 5, "uniqueNames": 1})
        self.assertEqual(payload["cards"][0]["imageUrl"], "https://cards.scryfall.io/normal/front/1/1/11111111-1111-4111-8111-111111111111.jpg")
        self.assertEqual(payload["cards"][0]["scryfallUrl"], "https://scryfall.com/card/11111111-1111-4111-8111-111111111111")
        self.assertEqual(payload["cards"][0]["typeLine"], "Creature — Wizard")
        self.assertEqual(payload["cards"][0]["quantity"], 5)
        self.assertEqual(payload["cards"][0]["setCodes"], ["TST", "ALT"])
        self.assertEqual(len(payload["cards"][0]["printings"]), 2)
        self.assertEqual(payload["cards"][0]["printings"][1]["imageUrl"],
                         "https://cards.scryfall.io/normal/front/2/2/22222222-2222-4222-8222-222222222222.jpg")
        self.assertEqual(payload["cards"][0]["printings"][1]["scryfallUrl"],
                         "https://scryfall.com/card/22222222-2222-4222-8222-222222222222")
        self.assertEqual(payload["cards"][0]["legalities"]["standard"], "legal")
        self.assertEqual([token["name"] for token in payload["cards"][0]["relatedTokens"]], ["Wizard"])

    def test_card_images_uses_faces_for_double_faced_card(self):
        front, back = web.card_images({"card_faces": [
            {"image_uris": {"normal": "front.jpg"}},
            {"image_uris": {"normal": "back.jpg"}},
        ]})
        self.assertEqual((front, back), ("front.jpg", "back.jpg"))

    def test_hand_tester_module_is_served_to_browser(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler({"cards": []}))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen("http://127.0.0.1:{}/hand_tester.js".format(server.server_port)) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("MtgHandTester", response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_simulation_stats_module_is_served_to_browser(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler({"cards": []}))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen("http://127.0.0.1:{}/simulation_stats.js".format(server.server_port)) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("MtgSimulationStats", response.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_mobile_styles_keep_saved_decks_and_fit_chat_within_phone_width(self):
        stylesheet = (web.ROOT / "web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn("@media (max-width: 1000px)", stylesheet)
        self.assertIn(".deck-library { display: block;", stylesheet)
        self.assertIn("#saved-decks { display: flex;", stylesheet)
        self.assertIn(".chat-messages { min-height: 34dvh;", stylesheet)
        self.assertIn(".chat-form { grid-template-columns: minmax(0, 1fr) auto;", stylesheet)

    def test_chat_model_picker_defaults_to_gpt_5_6_luna(self):
        app_script = (web.ROOT / "web" / "app.js").read_text(encoding="utf-8")

        self.assertIn("const DEFAULT_CHAT_MODEL = 'gpt-5.6-luna';", app_script)
        self.assertIn("model.id === DEFAULT_CHAT_MODEL", app_script)

    def _post_simulation(self, decks, submitted):
        outputs = self.root / "outputs"
        for deck in decks:
            directory = outputs / deck["format"]
            directory.mkdir(parents=True, exist_ok=True)
            (directory / (deck["name"].lower().replace(" ", "-") + ".json")).write_text(
                json.dumps(deck), encoding="utf-8"
            )

        class FakeArchitect:
            DEFAULT_FORGE = "/forge"

        with patch.object(web, "ROOT", self.root), patch.object(web, "ForgeSimulationService") as service_type:
            service = service_type.return_value
            service.available.return_value = True
            service.start.return_value = {"id": "a" * 32, "status": "queued"}
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(
                {"cards": []}, architect=FakeArchitect(), engine_cards=[]
            ))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps(submitted).encode("utf-8")
                request = Request(
                    "http://127.0.0.1:{}/api/simulations".format(server.server_port),
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                try:
                    with urlopen(request) as response:
                        return response.status, json.loads(response.read()), service
                except HTTPError as error:
                    return error.code, json.loads(error.read()), service
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_battle_endpoint_starts_valid_same_format_pauper_and_standard_pairs(self):
        for format_name in ("pauper", "standard"):
            with self.subTest(format=format_name):
                deck_a = {"name": "Deck A", "format": format_name, "main": {"Card A": 60}}
                deck_b = {"name": "Deck B", "format": format_name, "main": {"Card B": 60}}
                status, result, service = self._post_simulation(
                    [deck_a, deck_b],
                    {"mode": "battle", "format": format_name, "deckA": "Deck A", "deckB": "Deck B", "games": 2},
                )
                self.assertEqual(status, 202)
                self.assertEqual(result["status"], "queued")
                service.start.assert_called_once_with(deck_a, [deck_b], 2)

    def test_battle_endpoint_rejects_missing_and_mismatched_decks(self):
        deck_a = {"name": "Deck A", "format": "pauper", "main": {"Card A": 60}}
        standard = {"name": "Deck B", "format": "standard", "main": {"Card B": 60}}
        cases = (
            ([deck_a], {"mode": "battle", "format": "pauper", "deckA": "Deck A", "deckB": "Missing"}),
            ([deck_a, standard], {"mode": "battle", "format": "pauper", "deckA": "Deck A", "deckB": "Deck B"}),
        )
        for decks, submitted in cases:
            with self.subTest(submitted=submitted):
                status, result, service = self._post_simulation(decks, submitted)
                self.assertEqual(status, 400)
                self.assertIn("saved", result["error"].casefold())
                service.start.assert_not_called()

    def test_battle_endpoint_rejects_commander_pairwise_route(self):
        decks = [
            {"name": name, "format": "commander", "main": {"Card": 99}, "commanders": {"Leader": 1}}
            for name in ("Deck A", "Deck B")
        ]
        status, result, service = self._post_simulation(
            decks,
            {"mode": "battle", "format": "commander", "deckA": "Deck A", "deckB": "Deck B"},
        )
        self.assertEqual(status, 400)
        self.assertIn("commander", result["error"].casefold())
        service.start.assert_not_called()

    def test_existing_commander_simulation_still_requires_three_opponents(self):
        names = ("Primary", "Opponent One", "Opponent Two", "Opponent Three")
        decks = [
            {"name": name, "format": "commander", "main": {"Card": 99}, "commanders": {"Leader": 1}}
            for name in names
        ]
        status, result, service = self._post_simulation(
            decks,
            {"format": "commander", "deckName": "Primary",
             "opponents": list(names[1:]), "games": 4},
        )
        self.assertEqual(status, 202)
        self.assertEqual(result["status"], "queued")
        self.assertEqual(service.start.call_args.args[0]["name"], "Primary")
        self.assertEqual([item["name"] for item in service.start.call_args.args[1]], list(names[1:]))

    def test_printing_images_use_exact_card_uuid_and_reject_invalid_identifiers(self):
        front, back = web.printing_image_urls("33333333-3333-4333-8333-333333333333")
        self.assertEqual(front, "https://cards.scryfall.io/normal/front/3/3/33333333-3333-4333-8333-333333333333.jpg")
        self.assertEqual(back, "https://cards.scryfall.io/normal/back/3/3/33333333-3333-4333-8333-333333333333.jpg")
        self.assertEqual(web.printing_image_urls("not-a-scryfall-id"), ("", ""))

    def test_missing_collection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            web.choose_collection(self.root / "missing.csv")

    def test_archive_saved_deck_moves_complete_bundle_to_trash(self):
        output = self.root / "outputs"
        directory = output / "standard"
        directory.mkdir(parents=True)
        slug = "test-deck"
        (directory / (slug + ".json")).write_text(json.dumps({
            "name": "Test Deck", "format": "standard", "main": {}
        }), encoding="utf-8")
        (directory / (slug + ".dck")).write_text("[Main]\n", encoding="utf-8")
        (directory / (slug + "-report.md")).write_text("# Test Deck\n", encoding="utf-8")
        result = web.archive_saved_deck("standard", "Test Deck", output)
        self.assertEqual(len(result["moved"]), 3)
        self.assertFalse((directory / (slug + ".json")).exists())
        self.assertTrue(Path(result["trash"]).is_dir())

    def test_network_handler_requires_basic_auth_except_healthcheck(self):
        handler = web.make_handler({}, auth_username="tester", auth_password="a-strong-test-password")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:{}".format(server.server_port)
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/api/collection")
            self.assertEqual(error.exception.code, 401)
            request = Request(base + "/api/collection", headers={
                "Authorization": "Basic dGVzdGVyOmEtc3Ryb25nLXRlc3QtcGFzc3dvcmQ="
            })
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)
            with urlopen(base + "/healthz") as response:
                self.assertEqual(json.loads(response.read()), {"ok": True})
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_chat_endpoint_uses_server_bridge(self):
        class FakeChat:
            def status(self):
                return {"available": True, "message": "connected"}

            def ask(self, request, saved_decks, cards, model=None):
                self.received = (request["message"], cards)
                return "A grounded suggestion."

        chat = FakeChat()
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler({"cards": [{"name": "Owned Card"}]}, chat=chat))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:{}".format(server.server_port)
        try:
            with urlopen(base + "/api/chat/status") as response:
                self.assertTrue(json.loads(response.read())["available"])
            body = json.dumps({"message": "suggest an edit", "history": [], "deckName": ""}).encode("utf-8")
            request = Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                self.assertEqual(json.loads(response.read()), {"answer": "A grounded suggestion."})
            self.assertEqual(chat.received, ("suggest an edit", [{"name": "Owned Card"}]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_chat_generated_deck_is_validated_and_saved(self):
        architect = web.load_architect()
        engine_cards = [
            {"name": "Owned Sprout", "quantity": 4, "mana_cost": "{G}", "mana_value": 1,
             "color_identity": ["G"], "type_line": "Creature — Plant", "oracle_text": "Trample.",
             "legalities": {"pauper": "legal"}, "printings": [{"set": "TST", "collector": "1"}]},
            {"name": "Forest", "quantity": 1, "mana_cost": "", "mana_value": 0,
             "color_identity": [], "type_line": "Basic Land — Forest", "oracle_text": "({T}: Add {G}.)",
             "legalities": {"pauper": "not_legal"}, "printings": [{"set": "TST", "collector": "2"}]},
        ]
        deck = {"name": "Chat Fixture Deck", "format": "pauper",
                "main": {"Owned Sprout": 4, "Forest": 56}, "sideboard": {}, "commanders": {},
                "archetype": "mono-green aggro", "colors": ["G"], "power_target": "fixture",
                "report": {"primary_objective": "Win through creature combat."}}

        class FakeDeckChat:
            def is_deck_creation_request(self, request):
                return "create" in request.get("message", "").casefold()

            def create_deck(self, request, saved_decks, cards, engine, owned_cards, model=None):
                return {"deck": deck, "answer": "Created the fixture deck."}

        chat = FakeDeckChat()
        with patch.object(web, "ROOT", self.root):
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(
                {"cards": []}, architect=architect, engine_cards=engine_cards, chat=chat
            ))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:{}".format(server.server_port)
            try:
                body = json.dumps({"message": "Create a mono-green Pauper deck."}).encode("utf-8")
                request = Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 201)
                self.assertTrue(result["deck"]["validation"]["valid"])
                self.assertTrue(Path(result["files"]["json"]).is_file())
                self.assertTrue(Path(result["files"]["forge"]).is_file())
                with urlopen(base + "/api/decks") as response:
                    saved_decks = json.loads(response.read())
                self.assertIn(deck["name"], [saved["name"] for saved in saved_decks])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_deck_save_keeps_the_provided_strategy_report(self):
        architect = web.load_architect()
        engine_cards = [
            {"name": "Owned Sprout", "quantity": 4, "mana_cost": "{G}", "mana_value": 1,
             "color_identity": ["G"], "type_line": "Creature â€” Plant", "oracle_text": "Trample.",
             "legalities": {"pauper": "legal"}, "printings": [{"set": "TST", "collector": "1"}]},
            {"name": "Forest", "quantity": 1, "mana_cost": "", "mana_value": 0,
             "color_identity": [], "type_line": "Basic Land â€” Forest", "oracle_text": "({T}: Add {G}.)",
             "legalities": {"pauper": "not_legal"}, "printings": [{"set": "TST", "collector": "2"}]},
        ]
        report = {"primary_objective": "Marvel Heroes gain life and grow a counter board.",
                  "early_game": "Deploy cheap Heroes and establish the life-gain engine."}
        with patch.object(web, "ROOT", self.root):
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(
                {"cards": []}, architect=architect, engine_cards=engine_cards
            ))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                body = json.dumps({
                    "name": "Report Fixture Deck", "format": "pauper",
                    "main": {"Owned Sprout": 4, "Forest": 56}, "sideboard": {}, "commanders": {},
                    "colors": ["G"], "archetype": "test", "report": report,
                }).encode("utf-8")
                request = Request("http://127.0.0.1:{}/api/decks".format(server.server_port),
                                  data=body, headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    saved = json.loads(response.read())["deck"]
                self.assertEqual(saved["report"]["primary_objective"], report["primary_objective"])
                self.assertEqual(saved["colors"], ["G"])
                saved_file = next((self.root / "outputs" / "pauper").glob("*.json"))
                self.assertEqual(json.loads(saved_file.read_text(encoding="utf-8"))["report"], report)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_bulk_and_deck_imports_update_collection_and_deck_reservations(self):
        csv_path = self.root / "collection.csv"
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Name", "Oracle Name", "Quantity", "Set code", "Scryfall Oracle ID"])
            writer.writeheader()
            writer.writerow({"Name": "Test Card", "Oracle Name": "Test Card", "Quantity": "1",
                             "Set code": "TST", "Scryfall Oracle ID": "oracle-test"})
        cache = self.root / "cache"
        cache.mkdir()
        bulk = cache / "oracle_cards.json"
        bulk.write_text(json.dumps([{
            "name": "Test Card", "oracle_id": "oracle-test", "id": "printing-test",
            "mana_cost": "{U}", "cmc": 1, "colors": ["U"], "color_identity": ["U"],
            "type_line": "Creature — Test", "oracle_text": "A test card.",
            "legalities": {"commander": "legal"},
        }]), encoding="utf-8")
        payload = web.load_collection(csv_path, cache)
        store = web.CollectionImportStore(self.root / "outputs" / "imports.json")

        with patch.object(web, "ROOT", self.root):
            server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(
                payload, import_store=store, collection_csv=csv_path, bulk_path=bulk, cache_dir=cache
            ))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:{}".format(server.server_port)
            try:
                for upload in (
                    {"mode": "bulk", "deckName": "", "csv": b"Name,Quantity,Set code\nTest Card,2,TST\n"},
                    {"mode": "deck", "deckName": "Friday Deck", "csv": b"Name,Quantity,Set code\nTest Card,1,TST\n"},
                ):
                    body = json.dumps({
                        "mode": upload["mode"], "deckName": upload["deckName"],
                        "csvBase64": base64.b64encode(upload["csv"]).decode("ascii"),
                    }).encode("utf-8")
                    request = Request(base + "/api/import", data=body, headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        self.assertEqual(response.status, 201)
                with urlopen(base + "/api/collection") as response:
                    collection = json.loads(response.read())
                card = collection["cards"][0]
                self.assertEqual(collection["summary"]["rows"], 3)
                self.assertEqual(card["quantity"], 4)
                self.assertEqual(card["availability"]["reserved"], 1)
                self.assertEqual(card["availability"]["available"], 3)
                with urlopen(base + "/api/decks") as response:
                    self.assertEqual(json.loads(response.read())[0]["format"], "scanned")

                body = json.dumps({"name": "Friday Deck", "format": "scanned"}).encode("utf-8")
                request = Request(base + "/api/decks", data=body, method="DELETE",
                                  headers={"Content-Type": "application/json"})
                with urlopen(request) as response:
                    self.assertTrue(json.loads(response.read())["removed"])
                with urlopen(base + "/api/collection") as response:
                    card = json.loads(response.read())["cards"][0]
                self.assertEqual(card["quantity"], 4, "deleting a scan deck must retain owned cards")
                self.assertEqual(card["availability"]["available"], 4)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
