from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


def controller_requests_abort(controller: Any) -> bool:
    """True when the controller asked to stop, or a pause ended with a stop.

    Blocks while the controller is paused. Shared by every environment's
    sample-collection loop and by the tuning engine's round loop.
    """
    if controller is None:
        return False
    if hasattr(controller, "wait_while_paused") and not controller.wait_while_paused():
        return True
    return bool(getattr(controller, "should_stop", False))


class BaseTuningEnvironment(ABC):
    """
    Abstract interface for all tuning environments (Hardware, Python Sim, Simulink).
    Decouples the core TuningEngine from the specific I/O details.
    """

    @abstractmethod
    def collect_samples(self) -> List[Dict[str, float]]:
        """
        Run the simulation or read from hardware until a full buffer of data is ready.
        Return a list of sample dictionaries. Return an empty list if interrupted.
        """
        pass

    @abstractmethod
    def apply_pid(self, primary_pid: Dict[str, float], secondary_pid: Optional[Dict[str, float]] = None) -> None:
        """Apply new PID parameters to the environment."""
        pass

    @abstractmethod
    def get_current_pid(self) -> Tuple[Dict[str, float], Optional[Dict[str, float]]]:
        """Return (primary_pid, secondary_pid)."""
        pass

    @abstractmethod
    def get_setpoint(self) -> float:
        """Return the current target setpoint."""
        pass

    def set_setpoint(self, setpoint: float) -> bool:
        """Try to update the target setpoint at runtime."""
        return False

    @abstractmethod
    def get_prompt_context(self) -> Dict[str, Any]:
        """Return context metadata for LLM prompt generation."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Clean up resources (close ports, stop engines, etc)."""
        pass

    @abstractmethod
    def reset_buffer_state(self) -> None:
        """Reset internal state before starting a new round."""
        pass
