from __future__ import annotations

import posixpath
from collections import Counter
from dataclasses import replace

import pytest

from backend.config import get_settings
from backend.events.schema import BuildingAdded, DistrictAdded, RoadAdded
from backend.ingest.districts import ROOT_DISTRICT, assign_districts
from backend.ingest.graph import PythonModuleIndex
from backend.ingest.parse import ImportRef, parse_source
from backend.ingest.pipeline import run_ingest
from backend.ingest.source import SourceError, parse_github_url


def test_parse_python_imports_and_functions():
    code = b"import a.b as c, d\nfrom ..x.y import z as w, v\nfrom . import m\n\ndef f():\n    def g(): pass\n"
    p = parse_source(code, "python")
    assert p.functions == 2
    assert p.imports == [
        ImportRef("a.b"),
        ImportRef("d"),
        ImportRef("x.y", 2, ("z", "v")),
        ImportRef("", 1, ("m",)),
    ]


def test_parse_js_imports():
    code = (
        b"import a from './a'; export * from '../b'; const c = require('c'); import('./d'); const e = require(`./${x}`)"
    )
    p = parse_source(code, "tsx")
    assert [i.module for i in p.imports] == ["./a", "../b", "c", "./d"]


def test_python_module_index_src_layout():
    idx = PythonModuleIndex(
        ["src/flask/__init__.py", "src/flask/app.py", "src/flask/json/__init__.py", "tests/test_app.py"]
    )
    assert idx.by_name["flask"] == "src/flask/__init__.py"
    assert idx.by_name["flask.app"] == "src/flask/app.py"
    assert idx.resolve("tests/test_app.py", ImportRef("flask", 0, ("app",))) == ["src/flask/app.py"]
    assert idx.resolve("src/flask/app.py", ImportRef("json", 1, ())) == ["src/flask/json/__init__.py"]
    assert idx.resolve("tests/test_app.py", ImportRef("requests")) == []


def test_districts_descend_into_dominant_folder():
    paths = [f"src/pkg/{d}/f{i}.py" for d in ("a", "b") for i in range(10)] + ["setup.py"]
    out = assign_districts(paths)
    assert out["src/pkg/a/f0.py"] == "src/pkg/a"
    assert out["setup.py"] == ROOT_DISTRICT


def test_github_url_validation():
    assert parse_github_url("https://github.com/pallets/flask") == ("pallets", "flask")
    assert parse_github_url("https://github.com/pallets/flask.git") == ("pallets", "flask")
    assert parse_github_url("https://evil.com/pallets/flask") is None
    assert parse_github_url("https://github.com/pallets/flask/../../x") is None


def test_local_source_rejected_unless_allowed(fixture_repo):
    with pytest.raises(SourceError):
        run_ingest(str(fixture_repo), lambda _e: None, replace(get_settings(), allow_local_sources=False))


def test_pipeline_end_to_end(fixture_repo):
    events = []
    r = run_ingest(str(fixture_repo), events.append, get_settings(), allow_local=True)

    paths = {b.path for b in r.buildings}
    assert "node_modules/dep/index.js" not in paths
    assert "README.md" not in paths
    assert len(paths) == 11

    by_path = {b.path: b for b in r.buildings}
    assert by_path["pkg/core.py"].churn_90 == 2  # two commits touched it
    assert by_path["pkg/util.py"].churn_90 == 1
    assert by_path["pkg/core.py"].complexity_total >= 4
    assert by_path["pkg/core.py"].risk > by_path["pkg/util.py"].risk

    assert set(r.edges) == {
        ("pkg/__init__.py", "pkg/core.py"),
        ("pkg/core.py", "pkg/util.py"),
        ("pkg/sub/deep.py", "pkg/util.py"),
        ("tests/test_core.py", "pkg/core.py"),
        ("web/app.js", "web/lib/a.ts"),
        ("web/app.js", "web/lib/b.js"),
        ("web/lib/index.js", "web/lib/a.ts"),  # './a.js' resolves to a.ts
    }
    assert r.external_imports["react"] == 1
    assert r.external_imports["@scope/pkg"] == 1
    assert r.external_imports["requests"] == 1
    assert [(x.src, x.dst, x.imports) for x in r.roads] == [("tests", "pkg", 1)]

    # Event order: districts before buildings, buildings grouped by district, roads last.
    kinds = [type(e) for e in events]
    first_building = kinds.index(BuildingAdded)
    assert all(k is not DistrictAdded for k in kinds[first_building:])
    assert kinds.index(RoadAdded) > max(i for i, k in enumerate(kinds) if k is BuildingAdded)
    districts_in_order = [e.district for e in events if isinstance(e, BuildingAdded)]
    assert districts_in_order == sorted(districts_in_order)

    # Folder counts arrive with each district so the frontend can lay out folder blocks up front.
    districts = {e.name: e for e in events if isinstance(e, DistrictAdded)}
    for d in districts.values():
        assert sum(d.folders.values()) == d.file_count
    assert districts["pkg"].folders["pkg/sub"] == 2
    built = Counter(posixpath.dirname(b.path) for b in r.buildings if b.district == "pkg")
    assert built == Counter(districts["pkg"].folders)


def test_is_test_path():
    from backend.ingest.languages import is_test_path

    for p in ("tests/test_app.py", "pkg/test_x.py", "x_test.go", "src/a.test.ts", "web/__tests__/b.js", "conftest.py"):
        assert is_test_path(p), p
    for p in ("src/flask/app.py", "lib/testing_utils_impl.py", "contest.py", "src/attest.js"):
        assert not is_test_path(p), p


def test_single_package_district_is_collapsed():
    paths = ["src/flask/app.py", "src/flask/json/a.py", "tests/t1.py", "tests/t2.py", "examples/e/a.py"]
    out = assign_districts(paths)
    assert out["src/flask/json/a.py"] == "src/flask"
    assert out["examples/e/a.py"] == "examples/e"
    assert out["tests/t1.py"] == "tests"
