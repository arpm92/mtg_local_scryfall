import json
import tempfile
import unittest
from pathlib import Path

from collection_import import CollectionImportStore, prepare_upload


class CollectionImportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.bulk = self.root / "oracle_cards.json"
        self.bulk.write_text(json.dumps([
            {"id": "representative-id", "oracle_id": "oracle-a", "name": "Abrade",
             "cmc": 2, "colors": ["R"], "color_identity": ["R"],
             "type_line": "Instant", "oracle_text": "Choose one — Destroy target artifact; or deal 3 damage.",
             "legalities": {"commander": "legal"}},
            {"id": "representative-id-2", "oracle_id": "oracle-b", "name": "Island",
             "cmc": 0, "colors": [], "color_identity": [],
             "type_line": "Basic Land — Island", "oracle_text": "({T}: Add {U}.)",
             "legalities": {"commander": "legal"}},
        ]), encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_prepare_bulk_upload_enriches_and_preserves_printing_and_quantity(self):
        source = (
            "Name;Quantity;Set code;Collector number;Scryfall ID;Scryfall URL\r\n"
            "Abrade;3;SOA;37;8f12c752-410c-45a6-a220-22cf115defbb;https://scryfall.com/card/soa/37/abrade\r\n"
        ).encode("utf-8-sig")
        result = prepare_upload(source, self.bulk, mode="bulk")
        row = result["rows"][0]
        self.assertEqual(result["summary"], {
            "rows": 1, "copies": 3, "matched_rows": 1, "unmatched_rows": 0,
        })
        self.assertEqual(row["Name"], "Abrade")
        self.assertEqual(row["Quantity"], "3")
        self.assertEqual(row["Set code"], "SOA")
        self.assertEqual(row["Collector number"], "37")
        self.assertEqual(row["Scryfall ID"], "8f12c752-410c-45a6-a220-22cf115defbb")
        self.assertEqual(row["Scryfall URL"], "https://scryfall.com/card/soa/37/abrade")
        self.assertEqual(row["Scryfall Oracle ID"], "oracle-a")
        self.assertEqual(row["Scryfall Match Status"], "matched")
        self.assertIsNone(result["deck"])

    def test_deck_scan_builds_counted_deck_and_store_deduplicates_same_upload(self):
        source = b"Name,Quantity,Set code\nAbrade,2,SOA\nIsland,3,TST\n"
        result = prepare_upload(source, self.bulk, mode="deck", deck_name="Sideboard Practice")
        self.assertEqual(result["deck"], {
            "name": "Sideboard Practice", "format": "scanned",
            "main": {"Abrade": 2, "Island": 3},
        })
        store = CollectionImportStore(self.root / "imports.json")
        self.assertTrue(store.add(result)["added"])
        self.assertFalse(store.add(result)["added"])
        self.assertEqual(len(store.imported_rows()), 2)
        self.assertEqual(store.scanned_decks(), [result["deck"]])

    def test_unknown_and_ambiguous_cards_are_reported_without_creating_partial_deck(self):
        source = b"Name,Quantity\nAbrade,1\nMissing Card,1\n"
        result = prepare_upload(source, self.bulk, mode="deck", deck_name="Incomplete Scan")
        self.assertEqual(result["summary"]["unmatched_rows"], 1)
        self.assertIsNone(result["deck"])
        self.assertEqual(result["unmatched"][0]["Card Name"], "Missing Card")
        self.assertFalse(result["can_create_deck"])

    def test_ambiguous_name_is_not_guessed_for_a_deck_scan(self):
        records = json.loads(self.bulk.read_text(encoding="utf-8"))
        duplicate = dict(records[0], id="different-printing", oracle_id="oracle-ambiguous")
        records.append(duplicate)
        ambiguous_bulk = self.root / "ambiguous.json"
        ambiguous_bulk.write_text(json.dumps(records), encoding="utf-8")
        result = prepare_upload(
            b"Name,Quantity\nAbrade,1\n", ambiguous_bulk,
            mode="deck", deck_name="Ambiguous deck scan",
        )
        self.assertIsNone(result["deck"])
        self.assertIn("Ambiguous name", result["unmatched"][0]["Reason"])

    def test_bad_headers_and_nonpositive_quantities_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "card name"):
            prepare_upload(b"Quantity,Set code\n1,TST\n", self.bulk, mode="bulk")
        with self.assertRaisesRegex(ValueError, "positive whole number"):
            prepare_upload(b"Name,Quantity\nAbrade,0\n", self.bulk, mode="bulk")

    def test_store_rejects_malformed_existing_data_without_overwriting_it(self):
        path = self.root / "imports.json"
        path.write_text("not json", encoding="utf-8")
        store = CollectionImportStore(path)
        with self.assertRaisesRegex(ValueError, "invalid"):
            store.imported_rows()
        self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_removing_scanned_deck_releases_reservation_but_keeps_imported_cards(self):
        store = CollectionImportStore(self.root / "imports.json")
        prepared = prepare_upload(b"Name,Quantity\nAbrade,2\n", self.bulk, mode="deck", deck_name="Friday Deck")
        store.add(prepared)

        result = store.remove_scanned_deck("friday deck")

        self.assertTrue(result["removed"])
        self.assertEqual(store.scanned_decks(), [])
        self.assertEqual(len(store.imported_rows()), 1)
        self.assertEqual(store.imported_rows()[0]["Quantity"], "2")

    def test_different_scan_cannot_reuse_an_existing_deck_name(self):
        store = CollectionImportStore(self.root / "imports.json")
        store.add(prepare_upload(b"Name,Quantity\nAbrade,2\n", self.bulk, mode="deck", deck_name="Friday Deck"))
        with self.assertRaisesRegex(ValueError, "already exists"):
            store.add(prepare_upload(b"Name,Quantity\nAbrade,3\n", self.bulk, mode="deck", deck_name="Friday Deck"))


if __name__ == "__main__":
    unittest.main()
