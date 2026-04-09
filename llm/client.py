#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm/client.py - LLM client wrapper with streaming and prompt selection.
"""

from __future__ import annotations

import json
import math
import re
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from core.config import CONFIG
from core.i18n import tr
from llm.prompts import SYSTEM_PROMPT, build_user_prompt, get_system_prompt


class JSONStreamFormatter:
    """Incrementally formats streamed JSON-like output for the console."""

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
            "status": tr("\n  [状态]", "\n  [Status]"),
        }
        self.str_re = re.compile(r'"([a-zA-Z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)')
        self.num_re = re.compile(
            r'"([a-zA-Z_]+)"\s*:\s*([0-9\.\-]+|true|false|null)\s*[,}\n]', re.IGNORECASE
        )

    @staticmethod
    def _default_writer(text: str) -> None:
        print(text, end="", flush=True)

    def process(self, full_text: str) -> None:
        str_matches = list(self.str_re.finditer(full_text))
        for match in str_matches:
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
                raw_value.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
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

        num_matches = list(self.num_re.finditer(full_text))
        for match in num_matches:
            key = match.group(1)
            value = match.group(2)
            if key in self.displayed_keys:
                continue

            self.displayed_keys.add(key)
            name = self.key_names.get(key, f"\n  [{key}]")
            if key in {"p", "i", "d"}:
                self.writer(f",{name}={value}")
            else:
                self.writer(f"{name}: {value}")


class LLMTuner:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider: str = "openai",
        stream_callback: Optional[Callable[[str, bool], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        emit_console: bool = True,
        abort_check: Optional[Callable[[], bool]] = None,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.provider_choice = self._normalize_provider_choice(provider)
        self.provider = self._resolve_transport()
        self.timeout = CONFIG.get("LLM_REQUEST_TIMEOUT", 60)
        self.debug_output = CONFIG.get("LLM_DEBUG_OUTPUT", False)
        self.emit_console = emit_console
        self.stream_callback = stream_callback
        self.log_callback = log_callback
        self.abort_check = abort_check
        self.use_sdk = False
        self.client = None

        try:
            if self.provider == "openai":
                import openai

                self.client = openai.OpenAI(api_key=api_key, base_url=self.base_url)
            elif self.provider == "anthropic":
                import anthropic

                self.client = anthropic.Anthropic(
                    api_key=api_key, base_url=self.base_url
                )
        except ImportError:
            self.requests = self._import_requests()
        except Exception:
            if self.debug_output:
                traceback.print_exc()
            self.requests = self._import_requests()
        else:
            self.use_sdk = True

    @staticmethod
    def _normalize_provider_choice(provider: Optional[str]) -> str:
        normalized = str(provider or "").strip().lower()
        normalized = normalized.replace("-", "_").replace(" ", "_")
        return normalized or "openai"

    def _resolve_transport(self) -> str:
        if self.provider_choice in (
            "openai",
            "openai_compat",
            "openai_compatible",
            "openai_claude",
            "claude_openai",
            "claude_relay",
        ):
            return "openai"
        if self.provider_choice in ("anthropic", "anthropic_native", "claude_native"):
            return "anthropic"

        base_url_lower = self.base_url.lower()
        if self.provider_choice == "auto" and "api.anthropic.com" in base_url_lower:
            return "anthropic"
        return "openai"

    def _import_requests(self):
        import requests

        return requests

    def _ensure_requests(self) -> None:
        if not hasattr(self, "requests") or self.requests is None:
            self.requests = self._import_requests()

    def _interruptible_sleep(self, seconds: float) -> bool:
        """每 0.1s 轮询 abort_check，若中止返回 False，否则睡完返回 True。"""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.abort_check and self.abort_check():
                return False
            time.sleep(min(0.1, deadline - time.time()))
        return True

    def _emit_log(self, label: str, message: str) -> None:
        if self.log_callback is not None:
            self.log_callback(label, message)
        if self.emit_console:
            print(message)

    def _emit_stream_update(
        self,
        full_content: str,
        *,
        done: bool = False,
        formatter: Optional[JSONStreamFormatter] = None,
    ) -> None:
        if formatter is not None:
            formatter.process(full_content)
        if self.stream_callback is not None:
            self.stream_callback(full_content, done)

    def _extract_openai_sdk_chunk_content(
        self,
        chunk: Any,
        accumulated_content: str,
    ) -> str:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return ""

        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            delta_content = getattr(delta, "content", None)
            if isinstance(delta_content, str) and delta_content:
                return delta_content

        message = getattr(choice, "message", None)
        if message is None:
            return ""

        message_content = getattr(message, "content", None)
        if not isinstance(message_content, str) or not message_content:
            return ""

        if not accumulated_content:
            return message_content

        if message_content.startswith(accumulated_content):
            return message_content[len(accumulated_content) :]

        return ""

    def _call_with_retry(
        self, func: Callable[..., str], *args: Any, **kwargs: Any
    ) -> str:
        max_retries = 5
        delays = [2, 4, 8, 16, 32]
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries):
            if self.abort_check and self.abort_check():
                return ""
            try:
                return func(*args, **kwargs)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exception = exc
                if attempt < max_retries - 1:
                    self._emit_log(
                        "warn",
                        f"\n[WARN] LLM call failed: {exc}. Retrying in {delays[attempt]}s...",
                    )
                    if not self._interruptible_sleep(delays[attempt]):
                        return ""
                else:
                    self._emit_log(
                        "error",
                        f"\n[ERROR] LLM call failed after {max_retries} attempts: {exc}",
                    )
                    raise

        if last_exception is not None:
            raise last_exception
        return ""

    def _request_via_http(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str = SYSTEM_PROMPT,
    ) -> str:
        self._ensure_requests()
        full_content = ""
        formatter = JSONStreamFormatter() if self.emit_console else None

        if self.provider == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "system": system_prompt,
                "messages": anthropic_msgs,
                "temperature": 0.3,
                "max_tokens": 1000,
                "stream": True,
            }
            base_url = self.base_url.rstrip("/")
            if not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"
            with self.requests.post(
                f"{base_url}/messages",
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line_str = line.decode("utf-8")
                    if not line_str.startswith("data: "):
                        continue
                    data_str = line_str[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "content_block_delta" and "delta" in data:
                        chunk = data["delta"].get("text", "")
                        if chunk:
                            full_content += chunk
                            self._emit_stream_update(full_content, formatter=formatter)
                            if self.abort_check and self.abort_check():
                                break
        else:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.model,
                "messages": openai_msgs,
                "temperature": 0.3,
                "stream": True,
            }
            with self.requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line_str = line.decode("utf-8")
                    if not line_str.startswith("data: "):
                        continue
                    data_str = line_str[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices", [])
                    if choices and "delta" in choices[0]:
                        chunk = choices[0]["delta"].get("content", "")
                        if chunk:
                            full_content += chunk
                            self._emit_stream_update(full_content, formatter=formatter)
                            if self.abort_check and self.abort_check():
                                break

        return full_content

    def _execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str = SYSTEM_PROMPT,
    ) -> str:
        self._emit_log("llm", "  LLM is thinking...")
        full_content = ""
        formatter = JSONStreamFormatter() if self.emit_console else None

        if self.use_sdk:
            try:
                if self.provider == "openai":
                    resp = self.client.chat.completions.create(  # type: ignore
                        model=self.model,
                        messages=openai_msgs,
                        temperature=0.3,
                        stream=True,
                    )
                    for chunk in resp:
                        content_chunk = self._extract_openai_sdk_chunk_content(
                            chunk,
                            full_content,
                        )
                        if content_chunk:
                            full_content += content_chunk
                            self._emit_stream_update(full_content, formatter=formatter)
                            if self.abort_check and self.abort_check():
                                break
                elif self.provider == "anthropic":
                    with self.client.messages.stream(  # type: ignore
                        model=self.model,
                        system=system_prompt,
                        messages=anthropic_msgs,
                        temperature=0.3,
                        max_tokens=1000,
                    ) as stream:
                        for text in stream.text_stream:
                            if text:
                                full_content += text
                                self._emit_stream_update(
                                    full_content, formatter=formatter
                                )
                                if self.abort_check and self.abort_check():
                                    break
            except Exception as sdk_error:
                self._emit_log(
                    "warn",
                    f"\n[WARN] SDK request failed, falling back to HTTP: {sdk_error}",
                )
                full_content = self._request_via_http(
                    openai_msgs, anthropic_msgs, system_prompt=system_prompt
                )
        else:
            full_content = self._request_via_http(
                openai_msgs, anthropic_msgs, system_prompt=system_prompt
            )

        if full_content:
            self._emit_stream_update(full_content, done=True)
        if self.emit_console:
            print()
        return full_content

    def _extract_json_candidates(self, text: str) -> List[str]:
        candidates: List[str] = []
        stripped = text.strip()

        if stripped:
            candidates.append(stripped)

        fenced_matches = re.findall(
            r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE
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

    def _sanitize_result(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = dict(data)

        for key in ("p", "i", "d"):
            value = sanitized.get(key)
            try:
                numeric = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                sanitized.pop(key, None)
                continue

            if not math.isfinite(numeric) or numeric < 0:
                sanitized.pop(key, None)
            else:
                sanitized[key] = numeric

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

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        for candidate in self._extract_json_candidates(text):
            try:
                return self._sanitize_result(json.loads(candidate))
            except Exception:
                pass
        return None

    def analyze(
        self,
        prompt_data: str,
        history_text: str,
        tuning_mode: str = "generic",
        prompt_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        system_prompt = get_system_prompt(tuning_mode)
        user_prompt = build_user_prompt(
            prompt_data,
            history_text,
            tuning_mode=tuning_mode,
            prompt_context=prompt_context,
        )

        openai_msgs: List[Any] = [{"role": "system", "content": system_prompt}]
        anthropic_msgs: List[Any] = []
        openai_msgs.append({"role": "user", "content": user_prompt})
        anthropic_msgs.append({"role": "user", "content": user_prompt})

        try:
            content = self._call_with_retry(
                self._execute_request, openai_msgs, anthropic_msgs, system_prompt
            )

            if self.debug_output:
                self._emit_log(
                    "debug", f"\n[LLM raw response preview]\n{content[:500]}...\n"
                )

            parsed = self._parse_json(content)
            if parsed:
                return parsed

            self._emit_log(
                "warn",
                "[WARN] LLM response could not be parsed as JSON; ignoring this round.",
            )
            return None
        except Exception as exc:
            self._emit_log("error", f"[ERROR] LLM request failed after retries: {exc}")
            return None
