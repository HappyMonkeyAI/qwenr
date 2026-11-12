"""Append-only placement ledger for post-run free-quota analysis."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PlacementLedger:
    """Memory ring + optional NDJSON file. Never stores API keys."""

    path: Path | None = None
    memory_limit: int = 500
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _entries: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        row = dict(entry)
        row.setdefault("ts", time.time())
        with self._lock:
            self._entries.append(row)
            if len(self._entries) > self.memory_limit:
                self._entries = self._entries[-self.memory_limit :]
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        return row

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._entries[-limit:]))

    def free_usage_counts(self) -> dict[str, int]:
        with self._lock:
            counts: Counter[str] = Counter()
            for row in self._entries:
                if row.get("lane") == "free" and row.get("ok") and row.get("model"):
                    counts[str(row["model"])] += 1
            return dict(counts)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            rows = list(self._entries)
        by_lane: Counter[str] = Counter()
        by_model: Counter[str] = Counter()
        by_role: Counter[str] = Counter()
        free_ok = 0
        token_ok = 0
        failovers = 0
        tokens_in = 0
        tokens_out = 0
        for row in rows:
            lane = str(row.get("lane") or "?")
            by_lane[lane] += 1
            if row.get("model"):
                by_model[str(row["model"])] += 1
            if row.get("requested"):
                by_role[str(row["requested"])] += 1
            if row.get("event") == "failover":
                failovers += 1
            if row.get("ok"):
                if lane == "free":
                    free_ok += 1
                elif lane == "token_plan":
                    token_ok += 1
            usage = row.get("usage") or {}
            if isinstance(usage, dict):
                tokens_in += int(usage.get("prompt_tokens") or 0)
                tokens_out += int(usage.get("completion_tokens") or 0)
        return {
            "total": len(rows),
            "by_lane": dict(by_lane),
            "by_model": dict(by_model.most_common(20)),
            "by_role": dict(by_role.most_common(20)),
            "free_ok": free_ok,
            "token_plan_ok": token_ok,
            "failovers": failovers,
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
        }
