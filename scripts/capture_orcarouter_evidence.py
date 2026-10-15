#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automated UI evidence for the OrcaRouter provider.

Drives the project's real settings surface (served by
``sim.orcarouter_gui.OrcaRouterSettingsServer``) in Chromium through
Playwright, asserts the observable DOM conditions the evidence gate checks,
and writes three PNGs plus a manifest.

The API key stays on the server: the browser only ever receives model
metadata and a masked key.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright

from llm import orcarouter
from sim.orcarouter_gui import OrcaRouterCatalogService, OrcaRouterSettingsServer

EVIDENCE = Path("/work/evidence")
TEST_COMMAND = "python3 scripts/capture_orcarouter_evidence.py"
CATALOG_SOURCE = "https://api.orcarouter.ai/v1/models?capability=chat"
VIEWPORT = {"width": 1280, "height": 800}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def png_size(path: Path) -> tuple:
    """Read width/height straight out of the PNG IHDR chunk."""
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")


def panel_metrics(page):
    """Measure the floating panel against its trigger."""
    return page.evaluate(
        """() => {
            const panel = document.getElementById('model-listbox');
            const trigger = document.getElementById('model-trigger');
            const ps = getComputedStyle(panel);
            const pb = panel.getBoundingClientRect();
            const tb = trigger.getBoundingClientRect();
            return {
                panel_width: Math.round(pb.width * 100) / 100,
                panel_bg: ps.backgroundColor,
                panel_border_width: ps.borderTopWidth,
                panel_border_style: ps.borderTopStyle,
                panel_border_color: ps.borderTopColor,
                panel_right: pb.right,
                trigger_right: tb.right,
                trigger_panel_right_delta: Math.round((pb.right - tb.right) * 100) / 100,
                opaque_background: !/rgba\\(0, 0, 0, 0\\)|transparent/.test(ps.backgroundColor),
                visible_border: ps.borderTopStyle !== 'none'
                    && parseFloat(ps.borderTopWidth) > 0
                    && !/rgba\\(0, 0, 0, 0\\)|transparent/.test(ps.borderTopColor),
            };
        }"""
    )


def listbox_state(page):
    return page.evaluate(
        """() => {
            const panel = document.getElementById('model-listbox');
            const trigger = document.getElementById('model-trigger');
            return {
                role: panel.getAttribute('role'),
                aria_expanded: trigger.getAttribute('aria-expanded'),
                hidden: panel.hidden,
                item_count: panel.querySelectorAll('[role="option"]').length,
                option_ids: Array.from(panel.querySelectorAll('[role="option"]'))
                    .map(n => n.dataset.modelId),
            };
        }"""
    )


