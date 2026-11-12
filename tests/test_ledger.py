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