#!/usr/bin/env python3
"""Probe qwenr placements for Vibes Desktop mission roles.

Maps planner/executor/reviewer/triage/default onto OpenBot-style auto:<class>
aliases and prints the chosen model/lane. Requires qwenr listening (default
http://127.0.0.1:8787).

Usage:
  .venv\\Scripts\\python scripts\\probe_vibes_roles.py
  .venv\\Scripts\\python scripts\\probe_vibes_roles.py --base http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Vibes role → qwenr alias (OpenBot contract)
ROLE_ALIASES: list[tuple[str, str]] = [
    ("planner", "auto:coord"),
    ("executor", "auto:coder"),
    ("reviewer", "auto:qa"),
    ("triage", "auto:ops"),
    ("default", "auto"),
]


def probe(base: str, vibe: str, model: str, timeout: float) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": f"Reply with exactly: OK {vibe}"}],
        "max_tokens": 24,
    }
    req = urllib.request.Request(
        f"{base.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            content = (msg.get("content") or "").strip()
            reasoning = (msg.get("reasoning_content") or "").strip()
            return {
                "vibe": vibe,
                "alias": model,
                "status": resp.status,
                "model": resp.headers.get("x-qwenr-model"),
                "lane": resp.headers.get("x-qwenr-lane"),
                "affinity": resp.headers.get("x-qwenr-affinity"),
                "cost": resp.headers.get("x-qwenr-cost"),
                "reason": resp.headers.get("x-qwenr-reason"),
                "reply": " ".join(content.split())[:120] if content else "",
                "reasoning_preview": " ".join(reasoning.split())[:80] if reasoning else "",
                "ok": True,
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        return {"vibe": vibe, "alias": model, "status": e.code, "ok": False, "error": body}
    except Exception as e:  # noqa: BLE001 — probe CLI surfaces any failure
        return {"vibe": vibe, "alias": model, "status": "ERR", "ok": False, "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8787")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--json", action="store_true", help="machine-readable rows only")
    args = ap.parse_args()

    rows = [probe(args.base, vibe, alias, args.timeout) for vibe, alias in ROLE_ALIASES]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"qwenr base={args.base}")
        print(
            f"{'vibe':<10} {'alias':<12} {'status':<6} {'lane':<11} {'model':<22} {'aff':<4} reply"
        )
        for r in rows:
            if not r.get("ok"):
                print(f"{r['vibe']:<10} {r['alias']:<12} {r.get('status')} ERR {r.get('error','')[:60]}")
                continue
            reply = r.get("reply") or (f"[reasoning] {r['reasoning_preview']}" if r.get("reasoning_preview") else "(empty)")
            print(
                f"{r['vibe']:<10} {r['alias']:<12} {r.get('status'):<6} "
                f"{(r.get('lane') or '-'):<11} {(r.get('model') or '-'):<22} "
                f"{(r.get('affinity') or '-'):<4} {reply}"
            )
        print("\nVibes Roles mapping (base URL http://127.0.0.1:8787 — normalize adds /v1):")
        for vibe, alias in ROLE_ALIASES:
            print(f"  {vibe}: model={alias}")

    return 0 if all(r.get("ok") for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
