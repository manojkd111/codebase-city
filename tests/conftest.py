from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

FIXTURE_FILES = {
    "setup.py": "from setuptools import setup\nsetup()\n",
    "pkg/__init__.py": "from .core import run\n",
    "pkg/core.py": (
        "import os\n"
        "from pkg import util\n"
        "\n"
        "def run(x, y):\n"
        "    if x and y:\n"
        "        return util.helper(x)\n"
        "    for i in range(3):\n"
        "        if i == y:\n"
        "            return i\n"
        "    return os.sep\n"
    ),
    "pkg/util.py": "def helper(x):\n    return x * 2\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/deep.py": "from ..util import helper\nimport requests\n\ndef go():\n    return helper(1)\n",
    "tests/test_core.py": "from pkg.core import run\n\ndef test_run():\n    assert run(0, 1) == 1\n",
    "web/app.js": (
        "import { a } from './lib/a';\n"
        "const b = require('./lib/b.js');\n"
        "import React from 'react';\n"
        "import { x } from '@scope/pkg/deep';\n"
        "export const f = () => a + b;\n"
    ),
    "web/lib/a.ts": "export const a: number = 1;\nexport function twice(n: number) { return n * 2 }\n",
    "web/lib/b.js": "module.exports = 2;\n",
    "web/lib/index.js": "export * from './a.js';\n",
    "node_modules/dep/index.js": "module.exports = 1;\n",  # must be skipped
    "README.md": "# not code\n",
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    for rel, content in FIXTURE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    (root / "pkg/core.py").write_text(FIXTURE_FILES["pkg/core.py"] + "\n# touched\n", encoding="utf-8")
    _git(root, "commit", "-q", "-am", "touch core")
    return root
