from pathlib import Path

from qwenr.free import FreeQuotaGate
from qwenr.free_sync import apply_free_tier_rows, parse_free_tier_payload, should_block, sync_free_tier
from qwenr.place import place
from qwenr.affinity import load_affinity
from qwenr.discounts import load_discounts

FIXTURE = Path(__file__).parent / "fixtures" / "free_tier.json"


def test_parse_and_block_reasons():
    payload = {
        "free_tier": [
            {"model_id": "a", "quota": {"status": "valid", "remaining": 10}},
            {"model_id": "b", "quota": {"status": "exhaust", "remaining": 0}},
            {"model_id": "c", "quota": {"status": "expire", "remaining": 100}},
            {"model_id": "d", "quota": {"status": "valid", "remaining": 0}},
        ]
    }
    rows = parse_free_tier_payload(payload)
    assert should_block(rows[0]) is None
    assert should_block(rows[1]) == "exhaust"
    assert should_block(rows[2]) == "expire"
    assert should_block(rows[3]) == "remaining_zero"


def test_sync_from_fixture_blocks_exhausted():
    gate = FreeQuotaGate(
        eligible=frozenset({"qwen3.8-flash", "kimi-k3.1-pro", "qwen3.8-max", "glm-5.3", "deepseek-v4.1-flash"})
    )
    result = sync_free_tier(gate, snapshot_path=FIXTURE)
    assert result.ok
    assert "kimi-k3.1-pro" in result.blocked
    assert "qwen3.8-max" in result.blocked
    assert "qwen3.8-flash" in result.valid
    available = gate.available()
    assert "kimi-k3.1-pro" not in available
    assert "qwen3.8-max" not in available
    assert "qwen3.8-flash" in available
    # Not in snapshot → still available (no false block).
    assert "deepseek-v4.1-flash" in available


def test_place_skips_sync_blocked_free_models():
    aff = load_affinity()
    disc = load_discounts()
    gate = FreeQuotaGate(eligible=frozenset({"kimi-k3.1-pro", "qwen3.8-flash", "glm-5.3"}))
    sync_free_tier(gate, snapshot_path=FIXTURE)
    chosen = place(
        "auto:coder",
        aff,
        disc,
        free_first=True,
        free_candidates=gate.available(),
    )
    assert chosen.lane == "free"
    assert chosen.model_id != "kimi-k3.1-pro"
