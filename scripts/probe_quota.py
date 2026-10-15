"""Measure how many requests each Gemini chat model allows on THIS free key, by sending tiny requests until a 429 and reading the quota id.

Free-tier limits are per model, per day (and per minute) and vary a lot; the numbers in docs or memory are not reliable. A model is also
checked for tool calling with its first request. Stops a model at --max requests (so "more than N" is the answer for generous models).

Run: uv run --env-file .env python scripts/probe_quota.py MODEL [MODEL ...] [--max 30] [--gap 6]
"""
from __future__ import annotations

import argparse
import os
import time

import httpx

URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
TOOL = {"type": "function", "function": {"name": "get_stage", "description": "stage of a gate", "parameters": {"type": "object", "properties": {"gate": {"type": "string"}}, "required": ["gate"]}}}


def post(key: str, body: dict):
    return httpx.post(URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)


def quota_of(r) -> tuple[str, str]:
    try:
        body = r.json()
        body = body[0] if isinstance(body, list) else body
        for d in body["error"].get("details", []):
            for v in d.get("violations", []) or []:
                return v.get("quotaId", "?"), str(v.get("quotaValue", "?"))
    except Exception:
        pass
    return "?", "?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--gap", type=float, default=6.0)
    a = ap.parse_args()
    key = os.environ["GEMINI_API_KEY"]
    for m in a.models:
        ok = 0
        tools_ok = None
        verdict = f"more than {a.max} requests"
        for i in range(a.max):
            body = {"model": m, "temperature": 0, "reasoning_effort": "low", "messages": [{"role": "user", "content": "Use the tool for gate PodPriority." if i == 0 else "Say ok."}]}
            body.update({"tools": [TOOL], "tool_choice": "auto"} if i == 0 else {"max_tokens": 5})
            try:
                r = post(key, body)
            except httpx.HTTPError as e:
                verdict = f"network error {type(e).__name__} after {ok} ok"
                break
            if r.status_code == 200:
                ok += 1
                if i == 0:
                    tools_ok = bool(r.json()["choices"][0]["message"].get("tool_calls"))
            elif r.status_code == 429:
                qid, val = quota_of(r)
                if "PerDay" in qid:
                    verdict = f"DAILY limit {val} reached after {ok} ok ({qid})"
                    break
                print(f"  {m}: per-minute limit ({qid} {val}) after {ok} ok; waiting 65s", flush=True)
                time.sleep(65)
                continue
            else:
                verdict = f"HTTP {r.status_code}: {r.text[:100]}".replace(key, "***")
                break
            time.sleep(a.gap)
        print(f"{m:34s} tool calling: {tools_ok}  | {verdict}", flush=True)


if __name__ == "__main__":
    main()
