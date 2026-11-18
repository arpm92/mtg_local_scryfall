"""Background Forge simulation jobs for the collection web application."""

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).casefold()).strip("-") or "deck"


class SimulationBusyError(RuntimeError):
    """Raised when the single Forge worker is already busy."""


class ForgeSimulationService:
    """Validate and run one bounded Forge simulation at a time."""

    def __init__(self, architect, cards, forge_dir, output_root, max_games=10):
        self.architect = architect
        self.cards = cards
        self.forge_dir = Path(forge_dir)
        self.output_root = Path(output_root)
        self.max_games = max_games
        self.lock = threading.Lock()
        self.jobs = {}
        self.active_job = None

    def available(self):
        jar = self.forge_dir / self.architect.FORGE_JAR
        if not jar.is_file():
            candidates = sorted(self.forge_dir.glob("forge-gui-desktop-*-jar-with-dependencies.jar"))
            jar = candidates[0] if len(candidates) == 1 else jar
        return jar.is_file() and (self.forge_dir / "res").is_dir() and bool(shutil.which("java"))

    def start(self, deck, opponents, games=10):
        if not self.available():
            raise RuntimeError("Forge simulation is unavailable: Java 17 and the Forge installation are required.")
        format_name = str(deck.get("format") or "").casefold()
        if format_name not in ("commander", "pauper", "standard", "legacy"):
            raise ValueError("Only Commander, Pauper, Standard, and Legacy decks can be simulated.")
        config = self.architect.load_format(format_name)
        try:
            games = int(games)
        except (TypeError, ValueError) as error:
            raise ValueError("Choose a whole number of games.") from error
        if not 1 <= games <= self.max_games:
            raise ValueError("Choose between 1 and {} games per simulation.".format(self.max_games))
        opponents = list(opponents or [])
        required = int(config["simulation"]["players"]) - 1
        if len(opponents) != required:
            raise ValueError("{} requires {} same-format opponent deck{}.".format(
                config["display_name"], required, "" if required == 1 else "s"))
        if len({str(item.get("name") or "").casefold() for item in opponents}) != len(opponents):
            raise ValueError("Choose different opponent decks for each seat.")

        participants = [deck] + opponents
        validations = []
        for participant in participants:
            if participant.get("format") != format_name:
                raise ValueError("All simulation decks must use the same format.")
            validation = self.architect.validate_deck(participant, self.cards, format_name)
            if not validation.get("valid"):
                raise ValueError("{} is not valid for this simulation: {}".format(
                    participant.get("name") or "Deck", " ".join(validation.get("errors") or [])))
            validations.append({"deck": participant.get("name"), "valid": True, "counts": validation.get("counts", {})})

        with self.lock:
            if self.active_job:
                active = self.jobs.get(self.active_job) or {}
                if active.get("status") in ("queued", "running"):
                    raise SimulationBusyError("A Forge simulation is already running. Wait for it to finish before starting another.")
            job_id = uuid.uuid4().hex
            job_dir = self.output_root / format_name / "simulations" / job_id
            deck_dir = job_dir / "decks"
            deck_dir.mkdir(parents=True, exist_ok=False)
            deck_paths = []
            for seat, participant in enumerate(participants, 1):
                path = deck_dir / "seat-{}-{}.dck".format(seat, _slug(participant["name"]))
                self.architect.export_forge(participant, path, self.cards)
                deck_paths.append(path)
            record = {
                "id": job_id,
                "status": "queued",
                "format": format_name,
                "deck": deck.get("name"),
                "opponents": [item.get("name") for item in opponents],
                "players": len(participants),
                "games_requested": games,
                "deck_validations": validations,
                "simulation_mode": config["simulation"]["forge_game_type"],
                "seed": int(uuid.UUID(hex=job_id)) % (2 ** 31),
                "players_in_seat_order": [item.get("name") for item in participants],
                "created_at": _now(),
                "output_directory": str(job_dir.relative_to(self.output_root)),
            }
            self.jobs[job_id] = record
            self.active_job = job_id
            self._save(job_dir, record)
            thread = threading.Thread(
                target=self._run,
                args=(job_id, deck_paths, format_name, games, record["seed"], job_dir),
                daemon=True,
                name="forge-simulation-{}".format(job_id[:8]),
            )
            thread.start()
            return dict(record)

    def _run(self, job_id, deck_paths, format_name, games, seed, job_dir):
        with self.lock:
            record = self.jobs[job_id]
            record.update({"status": "running", "started_at": _now()})
            self._save(job_dir, record)
        try:
            game_runs = []
            stdout_parts, stderr_parts = [], []
            for index in range(games):
                rotation = index % len(deck_paths)
                rotated_paths = deck_paths[rotation:] + deck_paths[:rotation]
                game_seed = seed + index
                result = self.architect.run_forge_simulation(
                    rotated_paths,
                    format_name,
                    forge_dir=self.forge_dir,
                    games=1,
                    seed=game_seed,
                    timeout_seconds=180,
                )
                stdout_parts.append("=== Game {} / seed {} / seats {} ===\n{}".format(
                    index + 1, game_seed, ", ".join(path.stem for path in rotated_paths), result.get("stdout", "")))
                stderr_parts.append("=== Game {} / seed {} ===\n{}".format(index + 1, game_seed, result.get("stderr", "")))
                forge_output = "\n".join((result.get("stdout", ""), result.get("stderr", "")))
                unsupported_cards = sorted(set(re.findall(
                    r'unsupported card was requested:\s*["\u201c](.*?)["\u201d]',
                    forge_output,
                    flags=re.IGNORECASE,
                )))
                unsupported = bool(unsupported_cards) or "could not find this card in the database" in forge_output.casefold()
                game_runs.append({
                    "game": index + 1,
                    "seed": game_seed,
                    "seat_order": [path.stem for path in rotated_paths],
                    "status": "unsupported" if unsupported else result.get("status"),
                    "completed_games": 0 if unsupported else int(result.get("completed_games") or 0),
                    "game_results": [] if unsupported else result.get("game_results", []),
                    "reported_game_results": result.get("game_results", []) if unsupported else [],
                    "unsupported_cards": unsupported_cards,
                    "exit_code": result.get("exit_code"),
                    "command": result.get("command", []),
                })
            (job_dir / "stdout.log").write_text("\n".join(stdout_parts), encoding="utf-8")
            (job_dir / "stderr.log").write_text("\n".join(stderr_parts), encoding="utf-8")
            completed = sum(run["completed_games"] for run in game_runs)
            result_lines = [line for run in game_runs for line in run["game_results"]]
            unsupported_cards = sorted(set(
                name for run in game_runs for name in run.get("unsupported_cards", [])
            ))
            if unsupported_cards or any(run["status"] == "unsupported" for run in game_runs):
                status = "unsupported"
            elif completed == games:
                status = "completed"
            elif any(run["status"] == "timeout" for run in game_runs):
                status = "timeout"
            elif completed:
                status = "partial"
            else:
                status = "failed"
            update = {
                "status": status,
                "attempted_games": len(game_runs),
                "completed_games": completed,
                "game_runs": game_runs,
                "game_results": result_lines,
                "commands": [run.get("command") for run in game_runs if run.get("command")],
                "unsupported_cards": unsupported_cards,
                "stdout_log": str((job_dir / "stdout.log").relative_to(self.output_root)),
                "stderr_log": str((job_dir / "stderr.log").relative_to(self.output_root)),
                "finished_at": _now(),
            }
            if status != "completed":
                if status == "unsupported":
                    update["message"] = "Forge could not be simulated faithfully because it does not support: {}. These games do not count as completed.".format(
                        ", ".join(unsupported_cards) or "one or more cards"
                    )
                else:
                    update["message"] = "Forge completed {} of {} requested games; review the saved logs.".format(completed, games)
        except Exception as error:  # Preserve failures for diagnosis in the report.
            (job_dir / "error.log").write_text(str(error), encoding="utf-8")
            update = {
                "status": "failed",
                "completed_games": 0,
                "message": str(error),
                "error_log": str((job_dir / "error.log").relative_to(self.output_root)),
                "finished_at": _now(),
            }
        with self.lock:
            self.jobs[job_id].update(update)
            self._save(job_dir, self.jobs[job_id])
            if self.active_job == job_id:
                self.active_job = None

    def get(self, job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", str(job_id or "")):
            return None
        with self.lock:
            if job_id in self.jobs:
                return dict(self.jobs[job_id])
        for format_name in ("commander", "pauper", "standard", "legacy"):
            path = self.output_root / format_name / "simulations" / job_id / "simulation.json"
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return None
        return None

    def _save(self, job_dir, record):
        path = job_dir / "simulation.json"
        temporary = job_dir / ".simulation.json.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
