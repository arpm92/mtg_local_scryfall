import csv
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import enrich_mtg_collection as app


def card(name, oracle_id, print_id, **overrides):
    value = {
        "name": name,
        "oracle_id": oracle_id,
        "id": print_id,
        "oracle_text": "Draw a card.",
        "mana_cost": "{U}",
        "cmc": 1.0,
        "colors": ["U"],
        "color_identity": ["U"],
        "type_line": "Instant",
        "keywords": [],
        "produced_mana": [],
        "legalities": {"commander": "legal"},
        "scryfall_uri": "https://scryfall.com/card/tst/1/test",
    }
    value.update(overrides)
    return value


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.cards = [
            card("Opt", "oracle-opt", "print-opt"),
            card(
                "Delver of Secrets // Insectile Aberration",
                "oracle-delver",
                "print-delver",
                oracle_text=None,
                mana_cost=None,
                type_line=None,
                colors=None,
                cmc=1.0,
                card_faces=[
                    {"name": "Delver of Secrets", "oracle_text": "At upkeep, look.",
                     "mana_cost": "{U}", "type_line": "Creature — Human Wizard", "colors": ["U"]},
                    {"name": "Insectile Aberration", "oracle_text": "Flying",
                     "mana_cost": "", "type_line": "Creature — Human Insect", "colors": ["U"]},
                ],
            ),
            card(
                "Fire // Ice",
                "oracle-fire-ice",
                "print-fire-ice",
                oracle_text=None,
                mana_cost=None,
                type_line=None,
                colors=["U", "R"],
                color_identity=["U", "R"],
                cmc=4.0,
                card_faces=[
                    {"name": "Fire", "oracle_text": "Fire deals 2 damage divided.",
                     "mana_cost": "{1}{R}", "type_line": "Instant"},
                    {"name": "Ice", "oracle_text": "Tap target permanent. Draw a card.",
                     "mana_cost": "{1}{U}", "type_line": "Instant"},
                ],
            ),
            card("Shared Name", "oracle-shared-a", "print-shared-a"),
            card("Shared Name", "oracle-shared-b", "print-shared-b"),
            card(
                "Opt // Opt", "oracle-opt-art", "print-opt-art",
                layout="art_series",
                card_faces=[{"name": "Opt"}, {"name": "Opt"}],
            ),
        ]
        self.bulk = self.root / "oracle_cards.json"
        self.bulk.write_text(json.dumps(self.cards), encoding="utf-8")
        self.indexes = app.build_index(self.bulk)

    def tearDown(self):
        self.tempdir.cleanup()

    def write_collection(self, rows, fields=None, delimiter=",", encoding="utf-8"):
        path = self.root / "collection.csv"
        fields = fields or ["Name", "Set code", "Quantity", "Scryfall ID"]
        with path.open("w", encoding=encoding, newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def enrich(self, rows):
        source = self.write_collection(rows)
        output = self.root / "output.csv"
        unmatched = self.root / "unmatched.csv"
        app.enrich_collection(source, output, unmatched, self.indexes)
        _, output_rows, _, _ = app.read_csv(output)
        _, unmatched_rows, _, _ = app.read_csv(unmatched)
        return output_rows, unmatched_rows

    def test_single_card_matches_by_print_id_and_extracts_fields(self):
        rows, unmatched = self.enrich([
            {"Name": "wrong name", "Set code": "TST", "Quantity": "2", "Scryfall ID": "print-opt"}
        ])
        self.assertEqual(rows[0]["Oracle Name"], "Opt")
        self.assertEqual(rows[0]["Mana Value"], "1.0")
        self.assertEqual(rows[0]["Color Identity"], "U")
        self.assertEqual(rows[0]["Oracle Text"], "Draw a card.")
        self.assertEqual(unmatched, [])

    def test_exact_card_name_beats_art_series_face_alias(self):
        rows, unmatched = self.enrich([
            {"Name": "Opt", "Set code": "INV", "Quantity": "1", "Scryfall ID": "non-representative"}
        ])
        self.assertEqual(rows[0]["Oracle Name"], "Opt")
        self.assertEqual(rows[0]["Scryfall Oracle ID"], "oracle-opt")
        self.assertEqual(unmatched, [])

    def test_double_faced_and_split_fields(self):
        rows, _ = self.enrich([
            {"Name": "Delver of Secrets", "Set code": "ISD", "Quantity": "1", "Scryfall ID": "bad"},
            {"Name": "Fire // Ice", "Set code": "APC", "Quantity": "1", "Scryfall ID": ""},
        ])
        self.assertIn("Delver of Secrets:\nAt upkeep, look.", rows[0]["Oracle Text"])
        self.assertEqual(rows[0]["Mana Cost"], "{U}")
        self.assertIn("Fire deals 2 damage", rows[1]["Oracle Text"])
        self.assertEqual(rows[1]["Mana Cost"], "{1}{R} // {1}{U}")
        self.assertEqual(rows[1]["Color Identity"], "U,R")
        self.assertEqual(rows[1]["Mana Value"], "4.0")

    def test_duplicate_printings_preserve_rows_and_quantities(self):
        source_rows = [
            {"Name": "Opt", "Set code": "INV", "Quantity": "3", "Scryfall ID": "old-print"},
            {"Name": "Opt", "Set code": "XLN", "Quantity": "4", "Scryfall ID": "new-print"},
        ]
        rows, unmatched = self.enrich(source_rows)
        self.assertEqual([row["Set code"] for row in rows], ["INV", "XLN"])
        self.assertEqual([row["Quantity"] for row in rows], ["3", "4"])
        self.assertEqual([row["Scryfall ID"] for row in rows], ["old-print", "new-print"])
        self.assertTrue(all(row["Scryfall Match Status"] == "matched" for row in rows))
        self.assertEqual(unmatched, [])

    def test_bad_identifier_falls_back_to_name_and_unknown_is_reported(self):
        rows, unmatched = self.enrich([
            {"Name": "Opt", "Set code": "TST", "Quantity": "1", "Scryfall ID": "incorrect"},
            {"Name": "Not A Card", "Set code": "TST", "Quantity": "1", "Scryfall ID": "missing"},
        ])
        self.assertEqual(rows[0]["Scryfall Match Method"], "exact card/face name")
        self.assertEqual(rows[1]["Scryfall Match Status"], "unmatched")
        self.assertEqual(len(unmatched), 1)
        self.assertIn("not found", unmatched[0]["Reason"].lower())

    def test_ambiguous_name_is_not_guessed(self):
        rows, unmatched = self.enrich([
            {"Name": "Shared Name", "Set code": "TST", "Quantity": "1", "Scryfall ID": ""}
        ])
        self.assertEqual(rows[0]["Scryfall Match Status"], "unmatched")
        self.assertIn("Ambiguous name", unmatched[0]["Reason"])

    def test_semicolon_cp1252_csv_is_read(self):
        source = self.write_collection(
            [{"Name": "Caf\u00e9 Card", "Quantity": "1"}],
            fields=["Name", "Quantity"], delimiter=";", encoding="cp1252",
        )
        fields, rows, delimiter, encoding = app.read_csv(source)
        self.assertEqual(fields, ["Name", "Quantity"])
        self.assertEqual(rows[0]["Name"], "Caf\u00e9 Card")
        self.assertEqual(delimiter, ";")
        self.assertEqual(encoding, "cp1252")

    def test_blank_duplicate_and_malformed_columns_are_rejected(self):
        duplicate = self.root / "duplicate.csv"
        duplicate.write_text("Name,name\nOpt,Opt\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "blank or duplicate"):
            app.read_csv(duplicate)
        malformed = self.root / "malformed.csv"
        malformed.write_text("Name,Quantity\nOpt\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "fewer values"):
            app.read_csv(malformed)
        missing = self.write_collection([{"Other": "value"}], fields=["Other"])
        output = self.root / "missing-output.csv"
        report = self.root / "missing-report.csv"
        with self.assertRaisesRegex(ValueError, "Could not identify"):
            app.enrich_collection(missing, output, report, self.indexes)

    def test_multi_face_produced_mana_is_aggregated(self):
        value = card(
            "Pathway Front // Pathway Back", "oracle-pathway", "print-pathway",
            produced_mana=None, colors=[], color_identity=["U", "R"],
            card_faces=[
                {"name": "Pathway Front", "produced_mana": ["U"]},
                {"name": "Pathway Back", "produced_mana": ["R"]},
            ],
        )
        enriched = app.make_enrichment(value, "test", "")
        self.assertEqual(enriched["Produced Mana"], "U,R")

    def test_cache_reuse_avoids_network(self):
        cache_dir = self.root / "cache"
        cache_dir.mkdir()
        cached = cache_dir / "oracle_cards.json"
        cached.write_text("[]", encoding="utf-8")
        os.utime(cached, (datetime.now(timezone.utc).timestamp(),) * 2)
        with mock.patch.object(app, "http_json") as request:
            result = app.ensure_bulk_data(cache_dir)
        self.assertEqual(result, cached)
        request.assert_not_called()

    def test_network_failure_uses_stale_cache(self):
        cache_dir = self.root / "stale-cache"
        cache_dir.mkdir()
        cached = cache_dir / "oracle_cards.json"
        cached.write_text("[]", encoding="utf-8")
        os.utime(cached, (0, 0))
        with mock.patch.object(app, "http_json", side_effect=OSError("offline")):
            result = app.ensure_bulk_data(cache_dir)
        self.assertEqual(result, cached)

    def test_network_failure_without_cache_is_clear(self):
        cache_dir = self.root / "empty-cache"
        with mock.patch.object(app, "http_json", side_effect=OSError("offline")):
            with self.assertRaisesRegex(ValueError, "Could not download Scryfall data"):
                app.ensure_bulk_data(cache_dir)


if __name__ == "__main__":
    unittest.main()
