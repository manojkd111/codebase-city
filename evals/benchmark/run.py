"""Run the agents on the planted-bug fixture and score them.

    .venv/Scripts/python -m evals.benchmark.run                 # full run (needs ANTHROPIC_API_KEY; costs money)
    .venv/Scripts/python -m evals.benchmark.run --metrics-only  # free: only checks the deterministic ranking

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
from backend.db.models import AnalysisRun, File, Finding, Repo
from backend.db.session import make_engine, make_session_factory
from backend.ingest.runner import execute_ingest

from .score import HERE, hotspot_recall, load_planted, score

# Opus 5.5 list prices, $ per million tokens; cached input is billed lower, so this is an upper bound.
PRICE_IN, PRICE_OUT = 4.0, 20.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metrics-only", action="store_true", help="skip the agents; score hotspot ranking only")
    ap.add_argument("--json", type=Path, help="also write the full result here")
    args = ap.parse_args(argv)

    fixture = (HERE / "planted" / "shop").resolve()
    planted = load_planted()
    # ignore_cleanup_errors: on Windows a lingering SQLite handle must not turn a result into a crash.
    with tempfile.TemporaryDirectory(prefix="cbc-bench-", ignore_cleanup_errors=True) as tmp:
        settings = replace(
            get_settings(),
            database_url=f"sqlite:///{(Path(tmp) / 'bench.db').as_posix()}",
            allow_local_sources=True,
            agent_files=6,
        )
        engine = make_engine(settings.database_url)
        try:
            return _bench(args, settings, make_session_factory(engine), fixture, planted)
        finally:
            engine.dispose()


def _bench(args, settings, sessions, fixture, planted) -> int:
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
    result = {"hotspot_recall_top5": hotspot_recall(files, planted, 5)}
    print(f"hotspot recall (planted files in top 5 by risk): {result['hotspot_recall_top5']:.0%}")
    if args.metrics_only:
        _write(args.json, result)
        return 0

    with sessions() as s:
        run = AnalysisRun(repo_id=repo_id, model=settings.model)
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
        usage = (run.input_tokens, run.output_tokens)
        plan = run.plan  # which files each reviewer got: the first thing to check on a miss

    sc = score(findings, planted)
    cost = usage[0] / 1e6 * PRICE_IN + usage[1] / 1e6 * PRICE_OUT
    print(f"recall: {len(sc.matched)}/{sc.total_planted} = {sc.recall:.0%}")
    print(f"precision (floor): {len(sc.matched)}/{sc.total_findings} = {sc.precision_floor:.0%}")
    print(f"tokens: {usage[0]:,} in / {usage[1]:,} out  (<= ${cost:.2f} at list price)")
    for p in planted:
        mark = "found " if p.id in sc.matched else "MISSED"
        print(f"  {mark} {p.id:<18} {p.file}:{p.line}  {p.what}")
    for i in sc.unmatched:
        f = findings[i]
        print(f"  extra  [{f['category']}] {f['file']}:{f['line_start']}  {f['title']}")
    result |= {
        "recall": sc.recall,
        "precision_floor": sc.precision_floor,
        "matched": sc.matched,
        "missed": sc.missed,
        "findings": findings,
        "plan": plan,
        "input_tokens": usage[0],
        "output_tokens": usage[1],
    }
    _write(args.json, result)
    return 0


def _write(path: Path | None, data: dict) -> None:
    if path:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
