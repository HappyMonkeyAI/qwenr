# qwenr — Qwenbot router

OpenAI-compatible LAN router that maps OpenBot `auto:<class>` roles onto **Qwen Cloud Token Plan** models, scoring **fit × economy** (list rates, night discounts, preview promos).

Sibling of [chimp](https://github.com/HappyMonkeyAI/chimp): same client contract (`auto`, `auto:coder`, … on port 8787), different placement brain (credits / allowlist / discounts instead of warm VRAM).

## Why a separate repo

Chimp owns LAN GPU placement. Qwenbot owns cloud Token Plan economics. Mixing them muddies both. Stop one, start the other — OpenBot keeps the same provider URL and role ids.

## Status

Minimal proxy on `:8787` (OpenBot drop-swap). Free-first + Token Plan lanes.

## Secrets

Copy `.env.example` → `.env` (gitignored) and fill:

```
QWEN_TOKEN_PLAN_API_KEY=sk-sp-...
QWEN_PAYG_API_KEY=sk-ws-...
```

`qwenr` loads `.env` next to `config.yaml` on startup. Process env wins if already set.

## OpenBot drop-swap (target)

1. Point OpenBot custom provider `chimpr` (or a twin `qwenr`) at `http://127.0.0.1:8787/v1`.
2. Keep agent models as `chimpr/auto:<class>` (or remapped `qwenr/auto:<class>`).
3. Stop chimp Compose; run qwenr on **8787**.
4. Smoke CEO `auto:coord` and one coder desk; check `x-qwenr-model` / reason headers.

## Token Plan notes (operator)

- Exact model-id allowlist — no fuzzy versions ([Token Plan Individual](https://docs.qwencloud.com/token-plan/personal/token-plan-personal-overview)).
- Platform already ships an `auto` model; qwenr is **role-aware + discount-aware** on top.
- Night window **22:00–08:00 UTC+8**: e.g. `qwen3.8-max` / `qwen3.8-flash` 60% off Credits; some DeepSeek ids 50% off.
- `qwen4-max-preview`: Token Plan only; limited preview promo (as low as 20% Credits, night stack to ~8%).
- **ToS:** Token Plan is for interactive use inside programming/agent tools — not custom batch backends. Treat OpenBot as an interactive harness; do not put this key behind unattended swarm scrapers.

## Related

- [Qwen Code](https://github.com/QwenLM/qwen-code) — terminal/agent harness that already speaks Token Plan auth.
- Qwen Cloud docs: Token Plan, model prices, night discounts.
