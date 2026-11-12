"""Load qwenr config from YAML."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from qwenr.affinity import AffinityCatalog, load_affinity
from qwenr.discounts import DiscountCatalog, load_discounts
from qwenr.free import FreeQuotaGate, load_free_models

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class UpstreamConfig:
    base_url: str
    api_key: str | None
    prefer_platform_auto: bool = False


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listen_port: int
    api_key: str | None
    token_plan: UpstreamConfig
    payg: UpstreamConfig | None
    affinity: AffinityCatalog
    discounts: DiscountCatalog
    free_gate: FreeQuotaGate
    free_first: bool = True
    # Safety default: never send PAYG once free quota is gone / unknown paid path.
    payg_allow_paid: bool = False
    sticky: bool = True
    sticky_ttl_seconds: float = 1800.0
    economy_lambda: float = 12.0
    free_exhausted_ttl_seconds: float = 3600.0
    free_affinity_slack: int = 3
    ledger_path: Path | None = None
    free_tier_sync: bool = True
    free_tier_sync_ttl_seconds: float = 300.0
    free_tier_sync_command: str = "qwencloud"
    free_tier_snapshot_path: Path | None = None
    config_path: Path | None = None

    # Back-compat alias used by older smoke helpers.
    @property
    def upstream(self) -> UpstreamConfig:
        return self.token_plan


def expand_env(value: object) -> object:
    if isinstance(value, str):
        return _ENV.sub(_replace_env, value)
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value


def _replace_env(match: re.Match[str]) -> str:
    name, default = match.group(1), match.group(2)
    if name in os.environ:
        return os.environ[name]
    return default if default is not None else ""


def load_settings(path: Path) -> Settings:
    if not path.is_file():
        raise ConfigError(f"Config not found at {path}. Copy config.example.yaml to config.yaml.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping.")
    data = expand_env(raw)
    assert isinstance(data, dict)
    host, port = _parse_listen(str(data.get("listen") or "127.0.0.1:8788"))
    token_plan = _parse_token_plan(data)
    payg = _parse_payg(data)
    root = path.parent
    models_path = _optional_path(data.get("models_path"), root) or (root / "models.yaml")
    discounts_path = _optional_path(data.get("discounts_path"), root) or (root / "discounts.yaml")
    free_path = _optional_path(data.get("free_models_path"), root) or (root / "free_models.yaml")
    ttl = float(data.get("free_exhausted_ttl_seconds") or 3600)
    free_gate = FreeQuotaGate(eligible=load_free_models(free_path), ttl_seconds=ttl)
    return Settings(
        listen_host=host,
        listen_port=port,
        api_key=_blank(data.get("api_key")),
        token_plan=token_plan,
        payg=payg,
        affinity=load_affinity(models_path),
        discounts=load_discounts(discounts_path),
        free_gate=free_gate,
        free_first=True if data.get("free_first") is None else bool(data.get("free_first")),
        payg_allow_paid=bool(data.get("payg_allow_paid") or False),
        sticky=True if data.get("sticky") is None else bool(data.get("sticky")),
        sticky_ttl_seconds=float(data.get("sticky_ttl_seconds") or 1800),
        economy_lambda=float(data.get("economy_lambda") or 12.0),
        free_exhausted_ttl_seconds=ttl,
        free_affinity_slack=int(data.get("free_affinity_slack") if data.get("free_affinity_slack") is not None else 3),
        ledger_path=_optional_path(data.get("ledger_path") or "placement.ndjson", root),
        free_tier_sync=True if data.get("free_tier_sync") is None else bool(data.get("free_tier_sync")),
        free_tier_sync_ttl_seconds=float(data.get("free_tier_sync_ttl_seconds") or 300),
        free_tier_sync_command=str(data.get("free_tier_sync_command") or "qwencloud").strip() or "qwencloud",
        free_tier_snapshot_path=_optional_path(data.get("free_tier_snapshot_path"), root),
        config_path=path,
    )


def _parse_token_plan(data: dict) -> UpstreamConfig:
    # Prefer token_plan:; fall back to legacy upstream:.
    raw = data.get("token_plan") or data.get("upstream") or {}
    if not isinstance(raw, dict):
        raise ConfigError("token_plan must be a mapping.")
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        raise ConfigError("token_plan.base_url is required.")
    return UpstreamConfig(
        base_url=base_url,
        api_key=_blank(raw.get("api_key")),
        prefer_platform_auto=bool(raw.get("prefer_platform_auto") or False),
    )


def _parse_payg(data: dict) -> UpstreamConfig | None:
    raw = data.get("payg")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("payg must be a mapping.")
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return None
    return UpstreamConfig(
        base_url=base_url,
        api_key=_blank(raw.get("api_key")),
        prefer_platform_auto=False,
    )


def _parse_listen(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep:
        raise ConfigError(f"listen must look like 127.0.0.1:8788, got {value!r}.")
    return host or "127.0.0.1", int(port)


def _blank(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_path(value: object, root: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else root / path
