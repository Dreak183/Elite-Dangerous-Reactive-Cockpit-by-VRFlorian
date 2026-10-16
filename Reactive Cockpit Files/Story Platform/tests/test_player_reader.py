from __future__ import annotations

import json
import unittest
from pathlib import Path

from story_platform.player_reader import build_view, format_duration


ROOT = Path(__file__).resolve().parents[1]
PROFILE = json.loads((ROOT / "stories" / "mission_02" / "player_reader.json").read_text(encoding="utf-8"))


def scan_state(*, mountain_complete: int = 0, canyon_complete: int = 0) -> dict:
    targets = {}
    for index in range(1, 8):
        targets[f"mountain_{index:02d}"] = {"phase": "completed" if index <= mountain_complete else "idle"}
    for index in range(1, 6):
        targets[f"canyon_{index:02d}"] = {"phase": "completed" if index <= canyon_complete else "idle"}
    return {"sets": {"mission2_aiabiko_anomalies": {"targets": targets}}}


def world(*, mountain_open: bool, canyon_open: bool, mountain_hour: float = 12.0) -> dict:
    return {
        "time": {
            "sites": {"mount_zoor": {"local_hour": mountain_hour}},
            "gates": {
                "mountain_window": {
                    "open": mountain_open,
                    "seconds_until_close": 3723,
                    "seconds_until_open": 81,
                },
                "canyon_window": {
                    "open": canyon_open,
                    "seconds_until_close": 725,
                    "seconds_until_open": 142,
                },
            },
        }
    }


def view(node: str, *, scans: dict | None = None, clock: dict | None = None, visits: dict | None = None) -> dict:
    return build_view(
        title="Mission 2",
        profile=PROFILE,
        graph_state={
            "graph_id": "Test mission 2",
            "active_nodes": [node],
            "visits": visits or {},
        },
        player_status={},
        world=clock or {},
        environment_state={"control_states": {"control.scanner": True}},
        scan_state=scans or {},
        cockpit={"state": "SUPERCRUISE", "theme": "Explorer Blue"},
    )


