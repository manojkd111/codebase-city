"""Ingestion pipeline: source -> metrics -> import graph, streaming events as it goes.

Deterministic code does all the counting here so Claude only spends tokens on judgment.
The pipeline knows nothing about databases or sockets: it calls `emit(payload)` and
returns an `IngestResult`. The caller persists and publishes.
"""

from __future__ import annotations

import math
import posixpath
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.config import Settings
from backend.events.schema import (
    BuildingAdded,
    DistrictAdded,
    IngestProgress,
    IngestStarted,
    Payload,
    RoadAdded,
)

from .churn import compute_churn
from .complexity import measure
from .districts import assign_districts
from .graph import build_edges, roll_up_roads
from .languages import is_test_path
from .parse import ImportRef, parse_source
from .source import resolve_source
from .walk import walk_repo

Emit = Callable[[Payload], None]


def risk_score(complexity_total: int, churn_90: int, coverage: float | None) -> float:
    """Hotspot risk: complex code that changes often and is poorly tested.

    log1p keeps one monster file from dominating; unknown coverage is neutral (1.0),
    0% coverage scores 1.5x and full coverage 0.5x.
    """
    coverage_factor = 1.0 if coverage is None else 1.5 - coverage
    return round(math.log1p(complexity_total) * (1 + math.log1p(churn_90)) * coverage_factor, 4)


@dataclass
class IngestResult:
    source: str
    name: str
    commit_sha: str | None
    buildings: list[BuildingAdded]
    districts: list[DistrictAdded]
    roads: list[RoadAdded]
    edges: list[tuple[str, str]]
    external_imports: Counter[str]
    skipped: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    duration_s: float = 0.0


def run_ingest(source: str, emit: Emit, settings: Settings, *, allow_local: bool | None = None) -> IngestResult:
    started = time.monotonic()
    emit(IngestStarted(source=source))
    allow = settings.allow_local_sources if allow_local is None else allow_local

    with resolve_source(source, allow_local=allow, workdir=settings.workdir) as src:
        emit(IngestProgress(stage="cloned", detail={"name": src.name, "commit_sha": src.commit_sha}))

        walked = walk_repo(src.path, max_files=settings.max_files, max_file_bytes=settings.max_file_bytes)
        files = walked.files
        district_of = assign_districts([f.path for f in files])
        emit(IngestProgress(stage="walked", total=len(files), detail={"skipped": dict(walked.skipped)}))

        # Districts first, with sizes, so the frontend can lay out the whole map
        # before any building arrives.
        counts: Counter[str] = Counter()
        sizes: Counter[str] = Counter()
        folders: dict[str, Counter[str]] = {}
        for f in files:
            d = district_of[f.path]
            counts[d] += 1
            sizes[d] += f.size_bytes
            folders.setdefault(d, Counter())[posixpath.dirname(f.path)] += 1
        districts = [
            DistrictAdded(name=d, file_count=counts[d], total_bytes=sizes[d], folders=dict(sorted(folders[d].items())))
            for d in sorted(counts)
        ]
        for d in districts:
            emit(d)

        churn = compute_churn(src.path) if src.is_git else {}
        emit(IngestProgress(stage="churn", detail={"files_with_churn": len(churn)}))

        buildings: list[BuildingAdded] = []
        imports: dict[str, list[ImportRef]] = {}
        languages: dict[str, str] = {}
        # Emit district by district so the city visibly rises one neighbourhood at a time.
        ordered = sorted(files, key=lambda f: (district_of[f.path], f.path))
        for i, f in enumerate(ordered, 1):
            raw = f.abs_path.read_bytes()
            text = raw.decode("utf-8", "replace")
            cx = measure(f.path, text)
            functions, parse_error = cx.functions, False
            if f.grammar:
                parsed = parse_source(raw, f.grammar)
                imports[f.path] = parsed.imports
                functions = max(functions, parsed.functions)
                parse_error = parsed.has_error
            languages[f.path] = f.language
            c30, c90 = churn.get(f.path, (0, 0))
            coverage = None  # see docs/decisions/0003: untrusted tests only run in the sandbox
            b = BuildingAdded(
                path=f.path,
                district=district_of[f.path],
                language=f.language,
                size_bytes=f.size_bytes,
                loc=cx.nloc,
                functions=functions,
                complexity_total=cx.total,
                complexity_max=cx.max,
                churn_30=c30,
                churn_90=c90,
                coverage=coverage,
                risk=risk_score(cx.total, c90, coverage),
                parse_error=parse_error,
                is_test=is_test_path(f.path),
            )
            buildings.append(b)
            emit(b)
            if i % 250 == 0:
                emit(IngestProgress(stage="parsing", done=i, total=len(ordered)))

        edges, external = build_edges(imports, languages)
        roads = [RoadAdded(src=a, dst=b, imports=n) for a, b, n in roll_up_roads(edges, district_of)]
        for r in roads:
            emit(r)
        emit(IngestProgress(stage="graph", detail={"edges": len(edges), "roads": len(roads)}))

        return IngestResult(
            source=source,
            name=src.name,
            commit_sha=src.commit_sha,
            buildings=buildings,
            districts=districts,
            roads=roads,
            edges=sorted(edges),
            external_imports=external,
            skipped=dict(walked.skipped),
            truncated=walked.truncated,
            duration_s=round(time.monotonic() - started, 2),
        )
