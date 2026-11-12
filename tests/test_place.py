from datetime import datetime
from zoneinfo import ZoneInfo

from qwenr.affinity import load_affinity
from qwenr.discounts import load_discounts
from qwenr.free import FreeQuotaGate, looks_like_quota_or_billing_block
from qwenr.place import place

SH = ZoneInfo("Asia/Shanghai")


def test_coord_prefers_strong_model_by_day():
    aff = load_affinity()
    disc = load_discounts()
    day = datetime(2026, 11, 12, 14, 0, tzinfo=SH)
    chosen = place("auto:coord", aff, disc, economy_lambda=12.0, now=day, free_first=False)
    assert chosen.model_id in {"qwen4-max-preview", "qwen3.8-max", "qwen3.7-max", "qwen3.8-max-0902"}
    assert chosen.affinity >= 90
    assert chosen.lane == "token_plan"


def test_night_shifts_toward_discounted_flash_or_max():
    aff = load_affinity()
    disc = load_discounts()
    night = datetime(2026, 11, 12, 23, 30, tzinfo=SH)
    day = datetime(2026, 11, 12, 14, 0, tzinfo=SH)
    night_pick = place("auto:qa", aff, disc, economy_lambda=20.0, now=night, free_first=False)
    day_pick = place("auto:qa", aff, disc, economy_lambda=20.0, now=day, free_first=False)
    assert night_pick.cost_weight <= day_pick.cost_weight
    assert night_pick.reason in {"night_deal", "fit", "promo"}


def test_chimpr_prefix_strips():
    aff = load_affinity()
    disc = load_discounts()
    chosen = place("chimpr/auto:coder", aff, disc, economy_lambda=8.0, free_first=False)
    assert chosen.requested == "auto:coder"


def test_free_first_beats_token_plan_when_candidates_exist():
    aff = load_affinity()
    disc = load_discounts()
    chosen = place(
        "auto:coord",
        aff,
        disc,
        free_first=True,
        free_candidates=("qwen3.8-flash", "qwen3.8-max"),
    )
    assert chosen.lane == "free"
    assert chosen.reason == "free_quota"
    assert chosen.model_id == "qwen3.8-max"  # higher coord affinity than flash
    assert chosen.cost_weight == 0.0


def test_exhausted_free_falls_through_to_token_plan():
    aff = load_affinity()
    disc = load_discounts()
    gate = FreeQuotaGate(eligible=frozenset({"qwen3.8-max", "qwen3.8-flash"}))
    gate.mark_exhausted("qwen3.8-max")
    gate.mark_exhausted("qwen3.8-flash")
    chosen = place(
        "auto:coord",
        aff,
        disc,
        free_first=True,
        free_candidates=gate.available(),
        now=datetime(2026, 11, 12, 14, 0, tzinfo=SH),
    )
    assert chosen.lane == "token_plan"
    assert chosen.model_id != "qwen3.8-flash" or chosen.reason != "free_quota"


def test_quota_marker_detection():
    assert looks_like_quota_or_billing_block(403, b'{"error":{"message":"Free quota exhausted"}}')
    assert not looks_like_quota_or_billing_block(500, b'{"error":{"message":"internal"}}')
