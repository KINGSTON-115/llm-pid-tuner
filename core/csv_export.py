from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
import re
import threading
from typing import Any, Dict, Optional, Set


CSV_FIELDNAMES = [
    "session_id",
    "mode",
    "round",
    "timestamp_ms",
    "setpoint",
    "input",
    "pwm",
    "error",
    "p",
    "i",
    "d",
    "p2",
    "i2",
    "d2",
]

_ROUND_PATTERN = re.compile(r"\bround\s+(\d+)\b", re.IGNORECASE)


class CsvEventExporter:
    """Best-effort CSV export for runtime sample events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handle = None
        self._writer: csv.DictWriter[str] | None = None
        self._path: Optional[Path] = None
        self._session_id = ""
        self._mode = ""
        self._round_index = 0
        self._warned_paths: Set[str] = set()

    def reset(self) -> None:
        with self._lock:
            self._close_unlocked()
            self._session_id = ""
            self._mode = ""
            self._round_index = 0

    def handle_event(
        self, event_type: str, payload: Dict[str, Any], *, csv_path: str
    ) -> None:
        normalized_path = csv_path.strip()
        with self._lock:
            if not normalized_path:
                self._close_unlocked()
                self._session_id = ""
                self._mode = ""
                self._round_index = 0
                return

            if event_type == "lifecycle":
                self._handle_lifecycle_unlocked(payload, normalized_path)
                return

            if event_type != "sample":
                return

            self._ensure_session_unlocked()
            if not self._ensure_writer_unlocked(normalized_path):
                return

            is_first_row_of_round = getattr(self, "_last_round_index", None) != self._round_index
            self._last_round_index = self._round_index

            # For dynamic setpoints, we need to record it whenever it changes
            current_setpoint = payload.get("setpoint", "")
            last_setpoint = getattr(self, "_last_setpoint", None)
            is_setpoint_changed = current_setpoint != last_setpoint
            self._last_setpoint = current_setpoint

            row = {
                "session_id": self._session_id if is_first_row_of_round else "",
                "mode": self._mode if is_first_row_of_round else "",
                "round": self._round_index if is_first_row_of_round else "",
                "timestamp_ms": payload.get("timestamp", ""),
                "setpoint": current_setpoint if is_first_row_of_round or is_setpoint_changed else "",
                "input": payload.get("input", ""),
                "pwm": payload.get("pwm", ""),
                "error": payload.get("error", ""),
                # Keep controller gains on every sample row so exports remain
                # self-contained for post-run analysis and round replays.
                "p": payload.get("p", ""),
                "i": payload.get("i", ""),
                "d": payload.get("d", ""),
                "p2": payload.get("p2", ""),
                "i2": payload.get("i2", ""),
                "d2": payload.get("d2", ""),
            }
            self._writer.writerow(row)
            self._handle.flush()

    def _handle_lifecycle_unlocked(
        self, payload: Dict[str, Any], normalized_path: str
    ) -> None:
        phase = str(payload.get("phase", "")).strip().lower()
        detail = str(payload.get("detail", "") or payload.get("message", "")).strip()

        if phase == "starting":
            self._ensure_session_unlocked(force_new=True)
            inferred_mode = self._infer_mode(detail)
            if inferred_mode:
                self._mode = inferred_mode
            self._ensure_writer_unlocked(normalized_path)
            return

        if phase == "collecting":
            round_index = self._extract_round_index(detail)
            if round_index is not None:
                self._round_index = round_index
            elif self._round_index <= 0:
                self._round_index = 1

            inferred_mode = self._infer_mode(detail)
            if inferred_mode and not self._mode:
                self._mode = inferred_mode
            return

        if phase in {"completed", "stopped", "error"}:
            self._close_unlocked()

    def _ensure_session_unlocked(self, *, force_new: bool = False) -> None:
        if self._session_id and not force_new:
            return
        self._session_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self._round_index = 0
        if force_new:
            self._mode = ""

    def _ensure_writer_unlocked(self, normalized_path: str) -> bool:
        resolved_path = Path(normalized_path).expanduser()

        if self._path == resolved_path and self._writer is not None and self._handle is not None:
            return True

        self._close_unlocked()
        try:
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            file_exists = resolved_path.exists() and resolved_path.stat().st_size > 0
            self._handle = resolved_path.open("a", encoding="utf-8", newline="")
            self._writer = csv.DictWriter(self._handle, fieldnames=CSV_FIELDNAMES)
            if not file_exists:
                self._writer.writeheader()
                self._handle.flush()
            self._path = resolved_path
            return True
        except OSError as exc:
            warning_key = str(resolved_path)
            if warning_key not in self._warned_paths:
                self._warned_paths.add(warning_key)
                print(f"[WARN] Failed to open CSV export path '{resolved_path}': {exc}")
            self._close_unlocked()
            return False

    def _close_unlocked(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
        self._handle = None
        self._writer = None
        self._path = None

    @staticmethod
    def _extract_round_index(detail: str) -> int | None:
        match = _ROUND_PATTERN.search(detail)
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def _infer_mode(detail: str) -> str:
        normalized = detail.lower()
        if "simulink" in normalized:
            return "simulink"
        if "python" in normalized and "simulation" in normalized:
            return "python_sim"
        if normalized.startswith("opening ") or "baud" in normalized or "serial" in normalized:
            return "hardware"
        return ""
