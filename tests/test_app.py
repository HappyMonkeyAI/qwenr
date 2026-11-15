"""App-level tests: auth, free→Token Plan failover, non-blocking free-tier sync."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from datetime import time as dtime
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from qwenr import app as app_module
from qwenr.affinity import AffinityCatalog
from qwenr.app import build_app
from qwenr.config import ConfigError, Settings, UpstreamConfig, load_settings
from qwenr.discounts import DiscountCatalog
from qwenr.free import FreeQuotaGate
from qwenr.free_sync import FreeTierSyncResult
from qwenr.place import Placement

PAYG = "https://payg.test/v1"
TOKEN_PLAN = "https://tp.test/v1"

# "best" is the strongest model on both lanes; "other" is a weaker free fallback.
AFFINITY = AffinityCatalog(
    scores={
        "best": {"auto": 95, "coord": 95},
        "other": {"auto": 60, "coord": 60},
    }
)
DISCOUNTS = DiscountCatalog(
    base_weight={"best": 1.0, "other": 1.0},
    promos=(),
    night_start=dtime(22, 0),
    night_end=dtime(8, 0),
)


def make_settings(
    *,
    free: set[str] = frozenset(),
    api_key: str | None = None,
    eligible: set[str] = frozenset({"best", "other"}),
    **overrides,
) -> Settings:
    gate = FreeQuotaGate(eligible=frozenset(eligible))
    gate.apply_sync_blocks({}, allowed=set(free), source="test")
    base = dict(
        listen_host="127.0.0.1",
        listen_port=8788,
        api_key=api_key,
        token_plan=UpstreamConfig(base_url=TOKEN_PLAN, api_key="sk-sp-test"),
        payg=UpstreamConfig(base_url=PAYG, api_key="sk-ws-test"),
        affinity=AFFINITY,
        discounts=DISCOUNTS,
        free_gate=gate,
        free_tier_sync=False,
        ledger_path=None,
    )
    base.update(overrides)
    return Settings(**base)


def ok_body(model: str) -> dict:
    return {"id": "x", "model": model, "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


def upstream(payg_status: int = 200, payg_body: dict | None = None):
    """Mock upstream. Records (lane, model) per call."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        if str(request.url).startswith(PAYG):
            calls.append(("payg", model))
            return httpx.Response(payg_status, json=payg_body or ok_body(model))
        calls.append(("token_plan", model))
        return httpx.Response(200, json=ok_body(model))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


QUOTA_BLOCK = {"error": {"message": "Free quota exhausted for this model."}}


def chat(client: TestClient, model: str, **kw) -> httpx.Response:
    return client.post("/v1/chat/completions", json={"model": model, "messages": []}, **kw)


# --- 1. API key enforcement -------------------------------------------------


