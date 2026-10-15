"""Hand-written tool-calling loop (CLAUDE.md §8). No agent framework: every line is ours to explain.

1. send system prompt + conversation + tool schemas to the LLM
2. run each requested tool (read-only Cypher), re-number its evidence ids so they are unique across the conversation
3. repeat until the model answers, at most MAX_ITERATIONS times
4. enforce, in code: no evidence -> refusal; unknown citations removed; answers should cite evidence ids
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from lineage.agent.llm import LLM, LLMResponse
from lineage.agent.prompts import NEEDS_CITATIONS, REFUSAL, SYSTEM_PROMPT
from lineage.agent.tools import ToolBox

log = logging.getLogger(__name__)

MAX_ITERATIONS = 6
MAX_TOOL_JSON = 12000
# A citation is "[e3]" or a group such as "[e7, e8]" (models write both); ids are always e + digits.
_CITE_GROUP = re.compile(r"\[\s*(e\d+(?:\s*[,;]\s*e\d+)*)\s*\]")
_ID = re.compile(r"e\d+")


_FULLWIDTH = re.compile(r"[【\[]\s*(e\d+(?:\s*[,;]\s*e\d+)*)\s*(?:†[^】\]]*)?[】\]]")


def normalize_citations(text: str) -> str:
    """Some models (Groq's gpt-oss) write citations as 【e6】 or 【e6†source】; rewrite them to the plain [e6] form used everywhere else."""
    return _FULLWIDTH.sub(lambda m: f"[{m.group(1)}]", text)


def cited_ids(text: str) -> list[str]:
    """Evidence ids cited in a text, in order of first appearance, from single or grouped brackets."""
    return list(dict.fromkeys(i for g in _CITE_GROUP.findall(text) for i in _ID.findall(g)))


def drop_unknown(text: str, known: set[str]) -> str:
    """Remove cited ids that do not exist (inside groups too); a bracket left empty disappears."""
    def fix(m: re.Match) -> str:
        keep = [i for i in _ID.findall(m.group(1)) if i in known]
        return f"[{', '.join(keep)}]" if keep else ""
    return _CITE_GROUP.sub(fix, text)


@dataclass
class AgentResult:
    answer: str
    status: str  # ok | insufficient_evidence | incomplete | uncited
    citations: list[dict] = field(default_factory=list)  # evidence items the answer cites
    evidence: list[dict] = field(default_factory=list)  # everything the tools returned
    path: list[str] = field(default_factory=list)
    tool_trace: list[dict] = field(default_factory=list)
    iterations: int = 0
    unsupported_citations: list[str] = field(default_factory=list)


def renumber(bundle: dict, start: int) -> dict:
    """Give evidence items ids e{start+1}.. so ids stay unique across all tool calls in a conversation."""
    out = {**bundle, "evidence": []}
    for i, e in enumerate(bundle["evidence"], start=start + 1):
        out["evidence"].append({**e, "id": f"e{i}"})
    return out


def for_model(bundle: dict) -> str:
    text = json.dumps(bundle, ensure_ascii=False, default=str)
    if len(text) > MAX_TOOL_JSON:
        keep = list(bundle["evidence"])
        while keep and len(json.dumps({**bundle, "evidence": keep}, ensure_ascii=False, default=str)) > MAX_TOOL_JSON:
            keep = keep[: max(1, len(keep) * 3 // 4)] if len(keep) > 1 else []
        text = json.dumps({**bundle, "evidence": keep, "truncated": True, "total_evidence": len(bundle["evidence"])}, ensure_ascii=False, default=str)
    return text


def run_agent(llm: LLM, tools: ToolBox, question: str, history: list[dict] | None = None, max_iterations: int = MAX_ITERATIONS) -> AgentResult:
    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}, *(history or []), {"role": "user", "content": question}]
    registry: dict[str, dict] = {}
    trace: list[dict] = []
    last_path: list[str] = []
    asked_for_citations = False
    for it in range(1, max_iterations + 1):
        resp: LLMResponse = llm.chat(messages, ToolBox.SCHEMAS)
        if resp.tool_calls:
            messages.append({"role": "assistant", "content": resp.text or "",
                             "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                                             **({"extra_content": c.extra} if c.extra else {})} for c in resp.tool_calls]})
            for c in resp.tool_calls:
                raw = tools.call(c.name, c.arguments)
                b = renumber(raw, len(registry))
                for e in b["evidence"]:
                    registry[e["id"]] = {**e, "tool": c.name}
                if b["status"] == "ok" and b.get("path"):
                    last_path = b["path"]
                trace.append({"tool": c.name, "params": c.arguments, "status": b["status"], "evidence": len(b["evidence"])})
                messages.append({"role": "tool", "tool_call_id": c.id, "content": for_model(b)})
            continue
        text = normalize_citations((resp.text or "").strip())
        evidence = list(registry.values())
        if not evidence:  # nothing found anywhere: refuse in code, whatever the model said
            searched = "; ".join(f"{t['tool']}({json.dumps(t['params'])})" for t in trace) or "no tools were called"
            return AgentResult(REFUSAL.format(searched=searched), "insufficient_evidence", [], [], [], trace, it)
        cited = cited_ids(text)
        unknown = [c for c in cited if c not in registry]
        if unknown:
            text = drop_unknown(text, set(registry))
        if not [c for c in cited if c in registry] and not asked_for_citations and it < max_iterations:
            asked_for_citations = True
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": NEEDS_CITATIONS}]
            continue
        good = [c for c in cited if c in registry]
        return AgentResult(text, "ok" if good else "uncited", [registry[c] for c in good], evidence, last_path, trace, it, unknown)
    return AgentResult("I could not finish within the allowed number of tool calls, so I am not giving an unverified answer. "
                       "Try a narrower question.", "incomplete", [], list(registry.values()), last_path, trace, max_iterations)
