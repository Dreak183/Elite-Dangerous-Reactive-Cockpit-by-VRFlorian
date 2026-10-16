from __future__ import annotations

import unittest

from story_platform.authoring import signal_catalog
from story_platform.dry_run import DryRunSession
from story_platform.environment_authoring import (
    default_environment,
    default_planetary_glide_audio_layer,
)
from story_platform.model import validate_graph
from story_platform.telemetry import WorldModel


GLIDE_BIT = 1 << 12


def glide_environment_graph() -> dict:
    zone = default_environment("planetary_glide")
    zone["location"] = {
        "system": "Col 359 Sector PN-Q c6-12",
        "body": "Col 359 Sector PN-Q c6-12 6 c",
    }
    zone["layers"] = [{
        "id": "arrival_fire",
        "type": "audio",
        "asset": "planetary glide.mp3",
        "volume": 1.0,
        "loop": False,
    }]
    return {
        "schema": "platform.story-graph.v1",
        "id": "planetary_glide_test",
        "version": "1",
        "title": "Planetary glide test",
        "start": "start",
        "variables": {},
        "nodes": {"start": {"kind": "start"}},
        "edges": [],
        "environments": [zone],
        "navigators": [],
    }


def world(gliding: bool) -> dict:
    return {
        "vehicle": "ship",
        "flags2": {"glide_mode": gliding},
        "location": {
            "system": "Col 359 Sector PN-Q c6-12",
            "body": "Col 359 Sector PN-Q c6-12 6 c",
        },
    }


def glide_plays(rows: list[dict]) -> list[dict]:
    return [
        row for row in rows
        if row.get("payload", {}).get("action") == "play_layer"
        and row.get("payload", {}).get("layer")
        == "environment.planetary_glide_arrival.arrival_fire"
    ]


class PlanetaryGlideSignalTests(unittest.TestCase):
    def test_status_emits_only_real_glide_transitions(self) -> None:
        model = WorldModel()
        baseline = {"timestamp": "2026-09-29T19:00:00Z", "Flags2": 0}
        self.assertEqual(
            [row["name"] for row in model.status_signals(baseline, None)],
            ["status.snapshot"],
        )

        entered = {"timestamp": "2026-09-29T19:00:01Z", "Flags2": GLIDE_BIT}
        names = [row["name"] for row in model.status_signals(entered, baseline)]
        self.assertIn("status.glide.entered", names)
        self.assertNotIn("status.glide.exited", names)

        unchanged = {"timestamp": "2026-09-29T19:00:02Z", "Flags2": GLIDE_BIT}
        names = [row["name"] for row in model.status_signals(unchanged, entered)]
        self.assertNotIn("status.glide.entered", names)
        self.assertNotIn("status.glide.exited", names)

        exited = {"timestamp": "2026-09-29T19:00:03Z", "Flags2": 0}
        names = [row["name"] for row in model.status_signals(exited, unchanged)]
        self.assertIn("status.glide.exited", names)
        self.assertNotIn("status.glide.entered", names)

    def test_editor_exposes_glide_signals_and_safe_preset(self) -> None:
        names = {item.name for item in signal_catalog()}
        self.assertIn("status.glide.entered", names)
        self.assertIn("status.glide.exited", names)
        graph = glide_environment_graph()
        self.assertEqual(validate_graph(graph), [])
        zone = graph["environments"][0]
        self.assertEqual(zone["active_vehicles"], ["ship"])
        self.assertEqual(zone["rearm"], "immediate")
        layer = default_planetary_glide_audio_layer()
        self.assertFalse(layer["loop"])
        self.assertEqual(layer["volume_transition_seconds"], 0.0)

    def test_one_sound_per_glide_and_rearms_after_exit(self) -> None:
        graph = glide_environment_graph()
        with DryRunSession(graph) as session:
            # A restored world that is already gliding must not fake an entry.
            session.replace_world(world(True))
            self.assertEqual(glide_plays(session.outputs()), [])

            session.dispatch("status.glide.entered", {"active": True}, source="status")
            self.assertEqual(len(glide_plays(session.outputs())), 1)
            session.dispatch("status.glide.entered", {"active": True}, source="status")
            self.assertEqual(len(glide_plays(session.outputs())), 1)

            session.replace_world(world(False))
            session.dispatch("status.Flags2.changed", source="status")
            self.assertEqual(
                session.environment_state["zones"]["planetary_glide_arrival"]["phase"],
                "inactive",
            )

            session.replace_world(world(True))
            session.dispatch("status.glide.entered", {"active": True}, source="status")
            self.assertEqual(len(glide_plays(session.outputs())), 2)


if __name__ == "__main__":
    unittest.main()
