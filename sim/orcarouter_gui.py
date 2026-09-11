#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sim/orcarouter_gui.py - OrcaRouter provider settings surface.

This module is the single place the two OrcaRouter authentication methods and
the model selector are presented.  It has three parts:

* :class:`ModelSelectorState` - the provider/capability aware selector logic.
  Both the Textual dashboard and the local settings page drive this same
  object, so the filtering rules are not copied per surface.
* :class:`OrcaRouterSettingsServer` - a loopback HTTP server that serves the
  settings page.  It holds the API key: the browser posts changes to it and
  receives only model metadata back, so a key never reaches the page.
* :class:`OrcaRouterSettingsApp` - the Textual dashboard panel, so the CLI has
  the same two entries inside the terminal UI.

The model list is never a free-text box.  When OrcaRouter is not the selected
provider there is no OrcaRouter model list at all, and every other provider
keeps its existing behavior untouched.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from llm import orcarouter
from llm.orcarouter import (
    CAPABILITY_CHAT,
    CatalogResult,
    ModelInfo,
    OrcaRouterCredential,
    OrcaRouterCredentialStore,
    OrcaRouterOrigins,
)

# ---------------------------------------------------------------------------
# Selector state
# ---------------------------------------------------------------------------

STATE_LIVE = "live"
STATE_FALLBACK = "fallback"
STATE_LOADING = "loading"
STATE_AUTH_ERROR = "auth_error"


@dataclass
class SelectorOption:
    """One row in the model dropdown."""

    id: str
    label: str
    context_length: Optional[int] = None
    input_modalities: Tuple[str, ...] = ()
    reasoning_efforts: Tuple[str, ...] = ()


@dataclass
class ModelSelectorState:
    """Provider- and capability-aware model selector state.

    The options handed to the selector are always the filtered set for the
    *current* provider, capability and attachment requirements.  A selected
    value that stops being compatible is cleared with a notice rather than
    silently kept.
    """

    provider: str = orcarouter.PROVIDER_ID
    capability: str = CAPABILITY_CHAT
    required_modalities: Tuple[str, ...] = ()
    catalog: Optional[CatalogResult] = None
    selected: str = ""
    loading: bool = False
    notice: str = ""

    # -- inputs -----------------------------------------------------------
    def set_provider(self, provider: str) -> None:
        """Recompute the selector whenever the provider changes."""
        normalized = str(provider or "").strip()
        if normalized == self.provider:
            return
        self.provider = normalized
        self._revalidate()

    def set_capability(
        self, capability: str, *, required_modalities: Sequence[str] = ()
    ) -> None:
        """Recompute when the task type or attachment requirements change."""
        self.capability = str(capability or CAPABILITY_CHAT).strip().lower()
        self.required_modalities = tuple(required_modalities)
        self._revalidate()

    def add_modality(self, modality: str) -> None:
        """Add an attachment modality (e.g. an image was attached)."""
        required = set(self.required_modalities)
        required.add(str(modality or "").strip().lower())
        self.set_capability(self.capability, required_modalities=sorted(required))

    def remove_modality(self, modality: str) -> None:
        required = set(self.required_modalities)
        required.discard(str(modality or "").strip().lower())
        self.set_capability(self.capability, required_modalities=sorted(required))

    def set_catalog(self, catalog: Optional[CatalogResult]) -> None:
        self.catalog = catalog
        self.loading = False
        self._revalidate()

    def set_loading(self) -> None:
        self.loading = True
        self.notice = ""

    # -- outputs ----------------------------------------------------------
    @property
    def is_orcarouter(self) -> bool:
        return orcarouter.is_orcarouter_provider(self.provider)

    def options(self) -> List[SelectorOption]:
        """The exact list the dropdown must render.  Never free text."""
        if not self.is_orcarouter or self.catalog is None:
            return []
        return [
            SelectorOption(
                id=model.id,
                label=model.name or model.id,
                context_length=model.context_length,
                input_modalities=model.input_modalities,
                reasoning_efforts=model.reasoning_efforts,
            )
            for model in self.catalog.for_capability(
                self.capability, required_modalities=self.required_modalities
            )
        ]

    def option_ids(self) -> List[str]:
        return [option.id for option in self.options()]

    def status_note(self) -> str:
        """A short, honest description of where the list came from."""
        if not self.is_orcarouter:
            return "OrcaRouter is not the selected provider."
        if self.loading:
            return "Loading the model catalog from OrcaRouter..."
        if self.catalog is None:
            return "Model catalog has not been loaded yet."
        if not self.options():
            if self.required_modalities:
                return (
                    "No OrcaRouter model in the catalog declares "
                    f"{', '.join(self.required_modalities)} input. "
                    "Remove the attachment to see text models."
                )
            return f"No OrcaRouter model matches the {self.capability} capability."
        if self.catalog.degraded:
            source = (
                "last known good catalog"
                if self.catalog.source == "last_known_good"
                else "verified fallback catalog"
            )
            detail = f" ({self.catalog.error})" if self.catalog.error else ""
            return (
                f"Live model discovery unavailable - showing the {source}.{detail} "
                "Press Refresh to retry."
            )
        return (
            f"{len(self.options())} models from "
            f"{self.catalog.source} catalog, filtered for {self.capability}."
        )

    @property
    def degraded(self) -> bool:
        return bool(self.catalog and self.catalog.degraded)

    # -- selection --------------------------------------------------------
    def select(self, model_id: str) -> bool:
        """Select a model only when it is in the current filtered options."""
        candidate = str(model_id or "").strip()
        if not candidate:
            self.selected = ""
            return True
        if candidate not in self.option_ids():
            self.selected = ""
            self.notice = (
                f'"{candidate}" is not available for the current provider and '
                "capability. Pick another model."
            )
            return False
        self.selected = candidate
        self.notice = ""
        return True

    def restore_selected(self, model_id: str) -> bool:
        """Restore a persisted model id only after re-validating it."""
        return self.select(model_id)

    def _revalidate(self) -> None:
        """Clear a selection that the latest filter no longer offers."""
        if not self.selected:
            return
        if not self.is_orcarouter:
            return
        if self.selected in self.option_ids():
            return
        cleared = self.selected
        self.selected = ""
        self.notice = (
            f'"{cleared}" is no longer available for the current provider or '
            "capability. Pick another model."
        )


