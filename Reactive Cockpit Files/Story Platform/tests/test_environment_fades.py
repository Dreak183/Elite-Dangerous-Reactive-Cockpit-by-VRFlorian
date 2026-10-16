from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from story_platform.environments import EnvironmentRuntime
from story_platform.model import StoryGraph, validate_graph
from story_platform.presentation_adapter import AudioPlayer, LayeredAudioMixer, PresentationAdapter


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


def ramped_player_type(clock: Clock):
    """An independent player model with audible volume and stop-on-fade-end."""
    class Player:
        instances: list = []

        def __init__(self, alias: str) -> None:
            self.alias = alias
            self.playing = False
            self.volume = 0.0
            self.ramp = None
            self.stop_at_end = False
            self.starts = 0
            self.stops = 0
            self.instances.append(self)

        def play_file(self, path: Path, volume: float, loop: bool) -> None:
            self.path, self.loop = path, loop
            self.volume = volume
            self.playing = True
            self.starts += 1

        def advance(self) -> None:
            if self.ramp is None:
                return
            start, seconds, initial, target = self.ramp
            ratio = min(1.0, (clock.now - start) / seconds)
            self.volume = initial + (target - initial) * ratio
            if ratio >= 1.0:
                self.ramp = None
                if self.stop_at_end:
                    self.playing = False

        def set_volume(self, volume: float, seconds: float = 0.0) -> None:
            self.advance()
            self.stop_at_end = False
            if seconds > 0:
                self.ramp = (clock.now, seconds, self.volume, volume)
            else:
                self.volume = volume
                self.ramp = None

        def fade_out(self, seconds: float) -> None:
            self.set_volume(0.0, seconds)
            self.stop_at_end = True

        def stop(self) -> None:
            self.playing = False
            self.ramp = None
            self.stops += 1

        def is_playing(self) -> bool:
            self.advance()
            return self.playing

    return Player


def environment_graph(seconds: float = 5.0, *, loop: bool = True, scanner: bool = False) -> dict:
    zones = []
    for vehicle in ("ship", "srv", "on_foot"):
        layer = {
            "id": "wind", "type": "audio", "asset": f"{vehicle}.mp3",
            "volume": 1.0, "loop": loop, "volume_transition_seconds": seconds,
        }
        zone = {
            "id": vehicle, "title": vehicle, "active_vehicles": [vehicle],
            "entry_when": {"always": True}, "rearm": "after_exit",
            "location": {"system": "Storm", "body": "Storm 1", "max_altitude_m": 20000.0},
            "layers": [layer],
        }
        if scanner:
            layer.update({
                "playback_mode": "scanner_pulse",
                "scanner_pulse": {"outer_gap_seconds": 5.0, "core_gap_seconds": 0.0},
            })
            zone["location"].update({
                "latitude": 0.0, "longitude": 0.0, "entry_radius_m": 100.0,
                "exit_radius_m": 120.0, "body_radius_m": 1_000_000.0,
            })
        zones.append(zone)
    return {
        "schema": "platform.story-graph.v1", "id": "fades", "version": "1", "title": "Fades",
        "start": "start", "nodes": {"start": {"kind": "start"}}, "edges": [],
        "environments": zones,
    }


def world(vehicle: str = "ship", altitude: float = 19000.0) -> dict:
    return {
        "vehicle": vehicle,
        "location": {"system": "Storm", "body": "Storm 1", "altitude": altitude,
                     "latitude": 0.0, "longitude": 0.0, "body_radius_m": 1_000_000.0},
    }


class EnvironmentFadeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        media = self.root / "media"
        media.mkdir()
        for vehicle in ("ship", "srv", "on_foot"):
            (media / f"{vehicle}.mp3").write_bytes(b"fixture")
        # Fade tests use temporary fixture assets even when run from a player
        # installation, whose media resolver correctly forces the local folder.
        media_patch = patch("story_platform.presentation_adapter.story_media_dir", return_value=media)
        media_patch.start()
        self.addCleanup(media_patch.stop)
        self.clock = Clock()
        self.Player = ramped_player_type(self.clock)
        player_patch = patch("story_platform.presentation_adapter.AudioPlayer", self.Player)
        player_patch.start()
        self.addCleanup(player_patch.stop)
        self.mixer = LayeredAudioMixer()
        self.addCleanup(self.mixer.stop)
        self.adapter = PresentationAdapter(self.root / "presentation", {
            "external_story_media_dir": str(media), "presentation": {},
        }, audio=self.mixer)
        self.read_lines = 0

    def start(self, graph: dict) -> None:
        self.assertEqual(validate_graph(graph), [])
        self.runtime = EnvironmentRuntime(
            StoryGraph(self.root / "graph.json", graph), self.root / "environment.json",
            self.root / "environment_events.jsonl", self.root / "outbox.jsonl",
            printer=lambda _text: None,
        )
        self.runtime.start_session(world(), {})
        self.drain()

    def drain(self) -> list[dict]:
        path = self.root / "outbox.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        new = rows[self.read_lines:]
        self.read_lines = len(rows)
        for row in new:
            self.adapter.handle(row)
        return new

    def dispatch(self, current_world: dict) -> list[dict]:
        self.runtime.dispatch({"name": "status.snapshot", "source": "status", "data": {}}, current_world, {})
        return self.drain()

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)
        self.adapter.poll_audio_completions()

    def test_planetwide_altitude_entry_and_exit_use_five_second_fades(self) -> None:
        self.start(environment_graph())
        player = self.mixer.players["environment.ship.wind"]
        self.assertEqual(player.volume, 0.0)
        self.advance(2.5)
        self.assertAlmostEqual(player.volume, 0.5)
        self.advance(2.5)
        self.assertAlmostEqual(player.volume, 1.0)
        self.dispatch(world(altitude=21000.0))
        self.assertEqual(player.stops, 0)
        self.advance(2.5)
        self.assertAlmostEqual(player.volume, 0.5)
        self.assertTrue(player.playing)
        self.advance(2.5)
        self.assertEqual(player.volume, 0.0)
        self.assertFalse(player.playing)
        self.assertNotIn("environment.ship.wind", self.mixer.players)
        self.assertEqual(len(self.Player.instances), 1)

    def test_ship_srv_foot_round_trip_reverses_without_restarting_sound(self) -> None:
        self.start(environment_graph())
        self.advance(5.0)
        ship = self.mixer.players["environment.ship.wind"]
        self.dispatch(world("srv"))
        srv = self.mixer.players["environment.srv.wind"]
        self.advance(2.5)
        self.assertAlmostEqual(ship.volume, 0.5)
        self.assertAlmostEqual(srv.volume, 0.5)
        self.dispatch(world("on_foot"))
        foot = self.mixer.players["environment.on_foot.wind"]
        self.advance(1.0)
        self.assertAlmostEqual(ship.volume, 0.3)
        self.assertAlmostEqual(srv.volume, 0.4)
        self.assertAlmostEqual(foot.volume, 0.2)
        self.dispatch(world("ship"))
        self.assertIs(self.mixer.players["environment.ship.wind"], ship)
        self.assertAlmostEqual(ship.volume, 0.3)
        self.advance(2.5)
        self.assertAlmostEqual(ship.volume, 0.65)
        self.advance(2.5)
        self.assertEqual(ship.starts, 1)
        self.assertEqual(ship.stops, 0)
        self.assertEqual(set(self.mixer.players), {"environment.ship.wind"})
        self.assertEqual(len(self.Player.instances), 3)

    def test_rapid_altitude_return_cancels_the_pending_stop(self) -> None:
        self.start(environment_graph())
        self.advance(5.0)
        ship = self.mixer.players["environment.ship.wind"]
        self.dispatch(world(altitude=21000.0))
        self.dispatch(world(altitude=21000.0))  # Rearm while outside.
        self.advance(2.5)
        self.dispatch(world(altitude=19000.0))
        self.assertIs(self.mixer.players["environment.ship.wind"], ship)
        self.assertAlmostEqual(ship.volume, 0.5)
        self.advance(5.0)
        self.assertEqual(ship.volume, 1.0)
        self.assertEqual(ship.starts, 1)
        self.assertEqual(ship.stops, 0)

    def test_zero_smoothing_keeps_immediate_start_and_stop(self) -> None:
        self.start(environment_graph(0.0))
        ship = self.mixer.players["environment.ship.wind"]
        self.assertEqual(ship.volume, 1.0)
        self.dispatch(world(altitude=21000.0))
        self.assertFalse(ship.playing)
        self.assertNotIn("environment.ship.wind", self.mixer.players)

    def test_one_shot_and_scanner_core_keep_their_existing_timing(self) -> None:
        for loop, scanner in ((False, False), (True, True)):
            with self.subTest(loop=loop, scanner=scanner):
                # Use a separate environment state for each playback mode.
                self.mixer.stop()
                self.read_lines = 0
                (self.root / "outbox.jsonl").unlink(missing_ok=True)
                (self.root / "environment.json").unlink(missing_ok=True)
                self.start(environment_graph(loop=loop, scanner=scanner))
                ship = self.mixer.players["environment.ship.wind"]
                self.assertEqual(ship.volume, 1.0)
                self.assertIsNone(ship.ramp)
                rows = self.dispatch(world("srv"))
                stop = [row["payload"] for row in rows if row["payload"].get("action") == "stop_layer"]
                self.assertTrue(stop)
                self.assertNotIn("transition_seconds", stop[0])
                self.assertFalse(ship.playing)

    def test_closing_live_gate_stops_crossfade_immediately(self) -> None:
        self.start(environment_graph())
        self.advance(5.0)
        self.dispatch(world("srv"))
        self.advance(1.0)
        self.adapter.handle({"channel": "system", "payload": {"action": "set_audio_gate", "open": False}})
        self.assertEqual(self.mixer.players, {})
        self.assertTrue(all(not player.playing for player in self.Player.instances))
        self.assertEqual(self.adapter.state["audio_layers"], {})

    def test_replaced_asset_finishes_fading_without_removing_new_layer(self) -> None:
        def play(asset: str) -> None:
            self.adapter.handle({"channel": "audio", "payload": {
                "action": "play_layer", "layer": "environment.shared.wind", "asset": asset,
                "volume": 1.0, "loop": True, "transition_seconds": 5.0,
            }})
        play("ship.mp3")
        self.advance(5.0)
        old = self.mixer.players["environment.shared.wind"]
        play("srv.mp3")
        new = self.mixer.players["environment.shared.wind"]
        self.assertNotEqual(old.alias, new.alias)
        self.advance(2.5)
        self.assertAlmostEqual(old.volume, 0.5)
        self.assertAlmostEqual(new.volume, 0.5)
        self.advance(2.5)
        self.assertFalse(old.playing)
        self.assertTrue(new.playing)
        self.assertEqual(self.mixer.retiring_players, [])
        self.assertEqual(self.adapter.state["audio_layers"]["environment.shared.wind"]["asset"], "srv.mp3")

    def test_repeated_stop_and_late_volume_do_not_extend_or_reverse_fade(self) -> None:
        self.start(environment_graph())
        self.advance(5.0)
        key = "environment.ship.wind"
        self.mixer.stop_layer(key, 5.0)
        self.advance(2.0)
        self.mixer.stop_layer(key, 5.0)
        self.assertFalse(self.mixer.set_layer_volume(key, 1.0, 5.0))
        self.advance(3.0)
        self.assertNotIn(key, self.mixer.players)

    def test_dead_fading_loop_is_not_recovered(self) -> None:
        self.start(environment_graph())
        self.advance(5.0)
        key = "environment.ship.wind"
        self.dispatch(world(altitude=21000.0))
        player = self.mixer.players[key]
        player.playing = False
        self.adapter.poll_audio_completions()
        self.assertNotIn(key, self.mixer.players)
        self.assertEqual(len(self.Player.instances), 1)

    def test_active_loop_recovery_fades_to_latest_adjusted_volume(self) -> None:
        self.start(environment_graph())
        self.advance(5.0)
        key = "environment.ship.wind"
        self.mixer.set_layer_volume(key, 0.2, 0.0)
        old = self.mixer.players[key]
        old.playing = False
        self.adapter.poll_audio_completions()
        recovered = self.mixer.players[key]
        self.assertIsNot(recovered, old)
        self.assertEqual(recovered.volume, 0.0)
        self.advance(2.5)
        self.assertAlmostEqual(recovered.volume, 0.1)
        self.advance(2.5)
        self.assertAlmostEqual(recovered.volume, 0.2)
        self.assertEqual(self.adapter.state["audio_layers"][key]["restart_count"], 1)

    def test_authored_stop_delay_runs_before_the_exit_fade(self) -> None:
        graph = environment_graph()
        graph["environments"][0]["layers"][0]["stop_delay_seconds"] = 3.0
        self.start(graph)
        self.advance(5.0)
        ship = self.mixer.players["environment.ship.wind"]
        self.dispatch(world(altitude=21000.0))
        self.assertIsNone(ship.ramp)
        self.runtime.advance_time(2.0)
        self.dispatch(world(altitude=21000.0))
        self.assertIsNone(ship.ramp)
        self.runtime.advance_time(1.1)
        self.dispatch(world(altitude=21000.0))
        self.advance(2.5)
        self.assertAlmostEqual(ship.volume, 0.5)
        self.advance(2.5)
        self.assertFalse(ship.playing)

    def test_dialogue_replacement_is_immediate_even_if_a_fade_is_requested(self) -> None:
        self.mixer.play_layer("dialogue", Path("line1.mp3"), 1.0, False, 5.0)
        old = self.mixer.players["dialogue"]
        self.mixer.play_layer("dialogue", Path("line2.mp3"), 1.0, False, 5.0)
        new = self.mixer.players["dialogue"]
        self.assertFalse(old.playing)
        self.assertEqual(new.volume, 1.0)
        self.assertIsNone(new.ramp)


