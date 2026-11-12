"""Effective Credit cost from discounts.yaml + clock (UTC+8 night window)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DiscountCatalog:
    base_weight: dict[str, float]
    promos: tuple[dict, ...]
    night_start: time
    night_end: time
    tz: ZoneInfo = SHANGHAI

    def is_night(self, now: datetime | None = None) -> bool:
        clock = (now or datetime.now(tz=self.tz)).astimezone(self.tz).time()
        if self.night_start <= self.night_end:
            return self.night_start <= clock < self.night_end
        # Wraps midnight (22:00 → 08:00).
        return clock >= self.night_start or clock < self.night_end

    def effective_weight(self, model_id: str, now: datetime | None = None) -> float:
        base = self.base_weight.get(model_id, 3.0)
        clock = now or datetime.now(tz=self.tz)
        night = self.is_night(clock)
        best = base
        for promo in self.promos:
            models = promo.get("models") or []
            if model_id not in models:
                continue
            until = promo.get("until")
            if until and _parse_until(str(until)) < clock.astimezone(self.tz):
                continue
            when = promo.get("when")
            if when == "night" and not night:
                continue
            if when not in (None, "night", "always"):
                continue
            mult = float(promo.get("multiplier", 1.0))
            best = min(best, base * mult)
        return best


def load_discounts(path: Path | None = None) -> DiscountCatalog:
    root = Path(__file__).resolve().parents[2]
    target = path or (root / "discounts.yaml")
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    night = raw.get("night") or {}
    return DiscountCatalog(
        base_weight={str(k): float(v) for k, v in (raw.get("base_weight") or {}).items()},
        promos=tuple(raw.get("promos") or ()),
        night_start=_parse_hhmm(str(night.get("start") or "22:00")),
        night_end=_parse_hhmm(str(night.get("end") or "08:00")),
        tz=ZoneInfo(str(raw.get("timezone") or "Asia/Shanghai")),
    )


def _parse_hhmm(value: str) -> time:
    hour, minute = value.strip().split(":")[:2]
    return time(int(hour), int(minute))


def _parse_until(value: str) -> datetime:
    # Accept "...+08:00" or date-only; treat naive as Shanghai.
    text = value.replace("T24:00:00", "T23:59:59")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI)
    return dt.astimezone(SHANGHAI)
