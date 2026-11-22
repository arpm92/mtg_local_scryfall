import unittest

from collection_allocation import annotate_card_availability, summarize_allocations


class CollectionAllocationTests(unittest.TestCase):
    def test_reservations_combine_main_sideboard_and_commanders_case_insensitively(self):
        cards = [{"name": "Abrade", "quantity": 4, "oracleId": "oracle-1"}]
        decks = [
            {"name": "Standard Heroes", "main": {"Abrade": 2},
             "sideboard": {"ABRADE": 1}, "commanders": {}},
            {"name": "Commander", "main": {}, "sideboard": {}, "commanders": {"abrade": 1}},
        ]

        result = annotate_card_availability(cards, decks)

        self.assertEqual(result[0]["availability"], {
            "totalOwned": 4,
            "reserved": 4,
            "available": 0,
            "shortage": 0,
            "reservedByDeck": [
                {"name": "Standard Heroes", "quantity": 3},
                {"name": "Commander", "quantity": 1},
            ],
        })
        self.assertEqual(cards[0]["quantity"], 4, "input collection rows must not be mutated")

    def test_shortage_never_makes_available_quantity_negative(self):
        result = annotate_card_availability(
            [{"name": "Forest", "quantity": 2}],
            [{"name": "Overfilled", "main": {"forest": 3}}],
        )

        self.assertEqual(result[0]["availability"]["available"], 0)
        self.assertEqual(result[0]["availability"]["shortage"], 1)

    def test_summary_aggregates_multiple_collection_groups_of_same_name(self):
        result = summarize_allocations(
            [{"name": "Abrade", "quantity": 2}, {"name": "ABRADE", "quantity": 3}],
            [{"name": "Draft deck", "main": {"Abrade": 2}}],
        )

        self.assertEqual(result["abrade"]["totalOwned"], 5)
        self.assertEqual(result["abrade"]["reserved"], 2)
        self.assertEqual(result["abrade"]["available"], 3)

    def test_proposed_decks_do_not_reserve_collection_copies(self):
        result = annotate_card_availability(
            [{"name": "Abrade", "quantity": 2}],
            [{"name": "Proposed deck", "ownership_mode": "proposal", "main": {"Abrade": 4}}],
        )

        self.assertEqual(result[0]["availability"]["available"], 2)
        self.assertEqual(result[0]["availability"]["reserved"], 0)


if __name__ == "__main__":
    unittest.main()
