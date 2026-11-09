from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import get_settings


def _client(tmp_path, allow_local=True) -> TestClient:
    settings = replace(
        get_settings(), database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", allow_local_sources=allow_local
    )
    return TestClient(create_app(settings))


def test_rejects_non_github_urls(tmp_path):
    with _client(tmp_path, allow_local=False) as c:
        assert c.post("/api/repos", json={"url": "https://gitlab.com/a/b"}).status_code == 422
        assert c.post("/api/repos", json={"url": "C:/Windows"}).status_code == 422


def test_ingest_streams_events_and_serves_city(fixture_repo, tmp_path):
    with _client(tmp_path) as c:
        repo_id = c.post("/api/repos", json={"url": str(fixture_repo)}).json()["id"]

        received = []
        with c.websocket_connect(f"/ws/repos/{repo_id}") as ws:
            while True:
                ev = ws.receive_json()
                received.append(ev)
                if ev["type"] in ("ingest.completed", "ingest.failed"):
                    break

        assert received[-1]["type"] == "ingest.completed", received[-1]
        seqs = [e["seq"] for e in received]
        assert seqs == list(range(1, len(seqs) + 1))  # no gaps, no duplicates
        assert sum(e["type"] == "city.building_added" for e in received) == 11

        # Replay from the middle returns exactly the tail.
        with c.websocket_connect(f"/ws/repos/{repo_id}?after_seq={seqs[-3]}") as ws:
            tail = [ws.receive_json()["seq"] for _ in range(2)]
        assert tail == seqs[-2:]

        city = c.get(f"/api/repos/{repo_id}/city").json()
        assert city["repo"]["status"] == "ready"
        assert len(city["buildings"]) == 11
        assert city["roads"] == [{"src": "tests", "dst": "pkg", "imports": 1}]

        hot = c.get(f"/api/repos/{repo_id}/hotspots?limit=1").json()
        assert hot[0]["path"] == "pkg/core.py"


def test_stop_marks_a_live_run_and_refuses_one_that_ended(tmp_path):
    from backend.db.models import AnalysisRun, Repo
    from backend.db.session import make_engine, make_session_factory

    with _client(tmp_path) as c:
        sessions = make_session_factory(make_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}"))
        with sessions() as s:
            repo = Repo(source="https://github.com/a/b", status="ready")
            s.add(repo)
            s.flush()
            live = AnalysisRun(repo_id=repo.id, model="m", status="running")
            ended = AnalysisRun(repo_id=repo.id, model="m", status="done")
            s.add_all([live, ended])
            s.commit()
            repo_id, live_id, ended_id = repo.id, live.id, ended.id

        r = c.post(f"/api/analyses/{live_id}/stop")
        assert r.status_code == 202 and r.json()["status"] == "stopping"
        assert c.post(f"/api/analyses/{live_id}/stop").status_code == 202  # pressing twice is harmless
        with sessions() as s:
            assert s.get(AnalysisRun, live_id).status == "stopping"
        assert c.post(f"/api/analyses/{ended_id}/stop").status_code == 409
        assert c.post("/api/analyses/9999/stop").status_code == 404
        # A run that is still stopping blocks a new one, so two can't overlap.
        assert c.post(f"/api/repos/{repo_id}/analyze").status_code == 409


