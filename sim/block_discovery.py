from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


PRIMARY_CONTROLLER_TAG = "llm_pid_tuner_primary"
SECONDARY_CONTROLLER_TAG = "llm_pid_tuner_secondary"
PID_BLOCK_TYPE_CANDIDATES = (
    "PIDController",
    "DiscretePIDController",
    "PIDController2DOF",
    "DiscretePIDController2DOF",
)


@dataclass(slots=True)
class ControllerDiscoveryResult:
    primary_path: str
    primary_paths: List[str]
    secondary_path: str
    secondary_paths: List[str]


class SimulinkBlockDiscovery:
    def __init__(
        self,
        *,
        find_all_blocks: Callable[[], List[str]],
        find_blocks_by_type: Callable[[str], List[str]],
        get_param: Callable[[str, str], object | None],
        count_controller_gain_params: Callable[[str], int],
    ) -> None:
        self._find_all_blocks = find_all_blocks
        self._find_blocks_by_type = find_blocks_by_type
        self._get_param = get_param
        self._count_controller_gain_params = count_controller_gain_params

    def normalize_param_text(self, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith("[") and text.endswith("]"):
            return text[1:-1].strip()
        return text

    def resolve_setpoint_block(
        self,
        explicit_setpoint_block: str,
    ) -> Tuple[str | None, str | None]:
        if explicit_setpoint_block:
            for block_type in ("Step", "Constant"):
                if self._get_param(explicit_setpoint_block, "BlockType") == block_type:
                    return explicit_setpoint_block, block_type
            return explicit_setpoint_block, None

        keywords = ("setpoint", "reference", "ref", "step", "鐩爣", "缁欏畾")
        candidates: List[Tuple[int, str, str]] = []
        for block_type in ("Step", "Constant"):
            for block_path in self._find_blocks_by_type(block_type):
                score = 0
                lowered = block_path.lower()
                if any(keyword in lowered for keyword in keywords):
                    score += 10
                if block_path.rsplit("/", 1)[-1] in {"Step", "Setpoint", "Reference"}:
                    score += 5
                candidates.append((score, block_path, block_type))

        if not candidates:
            return None, None
        candidates.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_path, best_type = candidates[0]
        if len(candidates) > 1 and best_score == candidates[1][0] == 0:
            return None, None
        return best_path, best_type

    @staticmethod
    def setpoint_parameter_name(block_type: str) -> str | None:
        if block_type == "Step":
            return "After"
        if block_type == "Constant":
            return "Value"
        return None

    def _normalize_tag_text(self, value: object) -> str:
        return str(value or "").strip().lower()

    def _discovery_rank(self, block_path: str) -> Tuple[int, str]:
        lowered = block_path.lower()
        score = 0
        if "primary" in lowered or "outer" in lowered:
            score += 20
        if lowered.endswith("/pid controller") or lowered.endswith("/pid"):
            score += 10
        if "secondary" in lowered or "inner" in lowered:
            score -= 10
        return score, block_path

    def _sort_discovered_paths(self, paths: List[str]) -> List[str]:
        unique_paths: List[str] = []
        for path in paths:
            normalized = str(path or "").strip()
            if normalized and normalized not in unique_paths:
                unique_paths.append(normalized)
        return [
            path
            for _score, path in sorted(
                (self._discovery_rank(path) for path in unique_paths),
                key=lambda item: (-item[0], item[1]),
            )
        ]

    def _find_tagged_controller_path(
        self,
        tag_name: str,
        *,
        excluded_paths: Optional[Set[str]] = None,
    ) -> str:
        excluded = excluded_paths or set()
        matches: List[str] = []
        for block_path in self._find_all_blocks():
            normalized_path = str(block_path or "").strip()
            if not normalized_path or normalized_path in excluded:
                continue
            raw_tag = self._get_param(normalized_path, "Tag")
            if self._normalize_tag_text(raw_tag) != tag_name:
                continue
            if self._count_controller_gain_params(normalized_path) < 2:
                continue
            matches.append(normalized_path)
        ranked = self._sort_discovered_paths(matches)
        return ranked[0] if ranked else ""

    def _find_pid_controller_blocks(
        self, *, excluded_paths: Optional[Set[str]] = None
    ) -> List[str]:
        excluded = excluded_paths or set()
        matches: List[str] = []
        for block_type in PID_BLOCK_TYPE_CANDIDATES:
            for block_path in self._find_blocks_by_type(block_type):
                normalized_path = str(block_path or "").strip()
                if not normalized_path or normalized_path in excluded:
                    continue
                if self._count_controller_gain_params(normalized_path) < 2:
                    continue
                matches.append(normalized_path)
        return self._sort_discovered_paths(matches)

    def _score_controller_block(self, block_path: str) -> Tuple[int, int]:
        lowered = block_path.lower()
        tail = lowered.rsplit("/", 1)[-1]
        controller_keywords = (
            "pid",
            "controller",
            "loop",
            "inner",
            "outer",
            "cascade",
        )
        non_controller_keywords = (
            "setpoint",
            "reference",
            "scope",
            "to workspace",
            "output",
            "y_out",
            "u_out",
            "signal specification",
            "passthrough",
        )

        if not any(keyword in lowered for keyword in controller_keywords):
            return 0, 0
        if any(keyword in lowered for keyword in non_controller_keywords):
            return 0, 0
        if tail in {"constant", "step", "sum", "gain", "scope"}:
            return 0, 0

        gain_count = self._count_controller_gain_params(block_path)
        score = 10 + 5 * gain_count
        return score, gain_count

    def _find_scored_controller_blocks(
        self, *, excluded_paths: Optional[Set[str]] = None
    ) -> List[str]:
        excluded = excluded_paths or set()
        candidates: List[Tuple[int, int, str]] = []
        for block_path in self._find_all_blocks():
            normalized_path = str(block_path or "").strip()
            if not normalized_path or normalized_path in excluded:
                continue
            score, gain_count = self._score_controller_block(normalized_path)
            if gain_count < 2 or score <= 0:
                continue
            candidates.append((score, gain_count, normalized_path))
        candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [path for _score, _gain_count, path in candidates]

    def autodiscover_controller_paths(
        self,
        *,
        explicit_primary: bool,
        explicit_secondary: bool,
        primary_path: str,
        primary_paths: List[str],
        secondary_path: str,
        secondary_paths: List[str],
    ) -> ControllerDiscoveryResult:
        taken_paths = {
            str(path).strip()
            for path in [primary_path, secondary_path, *primary_paths, *secondary_paths]
            if str(path).strip()
        }

        resolved_primary_path = primary_path
        resolved_primary_paths = list(primary_paths)
        resolved_secondary_path = secondary_path
        resolved_secondary_paths = list(secondary_paths)

        if not explicit_primary:
            tagged_primary = self._find_tagged_controller_path(
                PRIMARY_CONTROLLER_TAG,
                excluded_paths=taken_paths,
            )
            if tagged_primary:
                resolved_primary_path = tagged_primary
                resolved_primary_paths = [tagged_primary]
                taken_paths.add(tagged_primary)

        if not explicit_secondary:
            tagged_secondary = self._find_tagged_controller_path(
                SECONDARY_CONTROLLER_TAG,
                excluded_paths=taken_paths,
            )
            if tagged_secondary:
                resolved_secondary_path = tagged_secondary
                resolved_secondary_paths = [tagged_secondary]
                taken_paths.add(tagged_secondary)

        discovered_paths = self._find_pid_controller_blocks(excluded_paths=taken_paths)
        if not discovered_paths:
            discovered_paths = self._find_scored_controller_blocks(
                excluded_paths=taken_paths
            )
        if not discovered_paths:
            return ControllerDiscoveryResult(
                primary_path=resolved_primary_path,
                primary_paths=resolved_primary_paths,
                secondary_path=resolved_secondary_path,
                secondary_paths=resolved_secondary_paths,
            )

        if not explicit_primary and not resolved_primary_path:
            resolved_primary_path = discovered_paths[0]
            resolved_primary_paths = list(discovered_paths)
            taken_paths.add(resolved_primary_path)

        if not explicit_secondary and not resolved_secondary_path:
            for path in discovered_paths:
                if path not in taken_paths:
                    resolved_secondary_path = path
                    resolved_secondary_paths = [path]
                    break

        return ControllerDiscoveryResult(
            primary_path=resolved_primary_path,
            primary_paths=resolved_primary_paths,
            secondary_path=resolved_secondary_path,
            secondary_paths=resolved_secondary_paths,
        )

    def controller_sample_time_from_paths(
        self,
        *,
        separate_gain_paths: Dict[str, str],
        pid_block_path: str,
        pid_block_paths: List[str],
    ) -> str:
        for gain_key in ("p", "i", "d"):
            path = str(separate_gain_paths.get(gain_key, "") or "").strip()
            if not path:
                continue
            sample_time = self._get_param(path, "SampleTime")
            normalized = self.normalize_param_text(sample_time)
            if normalized:
                return normalized

        for path in [pid_block_path, *pid_block_paths]:
            normalized_path = str(path or "").strip()
            if not normalized_path:
                continue
            sample_time = self._get_param(normalized_path, "SampleTime")
            normalized = self.normalize_param_text(sample_time)
            if normalized:
                return normalized
        return ""

    def detect_control_domain(
        self,
        *,
        controller_1_sample_time: str,
        controller_2_sample_time: str,
        model_fixed_step: str,
        model_solver_type: str,
    ) -> str:
        sample_times: List[float] = []
        for raw_value in (
            controller_1_sample_time,
            controller_2_sample_time,
            model_fixed_step,
        ):
            try:
                numeric_value = float(str(raw_value or "").strip())
            except (TypeError, ValueError):
                continue
            if numeric_value > 0.0:
                sample_times.append(numeric_value)

        if sample_times:
            return "discrete"

        solver_type = model_solver_type.lower()
        if solver_type == "fixed-step":
            return "discrete_like"
        if solver_type == "variable-step":
            return "continuous_like"
        return "unspecified"


__all__ = [
    "ControllerDiscoveryResult",
    "PRIMARY_CONTROLLER_TAG",
    "SECONDARY_CONTROLLER_TAG",
    "SimulinkBlockDiscovery",
]
