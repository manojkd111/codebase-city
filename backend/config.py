"""Runtime settings, read from environment variables each time they are requested."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    max_files: int
    max_file_bytes: int
    allow_local_sources: bool
    workdir: str | None
    max_concurrent_ingests: int
    # Agents (Week 3). `model` writes fixes; `review_model` runs the coordinator and specialists.
    # A wrong finding is caught at triage, a bad fix lands in a PR, so only fixes get Opus by default
    # (benchmark run 10: Sonnet 5.5 reviewers 13/13 at $0.18 vs Opus 5.5 13/13 at $0.42).
    model: str = "claude-opus-5-5"
    review_model: str = "claude-sonnet-5-5"
    effort: str = "high"
    coordinator_effort: str = "low"
    agent_files: int = 5  # files each specialist is assigned
    agent_max_turns: int = 30
    max_concurrent_agents: int = 3
    # Workers + live events across processes (Week 4). Empty = in-process.
    redis_url: str | None = None
    # Fixer (Week 6)
    sandbox_enabled: bool = True  # CBC_SANDBOX=off: never run repo tests (fixes are marked untested)
    sandbox_image: str = "python:3.12-slim"
    sandbox_timeout_s: int = 600
    fixer_max_turns: int = 40
    github_token: str | None = None


def get_settings() -> Settings:
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///./codebase_city.db"),
        max_files=int(os.getenv("CBC_MAX_FILES", "5000")),
        max_file_bytes=int(os.getenv("CBC_MAX_FILE_BYTES", str(512 * 1024))),
        # Local paths are only accepted from the API when explicitly enabled (dev/tests).
        allow_local_sources=os.getenv("CBC_ALLOW_LOCAL_SOURCES") == "1",
        workdir=os.getenv("CBC_WORKDIR"),
        max_concurrent_ingests=int(os.getenv("CBC_MAX_CONCURRENT_INGESTS", "2")),
        model=os.getenv("CBC_MODEL", "claude-opus-5-5"),
        review_model=os.getenv("CBC_REVIEW_MODEL", "claude-sonnet-5-5"),
        effort=os.getenv("CBC_EFFORT", "high"),
        coordinator_effort=os.getenv("CBC_COORDINATOR_EFFORT", "low"),
        agent_files=int(os.getenv("CBC_AGENT_FILES", "5")),
        agent_max_turns=int(os.getenv("CBC_AGENT_MAX_TURNS", "30")),
        max_concurrent_agents=int(os.getenv("CBC_MAX_CONCURRENT_AGENTS", "3")),
        redis_url=os.getenv("REDIS_URL") or None,
        sandbox_enabled=os.getenv("CBC_SANDBOX", "docker").lower() != "off",
        sandbox_image=os.getenv("CBC_SANDBOX_IMAGE", "python:3.12-slim"),
        sandbox_timeout_s=int(os.getenv("CBC_SANDBOX_TIMEOUT_S", "600")),
        fixer_max_turns=int(os.getenv("CBC_FIXER_MAX_TURNS", "40")),
        # Only needed to open PRs. Never sent to the model or into the sandbox.
        github_token=os.getenv("GITHUB_TOKEN") or None,
    )
