"""Load models.yaml role scores for Token Plan ids."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AffinityCatalog:
    scores: dict[str, dict[str, int]]
    passthrough_auto: str | None = "auto"

    def score(self, model_id: str, role: str) -> int:
        row = self.scores.get(model_id) or {}
        key = role.removeprefix("auto:") if role.startswith("auto:") else role
        if key == "auto" or key == "":
            return int(row.get("auto", 50))
        return int(row.get(key, row.get("auto", 0)))

    def model_ids(self) -> tuple[str, ...]:
        return tuple(self.scores)


def load_affinity(path: Path | None = None) -> AffinityCatalog:
    root = Path(__file__).resolve().parents[2]
    target = path or (root / "models.yaml")
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    scores: dict[str, dict[str, int]] = {}
    for item in raw.get("models") or []:
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        roles = item.get("roles") or {}
        scores[mid] = {str(k): int(v) for k, v in roles.items()}
    return AffinityCatalog(
        scores=scores,
        passthrough_auto=str(raw.get("passthrough_auto") or "auto") or None,
    )
