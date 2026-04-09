import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.block_discovery import SimulinkBlockDiscovery


class SimulinkBlockDiscoveryTests(unittest.TestCase):
    def _make_discovery(self, blocks: dict[str, dict[str, object]]) -> SimulinkBlockDiscovery:
        def find_all_blocks():
            return list(blocks.keys())

        def find_blocks_by_type(block_type: str):
            return [
                path for path, meta in blocks.items()
                if meta.get("BlockType") == block_type
            ]

        def get_param(block_path: str, parameter_name: str):
            return blocks.get(block_path, {}).get(parameter_name)

        def count_controller_gain_params(block_path: str):
            meta = blocks.get(block_path, {})
            return sum(
                1
                for gain_names in (
                    ("P", "Kp", "ProportionalGain"),
                    ("I", "Ki", "IntegralGain"),
                    ("D", "Kd", "DerivativeGain"),
                )
                if any(name in meta for name in gain_names)
            )

        return SimulinkBlockDiscovery(
            find_all_blocks=find_all_blocks,
            find_blocks_by_type=find_blocks_by_type,
            get_param=get_param,
            count_controller_gain_params=count_controller_gain_params,
        )

    def test_tagged_primary_and_secondary_win_over_generic_blocks(self):
        blocks = {
            "demo/LoopA": {"BlockType": "PIDController", "Kp": 1, "Ki": 0.1, "Kd": 0.01},
            "demo/LoopB": {
                "BlockType": "PIDController",
                "Kp": 2,
                "Ki": 0.2,
                "Kd": 0.02,
                "Tag": "llm_pid_tuner_primary",
            },
            "demo/LoopC": {
                "BlockType": "PIDController",
                "Kp": 3,
                "Ki": 0.3,
                "Kd": 0.03,
                "Tag": "llm_pid_tuner_secondary",
            },
        }
        discovery = self._make_discovery(blocks)

        result = discovery.autodiscover_controller_paths(
            explicit_primary=False,
            explicit_secondary=False,
            primary_path="",
            primary_paths=[],
            secondary_path="",
            secondary_paths=[],
        )

        self.assertEqual(result.primary_path, "demo/LoopB")
        self.assertEqual(result.secondary_path, "demo/LoopC")

    def test_pid_controller_blocks_are_used_before_scored_fallback(self):
        blocks = {
            "demo/Loop1": {"BlockType": "PIDController", "Kp": 1, "Ki": 0.1, "Kd": 0.01},
            "demo/OuterCandidate": {"Kp": 4, "Ki": 0.4, "Kd": 0.04},
        }
        discovery = self._make_discovery(blocks)

        result = discovery.autodiscover_controller_paths(
            explicit_primary=False,
            explicit_secondary=False,
            primary_path="",
            primary_paths=[],
            secondary_path="",
            secondary_paths=[],
        )

        self.assertEqual(result.primary_path, "demo/Loop1")

    def test_explicit_setpoint_block_is_honored(self):
        blocks = {
            "demo/ManualSetpoint": {"BlockType": "Constant"},
        }
        discovery = self._make_discovery(blocks)

        block_path, block_type = discovery.resolve_setpoint_block("demo/ManualSetpoint")

        self.assertEqual(block_path, "demo/ManualSetpoint")
        self.assertEqual(block_type, "Constant")


if __name__ == "__main__":
    unittest.main()
