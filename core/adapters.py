import time
from typing import Any, Dict, List, Optional, Tuple
from core.env import BaseTuningEnvironment, controller_requests_abort
from core.config import CONFIG
from hw.profiles import (
    DEFAULT_HARDWARE_PROFILE,
    get_hardware_sample_format_hint,
    normalize_hardware_profile,
)

def _apply_requested_setpoint(controller: Any, setter: Any) -> None:
    if controller is None or not hasattr(controller, "consume_setpoint_request"):
        return
    requested = controller.consume_setpoint_request()
    if requested is None:
        return
    setter(float(requested))

class PythonSimEnv(BaseTuningEnvironment):
    def __init__(self, sim: Any, setpoint: float, controller: Any = None):
        self.sim = sim
        self._setpoint = float(setpoint)
        self.controller = controller
        self.prompt_context = {}
        self.last_setpoint_issue = ""
        self.last_setpoint_message = ""

    def collect_samples(self) -> List[Dict[str, float]]:
        samples = []
        target_steps = getattr(self.sim, "target_steps", CONFIG["BUFFER_SIZE"])
        
        while len(samples) < target_steps: # Use explicit step count instead of buffer full check
            if controller_requests_abort(self.controller):
                return samples

            _apply_requested_setpoint(self.controller, self.set_setpoint)
            self.sim.compute_pid()
            self.sim.update()
            data = self.sim.get_data()
            if isinstance(data, list):
                if not data:
                    break
                # Only take up to target_steps
                samples.extend(data)
                if len(samples) >= target_steps:
                    break
            else:
                samples.append(data)

        return samples

    def apply_pid(self, primary_pid: Dict[str, float], secondary_pid: Optional[Dict[str, float]] = None) -> None:
        self.sim.set_pid(primary_pid["p"], primary_pid["i"], primary_pid["d"])

    def get_current_pid(self) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        return {"p": self.sim.kp, "i": self.sim.ki, "d": self.sim.kd}, None

    def get_setpoint(self) -> float:
        try:
            return float(getattr(self.sim, "setpoint", self._setpoint))
        except (TypeError, ValueError):
            return self._setpoint

    def set_setpoint(self, setpoint: float) -> bool:
        self.last_setpoint_issue = ""
        value = float(setpoint)
        if hasattr(self.sim, "set_setpoint"):
            self.sim.set_setpoint(value)
        else:
            setattr(self.sim, "setpoint", value)
            if hasattr(self.sim, "base_setpoint"):
                setattr(self.sim, "base_setpoint", value)
        self._setpoint = value
        self.last_setpoint_message = f"Setpoint changed to {value:g}."
        return True

    def get_prompt_context(self) -> Dict[str, Any]:
        return self.prompt_context

    def shutdown(self) -> None:
        pass

    def reset_buffer_state(self) -> None:
        pass

