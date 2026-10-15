#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for the OrcaRouter settings surface: the loopback settings server, the
catalog service's key boundary, the login lock, and locale parity.
"""

import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm import orcarouter
from llm.orcarouter import (
    CAPABILITY_CHAT,
    CatalogResult,
    ModelInfo,
    OrcaRouterCredential,
)
from sim import orcarouter_gui
from sim.orcarouter_gui import (
    ModelSelectorState,
    OrcaRouterCatalogService,
    OrcaRouterPanelModel,
    OrcaRouterSettingsServer,
)

FAKE_KEY = "sk-orca-gui-0000000000000000"

RECORDS = [
    {
        "id": "openai/gpt-5.5",
        "name": "OpenAI: GPT-5.5",
        "supported_endpoint_types": ["openai", "openai-response"],
        "architecture": {"input_modalities": ["text", "image"]},
        "context_length": 1_000_000,
    },
    {
        "id": "deepseek/deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "supported_endpoint_types": ["openai"],
        "architecture": {"input_modalities": ["text"]},
    },
    {
        "id": "google/imagen-4.0-generate-001",
        "supported_endpoint_types": ["image-generation"],
    },
]


def build_live_catalog():
    """A CatalogResult straight from the fixture records (no fetcher signature)."""
    models = [
        model
        for model in (orcarouter.parse_model_record(record) for record in RECORDS)
        if model
    ]
    return CatalogResult(models=models, source="live", degraded=False)


def live_catalog(credential, origins, capability=CAPABILITY_CHAT, **kwargs):
    models = [
        model
        for model in (orcarouter.parse_model_record(record) for record in RECORDS)
        if model
    ]
    return CatalogResult(models=models, source="live", degraded=False)


class CatalogServiceTests(unittest.TestCase):
    def test_browser_payload_carries_no_credential(self):
        config = {"ORCAROUTER_API_KEY": FAKE_KEY, "ORCAROUTER_CREDENTIAL_GENERATION": 1}
        service = OrcaRouterCatalogService(config, fetcher=live_catalog)
        result = service.load(CAPABILITY_CHAT)
        payload = service.to_json(result, CAPABILITY_CHAT)
        encoded = json.dumps(payload)
        self.assertNotIn(FAKE_KEY, encoded)
        ids = [model["id"] for model in payload["models"]]
        self.assertIn("openai/gpt-5.5", ids)
        self.assertNotIn("google/imagen-4.0-generate-001", ids)

    def test_last_known_good_is_kept_after_a_failure(self):
        config = {"ORCAROUTER_API_KEY": FAKE_KEY, "ORCAROUTER_CREDENTIAL_GENERATION": 1}
        service = OrcaRouterCatalogService(config, fetcher=live_catalog)
        service.load(CAPABILITY_CHAT)

        def failing(credential, origins, capability=CAPABILITY_CHAT,
                    last_known_good=None, **kwargs):
            return CatalogResult(
                models=list(last_known_good or orcarouter.fallback_catalog()),
                source="last_known_good" if last_known_good else "fallback",
                degraded=True,
                error="network down",
            )

        service._fetcher = failing
        result = service.load(CAPABILITY_CHAT)
        self.assertTrue(result.degraded)
        self.assertEqual(result.source, "last_known_good")
        self.assertIn("openai/gpt-5.5", result.id_set())

    def test_service_uses_the_api_origin_not_the_auth_origin(self):
        service = OrcaRouterCatalogService({"ORCAROUTER_API_KEY": FAKE_KEY})
        self.assertEqual(
            service.origins.models_url, "https://api.orcarouter.ai/v1/models"
        )
        self.assertEqual(service.origins.auth_base, "https://www.orcarouter.ai")


class SettingsPageTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "ORCAROUTER_API_KEY": FAKE_KEY,
            "ORCAROUTER_CREDENTIAL_GENERATION": 1,
            "ORCAROUTER_AUTH_STATE": "ok",
        }
        service = OrcaRouterCatalogService(self.config, fetcher=live_catalog)
        self.server = OrcaRouterSettingsServer(self.config, service=service).start()
        self.addCleanup(self.server.stop)

    def _get(self, path):
        return urllib.request.urlopen(self.server.base_url + path, timeout=10)

    def _post(self, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            self.server.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=10)

    def test_page_shows_both_authentication_entries(self):
        html = self._get("/").read().decode("utf-8")
        # API-key entry, masked.
        self.assertIn('id="orcarouter-api-key"', html)
        self.assertIn('type="password"', html)
        self.assertIn("Save key", html)
        self.assertIn("Clear key", html)
        # PKCE entry, side by side with it.
        self.assertIn("Connect with OrcaRouter", html)
        self.assertIn('id="orcarouter-connect"', html)
        self.assertIn("OAuth 2.0 + PKCE", html)

    def test_page_offers_a_real_listbox_not_a_free_text_field(self):
        html = self._get("/").read().decode("utf-8")
        self.assertIn('role="listbox"', html)
        self.assertIn('aria-haspopup="listbox"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('id="model-trigger"', html)
        # The model control is a button + listbox, never an editable input.
        self.assertNotIn('id="model-input"', html)
        self.assertIn(orcarouter.ORCAROUTER_ICON_URL, html)

    def test_page_never_contains_the_stored_key(self):
        html = self._get("/").read().decode("utf-8")
        self.assertNotIn(FAKE_KEY, html)

    def test_pagehide_clears_busy_state_and_cancels_on_the_server(self):
        html = self._get("/").read().decode("utf-8")
        self.assertIn('addEventListener("pagehide"', html)
        self.assertIn("keepalive: true", html)
        self.assertIn("/api/connect/cancel", html)
        # The handler must release the UI synchronously, not via a guarded
        # finally block that a stale generation would suppress.
        self.assertIn("releaseLogin(", html)

    def test_status_endpoint_reports_a_masked_key_only(self):
        payload = json.loads(self._get("/api/status").read().decode("utf-8"))
        self.assertTrue(payload["has_key"])
        self.assertNotIn(FAKE_KEY, json.dumps(payload))
        self.assertEqual(payload["api_base"], "https://api.orcarouter.ai/v1")

    def test_catalog_endpoint_returns_filtered_models_without_the_key(self):
        body = self._get("/api/catalog?capability=chat").read().decode("utf-8")
        payload = json.loads(body)
        self.assertNotIn(FAKE_KEY, body)
        self.assertEqual(payload["capability"], "chat")
        self.assertFalse(payload["degraded"])
        ids = [model["id"] for model in payload["models"]]
        self.assertEqual(ids, ["openai/gpt-5.5", "deepseek/deepseek-v4-pro"])

    def test_saving_and_clearing_a_key_goes_through_the_shared_store(self):
        saved = json.loads(
            self._post("/api/credential", {"api_key": "sk-orca-gui-2222"}).read()
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(self.config["ORCAROUTER_API_KEY"], "sk-orca-gui-2222")
        self.assertNotIn("sk-orca-gui-2222", json.dumps(saved))
        self.assertGreater(self.config["ORCAROUTER_CREDENTIAL_GENERATION"], 1)

        cleared = json.loads(self._post("/api/credential/clear").read())
        self.assertTrue(cleared["ok"])
        self.assertEqual(self.config["ORCAROUTER_API_KEY"], "")

    def test_an_empty_key_is_refused_with_an_actionable_message(self):
        try:
            self._post("/api/credential", {"api_key": ""})
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            payload = json.loads(exc.read().decode("utf-8"))
        else:
            self.fail("an empty key should be refused")
        self.assertFalse(payload["ok"])

    def test_connect_start_points_at_the_auth_origin(self):
        payload = json.loads(self._post("/api/connect/start").read())
        self.assertTrue(payload["authorize_url"].startswith("https://www.orcarouter.ai/auth?"))
        self.assertNotIn("api.orcarouter.ai", payload["authorize_url"])
        self.assertIn("code_challenge_method=S256", payload["authorize_url"])

    def test_connect_lock_is_released_by_cancel_and_a_new_login_can_start(self):
        first = json.loads(self._post("/api/connect/start").read())
        self._post("/api/connect/cancel")
        # Without a remount, a second login starts cleanly.
        second = json.loads(self._post("/api/connect/start").read())
        self.assertGreater(second["attempt"], first["attempt"])
        self.assertNotEqual(second["authorize_url"], first["authorize_url"])

    def test_starting_a_login_twice_does_not_strand_the_old_listener(self):
        self._post("/api/connect/start")
        pending = self.server._pending
        self.assertIsNotNone(pending)
        self._post("/api/connect/start")
        self.assertIsNot(self.server._pending, pending)

    def test_manual_cancel_releases_the_lock(self):
        self._post("/api/connect/start")
        generation = self.server.current_generation()
        self.server.cancel_login()
        self.assertGreater(self.server.current_generation(), generation)
        self.assertIsNone(self.server._pending)


class PanelModelTests(unittest.TestCase):
    def test_provider_switch_recomputes_options(self):
        panel = OrcaRouterPanelModel()
        panel.set_catalog(build_live_catalog())
        self.assertEqual(
            panel.state.option_ids(),
            ["openai/gpt-5.5", "deepseek/deepseek-v4-pro"],
        )
        panel.provider_changed("openai")
        self.assertEqual(panel.state.option_ids(), [])

    def test_attach_image_narrows_the_option_list(self):
        panel = OrcaRouterPanelModel()
        panel.set_catalog(build_live_catalog())
        panel.attach_image(True)
        self.assertEqual(panel.state.option_ids(), ["openai/gpt-5.5"])
        panel.attach_image(False)
        self.assertEqual(len(panel.state.option_ids()), 2)


class LocaleParityTests(unittest.TestCase):
    def test_every_locale_defines_every_orcarouter_label(self):
        catalogs = orcarouter_gui.TRANSLATIONS
        self.assertIn("en", catalogs)
        self.assertIn("zh", catalogs)
        reference = set(catalogs["en"])
        for language, table in catalogs.items():
            self.assertEqual(
                set(table), reference, f"{language} is missing OrcaRouter labels"
            )
            for key, value in table.items():
                self.assertTrue(value.strip(), f"{language}.{key} is empty")

    def test_unknown_locale_falls_back_to_english(self):
        self.assertEqual(
            orcarouter_gui.tr("de", "orca_connect"),
            orcarouter_gui.TRANSLATIONS["en"]["orca_connect"],
        )

    def test_both_auth_labels_are_distinct_in_every_locale(self):
        for language, table in orcarouter_gui.TRANSLATIONS.items():
            self.assertNotEqual(
                table["orca_api_key"], table["orca_connect"], language
            )


if __name__ == "__main__":
    unittest.main()
