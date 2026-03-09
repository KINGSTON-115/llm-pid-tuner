import sys
import types
import unittest
from unittest.mock import patch


sys.path.insert(0, r"D:\Python_Learning\llm-pid-tuner")

from tuner import LLMTuner


class FakeOpenAI:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url


class FakeAnthropic:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeRequests:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json or {},
                "timeout": timeout,
            }
        )
        return FakeResponse(self.payload)


def build_fake_module(name, client_class_name, client_class):
    module = types.ModuleType(name)
    setattr(module, client_class_name, client_class)
    return module


class ProviderResolutionTests(unittest.TestCase):
    def setUp(self):
        openai_module = build_fake_module("openai", "OpenAI", FakeOpenAI)
        anthropic_module = build_fake_module("anthropic", "Anthropic", FakeAnthropic)
        self.module_patch = patch.dict(
            sys.modules,
            {"openai": openai_module, "anthropic": anthropic_module},
        )
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()

    def test_claude_model_keeps_openai_transport_for_openai_provider(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-5-sonnet",
            "openai",
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
            "anthropic",
        )
        fake_requests = FakeRequests({"content": [{"text": "ok"}]})
        tuner.requests = fake_requests

        content = tuner._request_via_http("hello")

        self.assertEqual(tuner.provider, "anthropic")
        self.assertEqual(content, "ok")
        self.assertEqual(fake_requests.calls[0]["url"], "https://api.anthropic.com/messages")
        self.assertIn("x-api-key", fake_requests.calls[0]["headers"])

    def test_claude_openai_transport_uses_chat_completions_endpoint(self):
        tuner = LLMTuner(
            "test-key",
            "https://relay.example.com/v1",
            "claude-3-5-sonnet",
            "openai_claude",
        )
        fake_requests = FakeRequests(
            {"choices": [{"message": {"content": "{\"status\":\"DONE\"}"}}]}
        )
        tuner.requests = fake_requests

        content = tuner._request_via_http("hello")

        self.assertEqual(content, "{\"status\":\"DONE\"}")
        self.assertEqual(
            fake_requests.calls[0]["url"],
            "https://relay.example.com/v1/chat/completions",
        )
        self.assertEqual(
            fake_requests.calls[0]["headers"].get("Authorization"),
            "Bearer test-key",
        )


if __name__ == "__main__":
    unittest.main()
