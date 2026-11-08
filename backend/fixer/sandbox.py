"""Run an ingested repo's tests in a disposable Docker container (ADR 0003, ADR 0006).

Two phases, so dependency installs can reach a package index but the code under test can't
reach anything:
  1. setup: network on, no secrets, no host env, resource limits, deps installed into /work.
  2. test:  --network none, same limits, same mount.
Nothing from the host is mounted except the throwaway checkout, and no environment variables
are passed through, so GITHUB_TOKEN / ANTHROPIC_API_KEY never enter the container.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

OUTPUT_LIMIT = 20_000


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TestPlan:
    image: str
    setup: str
    test: str
    coverage: str | None = None  # command writing coverage.json (Python only for now)


@dataclass
class SandboxResult:
    exit_code: int
    output: str
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def detect_plan(root: Path, python_image: str = "python:3.12-slim") -> TestPlan | None:
    """Pick a test command from the repo's own manifests. None if we can't tell."""
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or (root / "requirements.txt").exists():
        venv = "python -m venv /work/.cbc-venv && . /work/.cbc-venv/bin/activate"
        install = (
            "(pip install -q -e '.[test]' || pip install -q -e '.[dev]' || pip install -q -e . "
            "|| pip install -q -r requirements.txt || true)"
        )
        return TestPlan(
            image=python_image,
            setup=f"{venv} && pip install -q --upgrade pip && {install} && pip install -q pytest pytest-cov",
            test=". /work/.cbc-venv/bin/activate && python -m pytest -q -x --no-header -p no:cacheprovider",
            coverage=(
                ". /work/.cbc-venv/bin/activate && python -m pytest -q --no-header -p no:cacheprovider "
                "--cov=. --cov-report=json:/work/.cbc-coverage.json || true"
            ),
        )
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        if "test" in scripts:
            # --ignore-scripts: install hooks would otherwise run repo code with network on.
            return TestPlan(
                image="node:20-slim",
                setup="npm ci --ignore-scripts --no-audit --no-fund "
                "|| npm install --ignore-scripts --no-audit --no-fund",
                test="npm test --silent",
            )
    if (root / "go.mod").exists():
        return TestPlan(
            image="golang:1.22",
            setup="GOMODCACHE=/work/.cbc-gomod go mod download",
            test="GOMODCACHE=/work/.cbc-gomod GOFLAGS=-mod=mod go test ./...",
        )
    return None


class DockerSandbox:
    def __init__(self, timeout_s: int = 600, docker: str = "docker") -> None:
        self.timeout_s = timeout_s
        self.docker = docker
        self._ok: bool | None = None

    def available(self) -> bool:
        if self._ok is None:
            exe = shutil.which(self.docker)
            self._ok = (
                bool(exe) and subprocess.run([self.docker, "info"], capture_output=True, timeout=30).returncode == 0
            )
        return self._ok

    def _args(self, root: Path, image: str, network: bool, cmd: str) -> list[str]:
        return [
            self.docker,
            "run",
            "--rm",
            "--network",
            "bridge" if network else "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "512",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--env",
            "HOME=/tmp",
            "-v",
            f"{root.resolve()}:/work",
            "-w",
            "/work",
            image,
            "sh",
            "-c",
            cmd,
        ]

    def _run(self, args: list[str]) -> SandboxResult:
        try:
            p = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                env=scrubbed_env(),  # the docker CLI itself gets no secrets either
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
            return SandboxResult(124, _tail(out + "\n[timed out]"), timed_out=True)
        return SandboxResult(p.returncode, _tail(p.stdout + p.stderr))

    def run_tests(self, root: Path, plan: TestPlan, *, setup: bool = True) -> SandboxResult:
        if not self.available():
            raise SandboxUnavailable("Docker is not available, so tests can't be run safely.")
        if setup:
            prep = self._run(self._args(root, plan.image, True, plan.setup))
            if not prep.passed:
                return SandboxResult(prep.exit_code, "[setup failed]\n" + prep.output, prep.timed_out)
        return self._run(self._args(root, plan.image, False, plan.test))

    def coverage(self, root: Path, plan: TestPlan) -> dict[str, float]:
        """Line coverage per repo-relative file (0..1). Empty if unsupported or nothing ran."""
        if not plan.coverage:
            return {}
        if not self.available():
            raise SandboxUnavailable("Docker is not available, so coverage can't be collected safely.")
        prep = self._run(self._args(root, plan.image, True, plan.setup))
        if not prep.passed:
            return {}
        self._run(self._args(root, plan.image, False, plan.coverage))
        return parse_coverage_json(root / ".cbc-coverage.json")


def parse_coverage_json(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, float] = {}
    for name, info in data.get("files", {}).items():
        summary = info.get("summary", {})
        total = summary.get("num_statements", 0)
        if total:
            rel = name.replace("\\", "/").removeprefix("/work/").removeprefix("./")
            out[rel] = round(summary.get("covered_lines", 0) / total, 4)
    return out


def _tail(s: str) -> str:
    return s if len(s) <= OUTPUT_LIMIT else "…" + s[-OUTPUT_LIMIT:]


_SECRET_HINTS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ANTHROPIC", "GITHUB", "AWS_", "CREDENTIAL")


def scrubbed_env() -> dict[str, str]:
    """Host env minus anything that looks like a credential. Containers never inherit host env
    anyway (no --env-file, no -e NAME passthrough); this keeps secrets out of the CLI process too."""
    return {k: v for k, v in os.environ.items() if not any(h in k.upper() for h in _SECRET_HINTS)}
