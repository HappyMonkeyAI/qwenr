"""Load qwenr config from YAML."""

from __future__ import annotations

import ipaddress
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
    # When true, never call the PAYG key. Free quota stays unused.
    token_plan_only: bool = False
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
    free_tier_sync_max_age_seconds: float = 900.0
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
    api_key = _blank(data.get("api_key"))
    if not api_key and not _is_loopback(host):
        raise ConfigError(
            f"listen {host}:{port} is reachable beyond this machine; set api_key (QWENR_API_KEY) "
            "so callers must send 'Authorization: Bearer <key>'."
        )
    token_plan = _parse_token_plan(data)
    payg = _parse_payg(data)
    root = path.parent
    models_path = _optional_path(data.get("models_path"), root) or (root / "models.yaml")
    discounts_path = _optional_path(data.get("discounts_path"), root) or (root / "discounts.yaml")
    free_path = _optional_path(data.get("free_models_path"), root) or (root / "free_models.yaml")
    ttl = _float(data, "free_exhausted_ttl_seconds", 3600.0)
    max_age = _float(data, "free_tier_sync_max_age_seconds", 900.0)
    free_gate = FreeQuotaGate(
        eligible=load_free_models(free_path),
        ttl_seconds=ttl,
        max_age_seconds=max_age,
    )
    return Settings(
        listen_host=host,
        listen_port=port,
        api_key=api_key,
        token_plan=token_plan,
        payg=payg,
        affinity=load_affinity(models_path),
        discounts=load_discounts(discounts_path),
        free_gate=free_gate,
        free_first=_bool(data, "free_first", True),
        token_plan_only=_bool(data, "token_plan_only", False),
        payg_allow_paid=_bool(data, "payg_allow_paid", False),
        sticky=_bool(data, "sticky", True),
        sticky_ttl_seconds=_float(data, "sticky_ttl_seconds", 1800.0),
        economy_lambda=_float(data, "economy_lambda", 12.0),
        free_exhausted_ttl_seconds=ttl,
        free_affinity_slack=int(_float(data, "free_affinity_slack", 3)),
        ledger_path=_optional_path(data.get("ledger_path") or "placement.ndjson", root),
        free_tier_sync=_bool(data, "free_tier_sync", True),
        free_tier_sync_ttl_seconds=_float(data, "free_tier_sync_ttl_seconds", 300.0),
        free_tier_sync_max_age_seconds=max_age,
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
        prefer_platform_auto=_bool(raw, "prefer_platform_auto", False, prefix="token_plan."),
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
    if not sep or not port.strip().isdigit() or not 1 <= int(port) <= 65535:
        raise ConfigError(f"listen must look like 127.0.0.1:8788 (port 1-65535), got {value!r}.")
    return host or "127.0.0.1", int(port)


_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


def _bool(data: dict, key: str, default: bool, *, prefix: str = "") -> bool:
    # Env expansion turns YAML booleans into strings ("${X:-false}" -> "false"),
    # and bool("false") is True. Parse explicitly; blank means default.
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ConfigError(f"{prefix}{key} must be true or false, got {value!r}.")


def _float(data: dict, key: str, default: float) -> float:
    # Explicit None/blank check so 0 is a real value, not "use the default".
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise ConfigError(f"{key} must be a number, got {value!r}.")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{key} must be a number, got {value!r}.") from None


def _is_loopback(host: str) -> bool:
    name = host.strip().strip("[]")
    if name.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _blank(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_path(value: object, root: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else root / path
