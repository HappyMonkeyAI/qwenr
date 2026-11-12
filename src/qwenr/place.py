"""Pick a model: free PAYG quota first (safe), else Token Plan Credits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from qwenr.affinity import AffinityCatalog
from qwenr.discounts import DiscountCatalog

SKIP_PREFIXES = ("qwen-image", "qwen-audio", "wan", "happyhorse")
DEFAULT_FREE_AFFINITY_SLACK = 3


@dataclass(frozen=True)
class Placement:
    model_id: str
    requested: str
    reason: str
    affinity: int
    cost_weight: float
    score: float
    lane: str = "token_plan"  # free | token_plan


class PlaceError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def place(
    requested: str | None,
    affinity: AffinityCatalog,
    discounts: DiscountCatalog,
    *,
    economy_lambda: float = 12.0,
    prefer_platform_auto: bool = False,
    now: datetime | None = None,
    allowlist: tuple[str, ...] | None = None,
    free_candidates: tuple[str, ...] | None = None,
    free_first: bool = True,
    exclude: frozenset[str] = frozenset(),
    free_affinity_slack: int = DEFAULT_FREE_AFFINITY_SLACK,
    free_usage: Mapping[str, int] | None = None,
) -> Placement:
    role = _normalize_role(requested)
    if prefer_platform_auto and role in {"auto", ""}:
        return Placement(
            model_id=affinity.passthrough_auto or "auto",
            requested=role or "auto",
            reason="platform_auto",
            affinity=100,
            cost_weight=0.0,
            score=100.0,
            lane="token_plan",
        )

    if free_first and free_candidates:
        free_pick = _best_free(
            role or "auto",
            affinity,
            free_candidates,
            exclude,
            free_affinity_slack=free_affinity_slack,
            free_usage=free_usage,
        )
        if free_pick is not None:
            return free_pick

    ids = allowlist or affinity.model_ids()
    candidates: list[Placement] = []
    for mid in ids:
        if mid in exclude or mid == "auto" or mid.startswith(SKIP_PREFIXES):
            continue
        aff = affinity.score(mid, role or "auto")
        if aff <= 0:
            continue
        cost = discounts.effective_weight(mid, now=now)
        score = float(aff) - economy_lambda * cost
        reason = "night_deal" if discounts.is_night(now) and cost < discounts.base_weight.get(mid, cost) else "fit"
        if cost < discounts.base_weight.get(mid, cost) and not discounts.is_night(now):
            reason = "promo"
        candidates.append(
            Placement(
                model_id=mid,
                requested=role or "auto",
                reason=reason,
                affinity=aff,
                cost_weight=cost,
                score=score,
                lane="token_plan",
            )
        )
    if not candidates:
        raise PlaceError(404, "model_not_found", f"No Token Plan model for {role!r}.")
    return max(candidates, key=lambda p: (p.score, p.affinity, -p.cost_weight))


def _best_free(
    role: str,
    affinity: AffinityCatalog,
    free_candidates: tuple[str, ...],
    exclude: frozenset[str],
    *,
    free_affinity_slack: int = DEFAULT_FREE_AFFINITY_SLACK,
    free_usage: Mapping[str, int] | None = None,
) -> Placement | None:
    scored: list[Placement] = []
    for mid in free_candidates:
        if mid in exclude or mid.startswith(SKIP_PREFIXES):
            continue
        aff = affinity.score(mid, role)
        if aff <= 0:
            continue
        # Free lane: cost_weight 0; ranking is affinity with near-tie spread.
        scored.append(
            Placement(
                model_id=mid,
                requested=role,
                reason="free_quota",
                affinity=aff,
                cost_weight=0.0,
                score=float(aff),
                lane="free",
            )
        )
    if not scored:
        return None
    greedy = max(scored, key=lambda c: (c.affinity, c.model_id))
    slack = max(0, int(free_affinity_slack))
    pool = [c for c in scored if c.affinity >= greedy.affinity - slack]
    usage = free_usage or {}
    # Prefer underused free models inside the affinity slack band; break ties by
    # higher affinity then stable id.
    pool.sort(key=lambda c: (usage.get(c.model_id, 0), -c.affinity, c.model_id))
    pick = pool[0]
    if pick.model_id != greedy.model_id:
        return Placement(
            model_id=pick.model_id,
            requested=pick.requested,
            reason="free_quota_spread",
            affinity=pick.affinity,
            cost_weight=0.0,
            score=float(pick.affinity),
            lane="free",
        )
    return pick


def _normalize_role(requested: str | None) -> str:
    name = (requested or "auto").strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    folded = name.casefold()
    if folded.startswith("chimpr/") or folded.startswith("qwenr/"):
        name = name.split("/", 1)[-1]
    return name.casefold()
