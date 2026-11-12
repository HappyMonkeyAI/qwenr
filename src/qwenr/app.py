"""OpenAI-compatible proxy: free PAYG quota first, then Token Plan Credits."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from qwenr import __version__
from qwenr.config import Settings, UpstreamConfig
from qwenr.free import looks_like_quota_or_billing_block
from qwenr.place import PlaceError, Placement, place

log = logging.getLogger("qwenr")


def build_app(settings: Settings, client: httpx.AsyncClient | None = None) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        owns = client is None
        http = client or httpx.AsyncClient(timeout=httpx.Timeout(600.0))
        app.state.settings = settings
        app.state.client = http
        app.state.decisions: list[dict[str, Any]] = []
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
    return Starlette(routes=routes, lifespan=lifespan)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__, "service": "qwenr"})


async def status(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    return JSONResponse(
        {
            "service": "qwenr",
            "version": __version__,
            "token_plan": settings.token_plan.base_url,
            "payg": settings.payg.base_url if settings.payg else None,
            "free_first": settings.free_first,
            "payg_allow_paid": settings.payg_allow_paid,
            # Only models the free lane can actually attempt (needs PAYG key).
            "free_available": list(_free_candidates(settings)),
            "economy_lambda": settings.economy_lambda,
            "night": settings.discounts.is_night(),
            "recent": list(reversed(request.app.state.decisions[-20:])),
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
    try:
        payload = await request.json()
    except Exception:
        return _error(400, "invalid_json", "Request body must be JSON.")
    if not isinstance(payload, dict):
        return _error(400, "invalid_json", "Request body must be a JSON object.")

    requested = payload.get("model")
    exclude: set[str] = set()
    last_error: Response | None = None

    for _attempt in range(4):
        free_candidates = _free_candidates(settings)
        try:
            placement = place(
                str(requested) if requested is not None else None,
                settings.affinity,
                settings.discounts,
                economy_lambda=settings.economy_lambda,
                prefer_platform_auto=settings.token_plan.prefer_platform_auto,
                free_candidates=free_candidates,
                free_first=settings.free_first and bool(free_candidates),
                exclude=frozenset(exclude),
            )
        except PlaceError as exc:
            if last_error is not None:
                return last_error
            return _error(exc.status, exc.code, exc.message)

        _remember(request, placement)
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
                exclude.add(placement.model_id)
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
        response = await _dispatch(request, upstream, body, placement)
        if response.status_code < 400:
            return response

        raw = getattr(response, "body", b"") or b""
        if placement.lane == "free" and looks_like_quota_or_billing_block(response.status_code, raw):
            settings.free_gate.mark_exhausted(placement.model_id)
            exclude.add(placement.model_id)
            last_error = response
            log.warning(
                "free quota/billing block on %s (%s); failing over to Token Plan",
                placement.model_id,
                response.status_code,
            )
            continue
        return response

    return last_error or _error(502, "upstream_unavailable", "No upstream could serve the request.")


def _free_candidates(settings: Settings) -> tuple[str, ...]:
    if not settings.free_first:
        return ()
    if settings.payg is None or not settings.payg.api_key:
        return ()
    # Without payg_allow_paid we only ever hit PAYG for free lane — good.
    return settings.free_gate.available()


def _upstream_for(settings: Settings, placement: Placement) -> UpstreamConfig | None:
    if placement.lane == "free":
        return settings.payg
    return settings.token_plan


def _remember(request: Request, placement: Placement) -> None:
    request.app.state.decisions.append(
        {
            "requested": placement.requested,
            "model": placement.model_id,
            "lane": placement.lane,
            "reason": placement.reason,
            "affinity": placement.affinity,
            "cost_weight": placement.cost_weight,
            "score": placement.score,
        }
    )


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
        stream_cm = client.stream("POST", url, headers=headers, json=payload)
        try:
            response = await stream_cm.__aenter__()
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
            body = await response.aread()
            await stream_cm.__aexit__(None, None, None)
            out = _placement_headers(placement)
            ctype = response.headers.get("content-type")
            if ctype:
                out["content-type"] = ctype
            return Response(body, status_code=response.status_code, headers=out, media_type=ctype)

        async def chunks():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)

        out_headers = _placement_headers(placement)
        ctype = response.headers.get("content-type")
        if ctype:
            out_headers["content-type"] = ctype
        return StreamingResponse(chunks(), status_code=response.status_code, headers=out_headers)

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
