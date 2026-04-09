#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm/client.py - LLM client wrapper with streaming and prompt selection.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from llm.prompts import SYSTEM_PROMPT, build_user_prompt, get_system_prompt
from llm.response_parser import parse_json_response
from llm.stream_formatter import JSONStreamFormatter
from llm.providers import BaseLLMProvider, OpenAISDKProvider, AnthropicSDKProvider, HTTPFallbackProvider


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
        timeout: float = 60.0,
        debug_output: bool = False,
    ):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.provider_choice = self._normalize_provider_choice(provider)
        self.provider = self._resolve_transport()
        self.timeout = timeout
        self.debug_output = debug_output
        self.emit_console = emit_console
        self.stream_callback = stream_callback
        self.log_callback = log_callback
        self.abort_check = abort_check

        self.llm_client: BaseLLMProvider = self._initialize_provider()
        # For backward compatibility in tests
        self.use_sdk = isinstance(self.llm_client, (OpenAISDKProvider, AnthropicSDKProvider))
        self.client = getattr(self.llm_client, "client", None)
        self.requests = getattr(self.llm_client, "requests", None)

    def _initialize_provider(self) -> BaseLLMProvider:
        try:
            if self.provider == "openai":
                return OpenAISDKProvider(self.api_key, self.base_url, self.model, self.timeout)
            elif self.provider == "anthropic":
                return AnthropicSDKProvider(self.api_key, self.base_url, self.model, self.timeout)
        except ImportError:
            pass
        except Exception:
            if self.debug_output:
                traceback.print_exc()
                
        return HTTPFallbackProvider(
            self.api_key, 
            self.base_url, 
            self.model, 
            self.timeout, 
            is_anthropic=(self.provider == "anthropic")
        )

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

    def _interruptible_sleep(self, seconds: float) -> bool:
        """Poll `abort_check` every 0.1s while sleeping."""
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

    def _execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str = SYSTEM_PROMPT,
    ) -> str:
        self._emit_log("llm", "  LLM is thinking...")
        full_content = []
        formatter = JSONStreamFormatter() if self.emit_console else None

        def on_chunk(chunk: str) -> None:
            full_content.append(chunk)
            self._emit_stream_update("".join(full_content), formatter=formatter)

        try:
            self.llm_client.execute_request(
                openai_msgs=openai_msgs,
                anthropic_msgs=anthropic_msgs,
                system_prompt=system_prompt,
                on_chunk=on_chunk,
                abort_check=self.abort_check,
            )
        except Exception as sdk_error:
            if self.use_sdk:
                self._emit_log(
                    "warn",
                    f"\n[WARN] SDK request failed, falling back to HTTP: {sdk_error}",
                )
                self.llm_client = HTTPFallbackProvider(
                    self.api_key,
                    self.base_url,
                    self.model,
                    self.timeout,
                    is_anthropic=(self.provider == "anthropic"),
                )
                self.use_sdk = False
                self.requests = self.llm_client.requests
                full_content.clear()
                self.llm_client.execute_request(
                    openai_msgs=openai_msgs,
                    anthropic_msgs=anthropic_msgs,
                    system_prompt=system_prompt,
                    on_chunk=on_chunk,
                    abort_check=self.abort_check,
                )
            else:
                raise

        final_text = "".join(full_content)
        if final_text:
            self._emit_stream_update(final_text, done=True)
        if self.emit_console:
            print()
        return final_text

    def request_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> Optional[Dict[str, Any]]:
        openai_msgs: List[Any] = [{"role": "system", "content": system_prompt}]
        anthropic_msgs: List[Any] = [{"role": "user", "content": user_prompt}]
        openai_msgs.append({"role": "user", "content": user_prompt})

        try:
            content = self._call_with_retry(
                self._execute_request, openai_msgs, anthropic_msgs, system_prompt
            )

            if self.debug_output:
                self._emit_log(
                    "debug", f"\n[LLM raw response preview]\n{content[:500]}...\n"
                )

            parsed = parse_json_response(content)
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
        return self.request_json(system_prompt=system_prompt, user_prompt=user_prompt)