class PlayerReaderTests(unittest.TestCase):
    def test_profile_only_names_real_graph_nodes(self) -> None:
        graph = json.loads((ROOT / "stories" / "mission_02" / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(PROFILE["graph_id"], graph["id"])
        self.assertEqual(set(PROFILE["node_clues"]) - set(graph["nodes"]), set())
        self.assertEqual(set(graph["nodes"]) - set(PROFILE["node_clues"]), set())

    def test_graph_objectives_agree_with_reader_profile_when_present(self) -> None:
        graph = json.loads((ROOT / "stories" / "mission_02" / "graph.json").read_text(encoding="utf-8"))
        for node_id, node in graph["nodes"].items():
            objectives = [effect for effect in node.get("on_enter", []) if effect.get("type") == "next_step"]
            self.assertLessEqual(len(objectives), 1, node_id)
            if objectives:
                self.assertEqual(objectives[0]["text"], PROFILE["node_clues"][node_id], node_id)

    def test_duration_is_player_readable(self) -> None:
        self.assertEqual(format_duration(3723), "1:02:03")
        self.assertEqual(format_duration(81), "1:21")

    def test_exact_beacon_instruction_is_preserved(self) -> None:
        result = view("call_acccpted_com_to_beacon_to_talk_secure")
        self.assertEqual(
            result["clue"],
            "Approach the Unregistered Comms Beacon near Europa, drop from supercruise and select the beacon.",
        )

    def test_exact_delivery_ship_instruction_is_preserved(self) -> None:
        result = view("git_scanner_from_ship")
        self.assertEqual(
            result["clue"],
            "When the ship has landed on the pad, approach its cargo bay at the back to receive your advanced scanner.",
        )

    def test_all_twelve_approved_clues_are_locked(self) -> None:
        expected = {
            "call_acccpted_com_to_beacon_to_talk_secure": "Approach the Unregistered Comms Beacon near Europa, drop from supercruise and select the beacon.",
            "now_near_bardins_vice": "Land on the hill overlooking Bardin’s Vice.",
            "git_scanner_from_ship": "When the ship has landed on the pad, approach its cargo bay at the back to receive your advanced scanner.",
            "solar_briefing_mountain_dawn": "The window to enter and investigate Mount Zoor has just opened. Locate the mountain and make your approach.",
            "solar_briefing_mountain_late": "The window to enter and investigate Mount Zoor is closing. Locate the mountain quickly and make your approach.",
            "solar_briefing_canyon_open": "The window to enter and investigate The Ayasai Canyon site is open. Locate the canyon and make your approach.",
            "solar_briefing_wait": "It’s currently the middle of night at Mount Zoor. Only a madman would attempt to ascend it in the dark. You will have to wait until Dawn. And it’s still day at the canyon. You must wait for the cover of night to enter it. While you wait... Aiabiko 9 a has tons of lush oasis with plant life. Scanning all 6 of its life forms will yield you several million credits.",
            "wait_for_odin": "Wait for Muni’s reply, this would be an excellent moment for a coffee or toilet break. Muni’s investigation will take about 5 minutes. Or if you want to continue right away initiate super cruise.",
            "glad_you_waited": "Travel to Aiabiko 3 and approach Dewan Cultivation Range while remaining unseen.",
            "exit_super_cruise_at_aiabiko_3": "Keep the range at a +/- 52 degree heading and land about 500 meters from the two main research domes. Use a Maverick Suit for this! Then switch to on-foot and approach the domes without being spotted.",
            "ok_make_you_way_back_to_the_main_research_dome_where_you_first_entered_press_control_a_when_you_ve_secured_the_sample": "Confirm that you remember the route back to the research dome where the secure sample container is. Press CTRL+A when ready.",
            "another_left_nursery_halway_stair": "Run back to the power station, start the power-down sequence, then run back to the sample container, take the sample and exit the base before all the power shuts down. Then return to your ship.",
        }
        for node, clue in expected.items():
            self.assertEqual(PROFILE["node_clues"][node], clue, node)

    def test_reader_shows_windows_scanner_and_scan_counts(self) -> None:
        result = view(
            "aiabiko_locations_choices",
            scans=scan_state(mountain_complete=4, canyon_complete=2),
            clock=world(mountain_open=True, canyon_open=False, mountain_hour=3.5),
            visits={"git_scanner_from_ship": 1},
        )
        self.assertEqual(result["scanner"], "ON")
        self.assertTrue(result["show_scanner"])
        self.assertEqual(result["survey"][0]["remaining"], 3)
        self.assertEqual(result["survey"][0]["change_in"], "1:02:03")
        self.assertEqual(result["survey"][1]["remaining"], 3)
        self.assertEqual(
            result["clue"],
            "The window to enter and investigate Mount Zoor has just opened. Locate the mountain and make your approach.",
        )

    def test_only_remaining_location_becomes_next_clue(self) -> None:
        result = view(
            "aiabiko_locations_choices",
            scans=scan_state(mountain_complete=5, canyon_complete=5),
            clock=world(mountain_open=False, canyon_open=False),
            visits={"git_scanner_from_ship": 1},
        )
        self.assertEqual(result["clue"], "Complete the 2 remaining survey scans on Mount Zoor.")

    def test_intentional_madman_wording_is_preserved(self) -> None:
        result = view(
            "aiabiko_locations_choices",
            scans=scan_state(),
            clock=world(mountain_open=False, canyon_open=False),
            visits={"git_scanner_from_ship": 1},
        )
        self.assertIn("Only a madman would attempt to ascend it in the dark.", result["clue"])
        self.assertIn("Scanning all 6 of its life forms", result["clue"])

    def test_survey_panel_disappears_after_survey(self) -> None:
        result = view(
            "all_aiabiko_scans_complete",
            scans=scan_state(mountain_complete=7, canyon_complete=5),
            clock=world(mountain_open=False, canyon_open=False),
            visits={"git_scanner_from_ship": 1, "all_aiabiko_scans_complete": 1},
        )
        self.assertEqual(result["survey"], [])
        self.assertFalse(result["show_scanner"])

    def test_cockpit_identity_is_player_facing(self) -> None:
        result = view("start")
        self.assertEqual(result["cockpit_state"], "SUPERCRUISE")
        self.assertEqual(result["cockpit_theme"], "Explorer Blue")
        self.assertNotIn("active_nodes", result)
        self.assertNotIn("variables", result)


if __name__ == "__main__":
    unittest.main()
