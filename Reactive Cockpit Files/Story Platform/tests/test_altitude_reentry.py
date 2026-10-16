from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from story_platform.environments import EnvironmentRuntime
from story_platform.model import StoryGraph, validate_graph
from story_platform.telemetry import WorldModel


SYSTEM = "Col 359 Sector DZ-U b5-2"
BODY = "Col 359 Sector DZ-U b5-2 B 3 a"
SHIP_SURFACE_FLAGS = (1 << 24) | (1 << 21)


def graph(*, vehicles: tuple[str, ...] = ("ship",), bounded: bool = True) -> dict:
    zones = []
    for vehicle in vehicles:
        zones.append({
            "id": vehicle, "title": vehicle, "active_vehicles": [vehicle],
            "location": {"system": SYSTEM, "body": BODY, **({"max_altitude_m": 20000.0} if bounded else {})},
            "layers": [
                {"id": "wind", "type": "audio", "asset": "wind.mp3", "loop": True, "volume_transition_seconds": 5.0},
                {"id": "danger", "type": "visual", "cue": "deep_danger"},
            ],
        })
    return {
        "schema": "platform.story-graph.v1", "id": "altitude_reentry", "version": "1", "title": "Altitude re-entry",
        "start": "start", "nodes": {"start": {"kind": "start"}}, "edges": [], "environments": zones,
    }


def status(altitude: float | None, *, flags: int = SHIP_SURFACE_FLAGS, flags2: int = 0) -> dict:
    return {
        "timestamp": "2026-10-02T18:22:00Z", "Flags": flags, "Flags2": flags2,
        "BodyName": BODY, "Latitude": 25.0, "Longitude": -85.0,
        **({"Altitude": altitude} if altitude is not None else {}),
    }


class AltitudeReentryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.world = WorldModel()
        self.world.journal_signal({"event": "Location", "StarSystem": SYSTEM, "Body": BODY})
        self.previous_status = None

    def start(self, initial_status: dict, graph_data: dict | None = None) -> None:
        self.world.status_signals(initial_status, None)
        self.previous_status = initial_status
        data = graph_data if graph_data is not None else graph()
        self.assertEqual(validate_graph(data), [])
        self.runtime = EnvironmentRuntime(
            StoryGraph(self.root / "graph.json", data), self.root / "state.json",
            self.root / "events.jsonl", self.root / "outbox.jsonl", printer=lambda _text: None,
        )
        self.runtime.start_session(self.world.data, {})

    def journal(self, event: dict) -> None:
        self.runtime.dispatch(self.world.journal_signal(event), self.world.data, {})

    def update_status(self, current: dict) -> None:
        for signal in self.world.status_signals(current, self.previous_status):
            self.runtime.dispatch(signal, self.world.data, {})
        self.previous_status = current

    def payloads(self, action: str) -> list[dict]:
        outbox = self.root / "outbox.jsonl"
        rows = [json.loads(line) for line in outbox.read_text().splitlines()] if outbox.exists() else []
        return [row["payload"] for row in rows if row["payload"].get("action") == action]

    def replay_logged_exit(self, offset: int) -> None:
        records = json.loads((Path(__file__).parent / "fixtures/atmosphere_exit_telemetry.json").read_text())
        before, transition, after = records[offset:offset + 3]
        self.start(before["status"])
        self.assertGreater(before["status"]["Altitude"], 20000.0)
        self.journal(transition["journal"])
        self.assertNotIn("altitude", self.world.data["location"])
        self.runtime.dispatch({"name": "system.tick", "source": "system", "data": {}}, self.world.data, {})
        self.assertEqual(self.payloads("play_layer"), [])
        self.assertEqual(self.payloads("set_environment_layer"), [])
        self.update_status(after["status"])
        self.assertEqual(self.payloads("play_layer"), [])
        self.assertEqual(self.payloads("set_environment_layer"), [])

    def test_logged_supercruise_entry_above_ceiling_does_not_restart_storm(self) -> None:
        self.replay_logged_exit(0)

    def test_logged_leave_body_in_orbit_does_not_restart_storm(self) -> None:
        self.replay_logged_exit(3)

    def test_unknown_altitude_waits_for_a_known_reading_before_entry(self) -> None:
        self.start(status(None))
        self.assertEqual(self.payloads("play_layer"), [])
        self.assertEqual(self.payloads("set_environment_layer"), [])
        self.update_status(status(1000.0))
        self.assertEqual(len(self.payloads("play_layer")), 1)
        self.assertEqual(len(self.payloads("set_environment_layer")), 1)

    def test_temporary_altitude_gap_keeps_active_layers_then_honors_known_exit(self) -> None:
        self.start(status(1000.0))
        self.journal({"event": "SupercruiseEntry", "StarSystem": SYSTEM})
        self.assertEqual(self.payloads("stop_layer"), [])
        self.assertEqual(self.payloads("stop_environment_layer"), [])
        self.update_status(status(1500.0))
        self.assertEqual(len(self.payloads("play_layer")), 1)
        self.update_status(status(30000.0))
        self.assertEqual(len(self.payloads("stop_layer")), 1)
        self.assertEqual(len(self.payloads("stop_environment_layer")), 1)
        self.assertEqual(self.payloads("stop_layer")[0]["transition_seconds"], 5.0)

    def test_on_foot_missing_altitude_still_allows_surface_ambience(self) -> None:
        self.start(status(None, flags=1 << 21, flags2=1), graph(vehicles=("on_foot",)))
        self.assertEqual(len(self.payloads("play_layer")), 1)
        self.assertEqual(len(self.payloads("set_environment_layer")), 1)

    def test_known_loss_of_surface_coordinates_still_exits_and_blocks_reentry(self) -> None:
        self.start(status(1000.0))
        self.update_status(status(None, flags=1 << 24))
        self.assertEqual(len(self.payloads("stop_layer")), 1)
        self.assertEqual(len(self.payloads("stop_environment_layer")), 1)
        self.runtime.dispatch({"name": "system.tick", "source": "system", "data": {}}, self.world.data, {})
        self.journal({"event": "SupercruiseEntry", "StarSystem": SYSTEM})
        self.assertEqual(len(self.payloads("play_layer")), 1)

    def test_missing_altitude_cannot_satisfy_a_minimum_height_either(self) -> None:
        data = graph()
        data["environments"][0]["location"] = {"system": SYSTEM, "body": BODY, "min_altitude_m": 500.0}
        self.start(status(None), data)
        self.assertEqual(self.payloads("play_layer"), [])
        self.update_status(status(900.0))
        self.assertEqual(len(self.payloads("play_layer")), 1)

    def test_unbounded_planet_ambience_remains_independent_of_altitude(self) -> None:
        self.start(status(None), graph(bounded=False))
        self.assertEqual(len(self.payloads("play_layer")), 1)
        self.journal({"event": "SupercruiseEntry", "StarSystem": SYSTEM})
        self.assertEqual(len(self.payloads("play_layer")), 1)
        self.assertEqual(self.payloads("stop_layer"), [])

    def test_embark_waits_for_altitude_then_resumes_existing_activation(self) -> None:
        self.start(status(0.0), graph(vehicles=("ship", "on_foot")))
        self.journal({"event": "Disembark"})
        self.update_status(status(None, flags=1 << 21, flags2=1))
        self.journal({"event": "Embark", "SRV": False})
        ship = self.runtime.state["zones"]["ship"]
        self.assertEqual(ship["phase"], "vehicle_suppressed")
        self.update_status(status(0.0))
        self.assertEqual(ship["phase"], "active")
        self.assertEqual(ship["activated_count"], 1)
        plays = [item for item in self.payloads("play_layer") if item["layer"] == "environment.ship.wind"]
        self.assertEqual(len(plays), 2)

    def test_disabled_layer_is_still_released_during_unknown_altitude_resume(self) -> None:
        self.start(status(0.0), graph(vehicles=("ship", "on_foot")))
        self.journal({"event": "Disembark"})
        self.runtime.zones[0]["enabled"] = False
        self.journal({"event": "Embark", "SRV": False})
        self.assertEqual(self.runtime.state["zones"]["ship"]["phase"], "suppressed")


if __name__ == "__main__":
    unittest.main()
