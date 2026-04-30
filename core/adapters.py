import time
from typing import Any, Dict, List, Optional, Tuple
from core.env import BaseTuningEnvironment
from core.config import CONFIG

class PythonSimEnv(BaseTuningEnvironment):
    def __init__(self, sim: Any, setpoint: float, controller: Any = None):
        self.sim = sim
        self._setpoint = setpoint
        self.controller = controller
        self.prompt_context = {}

    def collect_samples(self) -> List[Dict[str, float]]:
        samples = []
        target_steps = getattr(self.sim, "target_steps", CONFIG["BUFFER_SIZE"])
        
        while len(samples) < target_steps: # Use explicit step count instead of buffer full check
            if self.controller and hasattr(self.controller, "wait_while_paused") and not self.controller.wait_while_paused():
                return samples
            if self.controller and getattr(self.controller, "should_stop", False):
                return samples

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
        return self._setpoint

    def get_prompt_context(self) -> Dict[str, Any]:
        return self.prompt_context

    def shutdown(self) -> None:
        pass

    def reset_buffer_state(self) -> None:
        pass

class SimulinkEnv(BaseTuningEnvironment):
    def __init__(self, bridge: Any, setpoint: float, controller: Any = None):
        self.bridge = bridge
        self._setpoint = setpoint
        self.controller = controller
        self.prompt_context = {}

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
            if self.controller and hasattr(self.controller, "wait_while_paused") and not self.controller.wait_while_paused():
                return samples
            if self.controller and getattr(self.controller, "should_stop", False):
                return samples

            run_count += 1
            if run_count > max_run_steps:
                raise RuntimeError("Simulink data collection timed out.")
                
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
        self.bridge.set_pid_pair(primary_pid, secondary_pid)

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
        return self._setpoint

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
        self.controller = controller
        self.prompt_context = {}
        self.last_collect_issue = ""
        self.last_collect_warning = ""
        self.last_apply_issue = ""

    def collect_samples(self) -> List[Dict[str, float]]:
        samples = []
        target_size = CONFIG["BUFFER_SIZE"]
        timeout_sec = float(self.SAMPLE_TIMEOUT_SEC)
        started_at = time.time()
        invalid_lines = 0
        last_invalid_line = ""
        self.last_collect_issue = ""
        self.last_collect_warning = ""

        while len(samples) < target_size:
            if self.controller and hasattr(self.controller, "wait_while_paused") and not self.controller.wait_while_paused():
                return samples
            if self.controller and getattr(self.controller, "should_stop", False):
                return samples

            if (time.time() - started_at) >= timeout_sec:
                expected = "timestamp_ms,setpoint,input,pwm,error,p,i,d"
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
                        f"Expected CSV: {expected}.{detail}"
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
        return 0.0 # Handled by hardware

    def get_prompt_context(self) -> Dict[str, Any]:
        return self.prompt_context

    def shutdown(self) -> None:
        self.bridge.disconnect()

    def reset_buffer_state(self) -> None:
        pass
