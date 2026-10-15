"""Pick LLM models from what each API key can actually use, by probing, not from memory.

For Gemini and Groq: list the models, keep plausible chat candidates, send each one a tiny request that REQUIRES a tool call, and choose the
first that answers with one. Also picks a Gemini embedding model by embedding one sentence. Writes the choices into .env (never prints keys).

Run: uv run --env-file .env python scripts/pick_models.py [--write]
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import httpx

GEMINI = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_OPENAI = f"{GEMINI}/openai"
GROQ_OPENAI = "https://api.groq.com/openai/v1"
SKIP = re.compile(r"tts|image|transcribe|omni|robotics|computer|antigravity|deep-research|lyria|nano-banana|embedding|live|guard|whisper|orpheus|allam|safeguard|latest", re.I)
TOOL = {"type": "function", "function": {"name": "get_stage", "description": "Get the maturity stage of a feature gate.",
                                         "parameters": {"type": "object", "properties": {"gate": {"type": "string"}}, "required": ["gate"]}}}


def version_key(name: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", name)[:3]) or (0,)


def probe(base: str, key: str, model: str) -> tuple[bool, str]:
    body = {"model": model, "temperature": 0, "tools": [TOOL], "tool_choice": "auto",
            "messages": [{"role": "user", "content": "Use the tool to look up the stage of the feature gate PodPriority."}]}
    try:
        r = httpx.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except httpx.HTTPError as e:
        return False, f"network error {type(e).__name__}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:140].replace(key, '***')}"
    calls = r.json()["choices"][0]["message"].get("tool_calls") or []
    return (bool(calls) and calls[0]["function"]["name"] == "get_stage"), ("tool call ok" if calls else "answered without calling the tool")


def candidates_gemini(key: str) -> list[str]:
    ms = httpx.get(f"{GEMINI}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=30).json().get("models", [])
    names = [m["name"].split("/")[-1] for m in ms if "generateContent" in m.get("supportedGenerationMethods", [])]
    names = [n for n in names if n.startswith("gemini") and "flash" in n and not SKIP.search(n)]
    stable = sorted((n for n in names if "preview" not in n), key=version_key, reverse=True)
    preview = sorted((n for n in names if "preview" in n), key=version_key, reverse=True)
    return stable + preview


def pick_embedding(key: str) -> str | None:
    ms = httpx.get(f"{GEMINI}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=30).json().get("models", [])
    names = sorted((m["name"].split("/")[-1] for m in ms if "embedContent" in m.get("supportedGenerationMethods", []) and "preview" not in m["name"]), key=version_key, reverse=True)
    for n in names:
        r = httpx.post(f"{GEMINI}/models/{n}:embedContent", headers={"x-goog-api-key": key}, json={"model": f"models/{n}", "content": {"parts": [{"text": "feature gate"}]}}, timeout=30)
        print(f"  embed {n}: HTTP {r.status_code}" + (f" dim {len(r.json()['embedding']['values'])}" if r.status_code == 200 else f" {r.text[:100]}"))
        if r.status_code == 200:
            return n
        time.sleep(1)
    return None


def pick(base: str, key: str, names: list[str], limit: int = 6) -> str | None:
    for n in names[:limit]:
        ok, why = probe(base, key, n)
        print(f"  {n}: {why}")
        if ok:
            return n
        time.sleep(2.5)
    return None


def write_env(updates: dict[str, str]) -> None:
    p = Path(".env")
    lines = p.read_text().splitlines()
    seen = set()
    for i, line in enumerate(lines):
        k = line.split("=", 1)[0]
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            seen.add(k)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    p.write_text("\n".join(lines) + "\n")


def main() -> None:
    out: dict[str, str] = {}
    gk, qk = os.environ.get("GEMINI_API_KEY"), os.environ.get("GROQ_API_KEY")
    if gk:
        print("Gemini chat candidates (newest stable first):")
        cands = candidates_gemini(gk)
        print("  ", cands[:8])
        m = pick(GEMINI_OPENAI, gk, cands)
        if m:
            out["GEMINI_MODEL"] = m
        print("Gemini embeddings:")
        e = pick_embedding(gk)
        if e:
            out["GEMINI_EMBED_MODEL"] = e
    if qk:
        print("Groq chat candidates:")
        ids = [x["id"] for x in httpx.get(f"{GROQ_OPENAI}/models", headers={"Authorization": f"Bearer {qk}"}, timeout=30).json().get("data", []) if x.get("active", True)]
        cands = [i for i in ids if not SKIP.search(i)]
        cands.sort(key=lambda n: (0 if "gpt-oss-120b" in n else 1, n))  # the owner's stated preference first, then the rest
        print("  ", cands)
        m = pick(GROQ_OPENAI, qk, cands)
        if m:
            out["GROQ_MODEL"] = m
    print("\nchosen:", out)
    if "--write" in sys.argv and out:
        write_env(out)
        print(".env updated with:", ", ".join(out))


if __name__ == "__main__":
    main()
