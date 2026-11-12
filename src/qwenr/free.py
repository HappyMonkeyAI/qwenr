"""Track which PAYG free-quota models are exhausted (fail closed to Token Plan)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Phrases that mean "don't keep burning PAYG" — treat as free lane failure.
QUOTA_EXHAUSTED_MARKERS = (
    "free quota",
    "quota exceeded",
    "quota exhausted",
    "insufficient quota",
    "free tier",
    "arrears",
    "overdue",
    "payment required",
    "billing",
    "balance is not enough",
    "access denied",
)


@dataclass
class FreeQuotaGate:
    """In-memory exhausted set. Missing live console API → operator + error feedback."""

    eligible: frozenset[str]
    ttl_seconds: float = 3600.0
    _exhausted_until: dict[str, float] = field(default_factory=dict)

    def available(self) -> tuple[str, ...]:
        now = time.time()
        return tuple(mid for mid in sorted(self.eligible) if self._exhausted_until.get(mid, 0) <= now)

    def mark_exhausted(self, model_id: str) -> None:
        self._exhausted_until[model_id] = time.time() + self.ttl_seconds

    def is_exhausted(self, model_id: str) -> bool:
        return self._exhausted_until.get(model_id, 0) > time.time()


def load_free_models(path: Path | None = None) -> frozenset[str]:
    root = Path(__file__).resolve().parents[2]
    target = path or (root / "free_models.yaml")
    if not target.is_file():
        return frozenset()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    models = raw.get("models") or []
    return frozenset(str(item).strip() for item in models if str(item).strip())


def looks_like_quota_or_billing_block(status_code: int, body: bytes) -> bool:
    if status_code not in {400, 401, 402, 403, 429, 503}:
        return False
    text = body.decode("utf-8", errors="ignore").casefold()
    return any(marker in text for marker in QUOTA_EXHAUSTED_MARKERS)