class SimulinkEnv(BaseTuningEnvironment):
    def __init__(self, bridge: Any, setpoint: float, controller: Any = None):
        self.bridge = bridge
        self._setpoint = float(setpoint)
        self.controller = controller
        self.prompt_context = {}
        self.last_apply_issue = ""
        self.last_setpoint_issue = ""
        self.last_setpoint_message = ""

    def _bridge_gain(self, *names: str, default: float) -> float:
        for name in names:
            if not hasattr(self.bridge, name):
                continue
            value = getattr(self.bridge, name)
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return float(default)

    def collect_samples(self) -> List[Dict[str, float]]:
        samples = []
        max_run_steps = 200
        run_count = 0
        
        while True:
            if controller_requests_abort(self.controller):
                return samples

            run_count += 1
            if run_count > max_run_steps:
                raise RuntimeError("Simulink data collection timed out.")
                
            _apply_requested_setpoint(self.controller, self.set_setpoint)
            self.bridge.run_step()
            batch = self.bridge.get_data()
            for data in batch:
                if self.controller and getattr(self.controller, "should_stop", False):
                    return samples
                samples.append(data)
                
            if len(samples) >= getattr(self.bridge, "target_steps", getattr(self.bridge, "target_buffer_size", CONFIG["BUFFER_SIZE"])):
                break
                
        return samples

    def apply_pid(self, primary_pid: Dict[str, float], secondary_pid: Optional[Dict[str, float]] = None) -> None:
        self.last_apply_issue = ""
        try:
            self.bridge.set_pid_pair(primary_pid, secondary_pid)
        except Exception as exc:
            self.last_apply_issue = str(exc)
            return
        self.last_apply_issue = str(getattr(self.bridge, "last_apply_issue", "") or "")

    def get_current_pid(self) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        primary = {
            "p": self._bridge_gain("kp", default=1.0),
            "i": self._bridge_gain("ki", default=0.1),
            "d": self._bridge_gain("kd", default=0.05),
        }
        secondary = None
        if getattr(self.bridge, "has_secondary_pid", False):
            secondary = {
                "p": self._bridge_gain(
                    "secondary_kp", "kp2", default=primary["p"]
                ),
                "i": self._bridge_gain(
                    "secondary_ki", "ki2", default=primary["i"]
                ),
                "d": self._bridge_gain(
                    "secondary_kd", "kd2", default=primary["d"]
                ),
            }
        return primary, secondary

    def get_setpoint(self) -> float:
        try:
            return float(getattr(self.bridge, "setpoint", self._setpoint))
        except (TypeError, ValueError):
            return self._setpoint

    def set_setpoint(self, setpoint: float) -> bool:
        self.last_setpoint_issue = ""
        value = float(setpoint)
        previous_bridge_setpoint = getattr(self.bridge, "setpoint", self._setpoint)
        used_legacy_fallback = not hasattr(self.bridge, "set_setpoint")
        try:
            if not used_legacy_fallback:
                applied = self.bridge.set_setpoint(value)
                if applied is False:
                    raise RuntimeError("Simulink bridge rejected the setpoint update.")
            else:
                setattr(self.bridge, "setpoint", value)
                if hasattr(self.bridge, "_apply_model_setpoint"):
                    applied = self.bridge._apply_model_setpoint()
                    if applied is False:
                        raise RuntimeError(
                            "No writable Simulink setpoint block was found."
                        )
            self._setpoint = value
            self.last_setpoint_message = f"Setpoint changed to {value:g}."
            return True
        except Exception as exc:
            if used_legacy_fallback:
                setattr(self.bridge, "setpoint", previous_bridge_setpoint)
            self.last_setpoint_issue = f"Failed to update Simulink setpoint: {exc}"
            return False

    def get_prompt_context(self) -> Dict[str, Any]:
        return self.prompt_context

    def shutdown(self) -> None:
        self.bridge.disconnect()

    def reset_buffer_state(self) -> None:
        pass # The simulator.py _run_tuning_loop does session.buffer.reset() which is now in engine

