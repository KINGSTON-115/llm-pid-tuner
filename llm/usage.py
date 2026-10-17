"""Normalize reported usage without substituting estimates for unknown tokens."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


TOKEN_FIELDS = (
    "input_tokens", "output_tokens", "cached_input_tokens",
    "cache_creation_input_tokens", "reasoning_tokens",
)


def _get(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_usage(raw: Any, *, anthropic: bool = False) -> dict[str, int | None]:
    if anthropic:
        uncached = _count(_get(raw, "input_tokens"))
        cached = _count(_get(raw, "cache_read_input_tokens"))
        created = _count(_get(raw, "cache_creation_input_tokens"))
        # Anthropic input_tokens excludes the separately reported cache buckets.
        input_tokens = None if uncached is None else uncached + (cached or 0) + (created or 0)
        return dict(input_tokens=input_tokens, output_tokens=_count(_get(raw, "output_tokens")),
                    cached_input_tokens=cached, cache_creation_input_tokens=created,
                    reasoning_tokens=None)
    return dict(
        input_tokens=_count(_get(raw, "prompt_tokens")),
        output_tokens=_count(_get(raw, "completion_tokens")),
        cached_input_tokens=_count(_get(_get(raw, "prompt_tokens_details"), "cached_tokens")),
        cache_creation_input_tokens=None,
        reasoning_tokens=_count(_get(_get(raw, "completion_tokens_details"), "reasoning_tokens")),
    )


def usage_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Sums are reported subtotals when any attempt has missing usage."""
    totals = {}
    for key in TOKEN_FIELDS:
        values = [record["usage"][key] for record in attempts if record["usage"].get(key) is not None]
        totals[key] = sum(values) if values else (None if attempts else 0)
    complete = sum(
        record["completed"] and record["usage_final"]
        and all(record["usage"].get(k) is not None for k in ("input_tokens", "output_tokens"))
        for record in attempts
    )
    return {
        "transport_attempts": len(attempts), "attempts_with_complete_usage": complete,
        "tracking_available": True,
        "logical_requests": len({r["request"] for r in attempts}),
        "usage_complete": complete == len(attempts), "reported_tokens": totals,
        "input_chars": sum(r["input_chars"] for r in attempts),
        "output_chars": sum(r["output_chars"] for r in attempts),
        "attempts": deepcopy(attempts),
    }


def rejects_stream_usage(exc: Exception) -> bool:
    """Only negotiate a rejected option; never retry a generic HTTP 400 here."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    message = str(exc).lower()
    response = getattr(exc, "response", None)
    if status in (400, 422) and response is not None:
        message += " " + str(getattr(response, "text", "")).lower()
    return (
        (status in (400, 422) or isinstance(exc, TypeError))
        and any(word in message for word in ("stream_options", "include_usage"))
        and any(word in message for word in ("unsupported", "not supported", "unknown", "unrecognized", "unexpected", "extra", "not permitted"))
    )