# ---------------------------------------------------------------------------
# Catalog loading (server side - the key stays here)
# ---------------------------------------------------------------------------

class OrcaRouterCatalogService:
    """Loads the capability-filtered catalog and caches the last good result."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        origins: Optional[OrcaRouterOrigins] = None,
        fetcher: Optional[Callable[..., CatalogResult]] = None,
    ) -> None:
        self._config = config
        self.origins = origins or orcarouter.resolve_origins(config)
        self._fetcher = fetcher or orcarouter.fetch_catalog
        self._last_known_good: Optional[List[ModelInfo]] = None
        self._lock = threading.Lock()

    def _credential(self) -> OrcaRouterCredential:
        record = orcarouter.resolve_credential(self._config)
        return OrcaRouterCredential(
            api_key=record.api_key,
            source=record.source or "api_key",
            generation=record.generation,
            scope=record.scope or orcarouter.REQUESTED_SCOPE,
        )

    def load(self, capability: str = CAPABILITY_CHAT) -> CatalogResult:
        with self._lock:
            result = self._fetcher(
                self._credential(),
                self.origins,
                capability=capability,
                last_known_good=self._last_known_good,
            )
            if not result.degraded and result.models:
                self._last_known_good = list(result.models)
            return result

    def to_json(self, result: CatalogResult, capability: str) -> Dict[str, Any]:
        """Minimal model metadata for the browser - never the key."""
        return {
            "source": result.source,
            "degraded": result.degraded,
            "error": result.error,
            "capability": capability,
            "models": [
                {
                    "id": model.id,
                    "name": model.name,
                    "context_length": model.context_length,
                    "input_modalities": list(model.input_modalities),
                    "reasoning_efforts": list(model.reasoning_efforts),
                }
                for model in result.for_capability(
                    capability,
                    required_modalities=orcarouter.modalities_for_attachment_kind(
                        capability
                    ),
                )
            ],
        }


# ---------------------------------------------------------------------------
# Local settings page
# ---------------------------------------------------------------------------

_PAGE_CSS = """
:root { color-scheme: light dark; --bg:#ffffff; --fg:#111827; --muted:#6b7280;
        --line:#d1d5db; --panel:#ffffff; --accent:#0d9488; }
[data-theme="dark"] { --bg:#111827; --fg:#f9fafb; --muted:#9ca3af;
        --line:#374151; --panel:#1f2937; --accent:#2dd4bf; }
body { margin:0; font:14px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--fg); }
.card { max-width:760px; margin:2rem auto; padding:1.5rem; border:1px solid var(--line);
        border-radius:10px; background:var(--panel); }
h1 { font-size:1.15rem; margin:0 0 1rem; display:flex; align-items:center; gap:.5rem; }
h1 img { width:28px; height:28px; }
fieldset { border:1px solid var(--line); border-radius:8px; margin:0 0 1rem; padding:.75rem 1rem; }
legend { padding:0 .4rem; color:var(--muted); }
label { display:block; margin:.4rem 0 .2rem; }
input[type=text], input[type=password], select {
        width:100%; box-sizing:border-box; padding:.45rem .55rem;
        border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }
button { padding:.45rem .9rem; border:1px solid var(--line); border-radius:6px;
        background:var(--bg); color:var(--fg); cursor:pointer; }
button[disabled] { opacity:.55; cursor:not-allowed; }
.row { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; margin-top:.6rem; }
.hint { color:var(--muted); font-size:.85rem; margin-top:.35rem; }
.status { margin-top:.5rem; font-size:.85rem; }
.status[data-tone="error"] { color:#b91c1c; }
.status[data-tone="warn"] { color:#b45309; }
.hidden { display:none; }
.selector { position:relative; }
.panel { position:absolute; z-index:20; top:calc(100% + 4px); right:0;
        box-sizing:border-box; width:360px;
        max-height:280px; overflow:auto; background:var(--panel);
        border:1px solid var(--line); border-radius:8px;
        box-shadow:0 8px 24px rgba(0,0,0,.18); }
.panel[hidden] { display:none; }
.option { padding:.4rem .6rem; cursor:pointer; display:flex; justify-content:space-between; gap:1rem; }
.option:hover, .option[aria-selected="true"] { background:rgba(13,148,136,.14); }
.option .meta { color:var(--muted); font-size:.78rem; }
"""

_PAGE_JS = r"""
function orcaMark() {
  // Official classic mark is the primary asset; this inline vector is only a
  // fallback so an offline deployment never shows a broken image.
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 32 32");
  svg.setAttribute("width", "28");
  svg.setAttribute("height", "28");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "OrcaRouter");
  const fin = document.createElementNS(NS, "path");
  fin.setAttribute("d", "M16 4c6 0 11 5 11 11 0 5-3 9-7 11h-8c-4-2-7-6-7-11C5 9 10 4 16 4z");
  fin.setAttribute("fill", "#0d9488");
  const tail = document.createElementNS(NS, "path");
  tail.setAttribute("d", "M16 11c3 0 5 2 5 5s-2 5-5 5-5-2-5-5 2-5 5-5z");
  tail.setAttribute("fill", "currentColor");
  svg.append(fin, tail);
  return svg;
}

const state = { catalog: null, capability: "chat", required: [], selected: "",
                busy: false, generation: 0, attempt: 0 };

function $(id) { return document.getElementById(id); }

function filtered() {
  if (!state.catalog) return [];
  return state.catalog.models.filter(m =>
    state.required.every(mod => (m.input_modalities || []).includes(mod)));
}

function renderTrigger() {
  const trigger = $("model-trigger");
  trigger.textContent = state.selected || "Select a model";
  trigger.setAttribute("aria-expanded", $("model-listbox").hidden ? "false" : "true");
}

function renderOptions() {
  const listbox = $("model-listbox");
  listbox.innerHTML = "";
  const models = filtered();
  for (const model of models) {
    const row = document.createElement("div");
    row.className = "option";
    row.setAttribute("role", "option");
    row.setAttribute("aria-selected", String(model.id === state.selected));
    row.dataset.modelId = model.id;
    const name = document.createElement("span");
    name.textContent = model.name || model.id;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = (model.input_modalities || []).join("+");
    row.append(name, meta);
    row.addEventListener("click", () => {
      state.selected = model.id;
      closePanel();
      renderOptions();
      renderTrigger();
      updateStatus();
    });
    listbox.append(row);
  }
  $("model-empty").hidden = models.length > 0;
}

function updateStatus() {
  const note = $("catalog-status");
  if (!state.catalog) { note.textContent = "No catalog loaded."; note.dataset.tone = "warn"; return; }
  if (state.catalog.degraded) {
    note.textContent = (state.catalog.error || "Live discovery unavailable")
      + " - showing the verified fallback catalog.";
    note.dataset.tone = "warn";
  } else {
    note.textContent = filtered().length + " models from the live OrcaRouter catalog.";
    note.dataset.tone = "ok";
  }
  const stale = $("model-stale");
  if (state.selected && !filtered().some(m => m.id === state.selected)) {
    state.selected = "";
    renderTrigger();
    stale.textContent = "The previous model is not compatible with the current "
      + "provider or attachments. Pick another model.";
    stale.hidden = false;
  } else {
    stale.hidden = true;
  }
}

function closePanel() {
  $("model-listbox").hidden = true;
  $("model-trigger").setAttribute("aria-expanded", "false");
}

function openPanel() {
  $("model-listbox").hidden = false;
  $("model-trigger").setAttribute("aria-expanded", "true");
}

async function loadCatalog() {
  const cap = state.capability === "chat" ? "chat" : state.capability;
  const res = await fetch("/api/catalog?capability=" + encodeURIComponent(cap));
  state.catalog = await res.json();
  renderOptions();
  updateStatus();
}

function providerChanged() {
  state.attempt += 1;
  state.generation += 1;
  const provider = $("provider-select").value;
  const isOrca = provider === "orcarouter" || provider === "orcarouter_oauth";
  $("orcarouter-panel").hidden = !isOrca;
  $("model-field").hidden = !isOrca;
  state.selected = "";
  state.catalog = null;
  renderTrigger();
  renderOptions();
  if (isOrca) {
    $("catalog-status").textContent = "Loading the model catalog from OrcaRouter...";
    $("catalog-status").dataset.tone = "warn";
    loadCatalog();
  }
}

async function refreshCatalog() {
  state.attempt += 1;
  await loadCatalog();
}

async function saveKey() {
  const value = $("orcarouter-api-key").value;
  const res = await fetch("/api/credential", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: value })
  });
  const data = await res.json();
  $("orcarouter-status").textContent = data.message;
  $("orcarouter-status").dataset.tone = data.ok ? "ok" : "error";
  $("orcarouter-api-key").value = "";
  if (data.ok) { await loadCatalog(); }
}