def test_analyze_streams_agents_and_triage_findings(fixture_repo, tmp_path):
    import time

    from .fakes import FakeClient, agent_route, plan_json, response, text, tool
    from .test_agents import GOOD

    def factory():
        return FakeClient(
            {
                "coordinator": [plan_json([], [], ["pkg/core.py"])],
                "debt": [response(tool("submit_finding", GOOD)), response(text("done"))],
            },
            agent_route,
        )

    settings = replace(
        get_settings(), database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}", allow_local_sources=True
    )
    with TestClient(create_app(settings, llm_client_factory=factory)) as c:
        repo_id = c.post("/api/repos", json={"url": str(fixture_repo)}).json()["id"]
        for _ in range(100):
            if c.get(f"/api/repos/{repo_id}").json()["status"] == "ready":
                break
            time.sleep(0.05)

        run_id = c.post(f"/api/repos/{repo_id}/analyze").json()["run_id"]
        for _ in range(200):
            run = c.get(f"/api/repos/{repo_id}/analysis").json()
            if run["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert run["id"] == run_id and run["status"] == "done", run

        findings = c.get(f"/api/repos/{repo_id}/findings").json()
        assert [f["title"] for f in findings] == [GOOD["title"]]
        fid = findings[0]["id"]
        assert c.patch(f"/api/findings/{fid}", json={"status": "confirmed"}).json()["status"] == "confirmed"
        assert c.patch(f"/api/findings/{fid}", json={"status": "fixed"}).status_code == 422  # not settable by hand
        assert c.get(f"/api/repos/{repo_id}/findings?status=confirmed").json()[0]["id"] == fid

        with c.websocket_connect(f"/ws/repos/{repo_id}") as ws:
            types = []
            while True:
                ev = ws.receive_json()
                types.append(ev["type"])
                if ev["type"] == "finding.updated":
                    break
        assert {"analysis.started", "agent.dispatched", "finding.submitted", "analysis.completed"} <= set(types)


def test_analyze_requires_a_ready_city(tmp_path):
    with _client(tmp_path, allow_local=False) as c:
        assert c.post("/api/repos/999/analyze").status_code == 404


def test_fix_requires_confirmation_and_pr_requires_token(fixture_repo, tmp_path):
    import time

    from .fakes import FakeClient, agent_route, plan_json, response, tool
    from .test_agents import GOOD

    def factory():
        return FakeClient(
            {
                "coordinator": [plan_json([], [], ["pkg/core.py"])],
                "debt": [response(tool("submit_finding", GOOD))],
                "other": [  # the fixer's system prompt matches no specialist
                    response(tool("edit_file", {"path": "pkg/util.py", "old_text": "x * 2", "new_text": "2 * x"})),
                    response(tool("finish", {"summary": "s", "commit_message": "c"})),
                ],
            },
            agent_route,
        )

    settings = replace(
        get_settings(),
        database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}",
        allow_local_sources=True,
        github_token=None,
        sandbox_enabled=False,  # CI runners have Docker; this test must not pip-install in containers
    )

    def wait(get, done):
        for _ in range(300):
            v = get()
            if done(v):
                return v
            time.sleep(0.05)
        raise AssertionError(v)

    with TestClient(create_app(settings, llm_client_factory=factory)) as c:
        assert c.get("/api/config").json()["pull_requests"] is False
        repo_id = c.post("/api/repos", json={"url": str(fixture_repo)}).json()["id"]
        wait(lambda: c.get(f"/api/repos/{repo_id}").json(), lambda r: r["status"] == "ready")
        c.post(f"/api/repos/{repo_id}/analyze")
        wait(lambda: c.get(f"/api/repos/{repo_id}/analysis").json(), lambda r: r["status"] == "done")
        fid = c.get(f"/api/repos/{repo_id}/findings").json()[0]["id"]

        assert c.post(f"/api/findings/{fid}/fix").status_code == 409  # not confirmed yet
        c.patch(f"/api/findings/{fid}", json={"status": "confirmed"})
        fix_id = c.post(f"/api/findings/{fid}/fix").json()["id"]
        fix = wait(lambda: c.get(f"/api/fixes/{fix_id}").json(), lambda f: f["status"] in ("ready", "failed"))
        assert fix["status"] == "ready", fix["error"]
        assert "+    return 2 * x" in fix["diff"] and fix["files_changed"] == 1

        r = c.post(f"/api/fixes/{fix_id}/pr")
        assert r.status_code == 409 and "GITHUB_TOKEN" in r.json()["detail"]
        assert c.get(f"/api/findings/{fid}/fixes").json()[0]["id"] == fix_id
        deps = c.get(f"/api/repos/{repo_id}/imports", params={"path": "pkg/util.py"}).json()
        assert deps["imported_by"] == ["pkg/core.py", "pkg/sub/deep.py"] and deps["imports"] == []
