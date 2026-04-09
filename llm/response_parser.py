from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional


def extract_json_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    stripped = text.strip()

    if stripped:
        candidates.append(stripped)

    fenced_matches = re.findall(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    candidates.extend(fenced_matches)

    for start in range(len(text)):
        if text[start] != "{":
            continue
        depth = 0
        for end in range(start, len(text)):
            char = text[end]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start : end + 1])
                    break

    return candidates


def _sanitize_pid_mapping(mapping: Dict[str, Any]) -> Dict[str, float]:
    pid_values: Dict[str, float] = {}
    for key in ("p", "i", "d"):
        value = mapping.get(key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric) and numeric >= 0:
            pid_values[key] = numeric
    return pid_values


def sanitize_result(data: Dict[str, Any]) -> Dict[str, Any]:
    sanitized = dict(data)

    for key in ("p", "i", "d"):
        value = sanitized.get(key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            sanitized.pop(key, None)
            continue

        if not math.isfinite(numeric) or numeric < 0:
            sanitized.pop(key, None)
        else:
            sanitized[key] = numeric

    for controller_key in ("controller_1", "controller_2"):
        controller_value = sanitized.get(controller_key)
        if isinstance(controller_value, dict):
            sanitized[controller_key] = _sanitize_pid_mapping(controller_value)
            if not sanitized[controller_key]:
                sanitized.pop(controller_key, None)

    if "status" in sanitized:
        status = str(sanitized["status"]).strip().upper()
        sanitized["status"] = "DONE" if status == "DONE" else "TUNING"

    if not sanitized.get("analysis_summary"):
        sanitized["analysis_summary"] = str(
            sanitized.get("analysis") or "No analysis summary provided."
        )

    if not sanitized.get("thought_process"):
        sanitized["thought_process"] = str(
            sanitized.get("analysis_summary") or "No detailed reasoning provided."
        )

    if not sanitized.get("tuning_action"):
        sanitized["tuning_action"] = "ADJUST_PID"

    return sanitized


def parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    for candidate in extract_json_candidates(text):
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        return sanitize_result(data)
    return None


__all__ = [
    "extract_json_candidates",
    "parse_json_response",
    "sanitize_result",
]
