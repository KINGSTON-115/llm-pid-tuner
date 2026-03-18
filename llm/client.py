#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm/client.py - LLM 接口封装（支持 OpenAI / Anthropic SDK 及 HTTP 回退）
"""

import json
import math
import re
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from core.config import CONFIG
from llm.prompts import SYSTEM_PROMPT


class JSONStreamFormatter:
    """按行/增量解析 JSON 流，并格式化输出到控制台。"""

    def __init__(self, writer: Optional[Callable[[str], None]] = None):
        self.displayed_keys = set()
        self.current_key    = None
        self.printed_text   = ""
        self.writer         = writer or self._default_writer
        self.key_names      = {
            "thought_process" : "\n  [思考]",
            "analysis_summary": "\n  [分析]",
            "tuning_action"   : "\n  [调参]",
            "p"               : "\n  [建议] P",
            "i"               : " I",
            "d"               : " D",
            "status"          : "\n  [状态]",
        }
        # 预编译正则以提升性能
        self.str_re = re.compile(r'"([a-zA-Z_]+)"\s*:\s*"((?:[^"\\]|\\.)*)')
        self.num_re = re.compile(
            r'"([a-zA-Z_]+)"\s*:\s*([0-9\.\-]+|true|false|null)\s*[,}\n]', re.IGNORECASE
        )

    @staticmethod
    def _default_writer(text: str) -> None:
        print(text, end="", flush=True)

    def process(self, full_text: str):
        # 处理字符串类型字段
        str_matches = list(self.str_re.finditer(full_text))
        for m in str_matches:
            key     = m.group(1)
            raw_val = m.group(2)

            if key not in self.displayed_keys:
                self.displayed_keys.add(key)
                self.current_key  = key
                self.printed_text = ""
                name = self.key_names.get(key, f"\n  [{key}]")
                self.writer(f"{name} ")

            if self.current_key == key:
                decoded = (
                    raw_val.replace("\\n", "\n")
                    .replace('\\"', '"')
                    .replace("\\\\", "\\")
                )
                if raw_val.endswith("\\"):
                    decoded = decoded[:-1]

                new_text = decoded[len(self.printed_text) :]
                if new_text:
                    if key in ["thought_process", "analysis_summary"]:
                        new_text = new_text.replace("\n", "\n    ")
                    self.writer(new_text)
                    self.printed_text += new_text

        # 处理数字/布尔类型字段（必须有终结符保证完整性）
        num_matches = list(self.num_re.finditer(full_text))
        for m in num_matches:
            key = m.group(1)
            val = m.group(2)
            if key not in self.displayed_keys:
                self.displayed_keys.add(key)
                name = self.key_names.get(key, f"\n  [{key}]")
                if key in ["p", "i", "d"]:
                    self.writer(f",{name}={val}")
                elif key == "status":
                    self.writer(f"{name}: {val}")
                else:
                    self.writer(f"{name}: {val}")


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
    ):
        self.api_key         = api_key
        self.base_url        = (base_url or "").rstrip("/")
        self.model           = model
        self.provider_choice = self._normalize_provider_choice(provider)
        self.provider        = self._resolve_transport()
        self.timeout         = CONFIG.get("LLM_REQUEST_TIMEOUT", 60)
        self.debug_output    = CONFIG.get("LLM_DEBUG_OUTPUT", False)
        self.emit_console    = emit_console
        self.stream_callback = stream_callback
        self.log_callback    = log_callback
        self.use_sdk         = False
        self.client          = None

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
            # SDK 未安装：回退到 requests
            self.requests = self._import_requests()
        except Exception:
            # 其他初始化错误：调试模式下打印堆栈，然后回退
            if self.debug_output:
                traceback.print_exc()
            self.requests = self._import_requests()
        else:
            self.use_sdk  = True

    @staticmethod
    def _normalize_provider_choice(provider: Optional[str]) -> str:
        provider_choice = str(provider or "").strip().lower()
        provider_choice = provider_choice.replace("-", "_").replace(" ", "_")
        return provider_choice or "openai"

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
        max_retries    = 5
        delays         = [2, 4, 8, 16, 32]
        last_exception = None

        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    self._emit_log(
                        "warn",
                        f"\n[WARN] LLM 调用失败: {e}，将在 {delays[attempt]} 秒后重试...",
                    )
                    time.sleep(delays[attempt])
                else:
                    self._emit_log(
                        "error",
                        f"\n[ERROR] LLM 调用失败已达 {max_retries} 次: {e}",
                    )
                    raise

        if last_exception:
            raise last_exception
        return ""

    def _request_via_http(
        self, openai_msgs: List[Dict[str, Any]], anthropic_msgs: List[Dict[str, Any]]
    ) -> str:
        self._ensure_requests()
        full_content = ""
        formatter = (
            JSONStreamFormatter()
            if self.emit_console
            else None
        )

        if self.provider == "anthropic":
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            payload = {
                "model"      : self.model,
                "system"     : SYSTEM_PROMPT,
                "messages"   : anthropic_msgs,
                "temperature": 0.3,
                "max_tokens" : 1000,
                "stream"     : True,
            }
            base_url = self.base_url.rstrip("/")
            if not base_url.endswith("/v1"):
                base_url = f"{base_url}/v1"
            with self.requests.post(
                f"{base_url}/messages",
                headers = headers,
                json    = payload,
                timeout = self.timeout,
                stream  = True,
            ) as resp:
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if line:
                        line_str = line.decode("utf-8")
                        if line_str.startswith("data: "):
                            data_str = line_str[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                if (
                                    data.get("type") == "content_block_delta"
                                    and "delta" in data
                                ):
                                    chunk = data["delta"].get("text", "")
                                    if chunk:
                                        full_content += chunk
                                        self._emit_stream_update(
                                            full_content, formatter=formatter
                                        )
                            except json.JSONDecodeError:
                                pass

        else:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type" : "application/json",
            }
            payload = {
                "model"      : self.model,
                "messages"   : openai_msgs,
                "temperature": 0.3,
                "stream"     : True,
            }
            with self.requests.post(
                f"{self.base_url}/chat/completions",
                headers = headers,
                json    = payload,
                timeout = self.timeout,
                stream  = True,
            ) as resp:
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if line:
                        line_str = line.decode("utf-8")
                        if line_str.startswith("data: "):
                            data_str = line_str[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                choices = data.get("choices", [])
                                if choices and "delta" in choices[0]:
                                    chunk = choices[0]["delta"].get("content", "")
                                    if chunk:
                                        full_content += chunk
                                        self._emit_stream_update(
                                            full_content, formatter=formatter
                                        )
                            except json.JSONDecodeError:
                                pass

        return full_content

    def _execute_request(
        self, openai_msgs: List[Dict[str, Any]], anthropic_msgs: List[Dict[str, Any]]
    ) -> str:
        self._emit_log("llm", "  LLM 正在思考...")
        full_content = ""
        formatter = JSONStreamFormatter() if self.emit_console else None

        if self.use_sdk:
            try:
                if self.provider == "openai":
                    resp = self.client.chat.completions.create(  # type: ignore
                        model       = self.model,
                        messages    = openai_msgs,
                        temperature = 0.3,
                        stream      = True,
                    )
                    for chunk in resp:
                        content_chunk = chunk.choices[0].delta.content or ""
                        if content_chunk:
                            full_content += content_chunk
                            self._emit_stream_update(
                                full_content, formatter=formatter
                            )
                elif self.provider == "anthropic":
                    with self.client.messages.stream(  # type: ignore
                        model       = self.model,
                        system      = SYSTEM_PROMPT,
                        messages    = anthropic_msgs,
                        temperature = 0.3,
                        max_tokens  = 1000,
                    ) as stream:
                        for text in stream.text_stream:
                            if text:
                                full_content += text
                                self._emit_stream_update(
                                    full_content, formatter=formatter
                                )
            except Exception as sdk_error:
                self._emit_log(
                    "warn", f"\n[WARN] SDK 调用失败，尝试 HTTP 回退: {sdk_error}"
                )
                full_content = self._request_via_http(openai_msgs, anthropic_msgs)
        else:
            full_content = self._request_via_http(openai_msgs, anthropic_msgs)

        if full_content:
            self._emit_stream_update(full_content, done=True)
        if self.emit_console:
            print()  # 打印换行
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
                sanitized.get("analysis") or "未提供分析摘要"
            )

        if not sanitized.get("thought_process"):
            sanitized["thought_process"] = str(
                sanitized.get("analysis_summary") or "模型未提供详细推理"
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

    def analyze(self, prompt_data: str, history_text: str) -> Optional[Dict[str, Any]]:
        user_prompt = f"""
{history_text}

{prompt_data}

请基于以上历史和当前数据，分析 PID 参数表现并给出优化建议。
务必使用 JSON 格式返回，包含 thought_process 字段。
"""
        # 构建消息记录
        openai_msgs   : List[Any] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        anthropic_msgs: List[Any] = []

        openai_msgs.append({"role": "user", "content": user_prompt})
        anthropic_msgs.append({"role": "user", "content": user_prompt})

        try:
            content = self._call_with_retry(
                self._execute_request, openai_msgs, anthropic_msgs
            )

            if self.debug_output:
                self._emit_log("debug", f"\n[LLM 原始响应预览]\n{content[:500]}...\n")

            parsed = self._parse_json(content)
            if parsed:
                return parsed

            self._emit_log("warn", "[WARN] LLM 响应未能解析为 JSON，已忽略本轮建议。")
            return None

        except Exception as e:
            self._emit_log("error", f"[ERROR] LLM 调用或重试最终失败: {e}")
            return None