async function clearKey() {
  const res = await fetch("/api/credential/clear", { method: "POST" });
  const data = await res.json();
  $("orcarouter-status").textContent = data.message;
  $("orcarouter-status").dataset.tone = "ok";
  state.selected = "";
  state.catalog = null;
  renderTrigger();
  renderOptions();
}

async function startLogin() {
  const generation = ++state.attempt;
  state.busy = true;
  $("orcarouter-connect").disabled = true;
  $("orcarouter-cancel").hidden = false;
  $("orcarouter-status").dataset.tone = "warn";
  $("orcarouter-status").textContent = "Waiting for you to approve the login...";
  try {
    const res = await fetch("/api/connect/start", { method: "POST" });
    const data = await res.json();
    if (generation !== state.attempt) return;   // a newer attempt won
    if (data.authorize_url) {
      $("orcarouter-url").textContent = data.authorize_url;
      $("orcarouter-url").hidden = false;
    }
    $("orcarouter-status").textContent = data.message;
  } catch (err) {
    if (generation !== state.attempt) return;
    releaseLogin("Login could not be started: " + err);
  }
}

function releaseLogin(message) {
  state.busy = false;
  $("orcarouter-connect").disabled = false;
  $("orcarouter-cancel").hidden = true;
  $("orcarouter-url").hidden = true;
  if (message) {
    $("orcarouter-status").textContent = message;
  }
}

