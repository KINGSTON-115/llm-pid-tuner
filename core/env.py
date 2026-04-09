from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple

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
