"""OpenAI-compatible proxy: free PAYG quota first, then Token Plan Credits."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from qwenr import __version__
from qwenr.config import Settings, UpstreamConfig
from qwenr.free import looks_like_quota_or_billing_block
from qwenr.free_sync import sync_free_tier
from qwenr.ledger import PlacementLedger
from qwenr.place import PlaceError, Placement, place

log = logging.getLogger("qwenr")


def build_app(settings: Settings, client: httpx.AsyncClient | None = None) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        owns = client is None
        http = client or httpx.AsyncClient(timeout=httpx.Timeout(600.0))
        app.state.settings = settings
        app.state.client = http
        app.state.ledger = PlacementLedger(path=settings.ledger_path)
        app.state.sync_lock = asyncio.Lock()
        if settings.free_tier_sync:
            result = await asyncio.to_thread(_run_free_tier_sync, settings)
            if result.ok:
                log.info("free-tier sync startup: allowed=%s blocked=%s", result.valid, sorted(result.blocked))
            else:
                log.warning("free-tier sync failed at startup; free lane closed: %s", result.error)
        try:
            yield
        finally:
            if owns:
                await http.aclose()

    routes = [
        Route("/health", health),
        Route("/status", status),
        Route("/v1/models", models),
        Route("/v1/chat/completions", chat, methods=["POST"]),
    ]
    middleware = [Middleware(BearerAuth, key=settings.api_key)]
    return Starlette(routes=routes, lifespan=lifespan, middleware=middleware)


class BearerAuth:
    """Require `Authorization: Bearer <api_key>` on everything but /health when a key is set."""

    def __init__(self, app: ASGIApp, key: str | None) -> None:
        self.app = app
        self.key = key.encode() if key else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.key is None or scope["type"] != "http" or scope["path"] == "/health":
            await self.app(scope, receive, send)
            return
        auth = dict(scope["headers"]).get(b"authorization", b"")
        scheme, _, token = auth.partition(b" ")
        if scheme.lower() != b"bearer" or not hmac.compare_digest(token.strip(), self.key):
            response = _error(401, "unauthorized", "Missing or invalid API key.")
            response.headers["www-authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__, "service": "qwenr"})


async def status(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    ledger: PlacementLedger = request.app.state.ledger
    return JSONResponse(
        {
            "service": "qwenr",
            "version": __version__,
            "token_plan": settings.token_plan.base_url,
            "payg": settings.payg.base_url if settings.payg else None,
            "free_first": settings.free_first,
            "token_plan_only": settings.token_plan_only,
            "payg_allow_paid": settings.payg_allow_paid,
            "free_affinity_slack": settings.free_affinity_slack,
            # Only models the free lane can actually attempt (needs PAYG key).
            "free_available": list(_free_candidates(settings)),
            "free_tier_sync": settings.free_gate.sync_status(),
            "economy_lambda": settings.economy_lambda,
            "night": settings.discounts.is_night(),
            "ledger": str(settings.ledger_path) if settings.ledger_path else None,
            "summary": ledger.summary(),
            "recent": ledger.recent(20),
        }
    )


async def models(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    data = [{"id": mid, "object": "model", "owned_by": "qwenr"} for mid in _alias_ids()]
    free = set(settings.free_gate.available())
    for mid in settings.affinity.model_ids():
        if mid == "auto" or mid.startswith(("qwen-image", "qwen-audio", "wan", "happyhorse")):
            continue
        owner = "free-quota" if mid in free else "token-plan"
        data.append({"id": mid, "object": "model", "owned_by": owner})
    return JSONResponse({"object": "list", "data": data})


async def chat(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    ledger: PlacementLedger = request.app.state.ledger
    try:
        payload = await request.json()
    except Exception:
        return _error(400, "invalid_json", "Request body must be JSON.")
    if not isinstance(payload, dict):
        return _error(400, "invalid_json", "Request body must be a JSON object.")

    requested = payload.get("model")
    # Free-lane failures only remove a model from the free lane; the same id may
    # still be the right Token Plan pick.
    free_exclude: set[str] = set()
    last_error: Response | None = None
    attempt = 0

    await _maybe_refresh_free_tier(request.app)
    # Each free model may fail over once; then one Token Plan attempt (+1 spare).
    # A fixed budget let a burst of free quota blocks return an error before
    # Token Plan was ever tried.
    max_attempts = len(_free_candidates(settings)) + 2
    for attempt in range(max_attempts):
        free_candidates = tuple(m for m in _free_candidates(settings) if m not in free_exclude)
        try:
            placement = place(
                str(requested) if requested is not None else None,
                settings.affinity,
                settings.discounts,
                economy_lambda=settings.economy_lambda,
                prefer_platform_auto=settings.token_plan.prefer_platform_auto,
                free_candidates=free_candidates,
                free_first=settings.free_first and bool(free_candidates),
                exclude=frozenset(),
                free_affinity_slack=settings.free_affinity_slack,
                free_usage=ledger.free_usage_counts(),
            )
        except PlaceError as exc:
            if last_error is not None:
                return last_error
            return _error(exc.status, exc.code, exc.message)

        log.info(
            "place %s -> %s lane=%s reason=%s aff=%s cost=%.3f",
            placement.requested,
            placement.model_id,
            placement.lane,
            placement.reason,
            placement.affinity,
            placement.cost_weight,
        )

        upstream = _upstream_for(settings, placement)
        if upstream is None or not upstream.api_key:
            if placement.lane == "free":
                # Cannot safely use free lane without PAYG key — skip to Token Plan.
                settings.free_gate.mark_exhausted(placement.model_id)
                free_exclude.add(placement.model_id)
                ledger.record(
                    {
                        "event": "failover",
                        "ok": False,
                        "requested": placement.requested,
                        "model": placement.model_id,
                        "lane": placement.lane,
                        "reason": "no_payg_key",
                        "affinity": placement.affinity,
                        "attempt": attempt,
                    }
                )
                log.warning("free lane skipped (no PAYG key); falling through to Token Plan")
                continue
            return _error(
                503,
                "upstream_unconfigured",
                "Set token_plan.api_key / QWEN_TOKEN_PLAN_API_KEY (sk-sp-).",
                headers=_placement_headers(placement),
            )

        # Hard safety: never use PAYG for non-free placements unless explicitly allowed.
        if placement.lane != "free" and upstream is settings.payg and not settings.payg_allow_paid:
            return _error(
                503,
                "payg_paid_blocked",
                "PAYG paid path disabled (payg_allow_paid: false). Configure Token Plan or free quota.",
                headers=_placement_headers(placement),
            )

        body = dict(payload)
        body["model"] = placement.model_id
        started = time.perf_counter()
        response = await _dispatch(request, upstream, body, placement)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code < 400:
            usage = _usage_from_response(response)
            ledger.record(
                {
                    "event": "completion",
                    "ok": True,
                    "requested": placement.requested,
                    "model": placement.model_id,
                    "lane": placement.lane,
                    "reason": placement.reason,
                    "affinity": placement.affinity,
                    "cost_weight": placement.cost_weight,
                    "score": placement.score,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "usage": usage,
                    "attempt": attempt,
                    "stream": bool(payload.get("stream")),
                }
            )
            return response

        raw = getattr(response, "body", b"") or b""
        if placement.lane == "free" and looks_like_quota_or_billing_block(response.status_code, raw):
            settings.free_gate.mark_exhausted(placement.model_id)
            free_exclude.add(placement.model_id)
            last_error = response
            ledger.record(
                {
                    "event": "failover",
                    "ok": False,
                    "requested": placement.requested,
                    "model": placement.model_id,
                    "lane": placement.lane,
                    "reason": placement.reason,
                    "affinity": placement.affinity,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "attempt": attempt,
                }
            )
            log.warning(
                "free quota/billing block on %s (%s); failing over to Token Plan",
                placement.model_id,
                response.status_code,
            )
            continue
        ledger.record(
            {
                "event": "error",
                "ok": False,
                "requested": placement.requested,
                "model": placement.model_id,
                "lane": placement.lane,
                "reason": placement.reason,
                "affinity": placement.affinity,
                "status": response.status_code,
                "latency_ms": latency_ms,
                "attempt": attempt,
            }
        )
        return response

    return last_error or _error(502, "upstream_unavailable", "No upstream could serve the request.")


def _free_candidates(settings: Settings) -> tuple[str, ...]:
    if settings.token_plan_only or not settings.free_first:
        return ()
    if settings.payg is None or not settings.payg.api_key:
        return ()
    # Allow-list only. A missing or stale sync leaves this empty so PAYG is not called.
    return settings.free_gate.available()


def _run_free_tier_sync(settings: Settings):
    return sync_free_tier(
        settings.free_gate,
        command=settings.free_tier_sync_command,
        snapshot_path=settings.free_tier_snapshot_path,
    )


async def _maybe_refresh_free_tier(app: Starlette) -> None:
    """Refresh at most once per TTL (success or failure), off the event loop, one at a time."""
    settings: Settings = app.state.settings
    gate = settings.free_gate
    if not settings.free_tier_sync or settings.token_plan_only or not settings.free_first:
        return
    if settings.payg is None or not settings.payg.api_key:
        return
    if not gate.refresh_due(settings.free_tier_sync_ttl_seconds):
        return
    lock: asyncio.Lock = app.state.sync_lock
    if lock.locked():
        return  # another request is refreshing; use the current allow-list
    async with lock:
        if not gate.refresh_due(settings.free_tier_sync_ttl_seconds):
            return
        result = await asyncio.to_thread(_run_free_tier_sync, settings)
    if not result.ok:
        log.warning("free-tier sync refresh failed; free lane stays on the last fresh allow-list: %s", result.error)


def _upstream_for(settings: Settings, placement: Placement) -> UpstreamConfig | None:
    if placement.lane == "free":
        return settings.payg
    return settings.token_plan


def _usage_from_response(response: Response) -> dict[str, int] | None:
    if isinstance(response, StreamingResponse):
        return None
    raw = getattr(response, "body", b"") or b""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    out: dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in usage and usage[key] is not None:
            try:
                out[key] = int(usage[key])
            except (TypeError, ValueError):
                continue
    return out or None


async def _dispatch(
    request: Request,
    upstream: UpstreamConfig,
    payload: dict[str, Any],
    placement: Placement,
) -> Response:
    client: httpx.AsyncClient = request.app.state.client
    url = f"{upstream.base_url}/chat/completions"
    headers = {
        "authorization": f"Bearer {upstream.api_key}",
        "content-type": "application/json",
        "accept-encoding": "identity",
    }
    stream = payload.get("stream") is True
    if stream:
        try:
            response = await client.send(
                client.build_request("POST", url, headers=headers, json=payload),
                stream=True,
            )
        except httpx.TransportError as exc:
            log.warning("upstream transport error: %s", exc)
            return _error(
                502,
                "upstream_unavailable",
                "Upstream unreachable.",
                headers=_placement_headers(placement),
            )

        # Peek status before streaming body to client when free lane may need failover.
        if response.status_code >= 400:
            try:
                body = await response.aread()
            finally:
                await response.aclose()
            out = _placement_headers(placement)
            ctype = response.headers.get("content-type")
            if ctype:
                out["content-type"] = ctype
            return Response(body, status_code=response.status_code, headers=out, media_type=ctype)

        out_headers = _placement_headers(placement)
        ctype = response.headers.get("content-type")
        if ctype:
            out_headers["content-type"] = ctype
        return _UpstreamStreamingResponse(response, status_code=response.status_code, headers=out_headers)

    try:
        upstream_resp = await client.post(url, headers=headers, json=payload)
    except httpx.TransportError as exc:
        log.warning("upstream transport error: %s", exc)
        return _error(
            502,
            "upstream_unavailable",
            "Upstream unreachable.",
            headers=_placement_headers(placement),
        )
    out_headers = _placement_headers(placement)
    ctype = upstream_resp.headers.get("content-type")
    if ctype:
        out_headers["content-type"] = ctype
    return Response(
        upstream_resp.content,
        status_code=upstream_resp.status_code,
        headers=out_headers,
        media_type=ctype,
    )


class _UpstreamStreamingResponse(StreamingResponse):
    """Relay an upstream stream and always release its connection.

    Closing inside the body generator is not enough: if the client disconnects
    before the first chunk, the generator never starts (so its `finally` never
    runs) and Starlette skips `background` on ClientDisconnect.
    """

    def __init__(self, upstream: httpx.Response, **kwargs: Any) -> None:
        self._upstream = upstream
        super().__init__(upstream.aiter_bytes(), **kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._upstream.aclose()  # idempotent


def _alias_ids() -> list[str]:
    return [
        "auto",
        "auto:coder",
        "auto:ops",
        "auto:research",
        "auto:writer",
        "auto:design",
        "auto:qa",
        "auto:docs",
        "auto:skills",
        "auto:assistant",
        "auto:coord",
    ]


def _placement_headers(placement: Placement) -> dict[str, str]:
    return {
        "x-qwenr-model": placement.model_id,
        "x-qwenr-reason": placement.reason,
        "x-qwenr-lane": placement.lane,
        "x-qwenr-affinity": str(placement.affinity),
        "x-qwenr-cost": f"{placement.cost_weight:.3f}",
    }


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "qwenr_error", "code": code}},
        status_code=status,
        headers=headers,
    )