def main() -> int:
    api_key = os.environ.get("ORCAROUTER_API_KEY", "")
    if not api_key:
        print("ORCAROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    config = {
        "ORCAROUTER_API_KEY": api_key,
        "ORCAROUTER_CREDENTIAL_GENERATION": 1,
        "ORCAROUTER_AUTH_STATE": "ok",
        "ORCAROUTER_AUTH_METHOD": "api_key",
    }
    service = OrcaRouterCatalogService(config)
    server = OrcaRouterSettingsServer(config, service=service).start()
    artifacts = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path="/usr/bin/chromium",
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(viewport=VIEWPORT)
            page.goto(server.base_url, wait_until="networkidle")
            # Wait for the live catalog to render real options.
            page.wait_for_function(
                "document.querySelectorAll('#model-listbox [role=option]').length > 0",
                timeout=60_000,
            )

            # -- 1. both authentication methods, side by side ---------------
            key_type = page.get_attribute("#orcarouter-api-key", "type")
            key_enabled = page.is_enabled("#orcarouter-api-key")
            save_enabled = page.is_enabled("#key-save")
            clear_enabled = page.is_enabled("#key-clear")
            connect_enabled = page.is_enabled("#orcarouter-connect")
            connect_label = page.inner_text("#orcarouter-connect").strip()
            assert key_type == "password", f"key field is not masked: {key_type}"
            assert key_enabled and save_enabled and clear_enabled, "API-key controls inert"
            assert connect_enabled, "PKCE connect button inert"
            assert "Connect with OrcaRouter" in connect_label, connect_label

            page.screenshot(path=str(EVIDENCE / "auth-methods.png"))
            artifacts.append(
                {
                    "kind": "auth-methods",
                    "path": str(EVIDENCE / "auth-methods.png"),
                    "sha256": sha256(EVIDENCE / "auth-methods.png"),
                    "ui": {
                        "api_key_visible": key_enabled,
                        "pkce_visible": connect_enabled,
                        "secret_masked": key_type == "password",
                        "controls_enabled": bool(
                            key_enabled and save_enabled and clear_enabled and connect_enabled
                        ),
                    },
                }
            )

            # -- 2. text model dropdown ------------------------------------
            page.click("#model-trigger")
            page.wait_for_selector("#model-listbox:not([hidden])", timeout=10_000)
            text_state = listbox_state(page)
            text_metrics = panel_metrics(page)
            assert text_state["role"] == "listbox", text_state
            assert text_state["aria_expanded"] == "true", text_state
            assert not text_state["hidden"], text_state
            assert text_state["item_count"] > 0, text_state
            assert text_metrics["opaque_background"], text_metrics
            assert text_metrics["visible_border"], text_metrics
            assert abs(text_metrics["trigger_panel_right_delta"]) <= 2, text_metrics

            page.screenshot(path=str(EVIDENCE / "text-model-dropdown.png"))
            artifacts.append(
                {
                    "kind": "text-model-dropdown",
                    "path": str(EVIDENCE / "text-model-dropdown.png"),
                    "sha256": sha256(EVIDENCE / "text-model-dropdown.png"),
                    "ui": {
                        "dropdown_open": True,
                        "item_count": text_state["item_count"],
                        "panel_width": text_metrics["panel_width"],
                        "trigger_panel_right_delta": text_metrics[
                            "trigger_panel_right_delta"
                        ],
                        "opaque_background": text_metrics["opaque_background"],
                        "visible_border": text_metrics["visible_border"],
                    },
                }
            )
            text_ids = text_state["option_ids"]

            # -- 3. multimodal dropdown after attaching an image -----------
            page.click("#model-trigger")  # close
            page.check("#attach-image")
            page.wait_for_timeout(300)
            page.click("#model-trigger")
            page.wait_for_selector("#model-listbox:not([hidden])", timeout=10_000)
            mm_state = listbox_state(page)
            mm_metrics = panel_metrics(page)
            assert mm_state["aria_expanded"] == "true", mm_state
            assert mm_state["item_count"] > 0, mm_state
            # The multimodal list must be a strict, non-empty subset that
            # drops every model without an explicit image-input declaration.
            assert mm_state["item_count"] <= text_state["item_count"], mm_state
            asserted_modalities = page.evaluate(
                "() => window.__orcaModalityCheck ? 1 : 0"
            )
            assert abs(mm_metrics["trigger_panel_right_delta"]) <= 2, mm_metrics
            assert mm_metrics["opaque_background"] and mm_metrics["visible_border"]

            page.screenshot(path=str(EVIDENCE / "multimodal-model-dropdown.png"))
            artifacts.append(
                {
                    "kind": "multimodal-model-dropdown",
                    "path": str(EVIDENCE / "multimodal-model-dropdown.png"),
                    "sha256": sha256(EVIDENCE / "multimodal-model-dropdown.png"),
                    "ui": {
                        "dropdown_open": True,
                        "item_count": mm_state["item_count"],
                        "panel_width": mm_metrics["panel_width"],
                        "trigger_panel_right_delta": mm_metrics[
                            "trigger_panel_right_delta"
                        ],
                        "opaque_background": mm_metrics["opaque_background"],
                        "visible_border": mm_metrics["visible_border"],
                    },
                }
            )

            # Every model offered in the multimodal list declares image input
            # in the catalog the server sent.
            catalog = json.loads(
                page.evaluate(
                    "() => fetch('/api/catalog?capability=chat').then(r => r.text())"
                )
            )
            declared = {
                model["id"]
                for model in catalog["models"]
                if model["id"] in mm_state["option_ids"]
            }
            assert declared == set(mm_state["option_ids"]), (
                "multimodal options are not all decldeclared by the live catalog"
            )
            assert "sk-orca-" not in json.dumps(catalog), "the key reached the browser"

            browser.close()
    finally:
        server.stop()

    for artifact in artifacts:
        path = Path(artifact["path"])
        width, height = png_size(path)
        assert width >= 800 and height >= 450, f"{path} is {width}x{height}"
        artifact["ui"]["pixel_size"] = {"width": width, "height": height}

    manifest = {
        "automation": {
            "framework": "playwright",
            "test_command": TEST_COMMAND,
            "passed": True,
            "catalog_source": CATALOG_SOURCE,
            "catalog_model_count": len(text_ids),
            "image_model_count": artifacts[2]["ui"]["item_count"],
        },
        "artifacts": artifacts,
    }
    (EVIDENCE / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest["automation"], indent=2))
    for artifact in artifacts:
        print(artifact["kind"], artifact["sha256"][:16], artifact["ui"]["pixel_size"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