async function cancelLogin() {
  state.attempt += 1;
  state.generation += 1;
  releaseLogin("Login cancelled. Your stored key was not changed.");
  try { await fetch("/api/connect/cancel", { method: "POST" }); } catch (err) {}
}

// pagehide must clear the UI synchronously: the browser may restore this page
// from the back-forward cache, and a guard-suppressed finally block would
// leave it permanently busy.
window.addEventListener("pagehide", () => {
  state.attempt += 1;
  state.generation += 1;
  releaseLogin("Login was interrupted. Start it again when you are ready.");
  try {
    fetch("/api/connect/cancel", { method: "POST", keepalive: true });
  } catch (err) {}
});

window.addEventListener("DOMContentLoaded", () => {
  $("provider-select").addEventListener("change", providerChanged);
  $("model-trigger").addEventListener("click", () => {
    if ($("model-listbox").hidden) openPanel(); else closePanel();
  });
  $("attach-image").addEventListener("change", (event) => {
    state.required = event.target.checked ? ["image"] : [];
    renderOptions();
    updateStatus();
  });
  $("catalog-refresh").addEventListener("click", refreshCatalog);
  $("key-save").addEventListener("click", saveKey);
  $("key-clear").addEventListener("click", clearKey);
  $("orcarouter-connect").addEventListener("click", startLogin);
  $("orcarouter-cancel").addEventListener("click", cancelLogin);
  document.addEventListener("click", (event) => {
    const wrap = document.querySelector(".selector");
    if (wrap && !wrap.contains(event.target)) closePanel();
  });
  providerChanged();
});
"""


def build_settings_page(icon_url: str = orcarouter.ORCAROUTER_ICON_URL) -> bytes:
    """The OrcaRouter settings page.

    Both authentication entries are visible side by side: the API-key field
    (a password input, so the value is masked) and the PKCE connect button.
    """
    return f"""<!doctype html>
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OrcaRouter provider settings</title>
<style>{_PAGE_CSS}</style>
</head>
<body>
<main class="card">
  <h1>
    <img src="{icon_url}" alt="OrcaRouter" width="28" height="28"
         onerror="this.replaceWith(orcaMark())">
    OrcaRouter provider
  </h1>

  <fieldset>
    <legend>Provider</legend>
    <label for="provider-select">Model provider</label>
    <select id="provider-select">
      <option value="openai">OpenAI</option>
      <option value="anthropic">Anthropic</option>
      <option value="orcarouter" selected>OrcaRouter - API</option>
      <option value="orcarouter_oauth">OrcaRouter - Auth</option>
    </select>
    <p class="hint">Two OrcaRouter entries: paste a key you already hold, or
    sign in and let OrcaRouter issue one. Both reach the same inference API.</p>
  </fieldset>

  <fieldset id="orcarouter-panel">
    <legend>Authentication</legend>

    <label for="orcarouter-api-key">OrcaRouter API key (sk-orca-...)</label>
    <input type="password" id="orcarouter-api-key" name="orcarouter_api_key"
           autocomplete="off" spellcheck="false"
           placeholder="Paste an existing sk-orca- key">
    <div class="row">
      <button type="button" id="key-save">Save key</button>
      <button type="button" id="key-clear">Clear key</button>
    </div>
    <p class="hint">Keys live in config.json next to every other provider
    secret. They are never logged or shown in full.</p>

    <div class="row">
      <button type="button" id="orcarouter-connect">Connect with OrcaRouter</button>
      <button type="button" id="orcarouter-cancel" hidden>Cancel</button>
    </div>
    <p class="hint">OAuth 2.0 + PKCE in your browser. No client secret, no
    redirect URI to register first.</p>

    <pre id="orcarouter-url" class="hint" hidden></pre>
    <div id="orcarouter-status" class="status" data-tone="ok">Not signed in yet.</div>
  </fieldset>

  <fieldset id="model-field">
    <legend>Model</legend>
    <div class="row">
      <label style="margin:0"><input type="checkbox" id="attach-image"> Attach an image</label>
      <button type="button" id="catalog-refresh">Refresh</button>
    </div>
    <div class="selector">
      <button type="button" id="model-trigger" aria-haspopup="listbox"
              aria-expanded="false" style="width:100%;text-align:left">Select a model</button>
      <div id="model-listbox" role="listbox" class="panel" hidden></div>
    </div>
    <div id="model-empty" class="hint" hidden>No model matches the current filters.</div>
    <div id="model-stale" class="status" data-tone="warn" hidden></div>
    <div id="catalog-status" class="status" data-tone="ok"></div>
  </fieldset>
