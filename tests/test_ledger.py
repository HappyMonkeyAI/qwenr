from pathlib import Path

from qwenr.ledger import PlacementLedger


def test_ledger_records_and_summarizes(tmp_path: Path):
    path = tmp_path / "placement.ndjson"
    ledger = PlacementLedger(path=path, memory_limit=10)
    ledger.record(
        {
            "event": "completion",
            "ok": True,
            "requested": "auto:coord",
            "model": "qwen3.8-max-0902",
            "lane": "free",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    ledger.record(
        {
            "event": "failover",
            "ok": False,
            "requested": "auto:coder",
            "model": "qwen3.8-flash",
            "lane": "free",
        }
    )
    ledger.record(
        {
            "event": "completion",
            "ok": True,
            "requested": "auto:coder",
            "model": "kimi-k3.1-pro",
            "lane": "free",
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }
    )
    summary = ledger.summary()
    assert summary["total"] == 3
    assert summary["free_ok"] == 2
    assert summary["failovers"] == 1
    assert summary["prompt_tokens"] == 13
    assert ledger.free_usage_counts()["qwen3.8-max-0902"] == 1
    assert ledger.free_usage_counts()["kimi-k3.1-pro"] == 1
    assert path.is_file()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3
    assert ledger.recent(1)[0]["model"] == "kimi-k3.1-pro"

# --- 11. Seeded from disk, file writes off the event loop --------------------

import json  # noqa: E402
import threading  # noqa: E402


def _row(model: str, lane: str = "free", ok: bool = True) -> str:
    return json.dumps({"event": "completion", "ok": ok, "lane": lane, "model": model})


def test_ledger_seeds_memory_from_existing_file(tmp_path: Path):
    path = tmp_path / "placement.ndjson"
    path.write_text("\n".join([_row("m1"), _row("m1"), "not json", _row("m2", lane="token_plan"), _row("m1")]) + "\n")
    ledger = PlacementLedger(path=path)
    assert ledger.free_usage_counts() == {"m1": 3}
    assert len(ledger.recent(50)) == 4


def test_ledger_seed_respects_memory_limit(tmp_path: Path):
    path = tmp_path / "placement.ndjson"
    path.write_text("\n".join(_row(f"m{i}") for i in range(10)) + "\n")
    ledger = PlacementLedger(path=path, memory_limit=4)
    assert [r["model"] for r in ledger.recent(50)] == ["m9", "m8", "m7", "m6"]


async def test_arecord_writes_file_off_the_event_loop(tmp_path: Path, monkeypatch):
    path = tmp_path / "placement.ndjson"
    ledger = PlacementLedger(path=path)
    threads: list[int] = []
    original = ledger._append_line

    def spy(line: str) -> None:
        threads.append(threading.get_ident())
        original(line)

    monkeypatch.setattr(ledger, "_append_line", spy)
    await ledger.arecord({"event": "completion", "ok": True, "lane": "free", "model": "m1"})
    assert ledger.free_usage_counts() == {"m1": 1}
    assert threads and threads[0] != threading.get_ident()
    assert json.loads(path.read_text().strip())["model"] == "m1"
