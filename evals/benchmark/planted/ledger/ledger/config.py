import ast
import os


def load(path: str) -> dict:
    """Read `KEY = value` lines; values are Python literals (numbers, strings, lists)."""
    settings = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, raw = line.partition("=")
            settings[key.strip()] = ast.literal_eval(raw.strip())
    settings.setdefault("DATABASE", os.environ.get("LEDGER_DB", "ledger.db"))
    return settings
