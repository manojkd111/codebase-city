"""The one place that talks to the Claude API.

Callers pass a client so tests can inject a scripted fake; production uses `make_client()`.
Every request gets: adaptive thinking (always on for Opus 5.5), an explicit effort level,
automatic prompt caching (agent loops resend a growing prefix each turn), and server-side
refusal fallback so a declined turn is retried on another model instead of ending the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

PROMPTS = Path(__file__).parent / "prompts"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(RuntimeError):
    """No credentials or the API is unreachable; the run fails with this message."""


def make_client() -> Any:
    try:
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile.
        return anthropic.Anthropic(max_retries=4)
    except anthropic.AnthropicError as exc:
        raise LLMUnavailable(
            "No Anthropic credentials found. Set ANTHROPIC_API_KEY (or run `ant auth login`) and restart the API."
        ) from exc


def prompt(name: str, **values: str) -> str:
    # str.replace, not str.format: values can contain braces (paths, code).
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace("{" + k + "}", v)
    return text


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, response: Any) -> None:
        u = getattr(response, "usage", None)
        if u is None:
            return
        # Cached reads/writes are input too; count them so cost estimates aren't flattering.
        self.input_tokens += (
            (getattr(u, "input_tokens", 0) or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
        )
        self.output_tokens += getattr(u, "output_tokens", 0) or 0


def create(client: Any, *, model: str, effort: str, max_tokens: int = 16000, **kwargs: Any) -> Any:
    try:
        return client.beta.messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": effort, **kwargs.pop("output_config", {})},
            cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA],
            fallbacks="default",
            **kwargs,
        )
    except anthropic.AuthenticationError as exc:
        raise LLMUnavailable("Anthropic API rejected the credentials (401). Check ANTHROPIC_API_KEY.") from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMUnavailable(f"Anthropic API permission denied: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable("Could not reach the Anthropic API (network error).") from exc


def text_of(response: Any) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