def test_api_key_required_when_configured():
    http, _ = upstream()
    with TestClient(build_app(make_settings(api_key="secret-key"), client=http)) as client:
        assert chat(client, "auto").status_code == 401
        assert chat(client, "auto", headers={"authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/status").status_code == 401
        assert chat(client, "auto", headers={"authorization": "Bearer secret-key"}).status_code == 200
        assert client.get("/health").status_code == 200  # liveness stays open


def test_no_api_key_means_open_on_loopback():
    http, _ = upstream()
    with TestClient(build_app(make_settings(), client=http)) as client:
        assert chat(client, "auto").status_code == 200


def _write_config(tmp_path: Path, listen: str, api_key: str = "") -> Path:
    for name in ("models.yaml", "discounts.yaml", "free_models.yaml"):
        (tmp_path / name).write_text((Path(__file__).parents[1] / name).read_text(encoding="utf-8"), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"listen: {listen}\napi_key: '{api_key}'\ntoken_plan:\n  base_url: {TOKEN_PLAN}\n",
        encoding="utf-8",
    )
    return cfg


def test_non_loopback_bind_requires_api_key(tmp_path: Path):
    with pytest.raises(ConfigError, match="api_key"):
        load_settings(_write_config(tmp_path, "0.0.0.0:8788"))


def test_non_loopback_bind_with_api_key_loads(tmp_path: Path):
    settings = load_settings(_write_config(tmp_path, "0.0.0.0:8788", api_key="secret-key"))
    assert settings.api_key == "secret-key"


# --- 3. Free-lane failover is lane-scoped -----------------------------------


def test_exact_model_quota_block_fails_over_to_token_plan_same_model():
    http, calls = upstream(payg_status=429, payg_body=QUOTA_BLOCK)
    settings = make_settings(free={"best"})
    with TestClient(build_app(settings, client=http)) as client:
        resp = chat(client, "best")
    assert resp.status_code == 200
    assert resp.headers["x-qwenr-lane"] == "token_plan"
    assert resp.headers["x-qwenr-model"] == "best"
    assert calls == [("payg", "best"), ("token_plan", "best")]


def test_role_quota_block_keeps_model_eligible_on_token_plan():
    http, calls = upstream(payg_status=429, payg_body=QUOTA_BLOCK)
    settings = make_settings(free={"best"})
    with TestClient(build_app(settings, client=http)) as client:
        resp = chat(client, "auto:coord")
    assert resp.status_code == 200
    assert resp.headers["x-qwenr-lane"] == "token_plan"
    assert resp.headers["x-qwenr-model"] == "best"  # not demoted to "other"
    assert calls == [("payg", "best"), ("token_plan", "best")]


# --- 2. Free-tier sync must not block the event loop or retry-storm ---------


def test_failed_sync_runs_off_loop_and_backs_off(monkeypatch):
    seen: list[int] = []

    def fake_sync(gate, **_kw):
        seen.append(threading.get_ident())
        gate.note_sync_error("timeout", source="cli")
        return FreeTierSyncResult(ok=False, source="cli", error="timeout")

    monkeypatch.setattr(app_module, "sync_free_tier", fake_sync)
    http, _ = upstream()
    settings = make_settings(free_tier_sync=True, free_tier_sync_ttl_seconds=300)
    settings.free_gate._sync_at = None  # never synced: due for refresh
    settings.free_gate._sync_allowed = None
    loop_threads: list[int] = []

    with TestClient(build_app(settings, client=http)) as client:
        client.portal.call(lambda: loop_threads.append(threading.get_ident()))
        assert len(seen) == 1 and seen[0] != loop_threads[0], "startup sync must also run off-loop"
        seen.clear()
        settings.free_gate._sync_attempt_at = None  # make the next refresh due
        for _ in range(3):
            assert chat(client, "auto").status_code == 200
        client.get("/status")

    assert len(seen) == 1, "failed refresh must back off for the TTL, not retry per request"
    assert seen[0] != loop_threads[0], "sync must run in a worker thread, not on the event loop"


# --- 6. Retry budget covers every free candidate plus Token Plan -----------


def test_many_free_quota_blocks_still_reach_token_plan():
    free_ids = [f"free{i}" for i in range(6)]
    affinity = AffinityCatalog(scores={mid: {"auto": 90 - i} for i, mid in enumerate(free_ids)} | {"tp": {"auto": 80}})
    http, calls = upstream(payg_status=429, payg_body=QUOTA_BLOCK)
    settings = make_settings(free=set(free_ids), eligible=set(free_ids), affinity=affinity, free_affinity_slack=0)
    with TestClient(build_app(settings, client=http)) as client:
        resp = chat(client, "auto")
    assert resp.status_code == 200
    assert resp.headers["x-qwenr-lane"] == "token_plan"
    assert [lane for lane, _ in calls] == ["payg"] * 6 + ["token_plan"]


# --- 7. Streaming upstream is always released -------------------------------


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self):
        yield b"data: {}\n\n"

    async def aclose(self) -> None:
        self.closed = True


async def _streaming_response(stream: TrackingStream):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(client=httpx.AsyncClient(transport=transport))))
    placement = Placement(model_id="best", requested="auto", reason="fit", affinity=95, cost_weight=1.0, score=1.0)
    upstream_cfg = UpstreamConfig(base_url=TOKEN_PLAN, api_key="sk-sp-test")
    return await app_module._dispatch(request, upstream_cfg, {"model": "best", "stream": True}, placement)


HTTP_SCOPE = {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "POST", "path": "/", "headers": []}


async def _never_receive():
    raise AssertionError("receive should not be needed")


async def test_stream_released_when_client_disconnects_before_body():
    stream = TrackingStream()
    response = await _streaming_response(stream)

    async def send(message):
        raise OSError("client went away")  # disconnect on http.response.start

    with pytest.raises(Exception):
        await response(HTTP_SCOPE, _never_receive, send)
    assert stream.closed, "upstream stream must be closed even if the body was never iterated"


async def test_stream_released_after_normal_completion():
    stream = TrackingStream()
    response = await _streaming_response(stream)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await response(HTTP_SCOPE, _never_receive, send)
    assert stream.closed
    assert b"data:" in b"".join(m.get("body", b"") for m in sent)


