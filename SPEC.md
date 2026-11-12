# SPEC.md — qwenr acceptance criteria

## S1 — Install & config

- [ ] `config.example.yaml` with `${ENV:-}` only; real keys stay local.
- [ ] Token Plan base URL + API key load; listen default `127.0.0.1:8787`.

## S2 — Placement

- [ ] `auto:<class>` resolves via affinity among **exact** Token Plan allowlist ids.
- [ ] Night-window discount multipliers change ranking when clock is in 22:00–08:00 UTC+8.
- [ ] Preview / promo multipliers (e.g. qwen4-max-preview) are applied when configured.
- [ ] Sticky optional; pin only after successful upstream response.

## S3 — Proxy

- [ ] `/v1/chat/completions` streams when requested; honest error bodies.
- [ ] Response headers name chosen model and reason.

## S4 — OpenBot

- [ ] Same role ids as chimp; documented drop-swap on port 8787.
- [ ] One live smoke: `auto:coord` and `auto:coder` through OpenBot.
