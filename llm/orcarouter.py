#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm/orcarouter.py - OrcaRouter as a first-class provider.

OrcaRouter is an OpenAI-compatible AI gateway that exposes one endpoint for
chat/reasoning models and agents (routing, failover, observability,
guardrails).  Inference uses the OpenAI wire format, so the tuning loop keeps
speaking the existing OpenAI-compatible transport and nothing in the tuning or
safety path changes.

This module owns the three pieces the provider needs:

* **Origins** - authentication lives on ``https://www.orcarouter.ai`` while
  inference and model discovery live on ``https://api.orcarouter.ai/v1``.
  They are separate public origins and neither is derived from the other.
* **Credentials** - one small seam (:class:`CredentialSource`) with two
  adapters: a pasted API key and an OAuth 2.0 + PKCE login.  Both yield the
  same :class:`OrcaRouterCredential`, so the transport and the model catalog
  never need to know which one the user picked.
* **Model catalog** - capability-filtered discovery from
  ``GET {api_base}/models`` plus a small verified cold-start seed.

The key belongs to the user: it is billed to their OrcaRouter account and
revocable by them at any time.  It is never logged, printed, or sent to the
browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# The app label shown on the OrcaRouter consent screen.  It is a claim the
# screen presents to the user, so it names this project rather than a brand.
APP_NAME = "LLM PID Tuner"

PROVIDER_ID = "orcarouter"
PROVIDER_ID_PKCE = "orcarouter_oauth"

# Public defaults.  Authentication and inference are different origins; never
# derive one from the other by swapping a hostname or appending "/v1".
DEFAULT_AUTH_BASE = "https://www.orcarouter.ai"
DEFAULT_API_BASE = "https://api.orcarouter.ai/v1"

# Fixed paths.  The relay is on api.orcarouter.ai at /v1; the auth endpoints
# are on www.orcarouter.ai under /api/v1/auth.  A /v1/auth/keys path 404s.
AUTHORIZE_PATH = "/auth"
EXCHANGE_PATH = "/api/v1/auth/keys"
DEVICE_CODE_PATH = "/api/v1/auth/device/code"
DEVICE_TOKEN_PATH = "/api/v1/auth/device/token"
MODELS_PATH = "/models"

# Environment variables, in the project's existing env-over-config idiom.
ENV_SHARED_BASE = "ORCA_BASE_URL"
ENV_AUTH_BASE = "ORCA_AUTH_BASE_URL"
ENV_API_BASE = "ORCA_API_BASE_URL"

# The scope this client asks for.  "connector" is refused on the way in for a
# client that cannot use it, so ask for the one we can actually hold.
REQUESTED_SCOPE = "api"
ACCEPTED_SCOPES = ("api",)

# Bounds so a hostile or broken catalog cannot consume unbounded memory.
CATALOG_TIMEOUT = 10.0
CATALOG_MAX_BYTES = 4 * 1024 * 1024
CATALOG_MAX_ITEMS = 1000

# A reauth marker.  A durable OrcaRouter key is not a refresh token: there is
# no refresh grant, so a 401 means "re-run the connect flow".
REAUTH_NEEDED = "needs_reauth"
REAUTH_OK = "ok"

_KEY_PREFIX = "sk-orca-"


class OrcaRouterError(RuntimeError):
    """Base class for OrcaRouter provider failures."""


class OrcaRouterAuthError(OrcaRouterError):
    """The upstream rejected the credential (HTTP 401).

    Terminal: it marks the credential generation ``needs_reauth``.  There is
    no refresh grant to attempt.
    """

    def __init__(self, message: str, *, generation: int = -1, account: str = ""):
        super().__init__(message)
        self.generation = generation
        self.account = account


class OrcaRouterPKCEError(OrcaRouterError):
    """The PKCE authorization could not be completed."""


# ---------------------------------------------------------------------------
# Origins
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrcaRouterOrigins:
    """Resolved auth/inference origins.

    Explicit overrides win; ``ORCA_BASE_URL`` is a shared self-hosted
    fallback for deployments that put both behind one name.  Remote origins
    must be HTTPS - plain HTTP is only accepted for loopback development.
    """

    auth_base: str
    api_base: str

    @property
    def authorize_url(self) -> str:
        return f"{self.auth_base}{AUTHORIZE_PATH}"

    @property
    def exchange_url(self) -> str:
        return f"{self.auth_base}{EXCHANGE_PATH}"

    @property
    def models_url(self) -> str:
        return f"{self.api_base}{MODELS_PATH}"


