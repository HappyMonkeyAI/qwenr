# CONTEXT — qwenr

## Stack

- Python 3.12+, Starlette/httpx (same shape as chimp, smaller surface).
- Upstream: Qwen Cloud Token Plan OpenAI-compatible API (Singapore / Global).
- Client: OpenBot custom provider (and later Qwen Code if desired).
- License: MIT — Stephen Phillips and HappyMonkeyAI.

## Rules

- Never commit API keys or live Credit balances.
- Model ids are an **exact allowlist** — do not invent `qwen3-coder-max` etc.
- Prefer documented discount tables over guessing Credit burn.
- **Free first, never silent PAYG paid:** Singapore free quota (PAYG `sk-ws-`) before Token Plan Credits (`sk-sp-`). Default `payg_allow_paid: false` — after free is exhausted, fail over to Token Plan only.
- Token Plan ToS: interactive agent/tool use only — no unattended batch backends on this key.
- Keep chimp and qwenr separate; do not merge placement engines.

## What not to do

- Bolt Token Plan economics into chimp’s warm/cold LAN router.
- Treat platform `auto` as a substitute for OpenBot role classes (different job).
- Assume pay-as-you-go prices equal Token Plan Credit weights.
