"""Sync free-lane availability from `qwencloud usage free-tier`."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qwenr.free import FreeQuotaGate

log = logging.getLogger("qwenr.free_sync")

# Console statuses that mean "do not use free lane for this id".
BLOCK_STATUSES = frozenset({"exhaust", "expire", "exhausted", "expired"})


@dataclass(frozen=True)
class FreeTierRow:
    model_id: str
    status: str
    remaining: float | None
    total: float | None
    used_pct: float | None
    unit: str | None


@dataclass
class FreeTierSyncResult:
    ok: bool
    source: str
    blocked: dict[str, str] = field(default_factory=dict)
    valid: list[str] = field(default_factory=list)
    error: str | None = None
    fetched_at: float = field(default_factory=time.time)
    raw_count: int = 0


def parse_free_tier_payload(payload: dict[str, Any] | list[Any]) -> list[FreeTierRow]:
    rows_raw: list[Any]
    if isinstance(payload, list):
        rows_raw = payload
    elif isinstance(payload, dict):
        rows_raw = payload.get("free_tier") or []
        if not rows_raw and isinstance(payload.get("data"), dict):
            rows_raw = payload["data"].get("free_tier") or []
        if not rows_raw and isinstance(payload.get("data"), list):
            rows_raw = payload["data"]
    else:
        return []
    out: list[FreeTierRow] = []
    for item in rows_raw:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("model_id") or item.get("model") or item.get("id") or "").strip()
        if not mid:
            continue
        quota = item.get("quota") if isinstance(item.get("quota"), dict) else item
        status = str(quota.get("status") or "").strip().casefold()
        out.append(
            FreeTierRow(
                model_id=mid,
                status=status,
                remaining=_num(quota.get("remaining")),
                total=_num(quota.get("total")),
                used_pct=_num(quota.get("used_pct")),
                unit=str(quota.get("unit") or "") or None,
            )
        )
    return out


def should_block(row: FreeTierRow) -> str | None:
    if row.status in BLOCK_STATUSES:
        return row.status or "blocked"
    if row.remaining is not None and row.remaining <= 0:
        return "remaining_zero"
    return None


def apply_free_tier_rows(
    gate: FreeQuotaGate,
    rows: list[FreeTierRow],
    *,
    source: str,
) -> FreeTierSyncResult:
    blocked: dict[str, str] = {}
    valid: list[str] = []
    for row in rows:
        if row.model_id not in gate.eligible:
            continue
        reason = should_block(row)
        if reason:
            blocked[row.model_id] = reason
        else:
            valid.append(row.model_id)
    gate.apply_sync_blocks(blocked, source=source)
    return FreeTierSyncResult(
        ok=True,
        source=source,
        blocked=blocked,
        valid=sorted(valid),
        raw_count=len(rows),
    )


def fetch_free_tier_payload(
    command: str = "qwencloud",
    timeout_seconds: float = 45.0,
) -> tuple[dict[str, Any] | list[Any] | None, str | None]:
    exe = shutil.which(command) or shutil.which(f"{command}.cmd") or shutil.which(f"{command}.exe")
    if not exe:
        return None, f"command not found: {command}"
    try:
        completed = subprocess.run(
            [exe, "usage", "free-tier", "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as exc:
        return None, str(exc)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()[:300]
        return None, f"exit {completed.returncode}: {err or 'unknown'}"
    try:
        return json.loads(completed.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def sync_free_tier(
    gate: FreeQuotaGate,
    *,
    command: str = "qwencloud",
    snapshot_path: Path | None = None,
    timeout_seconds: float = 45.0,
) -> FreeTierSyncResult:
    """Live CLI by default; optional JSON snapshot for tests/airgap."""
    if snapshot_path is not None:
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            gate.note_sync_error(str(exc), source="file")
            return FreeTierSyncResult(ok=False, source="file", error=str(exc))
        rows = parse_free_tier_payload(payload)
        return apply_free_tier_rows(gate, rows, source=f"file:{snapshot_path}")

    payload, error = fetch_free_tier_payload(command=command, timeout_seconds=timeout_seconds)
    if error or payload is None:
        gate.note_sync_error(error or "sync failed", source="cli")
        return FreeTierSyncResult(ok=False, source="cli", error=error or "sync failed")
    rows = parse_free_tier_payload(payload)
    result = apply_free_tier_rows(gate, rows, source="cli")
    log.info(
        "free-tier sync ok: eligible_valid=%s blocked=%s raw=%s",
        len(result.valid),
        sorted(result.blocked),
        result.raw_count,
    )
    return result


def _num(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
