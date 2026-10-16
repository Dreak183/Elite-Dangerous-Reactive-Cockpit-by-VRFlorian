from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRAPH = json.loads(
    (ROOT / "stories" / "mission_02" / "graph.json").read_text(encoding="utf-8")
)


class Mission2DialogueLayerTests(unittest.TestCase):
    def test_spoken_cues_share_the_dialogue_layer(self) -> None:
        non_dialogue_assets = {
            "00_incoming_call_loop.mp3",
            "Mission 2/mountain currents.mp3",
            "Mission 2/Midnight Protocol.mp3",
            "Mission 2/Spycraft deeper inside.mp3",
            "Mission 2/Spycraft getting inside.mp3",
        }
        spoken = []
        for node_id, node in GRAPH["nodes"].items():
            for effect in node.get("on_enter", []):
                if effect.get("type") != "emit" or effect.get("channel") != "audio":
                    continue
                payload = effect.get("payload", {})
                if payload.get("action") != "play_layer":
                    continue
                asset = payload.get("asset")
                if not asset or asset in non_dialogue_assets:
                    continue
                spoken.append((node_id, asset, payload.get("layer")))

        self.assertGreater(len(spoken), 20)
        self.assertEqual(
            [(node_id, asset, layer) for node_id, asset, layer in spoken if layer != "dialogue"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
