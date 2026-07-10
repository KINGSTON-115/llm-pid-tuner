from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from core.env import BaseTuningEnvironment


class PLCEnvironment(BaseTuningEnvironment):
    """Tuning environment backed by a PLC bridge.

    The environment is safe by default because write permission is enforced by
    the bridge profile and PLC-side handshake rather than by the LLM loop.
    """

    def __init__(self, bridge: Any, controller: Any = None):
        self.bridge = bridge
        self.controller = controller
        self.prompt_context = self._build_prompt_context()
        self.last_collect_issue = ""
        self.last_collect_warning = ""
        self.last_apply_issue = ""
        self.last_setpoint_issue = ""
        self.last_setpoint_message = ""

    def _build_prompt_context(self) -> Dict[str, Any]:
        profile = self.bridge.profile
        return {
            "environment_type": "plc",
            "plc_profile": profile.name,
            "plc_protocol": profile.connection.protocol,
            "pid_form": profile.pid_semantics.form,
            "integral_time_unit": profile.pid_semantics.integral_time_unit,
            "derivative_time_unit": profile.pid_semantics.derivative_time_unit,
            "controller_action": profile.pid_semantics.controller_action,
            "write_mode": profile.write_policy.mode,
            "canonical_pid_limits": profile.canonical_pid_limits,
            **profile.process,
        }

    def collect_samples(self) -> List[Dict[str, float]]:
        profile = self.bridge.profile
        acquisition = profile.acquisition
        samples: List[Dict[str, float]] = []
        deadline = time.monotonic() + acquisition.timeout_sec
        self.last_collect_issue = ""
        self.last_collect_warning = ""

        while len(samples) < acquisition.sample_count:
            if self.controller and hasattr(self.controller, "wait_while_paused"):
                if not self.controller.wait_while_paused():
                    return samples
            if self.controller and getattr(self.controller, "should_stop", False):
                return samples
            if time.monotonic() >= deadline:
                message = (
                    f"PLC sampling timed out with {len(samples)}/"
                    f"{acquisition.sample_count} samples."
                )
                if len(samples) >= acquisition.minimum_samples:
                    self.last_collect_warning = message
                    return samples
                self.last_collect_issue = message
                return []
            try:
                snapshot = self.bridge.read_snapshot()
            except Exception as exc:
                message = f"PLC sample read failed: {exc}"
                if len(samples) >= acquisition.minimum_samples:
                    self.last_collect_warning = message
                    return samples
                self.last_collect_issue = message
                return []
            if "plc_quality" in snapshot and not bool(snapshot["plc_quality"]):
                message = "PLC reported bad sample quality."
                if len(samples) >= acquisition.minimum_samples:
                    self.last_collect_warning = message
                    return samples
                self.last_collect_issue = message
                return []
            samples.append(snapshot)
            if (
                len(samples) < acquisition.sample_count
                and acquisition.poll_interval_sec > 0.0
            ):
                time.sleep(acquisition.poll_interval_sec)
        return samples

    def apply_pid(
        self,
        primary_pid: Dict[str, float],
        secondary_pid: Optional[Dict[str, float]] = None,
    ) -> None:
        self.last_apply_issue = ""
        if secondary_pid is not None:
            self.last_apply_issue = (
                "the PLC prototype supports one PID loop per profile"
            )
            return
        if not self.bridge.apply_pid(primary_pid):
            self.last_apply_issue = self.bridge.last_error or "PLC PID update failed"

    def arm_writes_once(self) -> bool:
        return bool(self.bridge.arm_writes_once())

    def get_current_pid(
        self,
    ) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        return self.bridge.read_active_pid(), None

    def get_setpoint(self) -> float:
        return float(self.bridge.read_value("setpoint"))

    def set_setpoint(self, setpoint: float) -> bool:
        self.last_setpoint_message = ""
        self.last_setpoint_issue = (
            "PLC setpoint write-back is intentionally disabled in the prototype"
        )
        return False

    def get_prompt_context(self) -> Dict[str, Any]:
        return dict(self.prompt_context)

    def shutdown(self) -> None:
        self.bridge.disconnect()

    def reset_buffer_state(self) -> None:
        pass
