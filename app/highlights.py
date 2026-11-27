"""Highlight list, saved as JSON so it survives restarts."""
import json
import os
from pathlib import Path


def load(path: Path) -> list[str]:
    try:
        return json.loads(path.read_text())["symbols"]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []


def save(path: Path, symbols: list[str]) -> list[str]:
    symbols = list(dict.fromkeys(s.strip() for s in symbols if s.strip()))  # dedupe, keep order
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"symbols": symbols}, indent=2))
    os.replace(tmp, path)  # atomic: never leaves a half-written file
    return symbols
