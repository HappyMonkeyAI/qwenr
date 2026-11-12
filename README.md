# qwenr

OpenAI-compatible LAN router that maps OpenBot `auto:<class>` roles onto **Qwen Cloud** models — **Singapore free quota first**, then **Token Plan Credits**, scoring fit × economy (list rates, night discounts, preview promos).

Sibling of [chimp](https://github.com/HappyMonkeyAI/chimp): same client contract (`auto`, `auto:coder`, …), different placement brain (credits / free quota / discounts instead of warm VRAM).

## Why a separate repo

Chimp owns LAN GPU placement. qwenr owns cloud Token Plan economics. Mixing them muddies both. Stop one, start the other — OpenBot keeps the same provider URL and role ids.

## Install

Python **3.12+**.

```powershell
# Windows
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
copy config.example.yaml config.yaml
copy .env.example .env
```

```bash
# Linux / macOS
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env
```

Edit `.env` (gitignored):

```
QWEN_TOKEN_PLAN_API_KEY=sk-sp-...
QWEN_PAYG_API_KEY=sk-ws-...
```

- **Token Plan** keys start with `sk-sp-` (Credits).
- **PAYG** keys for the Singapore free-quota lane are typically `sk-ws-`.
- Optional URL overrides: `QWEN_TOKEN_PLAN_BASE_URL`, `QWEN_PAYG_BASE_URL` (see `.env.example`).

`config.yaml` is gitignored — only `config.example.yaml` ships in the public tree. Secrets stay in `.env` or process env (process env wins if already set).

## Run

Default example listen is `127.0.0.1:8788` so chimp can keep `:8787`. For an OpenBot drop-swap, set `listen: 127.0.0.1:8787` in `config.yaml` and stop chimp first.

```powershell
# Windows
$env:PYTHONPATH = "src"
.\.venv\Scripts\qwenr --config config.yaml
```

```bash
# Linux / macOS
export PYTHONPATH=src
.venv/bin/qwenr --config config.yaml
```

Or: `.venv/Scripts/python -m qwenr.server --config config.yaml`

### Smoke

```bash
curl -s http://127.0.0.1:8788/health
curl -s http://127.0.0.1:8788/status
```

```bash
curl -s http://127.0.0.1:8788/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto:coord","messages":[{"role":"user","content":"ping"}],"max_tokens":32}'
```

Response headers: `x-qwenr-model`, `x-qwenr-lane` (`free` | `token_plan`), `x-qwenr-reason`, `x-qwenr-affinity`, `x-qwenr-cost`.

`/status` includes a placement **summary** and recent decisions. Successful calls append to `placement.ndjson` (gitignored) for post-run quota analysis.

## OpenBot drop-swap

1. Point OpenBot custom provider `chimpr` (or a twin `qwenr`) at `http://127.0.0.1:8787/v1`.
2. Keep agent models as `chimpr/auto:<class>` (or remapped `qwenr/auto:<class>`).
3. Stop chimp; run qwenr on **8787**.
4. Smoke CEO `auto:coord` and a coder desk; check `x-qwenr-*` headers.

## How placement works

1. **Free first** — among Singapore PAYG free-quota candidates (`free_models.yaml`), pick by role affinity. Near-ties within `free_affinity_slack` prefer underused free models so one id does not eat the day.
2. If free is empty/exhausted (or PAYG key missing), fall through to **Token Plan** allowlist scoring: `affinity − λ × effective Credit weight` (night / promo tables in `discounts.yaml`).
3. Free-lane quota/billing errors mark that model exhausted (TTL) and retry toward Token Plan. With `payg_allow_paid: false` (default), qwenr never silently burns paid PAYG.

Role aliases: `auto`, `auto:coder`, `auto:ops`, `auto:research`, `auto:writer`, `auto:design`, `auto:qa`, `auto:docs`, `auto:skills`, `auto:assistant`, `auto:coord`.

## Operator notes

- Exact model-id allowlist — no fuzzy versions ([Token Plan Individual](https://docs.qwencloud.com/token-plan/personal/token-plan-personal-overview)).
- Night window **22:00–08:00 UTC+8**: e.g. `qwen3.8-max` / `qwen3.8-flash` often 60% off Credits; some DeepSeek ids 50% off (see `discounts.yaml`).
- `qwen4-max-preview`: Token Plan only (not free-lane).
- **ToS:** Token Plan is for interactive use inside programming/agent tools — not unattended batch backends. Treat OpenBot as an interactive harness.

## Develop

```bash
.venv/bin/pytest   # or .\.venv\Scripts\pytest
```

## License

[MIT](LICENSE) — Copyright (c) 2026 Stephen Phillips and HappyMonkeyAI.

## Related

- [chimp](https://github.com/HappyMonkeyAI/chimp) — LAN warm-VRAM OpenAI router
- [Qwen Code](https://github.com/QwenLM/qwen-code) — terminal/agent harness that speaks Token Plan auth
- [Agents Protocol](https://github.com/HappyMonkeyAI/AgentsProtocol) — shared agent stubs
