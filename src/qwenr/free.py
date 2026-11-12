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
    """In-memory exhausted set + optional live free-tier sync blocks."""

    eligible: frozenset[str]
    ttl_seconds: float = 3600.0
    _exhausted_until: dict[str, float] = field(default_factory=dict)
    # model_id -> reason from last successful free-tier sync (exhaust/expire/…).
    _sync_blocked: dict[str, str] = field(default_factory=dict)
    _sync_at: float | None = None
    _sync_source: str | None = None
    _sync_error: str | None = None

    def available(self) -> tuple[str, ...]:
        now = time.time()
        return tuple(
            mid
            for mid in sorted(self.eligible)
            if mid not in self._sync_blocked and self._exhausted_until.get(mid, 0) <= now
        )

    def mark_exhausted(self, model_id: str) -> None:
        self._exhausted_until[model_id] = time.time() + self.ttl_seconds

    def is_exhausted(self, model_id: str) -> bool:
        if model_id in self._sync_blocked:
            return True
        return self._exhausted_until.get(model_id, 0) > time.time()

    def apply_sync_blocks(self, blocked: dict[str, str], *, source: str | None = None) -> None:
        """Replace sync-derived blocks. Reactive TTL marks are left alone."""
        self._sync_blocked = dict(blocked)
        self._sync_at = time.time()
        self._sync_source = source
        self._sync_error = None

    def note_sync_error(self, error: str, *, source: str | None = None) -> None:
        self._sync_error = error
        self._sync_source = source
        # Keep previous blocks on failure so a blip doesn't reopen paid risk.

    def sync_status(self) -> dict[str, object]:
        return {
            "blocked": dict(self._sync_blocked),
            "synced_at": self._sync_at,
            "source": self._sync_source,
            "error": self._sync_error,
        }


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
