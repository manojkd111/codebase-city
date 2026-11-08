"""Command line: ingest a repo without running the server.

python -m backend.cli ingest https://github.com/pallets/flask
python -m backend.cli ingest ./some/local/checkout --top 15
"""

from __future__ import annotations

import argparse
import sys

from backend.config import get_settings
from backend.db.models import Repo
from backend.db.session import make_engine, make_session_factory
from backend.ingest.runner import execute_ingest


def _progress(env) -> None:
    if env.type in ("ingest.started", "ingest.progress", "ingest.failed"):
        d = env.data
        extra = f" {d['done']}/{d['total']}" if d.get("total") else ""
        print(f"  [{env.seq:>5}] {env.type} {d.get('stage', '')}{extra}", file=sys.stderr)


def cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    with sessions() as s:
        repo = Repo(source=args.source)
        s.add(repo)
        s.commit()
        repo_id = repo.id
    result = execute_ingest(repo_id, settings, sessions, _progress, allow_local=True)
    if result is None:
        with sessions() as s:
            print(f"FAILED: {s.get(Repo, repo_id).error}", file=sys.stderr)
        return 1

    b = result.buildings
    print(f"\n{result.name} @ {(result.commit_sha or 'no-git')[:10]}  (repo id {repo_id})")
    print(
        f"  {len(b)} files, {len(result.districts)} districts, {len(result.roads)} roads, "
        f"{len(result.edges)} import edges, {sum(x.loc for x in b)} LOC, {result.duration_s}s"
    )
    if result.skipped:
        print(f"  skipped: {result.skipped}{'  (TRUNCATED at file cap)' if result.truncated else ''}")
    print("  districts: " + ", ".join(f"{d.name}({d.file_count})" for d in result.districts))
    print(f"  test files: {sum(x.is_test for x in b)} (excluded from hotspots)")
    print(f"\n  Top {args.top} hotspots (complexity x churn):")
    print(f"  {'risk':>7} {'ccn':>5} {'max':>4} {'c90':>4} {'loc':>6}  path")
    for x in sorted((x for x in b if not x.is_test), key=lambda x: x.risk, reverse=True)[: args.top]:
        print(f"  {x.risk:7.2f} {x.complexity_total:5} {x.complexity_max:4} {x.churn_90:4} {x.loc:6}  {x.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="codebase-city")
    sub = p.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="ingest a GitHub URL or local directory")
    ing.add_argument("source")
    ing.add_argument("--top", type=int, default=10)
    ing.set_defaults(func=cmd_ingest)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
