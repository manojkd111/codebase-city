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


# $ per million tokens: (input, output, cache write, cache read). Cache writes use the default
# 5-minute TTL (1.25x input). Turns served by a refusal fallback model bill at that model's rates;
# they're rare, so costs are computed at the configured model's prices.
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
}


@dataclass
class Usage:
    """Token counts split the way they're billed. Agent loops resend the whole history every turn,
    so most input is cache reads at a fraction of the input price; one total would overstate cost ~5x.
    """

    uncached_input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output_tokens: int = 0

    @property
    def input_tokens(self) -> int:
        """All input, cached or not (what the model read)."""
        return self.uncached_input + self.cache_write + self.cache_read

    def add(self, response: Any) -> None:
        u = getattr(response, "usage", None)
        if u is None:
            return
        self.uncached_input += getattr(u, "input_tokens", 0) or 0
        self.cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.uncached_input + other.uncached_input,
            self.cache_write + other.cache_write,
            self.cache_read + other.cache_read,
            self.output_tokens + other.output_tokens,
        )

    def cost_usd(self, model: str) -> float | None:
        """Billed cost at list price, or None for a model without a price entry."""
        p = PRICES.get(model)
        if p is None:
            return None
        cost = self.uncached_input * p[0] + self.output_tokens * p[1] + self.cache_write * p[2] + self.cache_read * p[3]
        return round(cost / 1e6, 4)


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
