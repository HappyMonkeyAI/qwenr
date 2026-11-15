"""Pick a model: free PAYG quota first (safe), else Token Plan Credits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from qwenr.affinity import AffinityCatalog
from qwenr.discounts import DiscountCatalog

# Non-text model families: never routed for chat completions.
SKIP_PREFIXES = ("qwen-image", "qwen-audio", "wan", "happyhorse")
# OpenBot role ids (mirrors chimp). /v1/models lists these first.
ROLE_ALIASES = (
    "auto",
    "auto:coder",
    "auto:ops",
    "auto:research",
    "auto:writer",
    "auto:design",
    "auto:qa",
    "auto:docs",
    "auto:skills",
    "auto:assistant",
    "auto:coord",
)
# Router names clients may prefix ("qwenr/auto:coder").
ROUTER_PREFIXES = ("qwenr/", "chimpr/")
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
    free_candidates: tuple[str, ...] | None = None,
    free_first: bool = True,
    exclude: frozenset[str] = frozenset(),
    free_affinity_slack: int = DEFAULT_FREE_AFFINITY_SLACK,
    free_usage: Mapping[str, int] | None = None,
) -> Placement:
    role = _normalize_role(requested)
    # Concrete model ids must pass through. Treating "qwen3.8-max" as a role
    # made free_quota_spread remap it to kimi (and break vibes temperature=0.9).
    if role and not _is_role_alias(role):
        return _exact_model(
            role,
            affinity=affinity,
            discounts=discounts,
            now=now,
            free_candidates=free_candidates,
            free_first=free_first,
            exclude=exclude,
        )
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

    ids = affinity.model_ids()
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
    """Role aliases are case-folded; concrete model ids keep their exact spelling.

    Strips router prefixes ("qwenr/", "chimpr/"). Any other provider prefix is
    dropped only when what follows is a role alias ("openai/auto:coder"), so
    vendor-scoped model ids like "moonshotai/Kimi-K3" survive intact.
    """
    name = (requested or "auto").strip()
    folded = name.casefold()
    for prefix in ROUTER_PREFIXES:
        if folded.startswith(prefix):
            name = name[len(prefix) :]
            folded = folded[len(prefix) :]
            break
    tail = folded.rsplit("/", 1)[-1]
    if _is_role_alias(tail):
        return tail
    return name


def _is_role_alias(name: str) -> bool:
    return name == "auto" or name.startswith("auto:")


def _exact_model(
    model_id: str,
    *,
    affinity: AffinityCatalog,
    discounts: DiscountCatalog,
    now: datetime | None,
    free_candidates: tuple[str, ...] | None,
    free_first: bool,
    exclude: frozenset[str],
) -> Placement:
    ids = set(affinity.model_ids())
    free = set(free_candidates or ())
    model_id = _canonical_id(model_id, ids | free)
    if model_id in exclude:
        raise PlaceError(404, "model_not_found", f"Model {model_id!r} is excluded.")
    if free_first and model_id in free:
        return Placement(
            model_id=model_id,
            requested=model_id,
            reason="exact_free",
            affinity=affinity.score(model_id, "auto"),
            cost_weight=0.0,
            score=float(affinity.score(model_id, "auto")),
            lane="free",
        )
    if model_id in ids or model_id in free:
        cost = discounts.effective_weight(model_id, now=now)
        return Placement(
            model_id=model_id,
            requested=model_id,
            reason="exact",
            affinity=affinity.score(model_id, "auto"),
            cost_weight=cost,
            score=float(affinity.score(model_id, "auto")),
            lane="token_plan",
        )
    raise PlaceError(404, "model_not_found", f"Unknown model {model_id!r}.")


def _canonical_id(model_id: str, known: set[str]) -> str:
    """Resolve a requested id to a catalog id; unchanged if nothing matches.

    Order: exact, unique case-insensitive, then the same for the last path
    segment so provider-prefixed ids ("openai/qwen3.8-max") still route. The
    full id is tried first, so a vendor-scoped catalog id always wins.
    """
    for candidate in dict.fromkeys((model_id, model_id.rsplit("/", 1)[-1])):
        if candidate in known:
            return candidate
        matches = [mid for mid in known if mid.casefold() == candidate.casefold()]
        if len(matches) == 1:
            return matches[0]
    return model_id
