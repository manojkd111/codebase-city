"""Typed events: the single contract between the backend and the city.

Every payload class declares its wire `TYPE`. Producers emit payloads; the run
recorder wraps them in an `EventEnvelope` with a per-repo sequence number, stores it,
then publishes it, so any run can be replayed exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field


class Payload(BaseModel):
    TYPE: ClassVar[str]


class IngestStarted(Payload):
    TYPE: ClassVar[str] = "ingest.started"
    source: str


class IngestProgress(Payload):
    TYPE: ClassVar[str] = "ingest.progress"
    stage: str  # cloned | walked | churn | parsing | graph
    done: int = 0
    total: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)


class DistrictAdded(Payload):
    TYPE: ClassVar[str] = "city.district_added"
    name: str
    file_count: int
    total_bytes: int
    # folder path (posix dirname, "" for repo root) -> file count, so the frontend can lay out
    # one block per folder before any building arrives. Empty on events from before Week 2.
    folders: dict[str, int] = Field(default_factory=dict)


class BuildingAdded(Payload):
    TYPE: ClassVar[str] = "city.building_added"
    path: str
    district: str
    language: str
    size_bytes: int
    loc: int
    functions: int
    complexity_total: int
    complexity_max: int
    churn_30: int
    churn_90: int
    coverage: float | None  # None = unknown
    risk: float
    parse_error: bool = False
    is_test: bool = False


class RoadAdded(Payload):
    TYPE: ClassVar[str] = "city.road_added"
    src: str
    dst: str
    imports: int


class IngestCompleted(Payload):
    TYPE: ClassVar[str] = "ingest.completed"
    commit_sha: str | None
    files: int
    districts: int
    roads: int
    edges: int
    duration_s: float
    skipped: dict[str, int] = Field(default_factory=dict)
    truncated: bool = False


class IngestFailed(Payload):
    TYPE: ClassVar[str] = "ingest.failed"
    error: str


# --- Agents (Week 3). Drones in the city follow these. ---


class AnalysisStarted(Payload):
    TYPE: ClassVar[str] = "analysis.started"
    run_id: int
    model: str


class AgentDispatched(Payload):
    """The coordinator assigned files to a specialist; a drone takes off."""

    TYPE: ClassVar[str] = "agent.dispatched"
    run_id: int
    agent_id: str
    role: str  # security | tests | debt
    files: list[str]
    reason: str = ""


class AgentMoved(Payload):
    """A specialist is looking at a file; its drone flies to that building."""

    TYPE: ClassVar[str] = "agent.moved"
    run_id: int
    agent_id: str
    path: str
    action: str  # read | search | metrics | imports


class FindingSubmitted(Payload):
    """A finding that passed deterministic verification (file, lines and evidence all check out)."""

    TYPE: ClassVar[str] = "finding.submitted"
    run_id: int
    agent_id: str
    finding_id: int
    category: str
    severity: str
    file: str
    line_start: int
    line_end: int
    title: str
    confidence: float


class AgentTurn(Payload):
    """One model response for an agent: cumulative tokens so the UI shows live usage and cost."""

    TYPE: ClassVar[str] = "agent.turn"
    run_id: int
    agent_id: str
    turn: int
    max_turns: int
    input_tokens: int  # cumulative for this agent, cached or not
    output_tokens: int
    tool_calls: int  # requested in this turn
    duration_ms: int  # model latency for this turn
    # Defaults keep events stored before these fields existed valid; cost_usd None = no price known.
    cache_read_tokens: int = 0  # cumulative; the part of input_tokens billed at the cache-read rate
    cost_usd: float | None = None  # cumulative billed cost at list price


class AgentToolCall(Payload):
    """Every tool call an agent makes, with its outcome: the Mission Control activity feed."""

    TYPE: ClassVar[str] = "agent.tool_call"
    run_id: int
    agent_id: str
    tool: str
    target: str  # path, pattern, or finding title: what the call was about
    ok: bool
    duration_ms: int
    note: str = ""  # short outcome, e.g. "rejected: evidence not found"


class AgentFinished(Payload):
    TYPE: ClassVar[str] = "agent.finished"
    run_id: int
    agent_id: str
    findings: int
    rejected: int  # submissions that failed verification
    turns: int
    input_tokens: int
    output_tokens: int
    error: str | None = None
    cache_read_tokens: int = 0
    cost_usd: float | None = None


class AnalysisCompleted(Payload):
    TYPE: ClassVar[str] = "analysis.completed"
    run_id: int
    findings: int
    duration_s: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cost_usd: float | None = None  # whole run, coordinator included


class AnalysisFailed(Payload):
    TYPE: ClassVar[str] = "analysis.failed"
    run_id: int
    error: str


class FindingUpdated(Payload):
    """A human confirmed or rejected a finding, or a fix landed."""

    TYPE: ClassVar[str] = "finding.updated"
    finding_id: int
    status: str


# --- Fixer (Week 6) ---


class FixStarted(Payload):
    TYPE: ClassVar[str] = "fix.started"
    fix_id: int
    finding_id: int


class FixProgress(Payload):
    TYPE: ClassVar[str] = "fix.progress"
    fix_id: int
    finding_id: int
    step: str  # read | edit | test | ...
    detail: str = ""


class FixCompleted(Payload):
    TYPE: ClassVar[str] = "fix.completed"
    fix_id: int
    finding_id: int
    files_changed: int
    tests_passed: bool | None  # None = no test run (sandbox unavailable or no tests)


class FixFailed(Payload):
    TYPE: ClassVar[str] = "fix.failed"
    fix_id: int
    finding_id: int
    error: str


class PullRequestOpened(Payload):
    TYPE: ClassVar[str] = "fix.pr_opened"
    fix_id: int
    finding_id: int
    url: str


PAYLOAD_TYPES: dict[str, type[Payload]] = {
    cls.TYPE: cls
    for cls in (
        IngestStarted,
        IngestProgress,
        DistrictAdded,
        BuildingAdded,
        RoadAdded,
        IngestCompleted,
        IngestFailed,
        AnalysisStarted,
        AgentDispatched,
        AgentMoved,
        FindingSubmitted,
        AgentTurn,
        AgentToolCall,
        AgentFinished,
        AnalysisCompleted,
        AnalysisFailed,
        FindingUpdated,
        FixStarted,
        FixProgress,
        FixCompleted,
        FixFailed,
        PullRequestOpened,
    )
}


class EventEnvelope(BaseModel):
    repo_id: int
    seq: int
    type: str
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any]

    @classmethod
    def wrap(cls, repo_id: int, seq: int, payload: Payload) -> EventEnvelope:
        return cls(repo_id=repo_id, seq=seq, type=payload.TYPE, data=payload.model_dump())