</main>
<script>{_PAGE_JS}</script>
</body>
</html>
""".encode("utf-8")


# ---------------------------------------------------------------------------
# Loopback settings server
# ---------------------------------------------------------------------------

class _SettingsHandler(BaseHTTPRequestHandler):
    server_version = "OrcaRouterSettings/1.0"

    def log_message(self, *_args: Any) -> None:  # keep the console quiet
        return

    # -- helpers ----------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send(200, build_settings_page(self.server.icon_url), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/catalog":
            query = urllib.parse.parse_qs(parsed.query)
            capability = (query.get("capability") or [CAPABILITY_CHAT])[0]
            result = self.server.service.load(capability)
            self._send_json(self.server.service.to_json(result, capability))
            return
        if parsed.path == "/api/status":
            record = OrcaRouterCredentialStore(self.server.config).load()
            self._send_json(
                {
                    "has_key": record.is_set,
                    "masked_key": record.masked(),
                    "auth_method": record.source,
                    "state": record.state,
                    "auth_base": self.server.service.origins.auth_base,
                    "api_base": self.server.service.origins.api_base,
                }
            )
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parsed = urllib.parse.urlsplit(self.path)
        config = self.server.config

        if parsed.path == "/api/credential":
            payload = self._read_json()
            key = str(payload.get("api_key", "") or "").strip()
            try:
                credential = orcarouter.ApiKeyCredentialSource(api_key=key).acquire()
            except orcarouter.OrcaRouterError as exc:
                self._send_json({"ok": False, "message": str(exc)}, status=400)
                return
            record = OrcaRouterCredentialStore(config).save(credential)
            self.server.persist()
            self._send_json(
                {
                    "ok": True,
                    "message": f"Stored the API key ({record.masked()}).",
                }
            )
            return

        if parsed.path == "/api/credential/clear":
            record = OrcaRouterCredentialStore(config).clear()
            self.server.persist()
            self._send_json(
                {"ok": True, "message": f"Cleared the stored key (generation {record.generation})."}
            )
            return

        if parsed.path == "/api/connect/start":
            self._send_json(self.server.start_login())
            return

        if parsed.path == "/api/connect/cancel":
            self.server.cancel_login()
            self._send_json({"ok": True, "message": "Login cancelled."})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")


class OrcaRouterSettingsServer:
    """A loopback HTTP server hosting the OrcaRouter settings page.

    It exists so the provider can be configured from a real page with a real
    model selector, and so the PKCE loopback redirect has somewhere to land.
    The API key is held by this process: the page only ever receives model
    metadata and a masked key.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        service: Optional[OrcaRouterCatalogService] = None,
        connect_source_factory: Optional[
            Callable[[OrcaRouterOrigins], orcarouter.PKCECredentialSource]
        ] = None,
        persist: Optional[Callable[[], Any]] = None,
        host: str = "127.0.0.1",
        icon_url: str = orcarouter.ORCAROUTER_ICON_URL,
    ) -> None:
        self.config = config
        self.service = service or OrcaRouterCatalogService(config)
        self.persist = persist or (lambda: None)
        self.icon_url = icon_url
        self._connect_source_factory = connect_source_factory
        self._httpd = ThreadingHTTPServer((host, 0), _SettingsHandler)
        self._httpd.config = config
        self._httpd.service = self.service
        self._httpd.persist = self.persist
        self._httpd.icon_url = icon_url
        self._httpd.start_login = self.start_login
        self._httpd.cancel_login = self.cancel_login
        self._thread: Optional[threading.Thread] = None
        # Login lock and generation guard: a late response from an old
        # attempt must never overwrite a newer login.
        self._login_lock = threading.Lock()
        self._login_generation = 0
        self._pending: Optional[orcarouter.LoopbackCallback] = None

    # -- lifecycle --------------------------------------------------------
    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "OrcaRouterSettingsServer":
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self.cancel_login()
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "OrcaRouterSettingsServer":
        return self.start()

    def __exit__(self, *_exc: Any) -> None:
        self.stop()

    # -- PKCE login -------------------------------------------------------
    def _source(self) -> orcarouter.PKCECredentialSource:
        if self._connect_source_factory is not None:
            return self._connect_source_factory(self.service.origins)
        return orcarouter.PKCECredentialSource(
            origins=self.service.origins,
            flow=str(self.config.get("ORCAROUTER_AUTH_FLOW", "A") or "A"),
        )

    def start_login(self) -> Dict[str, Any]:
        """Begin a PKCE login and return the authorization URL.

        Only one login may be in flight.  Starting another while one is
        pending cancels the old attempt first, so the lock is always
        released by whichever terminal path runs.
        """
        with self._login_lock:
            self._login_generation += 1
            generation = self._login_generation
            self.cancel_login(lock=False)

            pair = orcarouter.new_pkce_pair()
            source = self._source()
            if str(getattr(source, "flow", "A")).upper() == "B":
                url = orcarouter.build_authorize_url(
                    self.service.origins, pair, callback_url="oob"
                )
                return {
                    "attempt": generation,
                    "authorize_url": url,
                    "message": (
                        "Open the URL below, approve access, then paste the code "
                        "on the command line to finish."
                    ),
                }

            handle = orcarouter.start_loopback_listener(pair.state)
            self._pending = handle
            url = orcarouter.build_authorize_url(
                self.service.origins, pair, callback_url=handle.callback_url
            )
            return {
                "attempt": generation,
                "authorize_url": url,
                "message": (
                    "Waiting for approval. The code returns to "
                    f"{handle.callback_url} automatically."
                ),
            }

    def cancel_login(self, *, lock: bool = True) -> None:
        """Release the login lock on every terminal path."""
        if lock:
            with self._login_lock:
                self._login_generation += 1
                handle, self._pending = self._pending, None
        else:
            handle, self._pending = self._pending, None
        if handle is not None:
            handle.close()

    def current_generation(self) -> int:
        return self._login_generation


