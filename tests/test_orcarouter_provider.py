#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for the OrcaRouter provider: the two authentication entry
points, the credential lifecycle, the model catalog, and the selector.

Every credential in here is a fabricated test value.  The tests assert that
those values never reach a URL, a log line, or an error message.
"""

import json
import sys
import unittest
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.doctoring import models_endpoint
from llm import orcarouter
from llm.client import LLMTuner
from llm.orcarouter import (
    CAPABILITY_CHAT,
    CAPABILITY_EMBEDDING,
    CAPABILITY_IMAGE,
    CAPABILITY_RERANK,
    CAPABILITY_VIDEO,
    CatalogResult,
    ModelInfo,
    OrcaRouterCredential,
    OrcaRouterCredentialStore,
    OrcaRouterError,
    OrcaRouterPKCEError,
    PROVIDER_ID,
    PROVIDER_ID_PKCE,
    REAUTH_NEEDED,
)

FAKE_KEY = "sk-orca-test-0000000000000000"
FAKE_KEY_ALT = "sk-orca-test-1111111111111111"
FAKE_CODE = "test-auth-code-abc"


class FakeConfig(dict):
    """A stand-in for the project CONFIG mapping."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def fake_fetch(payload, status=200):
    """Build a urllib-style opener returning ``payload`` as JSON."""

    class Response:
        def __init__(self, body):
            self._body = body
            self.status = status

        def read(self, _n=-1):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

    def opener(request, timeout=None):
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "err", {}, None
            )
        return Response(json.dumps(payload).encode("utf-8"))

    return opener


def catalog_payload(records):
    return {"data": records}


TEXT_MODEL = {
    "id": "openai/gpt-5.5",
    "name": "OpenAI: GPT-5.5",
    "supported_endpoint_types": ["openai", "openai-response"],
    "context_length": 1_000_000,
    "architecture": {"input_modalities": ["file", "image", "text"]},
    "reasoning_efforts": ["low", "medium", "high", "xhigh"],
}
TEXT_ONLY_MODEL = {
    "id": "deepseek/deepseek-v4-pro",
    "name": "DeepSeek V4 Pro",
    "supported_endpoint_types": ["openai", "openai-response"],
    "context_length": 1_048_576,
    "architecture": {"input_modalities": ["text"]},
}
IMAGE_GEN_MODEL = {
    "id": "google/imagen-4.0-generate-001",
    "supported_endpoint_types": ["image-generation"],
    "architecture": {"input_modalities": ["text"]},
}
EMBEDDING_MODEL = {
    "id": "google/gemini-embedding-001",
    "supported_endpoint_types": ["embeddings"],
}
VIDEO_MODEL = {
    "id": "openai/sora-2",
    "supported_endpoint_types": ["openai-video"],
}
RERANK_MODEL = {
    "id": "jina/jina-reranker-v3",
    "supported_endpoint_types": ["jina-rerank"],
}
NO_ENDPOINT_MODEL = {"id": "claude-opus-4-6", "name": "Claude Opus 4.6",
                     "architecture": {"input_modalities": ["text"]}}

ALL_RECORDS = [
    TEXT_MODEL,
    TEXT_ONLY_MODEL,
    IMAGE_GEN_MODEL,
    EMBEDDING_MODEL,
    VIDEO_MODEL,
    RERANK_MODEL,
    NO_ENDPOINT_MODEL,
]


# ---------------------------------------------------------------------------
# Origins
# ---------------------------------------------------------------------------

