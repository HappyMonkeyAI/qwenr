# PLAN — qwenr / Qwenbot

## Intent

A focused OpenAI-compatible router: OpenBot roles in → best **Token Plan** model out, preferring strength-for-role then Credit economy (including time-of-day discounts).

## Non-goals (v0)

- LAN GPU / LM Studio / Unsloth inventory (that is chimp).
- Multi-cloud shopping across OpenAI/Anthropic.
- Replacing Qwen Code; integrate *with* it later if useful.

## Free tier before Credits (P1.5)

Singapore **pay-as-you-go free quota** is separate from Token Plan Credits ([Free quota](https://docs.qwencloud.com/resources/free-quota)). Token Plan FAQ: plan itself has no free tokens — free quota only applies via Singapore PAYG keys/endpoints.

Placement order when both keys are configured:

1. **free** — model still has Singapore free quota → PAYG upstream (`sk-ws-` / region endpoint)
2. **token_plan** — Credit-scored allowlist (night/preview multipliers) → Token Plan upstream (`sk-sp-`)
3. optional PAYG paid fallback only if explicitly enabled (default off — avoid surprise spend)

Per-model free remaining should be refreshed from console Free Tier (or fail closed to Token Plan when unknown/exhausted). Marketplace `?free=1` is a catalog hint, not live balance.

## Placement brain (v0)

```
resolve auto:<class>
  → candidates = Token Plan text allowlist ∩ role affinity > 0
  → score = affinity(role, model) − λ * effective_credit_cost(model, now, estimated_tokens)
  → sticky optional (prompt_cache_key / session) for cache-friendly models
  → proxy to Qwen Cloud OpenAI-compatible endpoint
```

Effective cost applies night/preview multipliers from a small, refreshable `discounts.yaml` (scraped or hand-maintained from docs/console).

## Phases

| Phase | Deliverable |
|-------|-------------|
| P0 | Repo skeleton, role→model draft map, docs ToS caveat |
| P1 | Config + affinity + discount clock; unit tests for place() |
| P2 | Starlette `/v1/chat/completions` proxy + status UI |
| P3 | Live OpenBot drop-swap on :8787 |
| P4 | Optional: refresh allowlist/discounts from docs/console |

## OpenBot contract

Mirror chimp aliases: `auto`, `auto:coder`, `auto:ops`, `auto:research`, `auto:writer`, `auto:design`, `auto:qa`, `auto:docs`, `auto:skills`, `auto:assistant`, `auto:coord`.

Headers (proposed): `x-qwenr-model`, `x-qwenr-reason`, `x-qwenr-credits-tier`.
