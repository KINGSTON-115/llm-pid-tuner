import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

class BaseLLMProvider(ABC):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str,
        on_chunk: Callable[[str], None],
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        pass

class OpenAISDKProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        super().__init__(api_key, base_url, model, timeout)
        import openai
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str,
        on_chunk: Callable[[str], None],
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=openai_msgs,
            temperature=0.3,
            stream=True,
        )
        accumulated = ""
        for chunk in resp:
            content_chunk = self._extract_chunk(chunk, accumulated)
            if content_chunk:
                accumulated += content_chunk
                on_chunk(content_chunk)
                if abort_check and abort_check():
                    break

    def _extract_chunk(self, chunk: Any, accumulated: str) -> str:
        choices = getattr(chunk, "choices", None) or []
        if not choices: return ""
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is not None:
            delta_content = getattr(delta, "content", None)
            if isinstance(delta_content, str) and delta_content:
                return delta_content
        message = getattr(choice, "message", None)
        if message is None: return ""
        message_content = getattr(message, "content", None)
        if not isinstance(message_content, str) or not message_content:
            return ""
        if not accumulated: return message_content
        if message_content.startswith(accumulated):
            return message_content[len(accumulated):]
        return ""

class AnthropicSDKProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        super().__init__(api_key, base_url, model, timeout)
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key, base_url=base_url)

    def execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str,
        on_chunk: Callable[[str], None],
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        with self.client.messages.stream(
            model=self.model,
            system=system_prompt,
            messages=anthropic_msgs,
            temperature=0.3,
            max_tokens=1000,
        ) as stream:
            for text in stream.text_stream:
                if text:
                    on_chunk(text)
                    if abort_check and abort_check():
                        break

class HTTPFallbackProvider(BaseLLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str, timeout: float, is_anthropic: bool, requests_module=None):
        super().__init__(api_key, base_url, model, timeout)
        self.is_anthropic = is_anthropic
        if requests_module is None:
            import requests
            self.requests = requests
        else:
            self.requests = requests_module

    def execute_request(
        self,
        openai_msgs: List[Dict[str, Any]],
        anthropic_msgs: List[Dict[str, Any]],
        system_prompt: str,
        on_chunk: Callable[[str], None],
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        if self.is_anthropic:
            self._request_anthropic(anthropic_msgs, system_prompt, on_chunk, abort_check)
        else:
            self._request_openai(openai_msgs, on_chunk, abort_check)

    def _request_anthropic(self, msgs, system_prompt, on_chunk, abort_check):
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "system": system_prompt,
            "messages": msgs,
            "temperature": 0.3,
            "max_tokens": 1000,
            "stream": True,
        }
        base_url = self.base_url.rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        with self.requests.post(f"{base_url}/messages", headers=headers, json=payload, timeout=self.timeout, stream=True) as resp:
            resp.raise_for_status()
            self._parse_stream(resp, on_chunk, abort_check, self._extract_anthropic)

    def _extract_anthropic(self, data: Dict[str, Any]) -> str:
        if data.get("type") == "content_block_delta" and "delta" in data:
            return data["delta"].get("text", "")
        return ""

    def _request_openai(self, msgs, on_chunk, abort_check):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": msgs,
            "temperature": 0.3,
            "stream": True,
        }
        with self.requests.post(f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=self.timeout, stream=True) as resp:
            resp.raise_for_status()
            self._parse_stream(resp, on_chunk, abort_check, self._extract_openai)

    def _extract_openai(self, data: Dict[str, Any]) -> str:
        choices = data.get("choices", [])
        if choices and "delta" in choices[0]:
            return choices[0]["delta"].get("content", "")
        return ""

    def _parse_stream(self, resp, on_chunk, abort_check, extract_fn):
        for line in resp.iter_lines():
            if not line: continue
            line_str = line.decode("utf-8")
            if not line_str.startswith("data: "): continue
            data_str = line_str[6:]
            if data_str == "[DONE]": break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            chunk = extract_fn(data)
            if chunk:
                on_chunk(chunk)
                if abort_check and abort_check():
                    break