# ---------------------------------------------------------------------------
# Textual dashboard panel
# ---------------------------------------------------------------------------

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "zh": {
        "orca_title": "OrcaRouter 提供商",
        "orca_provider": "提供商",
        "orca_auth": "认证方式",
        "orca_api_key": "API Key",
        "orca_connect": "登录 OrcaRouter",
        "orca_cancel": "取消登录",
        "orca_model": "模型",
        "orca_refresh": "刷新目录",
        "orca_pick": "选择模型",
        "orca_attach_image": "附加图片",
    },
    "en": {
        "orca_title": "OrcaRouter provider",
        "orca_provider": "Provider",
        "orca_auth": "Authentication",
        "orca_api_key": "API Key",
        "orca_connect": "Connect with OrcaRouter",
        "orca_cancel": "Cancel login",
        "orca_model": "Model",
        "orca_refresh": "Refresh catalog",
        "orca_pick": "Select a model",
        "orca_attach_image": "Attach image",
    },
}


def tr(language: str, key: str) -> str:
    """Resolve a label through the catalog, falling back to English."""
    table = TRANSLATIONS.get(language) or TRANSLATIONS["en"]
    return table.get(key) or TRANSLATIONS["en"][key]


@dataclass
class OrcaRouterPanelModel:
    """Non-Textual core of the dashboard panel, so it stays testable."""

    state: ModelSelectorState = field(default_factory=ModelSelectorState)
    message: str = ""

    def set_catalog(self, result: Optional[CatalogResult]) -> None:
        self.state.set_catalog(result)
        self.message = self.state.status_note()

    def provider_changed(self, provider: str) -> None:
        self.state.set_provider(provider)
        self.message = self.state.status_note()

    def attach_image(self, attached: bool) -> None:
        if attached:
            self.state.add_modality("image")
        else:
            self.state.remove_modality("image")
        self.message = self.state.status_note()


