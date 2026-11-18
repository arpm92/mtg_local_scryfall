import json
import tempfile
import time
import unittest
from pathlib import Path

from forge_simulation import ForgeSimulationService


class FakeArchitect:
    FORGE_JAR = "forge.jar"

    @staticmethod
    def load_format(_format):
        return {"display_name": "Legacy", "simulation": {"players": 2, "forge_game_type": "constructed"}}

    @staticmethod
    def validate_deck(_deck, _cards, _format):
        return {"valid": True, "counts": {"main": 60, "sideboard": 0, "commanders": 0}}

    @staticmethod
    def export_forge(deck, path, _cards):
        Path(path).write_text("[metadata]\nName={}\n[Main]\n".format(deck["name"]), encoding="utf-8")

    @staticmethod
    def run_forge_simulation(*_args, **_kwargs):
        return {
            "status": "completed",
            "completed_games": 1,
            "game_results": ["Game Result: Game 1 status=NORMAL_WIN winner=Player 1"],
            "stdout": "Game Result: Game 1 status=NORMAL_WIN winner=Player 1",
            "stderr": 'An unsupported card was requested: "Unknown Card" from "[N.A.]".',
            "exit_code": 0,
            "command": ["java", "-jar", "forge.jar"],
        }


class ForgeSimulationTests(unittest.TestCase):
    def test_unsupported_forge_card_does_not_count_as_completed_game(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = ForgeSimulationService(FakeArchitect(), [], temporary, temporary)
            service.available = lambda: True
            deck = {"name": "Candidate", "format": "legacy", "main": {"Unknown Card": 1}}
            opponent = {"name": "Opponent", "format": "legacy", "main": {"Island": 60}}
            job = service.start(deck, [opponent], games=1)
            deadline = time.time() + 5
            while service.jobs[job["id"]]["status"] in ("queued", "running") and time.time() < deadline:
                time.sleep(0.01)

            result = service.get(job["id"])
            self.assertEqual(result["status"], "unsupported")
            self.assertEqual(result["attempted_games"], 1)
            self.assertEqual(result["completed_games"], 0)
            self.assertIn("Unknown Card", result["unsupported_cards"])
            self.assertIn("could not be simulated", result["message"])


if __name__ == "__main__":
    unittest.main()
