import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.client import LLMTuner


class FakeOpenAI:
    def __init__(self, api_key, base_url):
        self.api_key  = api_key
        self.base_url = base_url


class FakeAnthropic:
    def __init__(self, api_key, base_url):
        self.api_key  = api_key
        self.base_url = base_url


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_lines(self):
        import json

        if "content" in self.payload:
            text = self.payload["content"][0]["text"]
            data = {"type": "content_block_delta", "delta": {"text": text}}
            yield f"data: {json.dumps(data)}".encode("utf-8")
        elif "choices" in self.payload:
            content = self.payload["choices"][0]["message"]["content"]
            data    = {"choices": [{"delta": {"content": content}}]}
            yield f"data: {json.dumps(data)}".encode("utf-8")
        yield b"data: [DONE]"


class FakeRequests:
    def __init__(self, payload):
        self.payload = payload
        self.calls   = []

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        self.calls.append(
            {
                "url"    : url,
                "headers": headers or {},
                "json"   : json or {},
                "timeout": timeout,
                "stream" : kwargs.get("stream", False),
            }
        )
        return FakeResponse(self.payload)


def build_fake_module(name, client_class_name, client_class):
    module = types.ModuleType(name)
    setattr(module, client_class_name, client_class)
    return module


class ProviderResolutionTests(unittest.TestCase):
    def setUp(self):
        openai_module     = build_fake_module("openai", "OpenAI", FakeOpenAI)
        anthropic_module  = build_fake_module("anthropic", "Anthropic", FakeAnthropic)
        self.module_patch = patch.dict(
            sys.modules,
            {"openai": openai_module, "anthropic": anthropic_module}
        )
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()

    def test_claude_model_keeps_openai_transport_for_openai_provider(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-5-sonnet",
            "openai"
        )

        self.assertEqual(tuner.provider, "openai")
        self.assertEqual(type(tuner.client).__name__, "FakeOpenAI")

    def test_openai_claude_alias_routes_to_openai_transport(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-7-sonnet",
            "openai_claude",
        )

        self.assertEqual(tuner.provider, "openai")
        self.assertEqual(type(tuner.client).__name__, "FakeOpenAI")

    def test_native_anthropic_provider_routes_to_messages_api(self):
        tuner = LLMTuner(
            "test-key",
            "https://api.anthropic.com",
            "claude-3-5-sonnet",
            "anthropic"
        )
        fake_requests  = FakeRequests({"content": [{"text": "ok"}]})
        tuner.requests = fake_requests  # type: ignore[assignment]
        content        = tuner._request_via_http(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
        )

        self.assertEqual(tuner.provider, "anthropic")
        self.assertEqual(content, "ok")
        self.assertEqual(
            fake_requests.calls[0]["url"], "https://api.anthropic.com/v1/messages"
        )
        self.assertIn("x-api-key", fake_requests.calls[0]["headers"])

    def test_claude_openai_transport_uses_chat_completions_endpoint(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-5-sonnet",
            "openai_claude",
        )
        fake_requests  = FakeRequests(
            {"choices": [{"message": {"content": '{"status":"DONE"}'}}]}
        )
        tuner.requests = fake_requests  # type: ignore[assignment]

        content = tuner._request_via_http(
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello"}],
        )

        self.assertEqual(content, '{"status":"DONE"}')
        self.assertEqual(
            fake_requests.calls[0]["url"],
            "https://relay.example.com/v1/chat/completions",
        )
        self.assertEqual(
            fake_requests.calls[0]["headers"].get("Authorization"),
            "Bearer test-key"
        )


if __name__ == "__main__":
    unittest.main()
