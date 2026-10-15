"""Release gates exercising actual settings, transport, and dashboard flows."""

import asyncio
import json
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Queue
from unittest.mock import Mock, patch

from core.config import CONFIG
from llm.client import LLMTuner
from llm import orcarouter
from sim.orcarouter_gui import OrcaRouterSettingsServer


KEY = "sk-orca-release-test-0000000000"
MODEL = "release/chat-model"


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, payload):
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self.reply({"data": [{
            "id": MODEL, "supported_endpoint_types": ["openai"],
            "architecture": {"input_modalities": ["text"]},
        }]})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append((self.path, payload, self.headers.get("Authorization")))
        if self.path == orcarouter.EXCHANGE_PATH:
            self.reply({"key": KEY, "scope": "api", "user_id": "release-test"})
            return
        if self.path == "/v1/chat/completions":
            content = json.dumps({"status": "DONE", "p": 1, "i": .1, "d": .05})
            chunk = json.dumps({"choices": [{"delta": {"content": content}}]})
            encoded = f"data: {chunk}\n\ndata: [DONE]\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        self.send_error(404)


class ReleaseFlowTests(unittest.TestCase):
    def setUp(self):
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
        self.gateway.seen = []
        self.thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_gateway)
        self.base = f"http://127.0.0.1:{self.gateway.server_port}"
        self.config = {
            "LLM_PROVIDER": "openai", "LLM_MODEL_NAME": "old-provider-model",
            "ORCAROUTER_BASE_URL": self.base,
        }
        self.persist = Mock(return_value=True)
        self.server = OrcaRouterSettingsServer(self.config, persist=self.persist).start()
        self.addCleanup(self.server.stop)

    def close_gateway(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(2)

    def request(self, path, payload=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(
            self.server.base_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)

    def test_browser_login_persists_and_routes_real_streaming_request(self):
        started = self.request("/api/connect/start", {})
        self.assertTrue(started["ok"])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(started["authorize_url"]).query)
        callback = query["callback_url"][0]
        with urllib.request.urlopen(callback + "?" + urllib.parse.urlencode({
            "code": "release-code", "state": query["state"][0],
        }), timeout=5) as response:
            response.read()
        deadline = time.monotonic() + 5
        while self.request("/api/connect/status")["state"] == "pending":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.02)
        self.assertEqual(self.request("/api/connect/status")["state"], "success")
        self.assertEqual(self.config["ORCAROUTER_API_KEY"], KEY)
        self.assertEqual(self.config["LLM_PROVIDER"], "orcarouter_oauth")
        self.assertEqual(self.config["LLM_MODEL_NAME"], "orcarouter/auto")
        self.request("/api/catalog")
        self.assertTrue(self.request("/api/model", {"model": MODEL})["ok"])
        self.assertEqual(self.config["LLM_MODEL_NAME"], MODEL)
        with patch.dict(CONFIG, self.config, clear=True):
            tuner = LLMTuner("", "", MODEL, "orcarouter_oauth", emit_console=False)
            result = tuner.request_json(system_prompt="test", user_prompt="test")
        self.assertEqual(result["status"], "DONE")
        inference = [record for record in self.gateway.seen if record[0].endswith("completions")]
        self.assertEqual(len(inference), 1)
        self.assertEqual(inference[0][1]["model"], MODEL)
        self.assertEqual(inference[0][2], "Bearer " + KEY)
        self.assertNotIn(KEY, json.dumps(self.request("/api/status")))
        self.assertGreaterEqual(self.persist.call_count, 2)

    def test_cancel_during_exchange_cannot_restore_a_cleared_key(self):
        entered, release = threading.Event(), threading.Event()

        def exchange(*args, **kwargs):
            entered.set()
            release.wait(3)
            return orcarouter.ExchangeResult(KEY, "api", "test")

        with patch.object(orcarouter, "exchange_code", side_effect=exchange):
            started = self.request("/api/connect/start", {})
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(started["authorize_url"]).query)
            with urllib.request.urlopen(query["callback_url"][0] + "?" + urllib.parse.urlencode({
                "code": "test", "state": query["state"][0],
            }), timeout=5) as response:
                response.read()
            self.assertTrue(entered.wait(2))
            self.request("/api/credential/clear", {})
            release.set()
            time.sleep(.1)
        self.assertFalse(self.config["ORCAROUTER_API_KEY"])
        self.assertEqual(self.server.login_status()["state"], "cancelled")

    def test_hostile_origin_cannot_change_credentials(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/credential", {"api_key": KEY}, origin="https://unrelated.example")
        self.assertEqual(caught.exception.code, 403)
        self.assertNotIn("ORCAROUTER_API_KEY", self.config)

    def test_failed_write_rolls_back_provider_and_key(self):
        previous = dict(self.config)
        self.persist.return_value = False
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/api/credential", {"api_key": KEY})
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.config, previous)

    def test_revoked_key_has_no_retry_delay(self):
        import requests

        config = {"ORCAROUTER_API_KEY": KEY, "ORCAROUTER_CREDENTIAL_GENERATION": 1}
        response = requests.Response()
        response.status_code = 401
        error = requests.HTTPError(response=response)
        request = Mock(side_effect=error)
        with patch.dict(CONFIG, config, clear=True), patch("core.config.save_config"):
            tuner = LLMTuner("", "", MODEL, "orcarouter", emit_console=False)
            with self.assertRaises(orcarouter.OrcaRouterAuthError):
                tuner._call_with_retry(request)
            self.assertEqual(CONFIG["ORCAROUTER_AUTH_STATE"], "needs_reauth")
        self.assertEqual(request.call_count, 1)

    def test_doctor_uses_default_orcarouter_origin(self):
        from core.doctoring import collect_doctor_checks

        get = Mock(return_value=Mock(status_code=200, ok=True))
        checks = collect_doctor_checks(
            config={"LLM_PROVIDER": "orcarouter", "LLM_MODEL_NAME": MODEL, "ORCAROUTER_API_KEY": KEY},
            config_path="config.json", path_exists=lambda path: True,
            requests_get=get, list_serial_ports=lambda: [], tr_fn=lambda zh, en: en,
            initialize_runtime_config_fn=lambda **kwargs: None, getenv=lambda key: None,
        )
        self.assertEqual(get.call_args.args[0], "https://api.orcarouter.ai/v1/models")
        self.assertEqual(next(c.status for c in checks if c.name == "config fields"), "PASS")


class DashboardSettingsFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_save_model_and_reopen(self):
        from sim.tui import SimulationTUIApp
        from sim.runtime import SimulationController
        from textual.widgets import Input, Select

        catalog = orcarouter.CatalogResult(
            [orcarouter.ModelInfo(MODEL, endpoint_types=("openai",))], "live", False,
        )
        config = {"LLM_PROVIDER": "openai", "LLM_MODEL_NAME": "old-model"}
        app = SimulationTUIApp(Queue(), SimulationController(), None)
        with patch.dict(CONFIG, config, clear=True), patch("core.config.save_config", return_value=True), patch.object(
            orcarouter, "fetch_catalog", return_value=catalog
        ):
            async with app.run_test(size=(110, 45)) as pilot:
                await pilot.press("o")
                await pilot.pause(.1)
                screen = app.screen
                screen.query_one("#orca-api-key", Input).value = KEY
                await pilot.click("#orca-save-key")
                await pilot.pause(.15)
                self.assertEqual(CONFIG["LLM_PROVIDER"], "orcarouter")
                screen.query_one("#orca-model", Select).value = MODEL
                await pilot.pause(.1)
                self.assertEqual(CONFIG["LLM_MODEL_NAME"], MODEL)
                await pilot.press("escape")
                await pilot.press("o")
                await pilot.pause(.15)
                self.assertEqual(app.screen.query_one("#orca-model", Select).value, MODEL)
                await pilot.press("escape")
                self.assertEqual(len(app.screen_stack), 1)

    async def test_slow_catalog_does_not_block_escape(self):
        from sim.tui import SimulationTUIApp
        from sim.runtime import SimulationController

        gate = threading.Event()
        entered = threading.Event()

        def slow_catalog(*args, **kwargs):
            entered.set()
            gate.wait(3)
            return orcarouter.CatalogResult([], "fallback", True)

        app = SimulationTUIApp(Queue(), SimulationController(), None)
        with patch.object(orcarouter, "fetch_catalog", side_effect=slow_catalog):
            try:
                async with app.run_test(size=(110, 45)) as pilot:
                    await pilot.press("o")
                    self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                    await pilot.press("escape")
                    self.assertEqual(len(app.screen_stack), 1)
            finally:
                gate.set()


class PackagedCommandRoutingTests(unittest.TestCase):
    def test_launcher_forwards_provider_options(self):
        import launcher

        for args in (["tune", "--orcarouter-status"], ["--orcarouter-status"]):
            with self.subTest(args=args), patch.object(launcher, "initialize_runtime_config"), patch.object(
                launcher, "run_tuner"
            ) as run:
                launcher.main(args)
                run.assert_called_once_with(["--orcarouter-status"])