class NativePlayerFadeTests(unittest.TestCase):
    def test_fallback_fade_reverses_from_actual_volume_then_stops_at_zero(self) -> None:
        clock = Clock()
        commands: list[str] = []

        class WinMM:
            def mciSendStringW(self, command, *_args):
                commands.append(command)
                return 0

        with patch("story_platform.presentation_adapter.time.monotonic", lambda: clock.now):
            player = AudioPlayer("fallback")
            player.simulated_playing = True
            player.winmm = WinMM()
            player.current_volume = 1.0
            player.fade_out(5.0)
            clock.advance(2.5)
            player.is_playing()
            self.assertAlmostEqual(player.current_volume, 0.5)
            player.set_volume(1.0, 5.0)
            self.assertAlmostEqual(player.current_volume, 0.5)
            clock.advance(2.5)
            player.is_playing()
            self.assertAlmostEqual(player.current_volume, 0.75)
            self.assertIn("setaudio fallback volume to 750", commands)
            clock.advance(2.5)
            player.is_playing()
            player.fade_out(5.0)
            clock.advance(5.0)
            self.assertFalse(player.is_playing())
            self.assertIn("setaudio fallback volume to 0", commands)
            self.assertEqual(commands[-1], "close fallback")

    def test_native_fade_is_queued_and_return_sends_a_cancelling_volume_ramp(self) -> None:
        class Process:
            def __init__(self): self.stdin = io.StringIO()
            def poll(self): return None

        player = AudioPlayer()
        process = Process()
        player.process = process
        player.fade_out(5.0)
        self.assertIs(player.process, process)
        player.set_volume(0.8, 5.0)
        self.assertEqual(process.stdin.getvalue(), "fade-stop 5.000\nvolume 0.800000 5.000\n")


if __name__ == "__main__":
    unittest.main()
