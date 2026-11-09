"""Run the agents on the planted-bug fixture and score them.

    .venv/Scripts/python -m evals.benchmark.run                 # full run (needs ANTHROPIC_API_KEY; costs money)
    .venv/Scripts/python -m evals.benchmark.run --metrics-only  # free: only checks the deterministic ranking
    .venv/Scripts/python -m evals.benchmark.run --rescore evals/benchmark/baseline.json  # free: re-score a saved run
    .venv/Scripts/python -m evals.benchmark.run --fixture ledger  # the harder fixture (default: shop)

Uses a throwaway SQLite DB, never the app's. The fixture is ingested as a local path (CLI-only).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select

from backend.agents.runner import execute_analysis
from backend.config import get_settings
from backend.db.models import AnalysisRun, EventRow, File, Finding, Repo
from backend.db.session import make_engine, make_session_factory
from backend.ingest.runner import execute_ingest

from .score import FIXTURES, Score, fixture_spec, hotspot_recall, load_planted, score

# Opus 5.5 list prices, $ per million tokens. Only for saved runs from before the billed cost was
# recorded: pricing all input at the full rate overstates an agent loop's cost about 5x.
PRICE_IN, PRICE_OUT = 4.0, 20.0
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cost_usd")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics-only", action="store_true", help="skip the agents; score hotspot ranking only")
    ap.add_argument("--json", type=Path, help="also write the full result here")
    ap.add_argument("--rescore", type=Path, help="re-score a saved --json result (free; no ingest, no agents)")
    ap.add_argument("--fixture", choices=sorted(FIXTURES), help="planted repo to run on (default: shop)")
    args = ap.parse_args(argv)

    if args.rescore:
        saved = json.loads(args.rescore.read_text(encoding="utf-8"))
        expected = FIXTURES[args.fixture or saved.get("fixture", "shop")]
        _report(saved["findings"], expected, {k: saved.get(k) for k in USAGE_KEYS})
        return 0

    name = args.fixture or "shop"
    expected = FIXTURES[name]
    spec = fixture_spec(expected)
    planted = load_planted(expected)
    fixture = (expected.parent / spec["repo"]).resolve()
    # ignore_cleanup_errors: on Windows a lingering SQLite handle must not turn a result into a crash.
    with tempfile.TemporaryDirectory(prefix="cbc-bench-", ignore_cleanup_errors=True) as tmp:
        settings = replace(
            get_settings(),
            database_url=f"sqlite:///{(Path(tmp) / 'bench.db').as_posix()}",
            allow_local_sources=True,
            agent_files=spec.get("agent_files", 6),
        )
        engine = make_engine(settings.database_url)
        try:
            return _bench(args, settings, make_session_factory(engine), fixture, planted, name, expected, spec)
        finally:
            engine.dispose()


def _bench(args, settings, sessions, fixture, planted, name, expected, spec) -> int:
    with sessions() as s:
        repo = Repo(source=str(fixture), status="queued")
        s.add(repo)
        s.commit()
        repo_id = repo.id
    execute_ingest(repo_id, settings, sessions, allow_local=True, raise_errors=True)
    with sessions() as s:
        files = [
            {"path": f.path, "risk": f.risk, "is_test": f.is_test}
            for f in s.scalars(select(File).where(File.repo_id == repo_id))
        ]
    k = spec.get("hotspot_k", 5)
    result = {"fixture": name, "hotspot_k": k, "hotspot_recall": hotspot_recall(files, planted, k)}
    print(f"[{name}] hotspot recall (planted files in top {k} by risk): {result['hotspot_recall']:.0%}")
    if args.metrics_only:
        _write(args.json, result)
        return 0

    with sessions() as s:
        run = AnalysisRun(repo_id=repo_id, model=settings.review_model)
        s.add(run)
        s.commit()
        run_id = run.id
    execute_analysis(run_id, settings, sessions, allow_local=True)
    with sessions() as s:
        run = s.get(AnalysisRun, run_id)
        if run.status != "done":
            print(f"analysis {run.status}: {run.error}", file=sys.stderr)
            return 1
        findings = [
            {k: getattr(f, k) for k in ("file", "category", "severity", "line_start", "line_end", "title")}
            for f in s.scalars(select(Finding).where(Finding.run_id == run_id).order_by(Finding.id))
        ]
        plan = run.plan  # which files each reviewer got: the first thing to check on a miss
        done = s.scalars(
            select(EventRow)
            .where(EventRow.repo_id == run.repo_id, EventRow.type == "analysis.completed")
            .order_by(EventRow.seq.desc())
        ).first()
        usage = {k: (done.data.get(k) if done else None) for k in USAGE_KEYS}

    sc = _report(findings, expected, usage)
    result |= {
        "recall": sc.recall,
        "precision_floor": sc.precision_floor,
        "matched": sc.matched,
        "known_real": sc.known,
        "missed": sc.missed,
        "decoy_hits": sc.decoy_hits,
        "findings": findings,
        "plan": plan,
        **usage,
    }
    _write(args.json, result)
    return 0


def _cost_line(usage: dict) -> str:
    tin, tout = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
    line = f"tokens: {tin:,} in / {tout:,} out"
    if usage.get("cost_usd") is not None:
        cached = (usage.get("cache_read_tokens") or 0) / tin if tin else 0
        return f"{line}  ({cached:.0%} of input from cache)  cost: ${usage['cost_usd']:.2f} at list price"
    upper = tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT
    return f"{line}  (<= ${upper:.2f}: recorded before billed cost was tracked; real cost is lower)"


def _report(findings: list[dict], expected: Path, usage: dict) -> Score:
    planted = load_planted(expected)
    known = load_planted(expected, key="known_real")
    decoys = load_planted(expected, key="decoys")
    sc = score(findings, planted, known, decoys)
    correct = len(sc.matched) + len(sc.known)
    print(f"recall: {len(sc.matched)}/{sc.total_planted} = {sc.recall:.0%}")
    print(
        f"precision (floor): {correct}/{sc.total_findings} = {sc.precision_floor:.0%}"
        f"  ({len(sc.matched)} planted + {len(sc.known)} known-real)"
    )
    if decoys:
        print(f"decoys flagged: {len(sc.decoy_hits)}/{len(decoys)}  (code that looks dangerous but is fine)")
    print(_cost_line(usage))
    for p in planted:
        mark = "found " if p.id in sc.matched else "MISSED"
        print(f"  {mark} {p.id:<20} {p.file}:{p.line}  {p.what}")
    for k in known:
        if k.id in sc.known:
            print(f"  known  {k.id:<20} {k.file}:{k.line}  {k.what}")
    on_decoy = {i: d for d, hits in sc.decoy_hits.items() for i in hits}
    for i in sc.unmatched:
        f = findings[i]
        tag = f"DECOY {on_decoy[i]}" if i in on_decoy else "extra"
        print(f"  {tag}  [{f['category']}] {f['file']}:{f['line_start']}  {f['title']}")
    return sc


def _write(path: Path | None, data: dict) -> None:
    if path:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
