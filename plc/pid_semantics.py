from __future__ import annotations

import math
from typing import Any, Dict, Mapping

from plc.profile import PIDSemantics, PLCProfileError


def _nonnegative_finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PLCProfileError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or result < 0.0:
        raise PLCProfileError(f"{name} must be finite and non-negative")
    return result


def _unit_factor(unit: str) -> float:
    return 60.0 if unit == "minutes" else 1.0


class PIDSemanticsCodec:
    """Convert PLC-native PID values to the engine's parallel P/I/D form."""

    def __init__(self, semantics: PIDSemantics):
        self.semantics = semantics

    def native_to_canonical(
        self, kp: Any, integral: Any, derivative: Any
    ) -> Dict[str, float]:
        p = _nonnegative_finite(kp, "native kp")
        native_i = _nonnegative_finite(integral, "native integral")
        native_d = _nonnegative_finite(derivative, "native derivative")
        integral_factor = _unit_factor(self.semantics.integral_time_unit)
        derivative_factor = _unit_factor(self.semantics.derivative_time_unit)

        if self.semantics.form == "ideal":
            ti_sec = native_i * integral_factor
            td_sec = native_d * derivative_factor
            i = p / ti_sec if p > 0.0 and ti_sec > 0.0 else 0.0
            d = p * td_sec if p > 0.0 and td_sec > 0.0 else 0.0
        else:
            i = native_i / integral_factor
            d = native_d * derivative_factor
        return {"p": p, "i": i, "d": d}

    def canonical_to_native(self, pid: Mapping[str, Any]) -> Dict[str, float]:
        p = _nonnegative_finite(pid.get("p"), "canonical p")
        i = _nonnegative_finite(pid.get("i"), "canonical i")
        d = _nonnegative_finite(pid.get("d"), "canonical d")
        integral_factor = _unit_factor(self.semantics.integral_time_unit)
        derivative_factor = _unit_factor(self.semantics.derivative_time_unit)

        if self.semantics.form == "ideal":
            if p == 0.0 and (i > 0.0 or d > 0.0):
                raise PLCProfileError(
                    "ideal PID form cannot represent non-zero canonical I or D "
                    "when canonical P is zero"
                )
            ti_sec = p / i if p > 0.0 and i > 0.0 else 0.0
            td_sec = d / p if p > 0.0 and d > 0.0 else 0.0
            native_i = ti_sec / integral_factor
            native_d = td_sec / derivative_factor
        else:
            native_i = i * integral_factor
            native_d = d / derivative_factor
        return {"kp": p, "integral": native_i, "derivative": native_d}


def validate_canonical_pid(
    pid: Mapping[str, Any], limits: Mapping[str, Mapping[str, float]]
) -> Dict[str, float]:
    validated: Dict[str, float] = {}
    for gain in ("p", "i", "d"):
        value = _nonnegative_finite(pid.get(gain), f"canonical {gain}")
        gain_limits = limits[gain]
        minimum = float(gain_limits["min"])
        maximum = float(gain_limits["max"])
        if not minimum <= value <= maximum:
            raise PLCProfileError(
                f"canonical {gain}={value:g} is outside [{minimum:g}, {maximum:g}]"
            )
        validated[gain] = value
    return validated