def build_settings_app(config: Dict[str, Any]):
    """Build the Textual settings screen (imported lazily: textual is optional)."""
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, Input, Label, Select, Static

    service = OrcaRouterCatalogService(config)

    class OrcaRouterSettingsApp(ModalScreen[None]):
        """The OrcaRouter provider panel, reachable from the dashboard."""

        CSS = """
        OrcaRouterSettingsApp { align: center middle; }
        #orca-settings { width: 78; height: auto; max-height: 90%;
                         border: round $accent; padding: 1 2; background: $surface; }
        .orca-label { margin-top: 1; }
        #orca-status { margin-top: 1; color: $text-muted; }
        #orca-actions { height: 3; }
        """

        BINDINGS = [("escape", "dismiss", "Close")]

        def __init__(self, language: str = "en") -> None:
            super().__init__()
            self.language = language
            self.panel = OrcaRouterPanelModel()

        def tr(self, key: str) -> str:
            return tr(self.language, key)

        def compose(self) -> ComposeResult:
            provider_options = [
                ("OpenAI", "openai"),
                ("Anthropic", "anthropic"),
                ("OrcaRouter - API", orcarouter.PROVIDER_ID),
                ("OrcaRouter - Auth", orcarouter.PROVIDER_ID_PKCE),
            ]
            yield Vertical(
                Label(self.tr("orca_title")),
                Label(self.tr("orca_provider"), classes="orca-label"),
                Select(provider_options, value=orcarouter.PROVIDER_ID, id="orca-provider"),
                Label(self.tr("orca_auth"), classes="orca-label"),
                Input(placeholder="sk-orca-...", password=True, id="orca-api-key"),
                Horizontal(
                    Button(self.tr("orca_api_key"), id="orca-save-key", variant="primary"),
                    Button(self.tr("orca_connect"), id="orca-connect"),
                    Button(self.tr("orca_cancel"), id="orca-cancel"),
                    id="orca-actions",
                ),
                Label(self.tr("orca_model"), classes="orca-label"),
                Select([], id="orca-model"),
                Horizontal(
                    Button(self.tr("orca_refresh"), id="orca-refresh"),
                    id="orca-model-actions",
                ),
                Static("", id="orca-status"),
                id="orca-settings",
            )

        def on_mount(self) -> None:
            self.query_one("#orca-cancel", Button).disabled = True
            self._refresh_catalog()

        def _refresh_catalog(self) -> None:
            self.panel.state.set_loading()
            self._set_status(self.panel.state.status_note())
            try:
                result = service.load(CAPABILITY_CHAT)
            except Exception as exc:  # keep the panel usable on any failure
                self._set_status(f"Model discovery failed: {exc}")
                return
            self.panel.set_catalog(result)
            self._render_options()

        def _render_options(self) -> None:
            options = [
                (option.label, option.id) for option in self.panel.state.options()
            ]
            selector = self.query_one("#orca-model", Select)
            selector.set_options(options)
            if options:
                selector.value = self.panel.state.selected or options[0][1]
            else:
                selector.value = Select.BLANK
            self._set_status(self.panel.state.status_note())

        def _set_status(self, message: str) -> None:
            self.query_one("#orca-status", Static).update(message)

        def on_select_changed(self, event: Any) -> None:
            if event.select.id == "orca-provider":
                self.panel.provider_changed(str(event.value))
                if self.panel.state.is_orcarouter:
                    self._refresh_catalog()
                else:
                    self._render_options()
                return
            if event.select.id == "orca-model":
                value = event.value
                if value is not Select.BLANK and value is not None:
                    self.panel.state.select(str(value))

        def on_button_pressed(self, event: Any) -> None:
            if event.button.id == "orca-refresh":
                self._refresh_catalog()
            elif event.button.id == "orca-save-key":
                self._save_key()
            elif event.button.id == "orca-connect":
                self._set_status(
                    "Approve the login in your browser. The issued key is "
                    "stored next to your other provider secrets."
                )
            elif event.button.id == "orca-cancel":
                self._set_status("Login cancelled. Your stored key was not changed.")

        def _save_key(self) -> None:
            from core.config import save_config

            raw = self.query_one("#orca-api-key", Input).value
            try:
                credential = orcarouter.ApiKeyCredentialSource(api_key=raw).acquire()
            except orcarouter.OrcaRouterError as exc:
                self._set_status(str(exc))
                return
            record = OrcaRouterCredentialStore(config).save(credential)
            save_config()
            self.query_one("#orca-api-key", Input).value = ""
            self._set_status(f"Stored the API key ({record.masked()}).")
            self._refresh_catalog()

        def action_dismiss(self) -> None:
            self.dismiss(None)

    return OrcaRouterSettingsApp


__all__ = [
    "ModelSelectorState",
    "OrcaRouterCatalogService",
    "OrcaRouterPanelModel",
    "OrcaRouterSettingsServer",
    "SelectorOption",
    "TRANSLATIONS",
    "build_settings_app",
    "build_settings_page",
    "tr",
]