def _strip_slash(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _is_loopback(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "::1", "[::1]"}


def validate_origin(url: str, *, what: str) -> str:
    """Return a normalized origin, rejecting insecure remote schemes."""
    normalized = _strip_slash(url)
    if not normalized:
        raise OrcaRouterError(f"{what} base URL is empty")
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OrcaRouterError(f"{what} base URL is not an absolute http(s) URL")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise OrcaRouterError(
            f"{what} base URL must use HTTPS for a non-loopback host"
        )
    return normalized


def resolve_origins(
    config: Optional[Dict[str, Any]] = None,
    *,
    environ: Optional[Dict[str, str]] = None,
    shared_base: Optional[str] = None,
    auth_base: Optional[str] = None,
    api_base: Optional[str] = None,
) -> OrcaRouterOrigins:
    """Resolve the auth and inference origins with explicit wins.

    Precedence, highest first: explicit argument, dedicated environment
    variable, dedicated config key, then the shared base (argument, env, or
    config) for whichever origin was not set, then the public default.
    """
    env = os.environ if environ is None else environ
    cfg = config if config is not None else {}

    def pick(arg: Optional[str], env_key: str, cfg_key: str) -> Optional[str]:
        for candidate in (arg, env.get(env_key), cfg.get(cfg_key)):
            value = _strip_slash(candidate) if isinstance(candidate, str) else ""
            if value:
                return value
        return None

    shared = pick(shared_base, ENV_SHARED_BASE, "ORCAROUTER_BASE_URL")
    resolved_auth = pick(auth_base, ENV_AUTH_BASE, "ORCAROUTER_AUTH_BASE_URL")
    resolved_api = pick(api_base, ENV_API_BASE, "ORCAROUTER_API_BASE_URL")

    auth = resolved_auth or shared or DEFAULT_AUTH_BASE
    api = resolved_api or shared or DEFAULT_API_BASE

    auth = validate_origin(auth, what="OrcaRouter auth")
    api = validate_origin(api, what="OrcaRouter inference")
    if not api.rstrip("/").endswith("/v1"):
        # The relay is served under /v1.  Append it only to the API origin we
        # were handed - never to derive it from the auth origin.
        api = f"{api}/v1"
    return OrcaRouterOrigins(auth_base=auth, api_base=api)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact_secret(value: str) -> str:
    """Mirror of ``core.doctoring.mask_secret`` for rendering credentials.

    Kept local so the auth module has no import cycle with the doctor.
    """
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def scrub(text: str, *secrets_to_hide: str) -> str:
    """Remove known secret values from a message before it is shown or logged."""
    scrubbed = str(text)
    for secret in secrets_to_hide:
        if secret and len(secret) >= 8:
            scrubbed = scrubbed.replace(secret, "<redacted>")
    return scrubbed


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def b64url(raw: bytes) -> str:
    """base64url without padding, as the challenge encoding requires."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class PKCEPair:
    """A verifier and its S256 challenge.

    The verifier never leaves the process: it is not logged, not serialized,
    and never placed on the authorize URL.
    """

    verifier: str
    challenge: str
    state: str

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"PKCEPair(challenge={self.challenge!r}, state={self.state!r})"

    __str__ = __repr__


def new_pkce_pair(randbytes: Callable[[int], bytes] = secrets.token_bytes) -> PKCEPair:
    """Fresh verifier/state from a cryptographic RNG for every attempt."""
    verifier = b64url(randbytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = b64url(randbytes(16))
    return PKCEPair(verifier=verifier, challenge=challenge, state=state)


def build_authorize_url(
    origins: OrcaRouterOrigins,
    pair: PKCEPair,
    *,
    callback_url: str,
    app_name: str = APP_NAME,
    scope: str = REQUESTED_SCOPE,
    login_hint: Optional[str] = None,
    workspace_hint: Optional[str] = None,
    prompt: Optional[str] = None,
) -> str:
    """Build the consent URL.  S256 is always sent.

    ``callback_url=oob`` is Flow B; anything else is a Flow A loopback URL.
    The verifier is deliberately absent from this URL - only its hash travels.
    """
    params = {
        "callback_url": callback_url,
        "code_challenge": pair.challenge,
        "code_challenge_method": "S256",
        "state": pair.state,
        "app_name": app_name,
    }
    if scope:
        params["scope"] = scope
    if login_hint:
        params["login_hint"] = login_hint
    if workspace_hint:
        params["workspace_hint"] = workspace_hint
    if prompt:
        params["prompt"] = prompt
    return f"{origins.authorize_url}?{urllib.parse.urlencode(params)}"


def validate_callback_url(callback_url: str) -> str:
    """Apply OrcaRouter's callback_url rules before opening a browser."""
    if callback_url == "oob":
        return callback_url
    parsed = urllib.parse.urlsplit(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OrcaRouterPKCEError("callback_url must be an absolute http(s) URL or 'oob'")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise OrcaRouterPKCEError(
            "http callback_url is only allowed on localhost, 127.0.0.1 or [::1]"
        )
    if parsed.username or parsed.password or parsed.fragment:
        raise OrcaRouterPKCEError("callback_url must not carry userinfo or a fragment")
    return callback_url


def callback_url_matches_origins(callback_url: str, origins: OrcaRouterOrigins) -> bool:
    """Reject a callback that points back at the OrcaRouter auth origin."""
    if callback_url == "oob":
        return True
    return not callback_url.startswith(origins.auth_base)


@dataclass(frozen=True)
class ExchangeResult:
    """What the exchange actually granted - not what we asked for."""

    key: str
    scope: str
    user_id: str

    @property
    def scope_is_usable(self) -> bool:
        return self.scope in ACCEPTED_SCOPES


def _scope_warning(result: ExchangeResult) -> Optional[str]:
    if result.scope_is_usable:
        return None
    return (
        f'OrcaRouter granted scope "{result.scope}", not "{REQUESTED_SCOPE}". '
        "This client needs an api-scope key; ask a workspace owner to approve "
        "the wider grant, then connect again."
    )


def _post_json(
    url: str,
    payload: Dict[str, Any],
    *,
    timeout: float,
    opener: Optional[Callable[..., Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """POST JSON and return (status, parsed body) without raising on 4xx."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    open_fn = urllib.request.urlopen if opener is None else opener
    try:
        with open_fn(request, timeout=timeout) as response:
            raw = response.read(CATALOG_MAX_BYTES)
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read(CATALOG_MAX_BYTES) if hasattr(exc, "read") else b""
        status = exc.code
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    return status, parsed


def exchange_code(
    origins: OrcaRouterOrigins,
    pair: PKCEPair,
    code: str,
    *,
    timeout: float = 30.0,
    opener: Optional[Callable[..., Any]] = None,
) -> ExchangeResult:
    """Redeem an auth code for an OrcaRouter API key.

    Always posts to the auth origin's ``/api/v1/auth/keys``; the inference
    origin is never used for credential exchange.  Error responses are mapped
    to actionable messages without echoing the verifier or the code.
    """
    if not code or not code.strip():
        raise OrcaRouterPKCEError("No authorization code was provided.")

    status, payload = _post_json(
        origins.exchange_url,
        {
            "code": code.strip(),
            "code_verifier": pair.verifier,
            "code_challenge_method": "S256",
        },
        timeout=timeout,
        opener=opener,
    )

    if status == 200:
        key = str(payload.get("key", "") or "")
        if not key:
            raise OrcaRouterPKCEError(
                "OrcaRouter returned no key. Connect again from the settings panel."
            )
        return ExchangeResult(
            key=key,
            scope=str(payload.get("scope", "") or ""),
            user_id=str(payload.get("user_id", "") or ""),
        )
    if status == 400:
        raise OrcaRouterPKCEError(
            "OrcaRouter rejected the PKCE challenge method. Reconnect from the "
            "settings panel to start a fresh authorization."
        )
    if status == 403:
        raise OrcaRouterPKCEError(
            "This authorization code is unknown, expired, or already used. "
            "Codes are single-use with a 10 minute lifetime - start a new login."
        )
    if status == 429:
        raise OrcaRouterPKCEError(
            "OrcaRouter rate-limited this login with HTTP 429: a user may hold "
            "at most 10 PKCE-issued keys per 24 hours. Reuse the stored key, "
            "or try again later."
        )
    raise OrcaRouterPKCEError(
        f"OrcaRouter login failed with HTTP {status}. "
        "Check your network and try again."
    )


# ---------------------------------------------------------------------------
# Flow A - loopback redirect listener
# ---------------------------------------------------------------------------

_CALLBACK_PAGE = (
    "<!doctype html><meta charset='utf-8'>"
    "<title>OrcaRouter</title>"
    "<body style='font-family:system-ui;padding:3rem'>"
    "<h2>{heading}</h2><p>{detail}</p></body>"
)


@dataclass
class LoopbackCallback:
    """A one-shot listener on 127.0.0.1 that receives the redirect."""

    callback_url: str
    wait: Callable[[float], Optional[Tuple[Optional[str], Optional[str]]]]
    close: Callable[[], None]


def start_loopback_listener(
    state: str,
    *,
    timeout: float = 300.0,
    host: str = "127.0.0.1",
    path: str = "/cb",
) -> LoopbackCallback:
    """Open the listener *before* the browser so the port is known.

    Returns a handle whose ``wait`` blocks until the browser comes back,
    the user denies, or the window closes.  The state is compared with
    :func:`hmac.compare_digest` before the code is trusted.
    """
    received: Dict[str, Any] = {}
    done = threading.Event()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, 0))
    server.listen(1)
    port = server.getsockname()[1]
    server.settimeout(1.0)

    def serve() -> None:
        deadline = time.time() + timeout
        while time.time() < deadline and not done.is_set():
            try:
                conn, _ = server.accept()
            except (socket.timeout, OSError):
                continue
            try:
                raw = conn.recv(8192).decode("latin-1")
                request_line = raw.split("\r\n", 1)[0]
                parts = request_line.split(" ")
                target = parts[1] if len(parts) > 1 else "/"
                parsed = urllib.parse.urlsplit(target)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path != path:
                    conn.sendall(
                        b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
                    )
                    conn.close()
                    continue

                got_state = (query.get("state") or [""])[0]
                if not hmac.compare_digest(str(got_state), str(state)):
                    received["error"] = "state mismatch"
                else:
                    error = (query.get("error") or [""])[0]
                    if error:
                        received["error"] = error
                    else:
                        received["code"] = (query.get("code") or [""])[0]
                # Always answer the browser: never leave a blank window.
                page = _CALLBACK_PAGE.format(
                    heading="OrcaRouter connected"
                    if "code" in received
                    else "OrcaRouter authorization not completed",
                    detail="You can close this tab and return to the tuner.",
                ).encode("utf-8")
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                    b"Content-Length: " + str(len(page)).encode() + b"\r\n\r\n" + page
                )
                conn.close()
            except OSError:
                pass
            done.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def wait(wait_timeout: float = timeout) -> Optional[Tuple[Optional[str], Optional[str]]]:
        if not done.wait(wait_timeout):
            return None
        return received.get("code"), received.get("error")

    def close() -> None:
        done.set()
        try:
            server.close()
        except OSError:
            pass

    return LoopbackCallback(
        callback_url=f"http://{host}:{port}{path}", wait=wait, close=close
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrcaRouterCredential:
    """The single credential shape both adapters produce.

    Downstream code (transport, catalog, doctor) only ever sees this, so it
    cannot tell - and must not care - which entry point produced it.
    """

    api_key: str
    source: str
    generation: int = 0
    scope: str = REQUESTED_SCOPE
    user_id: str = ""
    obtained_at: float = 0.0

    @property
    def is_set(self) -> bool:
        return bool(self.api_key)

    def masked(self) -> str:
        return redact_secret(self.api_key)


class CredentialSource(ABC):
    """The seam: obtain-or-read a credential.

    ``ApiKeyCredentialSource`` and ``PKCESource`` are the two adapters.
    Nothing else in the provider knows how a key was acquired.
    """

    source_id: str = ""

    @abstractmethod
    def acquire(
        self, *, on_hint: Optional[Callable[[str], None]] = None
    ) -> OrcaRouterCredential:
        """Return a usable credential or raise :class:`OrcaRouterError`."""


@dataclass
class ApiKeyCredentialSource(CredentialSource):
    """Adapter 1: a key the user already holds and pasted in."""

    api_key: str
    generation: int = 0
    source_id: str = field(default="api_key", init=False)

    def acquire(
        self, *, on_hint: Optional[Callable[[str], None]] = None
    ) -> OrcaRouterCredential:
        key = (self.api_key or "").strip()
        if not key:
            raise OrcaRouterError(
                "No OrcaRouter API key set. Paste a key from "
                "https://www.orcarouter.ai/console or use the login button."
            )
        if not key.startswith(_KEY_PREFIX):
            # A format hint only.  An sk-orca- prefix is not proof of
            # validity, so this is a warning rather than a rejection: the
            # first real request establishes validity.
            if on_hint:
                on_hint(
                    "This key does not start with 'sk-orca-'. "
                    "OrcaRouter keys normally do - check for a copy/paste slip."
                )
        return OrcaRouterCredential(
            api_key=key,
            source=self.source_id,
            generation=self.generation,
            obtained_at=time.time(),
        )


@dataclass
class PKCECredentialSource(CredentialSource):
    """Adapter 2: OAuth 2.0 + PKCE login, which mints the same kind of key."""

    origins: OrcaRouterOrigins
    flow: str = "A"
    open_browser: Callable[[str], Any] = webbrowser.open
    prompt_for_code: Callable[[], str] = input
    timeout: float = 300.0
    generation: int = 0
    source_id: str = field(default="pkce", init=False)

    def acquire(
        self, *, on_hint: Optional[Callable[[str], None]] = None
    ) -> OrcaRouterCredential:
        pair = new_pkce_pair()
        if self.flow.upper() == "B":
            code = self._authorize_oob(pair, on_hint)
        else:
            code = self._authorize_loopback(pair, on_hint)

        result = exchange_code(self.origins, pair, code, timeout=self.timeout)
        warning = _scope_warning(result)
        if warning and on_hint:
            on_hint(warning)
        return OrcaRouterCredential(
            api_key=result.key,
            source=self.source_id,
            generation=self.generation,
            scope=result.scope,
            user_id=result.user_id,
            obtained_at=time.time(),
        )

    def _authorize_oob(
        self, pair: PKCEPair, on_hint: Optional[Callable[[str], None]]
    ) -> str:
        """Flow B - the code is shown to the user and pasted back."""
        validate_callback_url("oob")
        url = build_authorize_url(self.origins, pair, callback_url="oob")
        if on_hint:
            on_hint(
                "Open this URL, approve access, then paste the code OrcaRouter "
                f"shows you:\n{url}"
            )
        try:
            self.open_browser(url)
        except Exception:
            pass  # The URL was printed; a headless box simply skips this.
        try:
            code = str(self.prompt_for_code() or "").strip()
        except EOFError as exc:
            raise OrcaRouterPKCEError(
                "No authorization code entered; the login was cancelled."
            ) from exc
        if not code:
            raise OrcaRouterPKCEError("No authorization code entered; cancelled.")
        return code

    def _authorize_loopback(
        self, pair: PKCEPair, on_hint: Optional[Callable[[str], None]]
    ) -> str:
        """Flow A - a loopback listener receives the redirect automatically."""
        handle = start_loopback_listener(pair.state, timeout=self.timeout)
        try:
            callback = validate_callback_url(handle.callback_url)
            if not callback_url_matches_origins(callback, self.origins):
                raise OrcaRouterPKCEError(
                    "Refusing to send the authorization code back to the "
                    "OrcaRouter auth origin."
                )
            url = build_authorize_url(self.origins, pair, callback_url=callback)
            if on_hint:
                on_hint(f"Opening your browser to authorize with OrcaRouter:\n{url}")
            try:
                self.open_browser(url)
            except Exception:
                pass
            outcome = handle.wait(self.timeout)
        finally:
            handle.close()

        if outcome is None:
            raise OrcaRouterPKCEError(
                "The OrcaRouter login timed out before it was approved. "
                "Start it again when you are ready."
            )
        code, error = outcome
        if error:
            raise OrcaRouterPKCEError(
                f"The OrcaRouter login was not completed ({error})."
            )
        if not code:
            raise OrcaRouterPKCEError("OrcaRouter returned no authorization code.")
        return code


# ---------------------------------------------------------------------------
# Credential lifecycle
# ---------------------------------------------------------------------------

@dataclass
class CredentialRecord:
    """Persisted credential plus the generation that produced it."""

    api_key: str = ""
    source: str = ""
    scope: str = ""
    generation: int = 0
    state: str = REAUTH_OK

    @property
    def is_set(self) -> bool:
        return bool(self.api_key)

    def masked(self) -> str:
        return redact_secret(self.api_key)


class OrcaRouterCredentialStore:
    """Credential lifecycle over the project's own config store.

    The key lives where every other provider secret lives (``config.json``
    and the matching environment variable).  No second secret store is
    introduced.  A durable key is *not* a refresh token, so a 401 marks
    exactly the generation that made the rejected request.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config

    @property
    def config(self) -> Dict[str, Any]:
        if self._config is not None:
            return self._config
        from core.config import CONFIG

        return CONFIG

    # -- read -------------------------------------------------------------
    def load(self) -> CredentialRecord:
        cfg = self.config
        return CredentialRecord(
            api_key=str(cfg.get("ORCAROUTER_API_KEY", "") or "").strip(),
            source=str(cfg.get("ORCAROUTER_AUTH_METHOD", "") or "").strip(),
            scope=str(cfg.get("ORCAROUTER_SCOPE", "") or "").strip(),
            generation=int(cfg.get("ORCAROUTER_CREDENTIAL_GENERATION", 0) or 0),
            state=str(cfg.get("ORCAROUTER_AUTH_STATE", REAUTH_OK) or REAUTH_OK),
        )

    def credential(self) -> OrcaRouterCredential:
        record = self.load()
        return OrcaRouterCredential(
            api_key=record.api_key,
            source=record.source,
            generation=record.generation,
            scope=record.scope or REQUESTED_SCOPE,
        )

    # -- write ------------------------------------------------------------
    def save(self, credential: OrcaRouterCredential) -> CredentialRecord:
        """Persist a credential, bumping the generation.

        Bumping on every successful write is what makes a late 401 from an
        older request unable to mark the new credential broken.
        """
        cfg = self.config
        record = self.load()
        generation = record.generation + 1
        cfg["ORCAROUTER_API_KEY"] = credential.api_key
        cfg["ORCAROUTER_AUTH_METHOD"] = credential.source
        cfg["ORCAROUTER_SCOPE"] = credential.scope
        cfg["ORCAROUTER_AUTH_STATE"] = REAUTH_OK
        cfg["ORCAROUTER_CREDENTIAL_GENERATION"] = generation
        return CredentialRecord(
            api_key=credential.api_key,
            source=credential.source,
            scope=credential.scope,
            generation=generation,
            state=REAUTH_OK,
        )

    def save_credential(
        self, credential: OrcaRouterCredential, *, persist: Callable[..., Any]
    ) -> CredentialRecord:
        """Persist through a caller-supplied writer (the config file)."""
        record = self.save(credential)
        persist()
        return record

    def clear(self) -> CredentialRecord:
        cfg = self.config
        cfg["ORCAROUTER_API_KEY"] = ""
        cfg["ORCAROUTER_AUTH_METHOD"] = ""
        cfg["ORCAROUTER_SCOPE"] = ""
        cfg["ORCAROUTER_AUTH_STATE"] = REAUTH_OK
        cfg["ORCAROUTER_CREDENTIAL_GENERATION"] = (
            int(cfg.get("ORCAROUTER_CREDENTIAL_GENERATION", 0) or 0) + 1
        )
        return self.load()

    # -- upstream feedback ------------------------------------------------
    def mark_needs_reauth(self, generation: int) -> bool:
        """Mark the rejected generation ``needs_reauth``.

        Returns True when the marker was applied.  A response from an older
        generation is ignored so it cannot poison a freshly reauthorized key.
        The stale secret is deliberately left in place - only a successful
        new login replaces it.
        """
        cfg = self.config
        current = int(cfg.get("ORCAROUTER_CREDENTIAL_GENERATION", 0) or 0)
        if generation != current:
            return False
        cfg["ORCAROUTER_AUTH_STATE"] = REAUTH_NEEDED
        return True

    def is_terminal_reauth(self, generation: int) -> bool:
        cfg = self.config
        return (
            str(cfg.get("ORCAROUTER_AUTH_STATE", REAUTH_OK)) == REAUTH_NEEDED
            and int(cfg.get("ORCAROUTER_CREDENTIAL_GENERATION", 0) or 0) == generation
        )


def effective_api_key(config: Optional[Dict[str, Any]]) -> str:
    """The OrcaRouter key for ``config``, falling back to the generic slot.

    A user who set ``LLM_PROVIDER=orcarouter`` with ``LLM_API_KEY=sk-orca-...``
    keeps working, but the dedicated key wins when both are present.
    """
    cfg = config if config is not None else {}
    dedicated = str(cfg.get("ORCAROUTER_API_KEY", "") or "").strip()
    if dedicated:
        return dedicated
    generic = str(cfg.get("LLM_API_KEY", "") or "").strip()
    if generic and generic != "your-api-key-here" and generic.startswith(_KEY_PREFIX):
        return generic
    return ""


def resolve_credential(
    config: Optional[Dict[str, Any]] = None,
    *,
    explicit_key: str = "",
) -> CredentialRecord:
    """Read the stored credential without acquiring a new one."""
    cfg = config if config is not None else {}
    key = effective_api_key(cfg)
    if not key:
        candidate = str(explicit_key or "").strip()
        if candidate and candidate != "your-api-key-here":
            key = candidate
    return CredentialRecord(
        api_key=key,
        source=str(cfg.get("ORCAROUTER_AUTH_METHOD", "") or "").strip(),
        scope=str(cfg.get("ORCAROUTER_SCOPE", "") or "").strip(),
        generation=int(cfg.get("ORCAROUTER_CREDENTIAL_GENERATION", 0) or 0),
        state=str(cfg.get("ORCAROUTER_AUTH_STATE", REAUTH_OK) or REAUTH_OK),
    )


def build_credential_source(
    method: str,
    *,
    config: Optional[Dict[str, Any]],
    origins: OrcaRouterOrigins,
) -> CredentialSource:
    """Pick the adapter for ``method`` - the two entries on the same seam.

    ``orcarouter`` (and any alias) yields the API-key adapter;
    ``orcarouter_oauth`` yields the PKCE adapter.  Both return an
    :class:`OrcaRouterCredential`.
    """
    normalized = normalize_orcarouter_method(method)
    if normalized == PROVIDER_ID_PKCE:
        return PKCECredentialSource(origins=origins)
    cfg = config if config is not None else {}
    return ApiKeyCredentialSource(
        api_key=str(cfg.get("ORCAROUTER_API_KEY", "") or ""),
        generation=int(cfg.get("ORCAROUTER_CREDENTIAL_GENERATION", 0) or 0),
    )


_API_KEY_ALIASES = {"orcarouter", "orca", "orcarouter_api", "orcarouter_key", "orca_api"}
_PKCE_ALIASES = {
    "orcarouter_oauth",
    "orcarouter_oauth2",
    "orcarouter_pkce",
    "orcarouter_login",
    "orca_oauth",
    "orca_pkce",
}


def normalize_orcarouter_method(value: Optional[str]) -> str:
    """Map a provider value onto one of the two OrcaRouter entry points."""
    normalized = str(value or "").strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    if normalized in _PKCE_ALIASES:
        return PROVIDER_ID_PKCE
    return PROVIDER_ID


def is_orcarouter_provider(value: Optional[str]) -> bool:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return normalized in _API_KEY_ALIASES or normalized in _PKCE_ALIASES


def provider_registry() -> List[Dict[str, str]]:
    """The provider choices this project exposes, OrcaRouter included."""
    return [
        {"id": PROVIDER_ID, "label": "OrcaRouter - API", "icon": ORCAROUTER_ICON_URL},
        {
            "id": PROVIDER_ID_PKCE,
            "label": "OrcaRouter - Auth",
            "icon": ORCAROUTER_ICON_URL,
        },
    ]


# The official classic mark, used for both authentication entries.
ORCAROUTER_ICON_URL = "https://www.orcarouter.ai/orca-logo-classic.png"


# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

CHAT_ENDPOINT_TYPES = ("openai", "anthropic", "gemini", "openai-response")
NON_CHAT_ENDPOINT_TYPES = (
    "image-generation",
    "openai-video",
    "jina-rerank",
    "embeddings",
)

CAPABILITY_CHAT = "chat"
CAPABILITY_EMBEDDING = "embedding"
CAPABILITY_IMAGE = "image"
CAPABILITY_VIDEO = "video"
CAPABILITY_RERANK = "rerank"

# Capability -> the query value the catalog understands.
_CAPABILITY_QUERY = {
    CAPABILITY_CHAT: "chat",
    CAPABILITY_EMBEDDING: "embedding",
    CAPABILITY_IMAGE: "image",
}


@dataclass(frozen=True)
class ModelInfo:
    """One catalog entry, reduced to the minimum the picker needs."""

    id: str
    name: str = ""
    context_length: Optional[int] = None
    input_modalities: Tuple[str, ...] = ()
    endpoint_types: Tuple[str, ...] = ()
    reasoning_efforts: Tuple[str, ...] = ()

    @property
    def supports_image_input(self) -> bool:
        return "image" in self.input_modalities

    @property
    def supports_audio_input(self) -> bool:
        return "audio" in self.input_modalities

    @property
    def supports_video_input(self) -> bool:
        return "video" in self.input_modalities

    def supports_modalities(self, required: Iterable[str]) -> bool:
        """Fail closed: an undeclared modality is not a supported modality."""
        needed = {str(item).strip().lower() for item in required if str(item).strip()}
        if not needed:
            return True
        return needed.issubset(set(self.input_modalities))


def _coerce_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _as_str_tuple(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if isinstance(item, (str, int, float)))
    return ()


def parse_model_record(record: Any) -> Optional[ModelInfo]:
    """Reduce one catalog record to a :class:`ModelInfo`.

    Malformed records are dropped rather than guessed at.
    """
    if not isinstance(record, dict):
        return None
    model_id = str(record.get("id", "") or "").strip()
    if not model_id:
        return None

    architecture = record.get("architecture")
    architecture = architecture if isinstance(architecture, dict) else {}
    modalities = tuple(
        item.lower() for item in _as_str_tuple(architecture.get("input_modalities"))
    )
    if not modalities:
        modalities = tuple(
            item.lower()
            for item in _as_str_tuple(record.get("input_modalities"))
        )

    context_length = _coerce_int(record.get("context_length"))
    if context_length is None:
        top_provider = record.get("top_provider")
        if isinstance(top_provider, dict):
            context_length = _coerce_int(top_provider.get("context_length"))

    reasoning = _as_str_tuple(record.get("reasoning_efforts"))
    if not reasoning and isinstance(record.get("reasoning"), dict):
        reasoning = _as_str_tuple(record["reasoning"].get("efforts"))

    return ModelInfo(
        id=model_id,
        name=str(record.get("name", "") or model_id),
        context_length=context_length,
        input_modalities=modalities,
        endpoint_types=tuple(
            item.lower() for item in _as_str_tuple(record.get("supported_endpoint_types"))
        ),
        reasoning_efforts=tuple(reasoning),
    )


def _is_text_chat_model(model: ModelInfo) -> bool:
    """Text chat requires a speakeable text endpoint and no media-only route."""
    if not model.endpoint_types:
        return False
    if any(kind in model.endpoint_types for kind in NON_CHAT_ENDPOINT_TYPES):
        return False
    return any(kind in model.endpoint_types for kind in CHAT_ENDPOINT_TYPES)


def filter_models(
    models: Sequence[ModelInfo],
    capability: str,
    *,
    required_modalities: Iterable[str] = (),
) -> List[ModelInfo]:
    """Filter a catalog for one AI input point.

    Each entry point filters independently; a model never leaks into a
    selector because its name looked plausible.
    """
    capability = str(capability or CAPABILITY_CHAT).strip().lower()
    required = tuple(required_modalities)

    if capability == CAPABILITY_CHAT:
        selected = [m for m in models if _is_text_chat_model(m)]
        if required:
            # Multi-modal understanding: chat first, then an explicit
            # modality declaration.  Undeclared means excluded.
            selected = [m for m in selected if m.supports_modalities(required)]
        return selected

    if capability == CAPABILITY_EMBEDDING:
        return [m for m in models if "embeddings" in m.endpoint_types]

    if capability == CAPABILITY_IMAGE:
        return [m for m in models if "image-generation" in m.endpoint_types]

    if capability == CAPABILITY_VIDEO:
        return [m for m in models if "openai-video" in m.endpoint_types]

    if capability == CAPABILITY_RERANK:
        return [m for m in models if "jina-rerank" in m.endpoint_types]

    return []


_MODALITY_KEYWORDS = {
    "image": ("image", "vision", "multimodal", "vl"),
    "audio": ("audio", "speech", "voice"),
    "video": ("video",),
}


def modalities_for_attachment_kind(kind: str) -> Tuple[str, ...]:
    """Map an attachment/entry-point kind onto required input modalities."""
    return _MODALITY_KEYWORDS.get(str(kind or "").strip().lower(), ())


@dataclass(frozen=True)
class VerifiedModel:
    """A cold-start seed entry, with the metadata that must survive.

    Every field here is verified against the live catalog; the seed exists so
    a fresh install is usable during a catalog outage, not to invent models.
    """

    info: ModelInfo
    source: str
    verified_at: str


def _seed(
    model_id: str,
    *,
    context_length: Optional[int],
    modalities: Sequence[str],
    endpoints: Sequence[str],
    reasoning: Sequence[str] = (),
    source: str = "https://api.orcarouter.ai/v1/models",
) -> VerifiedModel:
    return VerifiedModel(
        info=ModelInfo(
            id=model_id,
            name=model_id,
            context_length=context_length,
            input_modalities=tuple(modalities),
            endpoint_types=tuple(endpoints),
            reasoning_efforts=tuple(reasoning),
        ),
        source=source,
        verified_at=VERIFIED_AT,
    )


# Date the seed entries below were checked against the live catalog.
VERIFIED_AT = "2026-09-11"

VERIFIED_FALLBACK_CATALOG: Tuple[VerifiedModel, ...] = (
    _seed(
        "openai/gpt-5.5",
        context_length=1_000_000,
        modalities=("file", "image", "text"),
        endpoints=("openai", "openai-response"),
        # Verified reasoning ladder; must not be flattened by live discovery.
        reasoning=("low", "medium", "high", "xhigh"),
    ),
    _seed(
        "anthropic/claude-opus-4.8",
        context_length=200_000,
        modalities=("text", "image", "file"),
        endpoints=("openai", "anthropic", "openai-response"),
        reasoning=("low", "medium", "high"),
    ),
    _seed(
        "google/gemini-3.5-flash",
        context_length=1_048_576,
        modalities=("text", "image", "file"),
        endpoints=("openai", "gemini"),
        reasoning=("low", "medium", "high"),
    ),
    _seed(
        "deepseek/deepseek-v4-pro",
        context_length=1_048_576,
        modalities=("text",),
        endpoints=("openai", "openai-response"),
        reasoning=("low", "medium", "high"),
    ),
    _seed(
        "orcarouter/auto",
        context_length=None,
        modalities=("text",),
        endpoints=("openai", "openai-response", "anthropic", "gemini"),
    ),
)


def fallback_catalog() -> List[ModelInfo]:
    """The verified cold-start catalog, used only when discovery fails."""
    return [entry.info for entry in VERIFIED_FALLBACK_CATALOG]


@dataclass
class CatalogResult:
    """A catalog plus where it came from, so the UI can show its state."""

    models: List[ModelInfo]
    source: str  # "live" | "fallback" | "last_known_good"
    degraded: bool
    error: str = ""
    checked_at: float = 0.0

    def for_capability(
        self, capability: str, *, required_modalities: Iterable[str] = ()
    ) -> List[ModelInfo]:
        return filter_models(
            self.models, capability, required_modalities=required_modalities
        )

    def id_set(self) -> set:
        return {model.id for model in self.models}


def _fetch_json(
    url: str,
    headers: Dict[str, str],
    *,
    timeout: float,
    opener: Optional[Callable[..., Any]] = None,
) -> Any:
    request = urllib.request.Request(url, headers=headers, method="GET")
    open_fn = urllib.request.urlopen if opener is None else opener
    with open_fn(request, timeout=timeout) as response:
        raw = response.read(CATALOG_MAX_BYTES)
    return json.loads(raw.decode("utf-8"))


def fetch_catalog(
    credential: OrcaRouterCredential,
    origins: OrcaRouterOrigins,
    *,
    capability: Optional[str] = CAPABILITY_CHAT,
    timeout: float = CATALOG_TIMEOUT,
    opener: Optional[Callable[..., Any]] = None,
    last_known_good: Optional[Sequence[ModelInfo]] = None,
) -> CatalogResult:
    """Discover the models this key can actually call.

    Live discovery is authoritative when it succeeds.  When it fails the
    verified seed (or a previously discovered catalog) is used, clearly
    marked degraded.  A seed is never merged into a successful live result.
    """
    if not credential.is_set:
        return CatalogResult(
            models=list(last_known_good or fallback_catalog()),
            source="last_known_good" if last_known_good else "fallback",
            degraded=True,
            error="No OrcaRouter credential is set.",
        )

    params = ""
    if capability and capability in _CAPABILITY_QUERY:
        params = "?" + urllib.parse.urlencode(
            {"capability": _CAPABILITY_QUERY[capability]}
        )
    url = f"{origins.models_url}{params}"
    try:
        payload = _fetch_json(
            url,
            {"Authorization": f"Bearer {credential.api_key}", "Accept": "application/json"},
            timeout=timeout,
            opener=opener,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return CatalogResult(
                models=list(last_known_good or fallback_catalog()),
                source="last_known_good" if last_known_good else "fallback",
                degraded=True,
                error=(
                    "OrcaRouter rejected the stored key (HTTP 401). "
                    "Connect again to issue a new one."
                ),
            )
        return CatalogResult(
            models=list(last_known_good or fallback_catalog()),
            source="last_known_good" if last_known_good else "fallback",
            degraded=True,
            error=f"Model discovery failed with HTTP {exc.code}.",
        )
    except Exception as exc:  # network, DNS, timeout, malformed JSON
        return CatalogResult(
            models=list(last_known_good or fallback_catalog()),
            source="last_known_good" if last_known_good else "fallback",
            degraded=True,
            error=scrub(f"Model discovery failed: {exc}", credential.api_key),
        )

    records = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return CatalogResult(
            models=list(last_known_good or fallback_catalog()),
            source="last_known_good" if last_known_good else "fallback",
            degraded=True,
            error="Model discovery returned an unexpected payload shape.",
        )

    models: List[ModelInfo] = []
    seen = set()
    for record in records[:CATALOG_MAX_ITEMS]:
        info = parse_model_record(record)
        if info is None or info.id in seen:
            continue
        seen.add(info.id)
        models.append(info)

    if not models:
        return CatalogResult(
            models=list(last_known_good or fallback_catalog()),
            source="last_known_good" if last_known_good else "fallback",
            degraded=True,
            error="Model discovery returned no usable models.",
        )

    return CatalogResult(
        models=models,
        source="live",
        degraded=False,
        checked_at=time.time(),
    )


__all__ = [
    "APP_NAME",
    "ApiKeyCredentialSource",
    "CAPABILITY_CHAT",
    "CAPABILITY_EMBEDDING",
    "CAPABILITY_IMAGE",
    "CAPABILITY_RERANK",
    "CAPABILITY_VIDEO",
    "CatalogResult",
    "CredentialRecord",
    "CredentialSource",
    "DEFAULT_API_BASE",
    "DEFAULT_AUTH_BASE",
    "ModelInfo",
    "ORCAROUTER_ICON_URL",
    "OrcaRouterAuthError",
    "OrcaRouterCredential",
    "OrcaRouterCredentialStore",
    "OrcaRouterError",
    "OrcaRouterOrigins",
    "OrcaRouterPKCEError",
    "PKCECredentialSource",
    "PKCEPair",
    "PROVIDER_ID",
    "PROVIDER_ID_PKCE",
    "REAUTH_NEEDED",
    "REAUTH_OK",
    "REQUESTED_SCOPE",
    "VERIFIED_FALLBACK_CATALOG",
    "VERIFIED_AT",
    "b64url",
    "build_authorize_url",
    "build_credential_source",
    "exchange_code",
    "fallback_catalog",
    "fetch_catalog",
    "filter_models",
    "is_orcarouter_provider",
    "modalities_for_attachment_kind",
    "new_pkce_pair",
    "normalize_orcarouter_method",
    "parse_model_record",
    "provider_registry",
    "redact_secret",
    "resolve_origins",
    "scrub",
    "start_loopback_listener",
    "validate_callback_url",
]