# --- 9. Role aliases and skip prefixes come from one place ------------------


def test_models_endpoint_uses_shared_role_and_skip_lists():
    from qwenr.place import ROLE_ALIASES, SKIP_PREFIXES

    affinity = AffinityCatalog(scores={"best": {"auto": 90}, f"{SKIP_PREFIXES[0]}-x": {"auto": 90}})
    http, _ = upstream()
    with TestClient(build_app(make_settings(affinity=affinity), client=http)) as client:
        ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    assert ids[: len(ROLE_ALIASES)] == list(ROLE_ALIASES)
    assert "best" in ids
    assert not any(i.startswith(SKIP_PREFIXES) for i in ids)


# --- 12. Failover rows record which quota marker matched --------------------


def test_failover_ledger_row_records_marker():
    http, _ = upstream(payg_status=429, payg_body=QUOTA_BLOCK)
    with TestClient(build_app(make_settings(free={"best"}), client=http)) as client:
        chat(client, "best")
        rows = client.app.state.ledger.recent(10)
    failover = next(r for r in rows if r["event"] == "failover")
    assert failover["marker"] == "free quota"


# --- 13. Request size limit --------------------------------------------------


def test_oversized_body_rejected_by_content_length():
    http, calls = upstream()
    with TestClient(build_app(make_settings(max_request_bytes=200), client=http)) as client:
        resp = client.post("/v1/chat/completions", json={"model": "auto", "messages": [{"content": "x" * 500}]})
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "request_too_large"
    assert calls == []


def test_oversized_chunked_body_rejected():
    http, calls = upstream()
    body = json.dumps({"model": "auto", "messages": [{"content": "x" * 500}]}).encode()

    def chunks():
        for i in range(0, len(body), 64):
            yield body[i : i + 64]

    with TestClient(build_app(make_settings(max_request_bytes=200), client=http)) as client:
        resp = client.post("/v1/chat/completions", content=chunks(), headers={"content-type": "application/json"})
    assert resp.status_code == 413
    assert calls == []


def test_body_under_limit_is_served():
    http, _ = upstream()
    with TestClient(build_app(make_settings(max_request_bytes=10_000), client=http)) as client:
        assert chat(client, "auto").status_code == 200


# --- 15. Money-path coverage ------------------------------------------------


def test_missing_payg_key_never_calls_payg():
    http, calls = upstream()
    settings = make_settings(free={"best"}, payg=UpstreamConfig(base_url=PAYG, api_key=None))
    with TestClient(build_app(settings, client=http)) as client:
        resp = chat(client, "auto:coord")
    assert resp.status_code == 200
    assert resp.headers["x-qwenr-lane"] == "token_plan"
    assert all(lane == "token_plan" for lane, _ in calls)


def test_paid_payg_guard_blocks_non_free_placement_on_payg(monkeypatch):
    http, calls = upstream()
    monkeypatch.setattr(app_module, "_upstream_for", lambda s, p: s.payg)  # simulate a routing bug
    with TestClient(build_app(make_settings(), client=http)) as client:
        resp = chat(client, "auto")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "payg_paid_blocked"
    assert calls == []


def test_streaming_free_quota_block_fails_over_to_token_plan():
    http, calls = upstream(payg_status=429, payg_body=QUOTA_BLOCK)
    with TestClient(build_app(make_settings(free={"best"}), client=http)) as client:
        resp = client.post("/v1/chat/completions", json={"model": "auto:coord", "messages": [], "stream": True})
    assert resp.status_code == 200
    assert resp.headers["x-qwenr-lane"] == "token_plan"
    assert calls == [("payg", "best"), ("token_plan", "best")]


# --- Follow-up: Content-Length must be ASCII decimal digits -----------------


def _stub_request(content_length: str, body: bytes):
    async def stream():
        yield body

    return SimpleNamespace(headers={"content-length": content_length}, stream=stream)


@pytest.mark.parametrize("declared", ["²00", "\u0663\u0660\u0660\u0660"])  # superscript; Arabic-Indic 3000
async def test_non_ascii_content_length_is_ignored_not_trusted(declared: str):
    body = b'{"model":"auto"}'
    assert await app_module._read_body_limited(_stub_request(declared, body), 200) == body


async def test_non_ascii_content_length_still_enforces_streamed_limit():
    assert await app_module._read_body_limited(_stub_request("²", b"x" * 500), 200) is None


async def test_ascii_content_length_over_limit_rejected_before_reading():
    assert await app_module._read_body_limited(_stub_request("500", b""), 200) is None