class OriginTests(unittest.TestCase):
    def test_public_defaults_are_separate_origins(self):
        origins = orcarouter.resolve_origins({}, environ={})
        self.assertEqual(origins.auth_base, "https://www.orcarouter.ai")
        self.assertEqual(origins.api_base, "https://api.orcarouter.ai/v1")
        self.assertEqual(origins.authorize_url, "https://www.orcarouter.ai/auth")
        self.assertEqual(
            origins.exchange_url, "https://www.orcarouter.ai/api/v1/auth/keys"
        )
        self.assertEqual(
            origins.models_url, "https://api.orcarouter.ai/v1/models"
        )

    def test_exchange_path_is_not_the_relay_auth_path(self):
        """The relay is at /v1; auth endpoints are not. This one 404s."""
        self.assertNotEqual(orcarouter.EXCHANGE_PATH, "/v1/auth/keys")
        self.assertFalse(orcarouter.EXCHANGE_PATH.startswith("/v1/"))
        self.assertEqual(orcarouter.EXCHANGE_PATH, "/api/v1/auth/keys")

    def test_explicit_overrides_win_over_shared_base(self):
        origins = orcarouter.resolve_origins(
            {"ORCAROUTER_API_BASE_URL": "https://api.selfhosted.example.com"},
            environ={
                "ORCA_BASE_URL": "https://shared.example.com",
                "ORCA_AUTH_BASE_URL": "https://auth.example.com",
            },
        )
        # The dedicated auth override beats the shared base...
        self.assertEqual(origins.auth_base, "https://auth.example.com")
        # ...and the dedicated API override beats it for inference, with /v1
        # appended to the API origin only - never derived from the auth origin.
        self.assertEqual(origins.api_base, "https://api.selfhosted.example.com/v1")
        self.assertNotIn("shared.example.com", origins.api_base)

    def test_shared_base_supplies_both_origins(self):
        origins = orcarouter.resolve_origins(
            {}, environ={"ORCA_BASE_URL": "https://one.example.com"}
        )
        self.assertEqual(origins.auth_base, "https://one.example.com")
        self.assertEqual(origins.api_base, "https://one.example.com/v1")

    def test_http_is_rejected_for_remote_hosts(self):
        with self.assertRaises(OrcaRouterError):
            orcarouter.resolve_origins(
                {}, environ={"ORCA_API_BASE_URL": "http://api.example.com"}
            )

    def test_http_is_allowed_for_loopback_dev(self):
        origins = orcarouter.resolve_origins(
            {}, environ={"ORCA_AUTH_BASE_URL": "http://127.0.0.1:8123"}
        )
        self.assertEqual(origins.auth_base, "http://127.0.0.1:8123")


# ---------------------------------------------------------------------------
# PKCE primitives
# ---------------------------------------------------------------------------

class PKCETests(unittest.TestCase):
    def test_verifier_and_state_are_fresh_and_random_per_attempt(self):
        first = orcarouter.new_pkce_pair()
        second = orcarouter.new_pkce_pair()
        self.assertNotEqual(first.verifier, second.verifier)
        self.assertNotEqual(first.state, second.state)
        # 32 random bytes -> 43 base64url characters, no padding.
        self.assertEqual(len(first.verifier), 43)
        self.assertNotIn("=", first.verifier)
        self.assertNotIn("=", first.challenge)

    def test_challenge_is_s256_of_the_verifier_without_padding(self):
        import base64
        import hashlib

        pair = orcarouter.new_pkce_pair()
        expected = (
            base64.urlsafe_b64encode(
                hashlib.sha256(pair.verifier.encode("ascii")).digest()
            )
            .decode("ascii")
            .rstrip("=")
        )
        self.assertEqual(pair.challenge, expected)

    def test_authorize_url_uses_auth_origin_and_carries_no_verifier(self):
        origins = orcarouter.resolve_origins({}, environ={})
        pair = orcarouter.new_pkce_pair()
        url = orcarouter.build_authorize_url(
            origins, pair, callback_url="http://127.0.0.1:51234/cb"
        )
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(parsed.netloc, "www.orcarouter.ai")
        self.assertEqual(parsed.path, "/auth")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["code_challenge"], [pair.challenge])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["state"], [pair.state])
        self.assertEqual(query["scope"], ["api"])
        # The verifier must never ride on a URL.
        self.assertNotIn(pair.verifier, url)

    def test_pkce_pair_repr_hides_the_verifier(self):
        pair = orcarouter.new_pkce_pair()
        self.assertNotIn(pair.verifier, repr(pair))
        self.assertNotIn(pair.verifier, str(pair))

    def test_authorize_url_rejects_a_non_loopback_http_callback(self):
        with self.assertRaises(OrcaRouterPKCEError):
            orcarouter.validate_callback_url("http://evil.example.com/cb")

    def test_authorize_url_accepts_oob(self):
        self.assertEqual(orcarouter.validate_callback_url("oob"), "oob")


# ---------------------------------------------------------------------------
# Exchange
# ---------------------------------------------------------------------------

