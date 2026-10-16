from __future__ import annotations

import unittest
from unittest import mock

from story_platform.visual_theme_adapter import VisualThemeAdapter


STORM_LAYER = "environment.stomr_ice_world_ship.danger_storm_world_atmosphere"


class VisualRestoreTests(unittest.TestCase):
    def adapter(self):
        adapter = VisualThemeAdapter.__new__(VisualThemeAdapter)
        adapter.environment_layers = {}
        adapter.sequences = {}
        adapter.sequence = []
        adapter.sequence_index = 0
        adapter.next_step_at = 0.0
        adapter.restore_theme = None
        adapter.last_persistent_slot = None
        adapter.persistent_refresh_pending = False
        adapter.unsafe_until = 0.0
        adapter._latest_core_theme = mock.Mock(return_value="Current cockpit theme")
        adapter._slot_theme = mock.Mock(side_effect=lambda slot: f"Theme: {slot}")
        adapter._apply_theme = mock.Mock(return_value=True)
        adapter._log = mock.Mock()
        return adapter

    @staticmethod
    def set_layer(adapter, layer=STORM_LAYER, cue="deep_danger", intensity=1.0):
        adapter.handle({"channel": "visual", "payload": {
            "action": "set_environment_layer", "layer": layer,
            "cue": cue, "intensity": intensity,
        }})

    @staticmethod
    def stop_layer(adapter, layer=STORM_LAYER):
        adapter.handle({"channel": "visual", "payload": {
            "action": "stop_environment_layer", "layer": layer,
        }})

    def test_final_layer_stop_from_recorded_storm_run_restores_once(self):
        adapter = self.adapter()
        self.set_layer(adapter)
        adapter._advance_visuals()
        self.stop_layer(adapter)
        adapter._advance_visuals()
        adapter._advance_visuals()
        self.assertEqual(adapter._apply_theme.call_args_list, [
            mock.call("Theme: deep_danger", "deep_danger", "environment"),
            mock.call("Current cockpit theme", "restore", "restore"),
        ])

    def test_reentering_and_leaving_twice_restores_on_both_exits(self):
        adapter = self.adapter()
        for _ in range(2):
            self.set_layer(adapter)
            adapter._advance_visuals()
            self.stop_layer(adapter)
            adapter._advance_visuals()
        self.assertEqual(adapter._apply_theme.call_count, 4)
        self.assertEqual(adapter._apply_theme.call_args.args,
                         ("Current cockpit theme", "restore", "restore"))

    def test_overlapping_layers_restore_remaining_cue_before_core_theme(self):
        adapter = self.adapter()
        self.set_layer(adapter, "combat_zone", "combat")
        self.set_layer(adapter)
        adapter._advance_visuals()
        self.stop_layer(adapter)
        adapter._advance_visuals()
        self.stop_layer(adapter, "combat_zone")
        adapter._advance_visuals()
        self.assertEqual(adapter._apply_theme.call_args_list, [
            mock.call("Theme: deep_danger", "deep_danger", "environment"),
            mock.call("Theme: combat", "combat", "environment"),
            mock.call("Current cockpit theme", "restore", "restore"),
        ])

    def test_intensity_falling_below_active_threshold_restores(self):
        adapter = self.adapter()
        self.set_layer(adapter)
        adapter._advance_visuals()
        self.set_layer(adapter, intensity=0.0)
        adapter._advance_visuals()
        adapter._apply_theme.assert_called_with("Current cockpit theme", "restore", "restore")

    def test_stop_waits_for_vehicle_guard_and_then_restores(self):
        adapter = self.adapter()
        with mock.patch("story_platform.visual_theme_adapter.time.monotonic", return_value=100.0):
            self.set_layer(adapter)
            adapter._advance_visuals()
            adapter.unsafe_until = 105.0
            self.stop_layer(adapter)
            adapter._advance_visuals()
        self.assertEqual(adapter._apply_theme.call_count, 1)
        with mock.patch("story_platform.visual_theme_adapter.time.monotonic", return_value=106.0):
            adapter._advance_visuals()
        adapter._apply_theme.assert_called_with("Current cockpit theme", "restore", "restore")

    def test_failed_restore_remains_pending_until_apply_succeeds(self):
        adapter = self.adapter()
        self.set_layer(adapter)
        adapter._advance_visuals()
        adapter._apply_theme.reset_mock()
        adapter._apply_theme.side_effect = [False, True]
        self.stop_layer(adapter)
        adapter._advance_visuals()
        adapter._advance_visuals()
        adapter._advance_visuals()
        self.assertEqual(adapter._apply_theme.call_count, 2)
        self.assertFalse(adapter.persistent_refresh_pending)

    def test_restore_uses_current_core_theme_instead_of_old_sequence_capture(self):
        adapter = self.adapter()
        adapter.restore_theme = "Old base theme"
        self.set_layer(adapter)
        adapter._advance_visuals()
        adapter._latest_core_theme.return_value = "Current critical theme"
        self.stop_layer(adapter)
        adapter._advance_visuals()
        adapter._apply_theme.assert_called_with("Current critical theme", "restore", "restore")

    def test_finished_sequence_restores_without_a_persistent_environment(self):
        adapter = self.adapter()
        adapter.sequence = [{"slot": "combat", "duration": .5}]
        with mock.patch("story_platform.visual_theme_adapter.time.monotonic", return_value=100.0):
            adapter._advance_visuals()
        with mock.patch("story_platform.visual_theme_adapter.time.monotonic", return_value=101.0):
            adapter._advance_visuals()
        adapter._apply_theme.assert_called_with("Current cockpit theme", "restore", "restore")

    def test_idle_adapter_does_not_write_a_theme(self):
        adapter = self.adapter()
        adapter._advance_visuals()
        adapter._apply_theme.assert_not_called()


if __name__ == "__main__":
    unittest.main()
