"""Append-only placement ledger for post-run free-quota analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("qwenr.ledger")


@dataclass
class PlacementLedger:
    """Memory ring + optional NDJSON file. Never stores API keys.

    On start the ring is seeded from the tail of the file, so free-quota spread
    and /status survive restarts.
    """

    path: Path | None = None
    memory_limit: int = 500
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _file_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _entries: deque[dict[str, Any]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._entries = deque(maxlen=self.memory_limit)
        self._seed()

    def _seed(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                tail = deque(fh, maxlen=self.memory_limit * 2)  # headroom for bad lines
        except OSError as exc:
            log.warning("ledger seed skipped (%s): %s", self.path, exc)
            return
        for line in tail:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                self._entries.append(row)

    def _remember(self, entry: dict[str, Any]) -> tuple[dict[str, Any], str]:
        row = dict(entry)
        row.setdefault("ts", time.time())
        with self._lock:
            self._entries.append(row)
        return row, json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"

    def _append_line(self, line: str) -> None:
        if self.path is None:
            return
        with self._file_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Synchronous record (scripts/tests). Request handlers use arecord."""
        row, line = self._remember(entry)
        self._append_line(line)
        return row

    async def arecord(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Update memory now; append to disk in a worker thread."""
        row, line = self._remember(entry)
        if self.path is not None:
            try:
                await asyncio.to_thread(self._append_line, line)
            except OSError as exc:
                log.warning("ledger write failed: %s", exc)
        return row

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self._entries)
        return list(reversed(rows[-limit:]))

    def free_usage_counts(self) -> dict[str, int]:
        with self._lock:
            rows = list(self._entries)
        counts: Counter[str] = Counter()
        for row in rows:
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