class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.origins = orcarouter.resolve_origins({}, environ={})
        self.pair = orcarouter.new_pkce_pair()
        self.calls = []

        def opener(request, timeout=None):
            self.calls.append(request)
            return fake_fetch(
                {"key": FAKE_KEY, "user_id": "12345", "scope": "api"}
            )(request, timeout)

        self.opener = opener

    def test_exchange_posts_to_the_auth_origin_keys_path(self):
        result = orcarouter.exchange_code(
            self.origins, self.pair, FAKE_CODE, opener=self.opener
        )
        self.assertEqual(result.key, FAKE_KEY)
        self.assertEqual(result.scope, "api")
        self.assertTrue(result.scope_is_usable)
        request = self.calls[0]
        self.assertEqual(
            request.full_url, "https://www.orcarouter.ai/api/v1/auth/keys"
        )
        self.assertEqual(request.get_method(), "POST")

    def test_exchange_body_carries_the_verifier_and_s256(self):
        orcarouter.exchange_code(
            self.origins, self.pair, FAKE_CODE, opener=self.opener
        )
        body = json.loads(self.calls[0].data.decode("utf-8"))
        self.assertEqual(body["code"], FAKE_CODE)
        self.assertEqual(body["code_verifier"], self.pair.verifier)
        self.assertEqual(body["code_challenge_method"], "S256")

    def test_scope_downgrade_is_reported_not_assumed(self):
        def opener(request, timeout=None):
            return fake_fetch(
                {"key": FAKE_KEY, "user_id": "1", "scope": "read"}
            )(request, timeout)

        result = orcarouter.exchange_code(
            self.origins, self.pair, FAKE_CODE, opener=opener
        )
        self.assertFalse(result.scope_is_usable)

    def test_rejected_code_is_terminal_and_leaks_nothing(self):
        def opener(request, timeout=None):
            return fake_fetch({}, status=403)(request, timeout)

        with self.assertRaises(OrcaRouterPKCEError) as ctx:
            orcarouter.exchange_code(
                self.origins, self.pair, FAKE_CODE, opener=opener
            )
        message = str(ctx.exception)
        self.assertNotIn(self.pair.verifier, message)
        self.assertNotIn(FAKE_CODE, message)

    def test_challenge_method_downgrade_is_reported(self):
        def opener(request, timeout=None):
            return fake_fetch({}, status=400)(request, timeout)

        with self.assertRaises(OrcaRouterPKCEError):
            orcarouter.exchange_code(
                self.origins, self.pair, FAKE_CODE, opener=opener
            )

    def test_rate_limited_login_is_explained(self):
        def opener(request, timeout=None):
            return fake_fetch({}, status=429)(request, timeout)

        with self.assertRaises(OrcaRouterPKCEError) as ctx:
            orcarouter.exchange_code(
                self.origins, self.pair, FAKE_CODE, opener=opener
            )
        self.assertIn("429", str(ctx.exception))


# ---------------------------------------------------------------------------
# Flow A end to end against a local fake auth server
# ---------------------------------------------------------------------------

