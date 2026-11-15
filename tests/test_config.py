"""Config parsing: strict booleans, zero-valued numbers, listen validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from qwenr.config import ConfigError, load_settings

ROOT = Path(__file__).parents[1]


def write_config(tmp_path: Path, extra: str = "", listen: str = "127.0.0.1:8788") -> Path:
    for name in ("models.yaml", "discounts.yaml", "free_models.yaml"):
        (tmp_path / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"listen: {listen}\ntoken_plan:\n  base_url: https://tp.test/v1\n{extra}",
        encoding="utf-8",
    )
    return cfg


# --- 4. Booleans ------------------------------------------------------------


def test_env_templated_false_stays_false(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("QWENR_TEST_PAID", raising=False)
    s = load_settings(write_config(tmp_path, "payg_allow_paid: ${QWENR_TEST_PAID:-false}\n"))
    assert s.payg_allow_paid is False


def test_env_templated_true_is_true(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QWENR_TEST_PAID", "yes")
    s = load_settings(write_config(tmp_path, "payg_allow_paid: ${QWENR_TEST_PAID:-false}\n"))
    assert s.payg_allow_paid is True


@pytest.mark.parametrize("key", ["free_first", "token_plan_only", "free_tier_sync", "sticky"])
def test_string_false_parses_false_for_every_flag(tmp_path: Path, key: str):
    s = load_settings(write_config(tmp_path, f"{key}: 'false'\n"))
    assert getattr(s, key) is False


def test_empty_bool_uses_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("QWENR_TEST_FF", raising=False)
    s = load_settings(write_config(tmp_path, "free_first: ${QWENR_TEST_FF:-}\npayg_allow_paid: ''\n"))
    assert s.free_first is True
    assert s.payg_allow_paid is False


def test_garbage_bool_is_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="payg_allow_paid"):
        load_settings(write_config(tmp_path, "payg_allow_paid: maybe\n"))


# --- 5. Zero-valued numbers and listen --------------------------------------


def test_zero_numbers_are_respected(tmp_path: Path):
    s = load_settings(
        write_config(
            tmp_path,
            "economy_lambda: 0\nfree_tier_sync_ttl_seconds: 0\nfree_exhausted_ttl_seconds: 0\nsticky_ttl_seconds: 0\n",
        )
    )
    assert s.economy_lambda == 0.0
    assert s.free_tier_sync_ttl_seconds == 0.0
    assert s.free_exhausted_ttl_seconds == 0.0
    assert s.free_gate.ttl_seconds == 0.0
    assert s.sticky_ttl_seconds == 0.0


def test_missing_numbers_use_defaults(tmp_path: Path):
    s = load_settings(write_config(tmp_path))
    assert s.economy_lambda == 12.0
    assert s.free_tier_sync_ttl_seconds == 300.0
    assert s.free_tier_sync_max_age_seconds == 900.0
    assert s.free_affinity_slack == 3


def test_non_numeric_number_is_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="economy_lambda"):
        load_settings(write_config(tmp_path, "economy_lambda: lots\n"))


@pytest.mark.parametrize("listen", ["127.0.0.1:abc", "127.0.0.1:0", "127.0.0.1:70000"])
def test_bad_listen_port_is_config_error(tmp_path: Path, listen: str):
    with pytest.raises(ConfigError, match="listen"):
        load_settings(write_config(tmp_path, listen=listen))
