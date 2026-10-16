from __future__ import annotations

import unittest
from unittest.mock import patch

from story_platform.visual_theme_adapter import VisualThemeAdapter


class _DiscoCore:
    def disco_mode_active(self) -> bool:
        return True


class DiscoVisualIsolationTests(unittest.TestCase):
    def test_story_theme_apply_is_rejected_while_disco_owns_lighting(self) -> None:
        adapter = VisualThemeAdapter.__new__(VisualThemeAdapter)
        adapter.rc = _DiscoCore()
        adapter.unsafe_until = 0.0
        self.assertFalse(adapter._apply_theme("Any Theme", "combat", "test"))

    def test_suppressed_story_sequence_expires_on_its_normal_timing(self) -> None:
        adapter = VisualThemeAdapter.__new__(VisualThemeAdapter)
        adapter.sequence = [{"duration": 0.10}, {"duration": 0.15}]
        adapter.sequence_index = 0
        adapter.next_step_at = 0.0
        with patch("story_platform.visual_theme_adapter.time.monotonic", return_value=100.0):
            adapter._advance_suppressed_sequence()
        self.assertEqual(adapter.sequence_index, 1)
        self.assertAlmostEqual(adapter.next_step_at, 100.10)
        with patch("story_platform.visual_theme_adapter.time.monotonic", return_value=100.30):
            adapter._advance_suppressed_sequence()
        self.assertEqual(adapter.sequence, [])
        self.assertEqual(adapter.sequence_index, 0)


if __name__ == "__main__":
    unittest.main()