class HardwareEnv(BaseTuningEnvironment):
    # Fixed hardware safeguards. These stay out of user-facing config because
    # most runs should share the same conservative serial sampling thresholds.
    SAMPLE_TIMEOUT_SEC = 20.0
    MIN_SAMPLES_PER_ROUND = 50

    def __init__(self, bridge: Any, initial_pid: Dict[str, float], controller: Any = None):
        self.bridge = bridge
        self.current_pid = dict(initial_pid)
        self.current_secondary_pid: Optional[Dict[str, float]] = None
        self.current_setpoint = 0.0
        self.controller = controller
        self.prompt_context = {}
        self.last_collect_issue = ""
        self.last_collect_warning = ""
        self.last_apply_issue = ""
        self.last_setpoint_issue = ""
        self.last_setpoint_message = ""

    def collect_samples(self) -> List[Dict[str, float]]:
        samples = []
        target_size = CONFIG["BUFFER_SIZE"]
        timeout_sec = float(self.SAMPLE_TIMEOUT_SEC)
        started_at = time.time()
        invalid_lines = 0
        last_invalid_line = ""
        self.last_collect_issue = ""
        self.last_collect_warning = ""
        hardware_profile = normalize_hardware_profile(
            getattr(self.bridge, "hardware_profile", DEFAULT_HARDWARE_PROFILE)
        )
        expected_formats = get_hardware_sample_format_hint(hardware_profile)

        while len(samples) < target_size:
            if controller_requests_abort(self.controller):
                return samples

            _apply_requested_setpoint(self.controller, self.set_setpoint)
            if (time.time() - started_at) >= timeout_sec:
                if len(samples) >= int(self.MIN_SAMPLES_PER_ROUND):
                    self.last_collect_warning = (
                        f"Hardware sampling timed out after {timeout_sec:.1f}s: "
                        f"collected {len(samples)}/{target_size} valid samples; "
                        f"reached minimum {self.MIN_SAMPLES_PER_ROUND}, proceeding."
                    )
                    return samples
                if samples:
                    self.last_collect_issue = (
                        f"Hardware sampling timed out after {timeout_sec:.1f}s: "
                        f"collected {len(samples)}/{target_size} valid samples, "
                        f"below minimum {self.MIN_SAMPLES_PER_ROUND}."
                    )
                elif invalid_lines > 0:
                    detail = f" Last raw line: '{last_invalid_line[:160]}'." if last_invalid_line else ""
                    self.last_collect_issue = (
                        f"No valid hardware samples were parsed within {timeout_sec:.1f}s. "
                        f"Expected {expected_formats}.{detail}"
                    )
                else:
                    self.last_collect_issue = (
                        f"No serial data was received within {timeout_sec:.1f}s. "
                        "Check serial port selection, baud rate, and firmware output."
                    )
                return []

            line = self.bridge.read_line()
            if line:
                data = self.bridge.parse_data(line)
                if data:
                    if all(key in data for key in ("p", "i", "d")):
                        self.current_pid = {
                            "p": float(data["p"]),
                            "i": float(data["i"]),
                            "d": float(data["d"]),
                        }
                    if "setpoint" in data:
                        self.current_setpoint = float(data["setpoint"])
                    if all(key in data for key in ("p2", "i2", "d2")):
                        self.current_secondary_pid = {
                            "p": float(data["p2"]),
                            "i": float(data["i2"]),
                            "d": float(data["d2"]),
                        }
                    samples.append(data)
                    continue
                invalid_lines += 1
                last_invalid_line = str(line)
        return samples

    def apply_pid(self, primary_pid: Dict[str, float], secondary_pid: Optional[Dict[str, float]] = None) -> None:
        self.last_apply_issue = ""
        hardware_profile = normalize_hardware_profile(
            getattr(self.bridge, "hardware_profile", DEFAULT_HARDWARE_PROFILE)
        )

        if hardware_profile == "mspm0_datavision":
            self.last_apply_issue = (
                "MSPM0 DataVision profile is read-only: PID write-back is disabled "
                "until a command protocol is confirmed."
            )
            return

        if hasattr(self.bridge, "send_profile_command"):
            primary_sent = self.bridge.send_profile_command("SET", primary_pid=primary_pid)
            if primary_sent is False:
                self.last_apply_issue = (
                    f"Failed to apply hardware PID: {self.bridge.last_error or 'unknown write error'}"
                )
                return
            self.current_pid = dict(primary_pid)
            if secondary_pid is not None:
                secondary_sent = self.bridge.send_profile_command(
                    "SET2",
                    secondary_pid=secondary_pid,
                )
                if secondary_sent is False:
                    self.last_apply_issue = (
                        "Primary PID was applied, but controller 2 update failed: "
                        f"{self.bridge.last_error or 'unknown write error'}"
                    )
                    return
                self.current_secondary_pid = dict(secondary_pid)
            return

        cmd = f"SET P:{primary_pid['p']} I:{primary_pid['i']} D:{primary_pid['d']}"
        primary_sent = self.bridge.send_command(cmd)
        if primary_sent is False:
            self.last_apply_issue = (
                f"Failed to apply hardware PID: {self.bridge.last_error or 'unknown write error'}"
            )
            return

        self.current_pid = dict(primary_pid)
        if secondary_pid is not None:
            cmd2 = f"SET2 P:{secondary_pid['p']} I:{secondary_pid['i']} D:{secondary_pid['d']}"
            secondary_sent = self.bridge.send_command(cmd2)
            if secondary_sent is False:
                self.last_apply_issue = (
                    "Primary PID was applied, but controller 2 update failed: "
                    f"{self.bridge.last_error or 'unknown write error'}"
                )
                return
            self.current_secondary_pid = dict(secondary_pid)

    def get_current_pid(self) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        secondary = (
            dict(self.current_secondary_pid)
            if self.current_secondary_pid is not None
            else None
        )
        return dict(self.current_pid), secondary

    def get_setpoint(self) -> float:
        return self.current_setpoint

    def set_setpoint(self, setpoint: float) -> bool:
        self.last_setpoint_issue = ""
        self.last_setpoint_message = ""
        value = float(setpoint)
        hardware_profile = normalize_hardware_profile(
            getattr(self.bridge, "hardware_profile", DEFAULT_HARDWARE_PROFILE)
        )

        if hardware_profile == "mspm0_datavision":
            self.last_setpoint_issue = (
                "MSPM0 DataVision profile is read-only: setpoint write-back is disabled "
                "until a command protocol is confirmed."
            )
            return False

        if hasattr(self.bridge, "send_profile_command"):
            sent = self.bridge.send_profile_command("SETPOINT", setpoint=value)
            if sent is False:
                self.last_setpoint_issue = (
                    f"Failed to apply hardware setpoint: {self.bridge.last_error or 'unknown write error'}"
                )
                return False
        else:
            sent = self.bridge.send_command(f"SETPOINT:{value:g}")
            if sent is False:
                self.last_setpoint_issue = (
                    f"Failed to apply hardware setpoint: {self.bridge.last_error or 'unknown write error'}"
                )
                return False

        self.current_setpoint = value
        self.last_setpoint_message = f"Setpoint changed to {value:g}."
        return True

    def get_prompt_context(self) -> Dict[str, Any]:
        return self.prompt_context

    def shutdown(self) -> None:
        self.bridge.disconnect()

    def reset_buffer_state(self) -> None:
        pass
