from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from story_platform.environments import EnvironmentRuntime
from story_platform.runner import StoryPlatformRunner, live_telemetry_confirmation_source
from story_platform.telemetry import (
    JournalTail,
    WorldModel,
    is_live_journal_telemetry,
    is_live_location_event,
    is_live_status_update,
)


class LiveTelemetryGateTests(unittest.TestCase):
    def test_absolute_location_events_with_a_system_open_the_gate(self) -> None:
        for name in ("Location", "FSDJump", "CarrierJump"):
            with self.subTest(name=name):
                self.assertTrue(is_live_location_event({"event": name, "StarSystem": "Sol"}))

    def test_menu_and_session_lifecycle_events_do_not_open_the_gate(self) -> None:
        for event in (
            {"event": "Music", "MusicTrack": "MainMenu"},
            {"event": "Fileheader"},
            {"event": "Commander"},
            {"event": "LoadGame"},
            {"event": "Shutdown"},
        ):
            with self.subTest(event=event):
                self.assertFalse(is_live_journal_telemetry(event))

    def test_any_fresh_gameplay_journal_event_can_recover_the_gate(self) -> None:
        for event in (
            {"event": "StartJump", "JumpType": "Supercruise"},
            {"event": "SupercruiseEntry", "StarSystem": "Aiabiko"},
            {"event": "Touchdown", "Body": "Aiabiko 9 a"},
            {"event": "Docked", "StationName": "Dewan Cultivation Range"},
            {"event": "Music", "MusicTrack": "Supercruise"},
        ):
            with self.subTest(event=event):
                self.assertTrue(is_live_journal_telemetry(event))

    def test_location_without_a_system_does_not_open_the_gate(self) -> None:
        self.assertFalse(is_live_location_event({"event": "Location"}))
        self.assertFalse(is_live_location_event({"event": "FSDJump", "StarSystem": "   "}))

    def test_meaningful_post_baseline_status_change_recovers_the_gate(self) -> None:
        previous = {
            "timestamp": "2026-09-20T16:30:18Z",
            "Flags": 419430552,
            "Fuel": {"FuelMain": 8.16, "FuelReservoir": 0.134755},
        }
        current = {
            "timestamp": "2026-09-20T16:30:38Z",
            "Flags": 419430552,
            "Fuel": {"FuelMain": 8.16, "FuelReservoir": 0.124543},
        }

        self.assertTrue(is_live_status_update(current, previous))
        self.assertEqual(
            live_telemetry_confirmation_source("status", current, previous),
            "status.changed",
        )

    def test_cached_or_timestamp_only_status_does_not_open_the_gate(self) -> None:
        baseline = {"timestamp": "2026-09-20T16:30:18Z", "Flags": 419430552}
        timestamp_only = {"timestamp": "2026-09-20T16:30:38Z", "Flags": 419430552}

        self.assertFalse(is_live_status_update(baseline, None))
        self.assertFalse(is_live_status_update(timestamp_only, baseline))
        self.assertIsNone(live_telemetry_confirmation_source("status", baseline, None))

    def test_runtime_accepts_live_telemetry_but_not_cached_world_state(self) -> None:
        cached_sol = {"location": {"system": "Sol"}}

        self.assertIsNone(live_telemetry_confirmation_source("world", cached_sol))
        self.assertIsNone(
            live_telemetry_confirmation_source(
                "journal", {"event": "Music", "MusicTrack": "MainMenu"}
            )
        )
        self.assertEqual(
            live_telemetry_confirmation_source(
                "journal", {"event": "StartJump", "JumpType": "Supercruise"}
            ),
            "journal.StartJump",
        )

    def test_gate_opener_is_delivered_after_it_starts_the_runtime(self) -> None:
        class FakeWorld:
            @staticmethod
            def journal_signal(event: dict[str, object]) -> dict[str, object]:
                return {"name": f"journal.{event['event']}"}

            @staticmethod
            def status_signals(
                current: dict[str, object], previous: dict[str, object] | None,
            ) -> list[dict[str, object]]:
                return [{"name": "status.changed", "data": {"status": current}}]

        runner = StoryPlatformRunner.__new__(StoryPlatformRunner)
        runner.world = FakeWorld()
        runner.runtime_started = False
        order: list[str] = []

        def confirm(source: str) -> None:
            order.append(f"open:{source}")
            runner.runtime_started = True

        def dispatch(signal: dict[str, object]) -> None:
            self.assertTrue(runner.runtime_started, "gate-opening telemetry was dispatched too early")
            order.append(str(signal["name"]))

        runner._confirm_live_audio = confirm
        runner._dispatch = dispatch

        runner._dispatch_live_journal_event({"event": "StartJump", "JumpType": "Supercruise"})
        self.assertEqual(order, ["open:journal.StartJump", "journal.StartJump"])

        order.clear()
        runner.runtime_started = False
        previous = {"timestamp": "2026-09-20T16:30:18Z", "Flags": 1}
        current = {"timestamp": "2026-09-20T16:30:19Z", "Flags": 17}
        runner._dispatch_live_status_update(current, previous)
        self.assertEqual(order, ["open:status.changed", "status.changed"])

    def test_journal_tail_arms_before_hydration_and_reads_only_new_records(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            journal = folder / "Journal.2026-09-20T160000.01.log"
            journal.write_text(
                json.dumps({"event": "Location", "StarSystem": "Aiabiko"}) + "\n",
                encoding="utf-8",
            )
            tail = JournalTail(folder, from_end=True)
            self.assertTrue(tail.arm())
            self.assertEqual(list(tail.poll()), [])

            with journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": "StartJump", "JumpType": "Supercruise"}) + "\n")
            self.assertEqual([row["event"] for row in tail.poll()], ["StartJump"])
            tail.close()

    def test_journal_created_after_arming_is_read_from_its_beginning(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            tail = JournalTail(folder, from_end=True)
            self.assertFalse(tail.arm())

            journal = folder / "Journal.2026-09-20T160000.01.log"
            journal.write_text(
                "\n".join((
                    json.dumps({"event": "Fileheader"}),
                    json.dumps({"event": "Location", "StarSystem": "Aiabiko"}),
                )) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                [row["event"] for row in tail.poll()],
                ["Fileheader", "Location"],
            )
            tail.close()

    def test_surface_coordinates_are_cleared_when_ship_loses_planetary_fix(self) -> None:
        world = WorldModel()
        surface = {
            "timestamp": "2026-09-19T12:00:00Z",
            "Flags": (1 << 21) | (1 << 24),
            "BodyName": "Aiabiko 9 a",
            "Latitude": 1.0,
            "Longitude": 2.0,
            "Altitude": 90000.0,
            "Heading": 180.0,
        }
        space = {
            "timestamp": "2026-09-19T12:00:01Z",
            "Flags": (1 << 24),
            "BodyName": "Aiabiko 9 a",
        }

        world.status_signals(surface, None)
        self.assertEqual(world.data["location"]["altitude"], 90000.0)
        world.status_signals(space, surface)

        for key in ("latitude", "longitude", "altitude", "heading"):
            self.assertNotIn(key, world.data["location"])
        self.assertIsNone(world.data["location_updated_at"])

    def test_lost_ship_surface_fix_exits_altitude_bounded_environment(self) -> None:
        rule = {"max_altitude_m": 100000.0}
        ship_in_space = {
            "vehicle": "ship",
            "flags": {"has_lat_long": False},
            "location": {"system": "Aiabiko", "body": "Aiabiko 9 a"},
        }
        on_foot_without_altitude = {
            "vehicle": "on_foot",
            "flags": {"has_lat_long": True},
            "location": {"system": "Aiabiko", "body": "Aiabiko 9 a"},
        }

        self.assertEqual(
            EnvironmentRuntime._altitude_inside(rule, ship_in_space),
            (False, True),
        )
        self.assertEqual(
            EnvironmentRuntime._altitude_inside(rule, on_foot_without_altitude),
            (True, False),
        )


if __name__ == "__main__":
    unittest.main()
