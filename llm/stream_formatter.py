from __future__ import annotations

import re
from typing import Callable, Optional

from core.i18n import tr


class JSONStreamFormatter:
    """Incrementally format streamed JSON-like output for the console."""

    def __init__(self, writer: Optional[Callable[[str], None]] = None):
        self.displayed_keys = set()
        self.current_key = None
        self.printed_text = ""
        self.writer = writer or self._default_writer
        self.key_names = {
            "thought_process": tr("\n  [思考]", "\n  [Thought]"),
            "analysis_summary": tr("\n  [分析]", "\n  [Analysis]"),
            "tuning_action": tr("\n  [调参]", "\n  [Action]"),
            "p": tr("\n  [建议] P", "\n  [PID] P"),
            "i": " I",
            "d": " D",
            "controller_1.p": tr("\n  [控制器 1] P", "\n  [Controller 1] P"),
            "controller_1.i": " I",
            "controller_1.d": " D",
            "controller_2.p": tr("\n  [控制器 2] P", "\n  [Controller 2] P"),
            "controller_2.i": " I",
            "controller_2.d": " D",
            "status": tr("\n  [状态]", "\n  [Status]"),
        }
        self.controller_re = re.compile(
            r'"(controller_[12])"\s*:\s*\{([^{}]*)',
            re.DOTALL,
        )
        self.str_re = re.compile(r'"([a-zA-Z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)')
        self.num_re = re.compile(
            r'"([a-zA-Z_]+)"\s*:\s*([0-9\.\-]+|true|false|null)\s*[,}\n]',
            re.IGNORECASE,
        )

    @staticmethod
    def _default_writer(text: str) -> None:
        print(text, end="", flush=True)

    @staticmethod
    def _is_within_spans(index: int, spans: list[tuple[int, int]]) -> bool:
        return any(start <= index < end for start, end in spans)

    def process(self, full_text: str) -> None:
        controller_spans: list[tuple[int, int]] = []
        for controller_match in self.controller_re.finditer(full_text):
            controller_key = controller_match.group(1)
            controller_body = controller_match.group(2)
            controller_spans.append(
                (controller_match.start(2), controller_match.end(2))
            )
            for match in self.num_re.finditer(controller_body + "}"):
                key = match.group(1)
                composite_key = f"{controller_key}.{key}"
                if composite_key in self.displayed_keys:
                    continue

                self.displayed_keys.add(composite_key)
                name = self.key_names.get(composite_key, f"\n  [{composite_key}]")
                self.writer(f",{name}={match.group(2)}")

        for match in self.str_re.finditer(full_text):
            key = match.group(1)
            raw_value = match.group(2)

            if key not in self.displayed_keys:
                self.displayed_keys.add(key)
                self.current_key = key
                self.printed_text = ""
                name = self.key_names.get(key, f"\n  [{key}]")
                self.writer(f"{name} ")

            if self.current_key != key:
                continue

            decoded = (
                raw_value.replace("\\n", "\n")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
            if raw_value.endswith("\\"):
                decoded = decoded[:-1]

            new_text = decoded[len(self.printed_text) :]
            if not new_text:
                continue
            if key in {"thought_process", "analysis_summary"}:
                new_text = new_text.replace("\n", "\n    ")
            self.writer(new_text)
            self.printed_text += new_text

        for match in self.num_re.finditer(full_text):
            key = match.group(1)
            value = match.group(2)
            if self._is_within_spans(match.start(), controller_spans):
                continue
            if key in self.displayed_keys:
                continue

            self.displayed_keys.add(key)
            name = self.key_names.get(key, f"\n  [{key}]")
            if key in {"p", "i", "d"}:
                self.writer(f",{name}={value}")
            else:
                self.writer(f"{name}: {value}")


__all__ = ["JSONStreamFormatter"]
