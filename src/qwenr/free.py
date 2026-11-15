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
    """Free lane is an allow-list from the last fresh sync. No sync means no PAYG calls."""

    eligible: frozenset[str]
    ttl_seconds: float = 3600.0
    max_age_seconds: float = 900.0
    _exhausted_until: dict[str, float] = field(default_factory=dict)
    # model_id -> reason from last successful free-tier sync (exhaust/expire/…).
    _sync_blocked: dict[str, str] = field(default_factory=dict)
    # None until a sync succeeds. Models missing from that sync stay off the free lane.
    _sync_allowed: frozenset[str] | None = None
    _sync_at: float | None = None
    _sync_source: str | None = None
    _sync_error: str | None = None
    # Last sync attempt, success or failure. Failed refreshes back off on this.
    _sync_attempt_at: float | None = None

    def refresh_due(self, ttl_seconds: float, now: float | None = None) -> bool:
        if self._sync_attempt_at is None:
            return True
        return ((now or time.time()) - self._sync_attempt_at) >= ttl_seconds

    def available(self) -> tuple[str, ...]:
        if self.closed_reason() is not None or self._sync_allowed is None:
            return ()
        now = time.time()
        return tuple(
            mid
            for mid in sorted(self._sync_allowed)
            if self._exhausted_until.get(mid, 0) <= now
        )

    def closed_reason(self) -> str | None:
        if self._sync_at is None or self._sync_allowed is None:
            return "no_sync"
        if (time.time() - self._sync_at) > self.max_age_seconds:
            return "stale"
        return None

    def mark_exhausted(self, model_id: str) -> None:
        self._exhausted_until[model_id] = time.time() + self.ttl_seconds

    def is_exhausted(self, model_id: str) -> bool:
        if model_id in self._sync_blocked:
            return True
        return self._exhausted_until.get(model_id, 0) > time.time()

    def apply_sync_blocks(
        self,
        blocked: dict[str, str],
        *,
        allowed: set[str] | frozenset[str] | None = None,
        source: str | None = None,
    ) -> None:
        """Replace the allow-list. Only ids the sync left unblocked may use the PAYG key."""
        blocked_ids = {mid: reason for mid, reason in blocked.items() if mid in self.eligible}
        self._sync_blocked = blocked_ids
        permitted = set(allowed or ())
        self._sync_allowed = frozenset(
            mid for mid in permitted if mid in self.eligible and mid not in blocked_ids
        )
        self._sync_at = time.time()
        self._sync_attempt_at = self._sync_at
        self._sync_source = source
        self._sync_error = None

    def note_sync_error(self, error: str, *, source: str | None = None) -> None:
        self._sync_error = error
        self._sync_source = source
        self._sync_attempt_at = time.time()
        # Keep the previous allow-list. A failed refresh must not reopen the catalog.

    def sync_status(self) -> dict[str, object]:
        return {
            "blocked": dict(self._sync_blocked),
            "allowed": sorted(self._sync_allowed or ()),
            "synced_at": self._sync_at,
            "source": self._sync_source,
            "error": self._sync_error,
            "closed": self.closed_reason(),
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
