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
                
            if len(samples) >= getattr(self.bridge, "target_buffer_size", CONFIG["BUFFER_SIZE"]):
                break
                
        return samples

    def apply_pid(self, primary_pid: Dict[str, float], secondary_pid: Optional[Dict[str, float]] = None) -> None:
        self.bridge.set_pid_pair(primary_pid, secondary_pid)

    def get_current_pid(self) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        primary = {"p": self.bridge.kp, "i": self.bridge.ki, "d": self.bridge.kd}
        secondary = None
        if getattr(self.bridge, "has_secondary_pid", False):
            secondary = {"p": self.bridge.kp2, "i": self.bridge.ki2, "d": self.bridge.kd2}
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
    def __init__(self, bridge: Any, initial_pid: Dict[str, float], controller: Any = None):
        self.bridge = bridge
        self.current_pid = initial_pid
        self.controller = controller
        self.prompt_context = {}

    def collect_samples(self) -> List[Dict[str, float]]:
        samples = []
        target_size = CONFIG["BUFFER_SIZE"]
        
        while len(samples) < target_size:
            if self.controller and hasattr(self.controller, "wait_while_paused") and not self.controller.wait_while_paused():
                return samples
            if self.controller and getattr(self.controller, "should_stop", False):
                return samples
                
            line = self.bridge.read_line()
            if line:
                data = self.bridge.parse_data(line)
                if data:
                    samples.append(data)
            else:
                # If bridge runs out of data (useful in mocks) just return what we have
                break
        return samples

    def apply_pid(self, primary_pid: Dict[str, float], secondary_pid: Optional[Dict[str, float]] = None) -> None:
        cmd = f"SET P:{primary_pid['p']} I:{primary_pid['i']} D:{primary_pid['d']}"
        self.bridge.send_command(cmd)
        self.current_pid = primary_pid
        if secondary_pid is not None:
            cmd2 = f"SET2 P:{secondary_pid['p']} I:{secondary_pid['i']} D:{secondary_pid['d']}"
            self.bridge.send_command(cmd2)

    def get_current_pid(self) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        return self.current_pid, None

    def get_setpoint(self) -> float:
        return 0.0 # Handled by hardware

    def get_prompt_context(self) -> Dict[str, Any]:
        return self.prompt_context

    def shutdown(self) -> None:
        self.bridge.disconnect()

    def reset_buffer_state(self) -> None:
        pass
