import csv
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

            def ask(self, request, saved_decks, cards):
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

            def create_deck(self, request, saved_decks, cards, engine, owned_cards):
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
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