class FakeAuthHandler(BaseHTTPRequestHandler):
    """A local stand-in for the OrcaRouter auth origin."""

    def log_message(self, *_args):
        return

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        body = json.loads(raw.decode("utf-8"))
        type(self).seen.append({"path": self.path, "body": body})
        payload = {"key": FAKE_KEY, "user_id": "12345", "scope": "api"}
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class FlowATests(unittest.TestCase):
    """authorize -> callback -> exchange -> persist through the real adapter."""

    def setUp(self):
        FakeAuthHandler.seen = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeAuthHandler)
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.auth_base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.origins = orcarouter.OrcaRouterOrigins(
            auth_base=self.auth_base, api_base=self.auth_base + "/v1"
        )
        self.opened = []

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def _source(self):
        """A PKCE source whose 'browser' completes the loopback redirect."""
        source = orcarouter.PKCECredentialSource(
            origins=self.origins, flow="A", timeout=5.0
        )

        def open_browser(url):
            import urllib.request

            self.opened.append(url)
            parsed = urllib.parse.urlsplit(url)
            query = urllib.parse.parse_qs(parsed.query)
            callback = query["callback_url"][0]
            # The fake consent screen redirects straight back with the code.
            redirect = (
                f"{callback}?code={FAKE_CODE}&state={query['state'][0]}"
            )

            def hit():
                urllib.request.urlopen(redirect, timeout=5).read()

            Thread(target=hit, daemon=True).start()
            return True

        source.open_browser = open_browser
        return source

    def test_full_flow_acquires_and_the_verifier_stays_private(self):
        source = self._source()
        credential = source.acquire()
        self.assertEqual(credential.api_key, FAKE_KEY)
        self.assertEqual(credential.source, "pkce")

        # The authorize URL went to the auth origin and carried no verifier.
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(self.opened[0].startswith(self.auth_base + "/auth?"))

        # The exchange went to the auth origin's keys path, with S256.
        seen = FakeAuthHandler.seen
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["path"], "/api/v1/auth/keys")
        self.assertEqual(seen[0]["body"]["code"], FAKE_CODE)
        self.assertEqual(seen[0]["body"]["code_challenge_method"], "S256")
        verifier = seen[0]["body"]["code_verifier"]
        self.assertTrue(verifier)
        # The verifier left the process exactly once: on the exchange.
        self.assertNotIn(verifier, self.opened[0])
        self.assertNotIn(verifier, str(credential))

    def test_state_mismatch_never_reaches_the_exchange(self):
        source = self._source()

        def open_browser(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = query["callback_url"][0]
            redirect = f"{callback}?code={FAKE_CODE}&state=wrong-state"

            def hit():
                urllib.request.urlopen(redirect, timeout=5).read()

            Thread(target=hit, daemon=True).start()
            return True

        source.open_browser = open_browser
        with self.assertRaises(OrcaRouterPKCEError) as ctx:
            source.acquire()
        self.assertIn("state", str(ctx.exception).lower())
        self.assertEqual(FakeAuthHandler.seen, [])

    def test_denial_is_reported_and_does_not_hang(self):
        source = self._source()

        def open_browser(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = query["callback_url"][0]
            redirect = (
                f"{callback}?error=access_denied&state={query['state'][0]}"
            )

            def hit():
                urllib.request.urlopen(redirect, timeout=5).read()

            Thread(target=hit, daemon=True).start()
            return True

        source.open_browser = open_browser
        with self.assertRaises(OrcaRouterPKCEError) as ctx:
            source.acquire()
        self.assertIn("access_denied", str(ctx.exception))
        self.assertEqual(FakeAuthHandler.seen, [])

    def test_timeout_is_terminal(self):
        source = orcarouter.PKCECredentialSource(
            origins=self.origins, flow="A", timeout=0.4
        )
        source.open_browser = lambda _url: True  # nobody ever redirects
        with self.assertRaises(OrcaRouterPKCEError) as ctx:
            source.acquire()
        self.assertIn("timed out", str(ctx.exception))
        self.assertEqual(FakeAuthHandler.seen, [])

    def test_flow_b_requires_s256_and_pastes_the_code(self):
        source = orcarouter.PKCECredentialSource(
            origins=self.origins, flow="B", timeout=5.0
        )
        opened = []
        source.open_browser = lambda url: opened.append(url) or True
        source.prompt_for_code = lambda: FAKE_CODE
        credential = source.acquire()
        self.assertEqual(credential.api_key, FAKE_KEY)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query)
        self.assertEqual(query["callback_url"], ["oob"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(FakeAuthHandler.seen[0]["body"]["code"], FAKE_CODE)


# ---------------------------------------------------------------------------
# Credential seam: two adapters, one credential shape
# ---------------------------------------------------------------------------

class CredentialSeamTests(unittest.TestCase):
    def setUp(self):
        FakeAuthHandler.seen = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeAuthHandler)
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.auth_base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def test_api_key_adapter_yields_the_shared_credential_shape(self):
        registry = orcarouter.provider_registry()
        self.assertEqual({item["id"] for item in registry}, {PROVIDER_ID, PROVIDER_ID_PKCE})
        for item in registry:
            self.assertEqual(item["icon"], orcarouter.ORCAROUTER_ICON_URL)

    def test_both_adapters_produce_an_equivalent_credential(self):
        key_source = orcarouter.ApiKeyCredentialSource(api_key=FAKE_KEY)
        origins = orcarouter.resolve_origins({}, environ={})

        pkce_source = orcarouter.PKCECredentialSource(
            origins=origins, flow="B", timeout=5.0
        )
        opened = []
        pkce_source.open_browser = lambda url: opened.append(url) or True
        pkce_source.prompt_for_code = lambda: FAKE_CODE

        # Drive Flow B against a local fake auth origin instead of the network.
        pkce_source.origins = orcarouter.OrcaRouterOrigins(
            auth_base=self.auth_base, api_base=self.auth_base + "/v1"
        )

        from_api = key_source.acquire()
        from_pkce = pkce_source.acquire()

        self.assertIsInstance(from_api, OrcaRouterCredential)
        self.assertIsInstance(from_pkce, OrcaRouterCredential)
        self.assertEqual(type(from_api), type(from_pkce))
        # Same downstream shape; the source tag is the only difference.
        self.assertEqual(
            set(from_api.__dataclass_fields__), set(from_pkce.__dataclass_fields__)
        )
        self.assertEqual(from_api.source, "api_key")
        self.assertEqual(from_pkce.source, "pkce")
        self.assertEqual(from_api.api_key, from_pkce.api_key)
        # The PKCE adapter used the auth origin for the shown code.
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(opened[0]).query)
        self.assertEqual(query["callback_url"], ["oob"])
        self.assertTrue(self.auth_base in opened[0])

    def test_api_key_adapter_warns_but_does_not_reject_a_bad_prefix(self):
        hints = []
        orcarouter.ApiKeyCredentialSource(api_key="not-a-real-key-0000").acquire(
            on_hint=hints.append
        )
        self.assertTrue(hints)

    def test_api_key_adapter_refuses_an_empty_key(self):
        with self.assertRaises(OrcaRouterError):
            orcarouter.ApiKeyCredentialSource(api_key="  ").acquire()

    def test_credential_masking_hides_the_middle(self):
        credential = OrcaRouterCredential(api_key=FAKE_KEY, source="api_key")
        masked = credential.masked()
        self.assertNotEqual(masked, FAKE_KEY)
        self.assertNotIn(FAKE_KEY[4:-4], masked)
        self.assertFalse(OrcaRouterCredential(api_key="", source="").is_set)

    def test_store_reads_the_generic_key_slot_for_compatibility(self):
        config = FakeConfig(LLM_API_KEY=FAKE_KEY, LLM_PROVIDER=PROVIDER_ID)
        self.assertEqual(orcarouter.effective_api_key(config), FAKE_KEY)

    def test_dedicated_key_slot_wins_over_the_generic_slot(self):
        config = FakeConfig(LLM_API_KEY=FAKE_KEY_ALT, ORCAROUTER_API_KEY=FAKE_KEY)
        self.assertEqual(orcarouter.effective_api_key(config), FAKE_KEY)


# ---------------------------------------------------------------------------
# Credential lifecycle
# ---------------------------------------------------------------------------

class CredentialLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.config = FakeConfig()

    def _store(self):
        return OrcaRouterCredentialStore(self.config)

    def test_save_read_clear_round_trip(self):
        store = self._store()
        store.save(OrcaRouterCredential(api_key=FAKE_KEY, source="api_key"))
        self.assertEqual(store.load().api_key, FAKE_KEY)

        store.clear()
        self.assertFalse(store.load().is_set)
        self.assertEqual(self.config["ORCAROUTER_API_KEY"], "")

    def test_save_bumps_the_generation(self):
        store = self._store()
        first = store.save(OrcaRouterCredential(api_key=FAKE_KEY, source="api_key"))
        second = store.save(OrcaRouterCredential(api_key=FAKE_KEY_ALT, source="pkce"))
        self.assertGreater(second.generation, first.generation)

    def test_401_marks_only_the_rejected_generation(self):
        store = self._store()
        rejected = store.save(
            OrcaRouterCredential(api_key=FAKE_KEY, source="api_key")
        )
        self.assertTrue(store.mark_needs_reauth(rejected.generation))
        self.assertEqual(store.load().state, REAUTH_NEEDED)

    def test_a_stale_401_cannot_poison_a_newer_credential(self):
        store = self._store()
        old = store.save(OrcaRouterCredential(api_key=FAKE_KEY, source="api_key"))
        new = store.save(OrcaRouterCredential(api_key=FAKE_KEY_ALT, source="pkce"))

        # A response belonging to the old generation arrives late.
        self.assertFalse(store.mark_needs_reauth(old.generation))
        self.assertEqual(store.load().state, orcarouter.REAUTH_OK)
        self.assertTrue(store.load().api_key, FAKE_KEY_ALT)
        self.assertEqual(store.load().generation, new.generation)

    def test_reauth_never_deletes_the_old_secret(self):
        store = self._store()
        record = store.save(OrcaRouterCredential(api_key=FAKE_KEY, source="acct"))
        store.mark_needs_reauth(record.generation)
        # The secret is retained until a successful new login replaces it.
        self.assertEqual(store.load().api_key, FAKE_KEY)

    def test_record_masking_does_not_expose_the_key(self):
        store = self._store()
        record = store.save(OrcaRouterCredential(api_key=FAKE_KEY, source="api_key"))
        self.assertNotIn(FAKE_KEY, record.masked())


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------

class ProviderRoutingTests(unittest.TestCase):
    def _config(self, **overrides):
        base = {
            "ORCAROUTER_API_KEY": FAKE_KEY,
            "ORCAROUTER_CREDENTIAL_GENERATION": 1,
            "ORCAROUTER_AUTH_STATE": "ok",
        }
        base.update(overrides)
        return FakeConfig(**base)

    def test_orcarouter_routes_to_the_api_origin_with_bearer_auth(self):
        from unittest.mock import patch

        with patch.dict("core.config.CONFIG", self._config(), clear=True):
            tuner = LLMTuner("", "https://api.openai.com/v1", "orcarouter/auto", PROVIDER_ID)
        self.assertEqual(tuner.provider, "openai")
        self.assertEqual(tuner.base_url, "https://api.orcarouter.ai/v1")
        self.assertEqual(tuner.api_key, FAKE_KEY)

    def test_pkce_id_routes_the_same_transport(self):
        from unittest.mock import patch

        with patch.dict("core.config.CONFIG", self._config(), clear=True):
            plain = LLMTuner("", "", "orcarouter/auto", PROVIDER_ID)
            oauth = LLMTuner("", "", "orcarouter/auto", PROVIDER_ID_PKCE)
        self.assertEqual(plain.provider, oauth.provider)
        self.assertEqual(plain.base_url, oauth.base_url)
        self.assertEqual(plain.api_key, oauth.api_key)

    def test_downstream_does_not_care_which_adapter_produced_the_key(self):
        from unittest.mock import patch

        api_config = self._config(ORCAROUTER_AUTH_METHOD="api_key")
        pkce_config = self._config(ORCAROUTER_AUTH_METHOD="pkce")

        with patch.dict("core.config.CONFIG", api_config, clear=True):
            from_api = LLMTuner("", "", "orcarouter/auto", PROVIDER_ID)
        with patch.dict("core.config.CONFIG", pkce_config, clear=True):
            from_pkce = LLMTuner("", "", "orcarouter/auto", PROVIDER_ID_PKCE)

        self.assertEqual(from_api.base_url, from_pkce.base_url)
        self.assertEqual(from_api.model, from_pkce.model)
        self.assertEqual(type(from_api.llm_client), type(from_pkce.llm_client))

    def test_other_providers_are_untouched(self):
        from unittest.mock import patch

        with patch.dict("core.config.CONFIG", self._config(), clear=True):
            tuner = LLMTuner(
                "sk-real-openai", "https://api.openai.com/v1", "gpt-4o", "openai"
            )
        self.assertEqual(tuner.base_url, "https://api.openai.com/v1")
        self.assertEqual(tuner.api_key, "sk-real-openai")
        self.assertIsNone(tuner.orcarouter)

    def test_missing_credential_is_reported_with_the_two_ways_to_fix_it(self):
        from unittest.mock import patch

        with patch.dict("core.config.CONFIG", FakeConfig(), clear=True):
            with self.assertRaises(OrcaRouterError) as ctx:
                LLMTuner("", "", "orcarouter/auto", PROVIDER_ID)
        message = str(ctx.exception)
        self.assertIn("ORCAROUTER_API_KEY", message)
        self.assertIn("--orcarouter-login", message)

    def test_reauth_state_blocks_a_known_dead_key(self):
        from unittest.mock import patch

        config = self._config(ORCAROUTER_AUTH_STATE=REAUTH_NEEDED)
        with patch.dict("core.config.CONFIG", config, clear=True):
            with self.assertRaises(OrcaRouterError) as ctx:
                LLMTuner("", "", "orcarouter/auto", PROVIDER_ID)
        self.assertIn("--orcarouter-login", str(ctx.exception))

    def test_doctor_discovers_models_on_the_api_origin(self):
        endpoint, headers = models_endpoint(PROVIDER_ID, "", FAKE_KEY)
        self.assertEqual(endpoint, "https://api.orcarouter.ai/v1/models")
        self.assertEqual(headers["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn("www.orcarouter.ai", endpoint)


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

class CatalogTests(unittest.TestCase):
    def _models(self):
        return [
            model
            for model in (
                orcarouter.parse_model_record(record) for record in ALL_RECORDS
            )
            if model
        ]

    def test_record_parsing_keeps_namespace_and_metadata(self):
        model = orcarouter.parse_model_record(TEXT_MODEL)
        self.assertEqual(model.id, "openai/gpt-5.5")
        self.assertEqual(model.context_length, 1_000_000)
        self.assertEqual(model.input_modalities, ("file", "image", "text"))
        self.assertEqual(
            model.reasoning_efforts, ("low", "medium", "high", "xhigh")
        )

    def test_malformed_records_are_dropped(self):
        self.assertIsNone(orcarouter.parse_model_record(None))
        self.assertIsNone(orcarouter.parse_model_record({}))
        self.assertIsNone(orcarouter.parse_model_record({"id": "  "}))

    def test_chat_filter_excludes_media_only_endpoints(self):
        ids = [m.id for m in orcarouter.filter_models(self._models(), CAPABILITY_CHAT)]
        self.assertIn("openai/gpt-5.5", ids)
        self.assertIn("deepseek/deepseek-v4-pro", ids)
        for excluded in (
            IMAGE_GEN_MODEL["id"],
            EMBEDDING_MODEL["id"],
            VIDEO_MODEL["id"],
            RERANK_MODEL["id"],
            NO_ENDPOINT_MODEL["id"],
        ):
            self.assertNotIn(excluded, ids)

    def test_multimodal_filter_fails_closed(self):
        models = self._models()
        chat = orcarouter.filter_models(models, CAPABILITY_CHAT)
        self.assertIn("deepseek/deepseek-v4-pro", [m.id for m in chat])

        with_image = orcarouter.filter_models(
            models, CAPABILITY_CHAT, required_modalities=["image"]
        )
        ids = [m.id for m in with_image]
        self.assertEqual(ids, ["openai/gpt-5.5"])

        # A model that declares nothing is not assumed to accept images.
        with_video = orcarouter.filter_models(
            models, CAPABILITY_CHAT, required_modalities=["video"]
        )
        self.assertEqual(with_video, [])

    def test_each_capability_filters_independently(self):
        models = self._models()
        self.assertEqual(
            [m.id for m in orcarouter.filter_models(models, CAPABILITY_EMBEDDING)],
            [EMBEDDING_MODEL["id"]],
        )
        self.assertEqual(
            [m.id for m in orcarouter.filter_models(models, CAPABILITY_IMAGE)],
            [IMAGE_GEN_MODEL["id"]],
        )
        self.assertEqual(
            [m.id for m in orcarouter.filter_models(models, CAPABILITY_VIDEO)],
            [VIDEO_MODEL["id"]],
        )
        self.assertEqual(
            [m.id for m in orcarouter.filter_models(models, CAPABILITY_RERANK)],
            [RERANK_MODEL["id"]],
        )

    def test_live_discovery_is_authoritative_and_not_merged_with_the_seed(self):
        credential = OrcaRouterCredential(api_key=FAKE_KEY, source="api_key")
        result = orcarouter.fetch_catalog(
            credential,
            orcarouter.resolve_origins({}, environ={}),
            opener=fake_fetch(catalog_payload(ALL_RECORDS)),
        )
        self.assertFalse(result.degraded)
        self.assertEqual(result.source, "live")
        ids = result.id_set()
        self.assertIn("openai/gpt-5.5", ids)
        # Seed-only entries must not be injected into a successful result.
        self.assertNotIn("orcarouter/auto", ids)

    def test_discovery_failure_keeps_the_verified_seed(self):
        def boom(request, timeout=None):
            raise OSError("network down")

        credential = OrcaRouterCredential(api_key=FAKE_KEY, source="api_key")
        result = orcarouter.fetch_catalog(
            credential, orcarouter.resolve_origins({}, environ={}), opener=boom
        )
        self.assertTrue(result.degraded)
        self.assertEqual(result.source, "fallback")
        ids = result.id_set()
        self.assertIn("openai/gpt-5.5", ids)
        self.assertIn("orcarouter/auto", ids)

    def test_seed_metadata_and_reasoning_ladder_survive_the_fallback(self):
        result = CatalogResult(
            models=orcarouter.fallback_catalog(), source="fallback", degraded=True
        )
        by_id = {model.id: model for model in result.models}
        gpt = by_id["openai/gpt-5.5"]
        self.assertEqual(
            gpt.reasoning_efforts, ("low", "medium", "high", "xhigh")
        )
        self.assertTrue(gpt.supports_image_input)
        self.assertIsNotNone(gpt.context_length)
        # Every seeded entry records where it was verified.
        for entry in orcarouter.VERIFIED_FALLBACK_CATALOG:
            self.assertTrue(entry.source.startswith("https://api.orcarouter.ai/"))
            self.assertTrue(entry.verified_at)

    def test_fallback_filtering_still_applies_per_capability(self):
        result = CatalogResult(
            models=orcarouter.fallback_catalog(), source="fallback", degraded=True
        )
        text_ids = [m.id for m in result.for_capability(CAPABILITY_CHAT)]
        self.assertIn("deepseek/deepseek-v4-pro", text_ids)
        image_ids = [
            m.id
            for m in result.for_capability(
                CAPABILITY_CHAT, required_modalities=["image"]
            )
        ]
        # deepseek-v4-pro is seeded text-only, so it must drop out.
        self.assertNotIn("deepseek/deepseek-v4-pro", image_ids)
        self.assertIn("openai/gpt-5.5", image_ids)

    def test_a_401_from_discovery_is_reported_without_the_key(self):
        def opener(request, timeout=None):
            return fake_fetch({}, status=401)(request, timeout)

        credential = OrcaRouterCredential(api_key=FAKE_KEY, source="api_key")
        result = orcarouter.fetch_catalog(
            credential, orcarouter.resolve_origins({}, environ={}), opener=opener
        )
        self.assertTrue(result.degraded)
        self.assertIn("401", result.error)
        self.assertNotIn(FAKE_KEY, result.error)

    def test_discovery_requires_a_credential(self):
        result = orcarouter.fetch_catalog(
            OrcaRouterCredential(api_key="", source=""),
            orcarouter.resolve_origins({}, environ={}),
        )
        self.assertTrue(result.degraded)

    def test_requested_capability_is_sent_as_a_query_parameter(self):
        seen = {}

        def opener(request, timeout=None):
            seen["url"] = request.full_url
            seen["auth"] = request.get_header("Authorization")
            return fake_fetch(catalog_payload(ALL_RECORDS))(request, timeout)

        credential = OrcaRouterCredential(api_key=FAKE_KEY, source="api_key")
        orcarouter.fetch_catalog(
            credential,
            orcarouter.resolve_origins({}, environ={}),
            capability=CAPABILITY_CHAT,
            opener=opener,
        )
        self.assertEqual(
            seen["url"], "https://api.orcarouter.ai/v1/models?capability=chat"
        )
        self.assertEqual(seen["auth"], f"Bearer {FAKE_KEY}")


# ---------------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------------

class SelectorTests(unittest.TestCase):
    def _state(self):
        from sim.orcarouter_gui import ModelSelectorState

        state = ModelSelectorState()
        state.set_catalog(
            CatalogResult(
                models=[
                    orcarouter.parse_model_record(TEXT_MODEL),
                    orcarouter.parse_model_record(TEXT_ONLY_MODEL),
                    orcarouter.parse_model_record(IMAGE_GEN_MODEL),
                ],
                source="live",
                degraded=False,
            )
        )
        return state

    def test_options_come_from_the_catalog_not_a_hand_written_list(self):
        state = self._state()
        self.assertEqual(
            state.option_ids(), ["openai/gpt-5.5", "deepseek/deepseek-v4-pro"]
        )

    def test_no_options_when_orcarouter_is_not_selected(self):
        state = self._state()
        state.set_provider("openai")
        self.assertEqual(state.option_ids(), [])

    def test_attaching_an_image_narrows_options_and_clears_a_stale_value(self):
        state = self._state()
        self.assertTrue(state.select("deepseek/deepseek-v4-pro"))
        state.add_modality("image")
        self.assertEqual(state.option_ids(), ["openai/gpt-5.5"])
        # The text-only model is no longer compatible: cleared, with a notice.
        self.assertEqual(state.selected, "")
        self.assertIn("no longer available", state.notice)

    def test_selecting_an_incompatible_model_is_refused(self):
        state = self._state()
        state.add_modality("image")
        self.assertFalse(state.select("deepseek/deepseek-v4-pro"))
        self.assertEqual(state.selected, "")

    def test_provider_change_recomputes_the_options(self):
        state = self._state()
        state.set_provider("orcarouter_oauth")
        self.assertTrue(state.options())
        state.set_provider("anthropic")
        self.assertEqual(state.options(), [])

    def test_restored_selection_is_revalidated(self):
        state = self._state()
        self.assertTrue(state.restore_selected("openai/gpt-5.5"))
        self.assertFalse(state.restore_selected("google/imagen-4.0-generate-001"))
        self.assertEqual(state.selected, "")

    def test_degraded_catalog_is_reported_and_marked(self):
        from sim.orcarouter_gui import ModelSelectorState

        state = ModelSelectorState()
        state.set_catalog(
            CatalogResult(
                models=orcarouter.fallback_catalog(),
                source="fallback",
                degraded=True,
                error="network down",
            )
        )
        self.assertTrue(state.degraded)
        note = state.status_note()
        self.assertIn("verified fallback catalog", note)
        # The verified seed is still offered, and it never looks live.
        self.assertIn("openai/gpt-5.5", [m.id for m in state.options()])

    def test_empty_state_explains_why_nothing_matches(self):
        state = self._state()
        state.set_capability("embedding")
        self.assertEqual(state.option_ids(), [])
        self.assertIn("embedding", state.status_note())


if __name__ == "__main__":
    unittest.main()
