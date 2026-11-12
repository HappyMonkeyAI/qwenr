"""Tiny .env loader — no dependency. Does not override existing process env."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> bool:
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value
    return True


def load_dotenv_near(config_path: Path) -> list[Path]:
    """Load `.env` next to config.yaml, then cwd — first wins per key via skip-if-set."""
    loaded: list[Path] = []
    for candidate in (config_path.parent / ".env", Path.cwd() / ".env"):
        resolved = candidate.resolve()
        if resolved in loaded:
            continue
        if load_dotenv(resolved):
            loaded.append(resolved)
    return loaded
