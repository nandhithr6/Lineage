"""The plain-retrieval answerer: the baseline the demo contrasts with Lineage.

Same question, same language model, same text (the chunks in FalkorDB's vector index), but no tools and no graph: embed the question, take the five most similar chunks, and let
the model answer from them. It cannot follow a relationship, so a question that needs two hops lands on whichever chunks happen to be similar. Its answer is NOT validated or
refused by code (that is the point of the contrast); only its citations are mapped onto the same [e1] chip format the UI already shows.
"""
from __future__ import annotations

import re
import time

from lineage.search.corpus import Passage

RAG_SYSTEM = ("You answer questions about the Kubernetes project using ONLY the numbered passages provided. Cite passages like [p2] after each claim. "
              "If the passages do not contain the answer, say \"insufficient evidence\" and do not guess.")


def rag_prompt(question: str, hits: list[tuple[Passage, float]]) -> list[dict]:
    ctx = "\n\n".join(f"[p{i}] ({p.source_url or 'no source'})\n{p.text}" for i, (p, _) in enumerate(hits, 1))
    return [{"role": "system", "content": RAG_SYSTEM}, {"role": "user", "content": f"Passages:\n{ctx}\n\nQuestion: {question}"}]


def answer_with_retrieval(llm, retriever, question: str, k: int = 5) -> dict:
    """An AskResponse-shaped dict for the retrieval answerer. Passages become evidence items e1..ek so the UI's citation chips and source links work unchanged."""
    t0 = time.perf_counter()
    name = getattr(retriever, "name", "vector")  # "vector" (FalkorDB index + local embeddings) or "tfidf" (the slim build)
    hits = retriever.search(question, k)
    search_ms = round((time.perf_counter() - t0) * 1000)
    text = (llm.chat(rag_prompt(question, hits), []).text or "").strip()
    text = re.sub(r"\[\s*p(\d+)\s*\]", lambda m: f"[e{m.group(1)}]", text)
    text = re.sub(r"\[\s*((?:p\d+\s*[,;]\s*)+p\d+)\s*\]", lambda m: "[" + ", ".join(f"e{n}" for n in re.findall(r"p(\d+)", m.group(1))) + "]", text)
    evidence = [{"id": f"e{i}", "kind": "passage", "type": "Chunk", "from": p.id, "to": None, "props": {"similarity": round(s, 3), "text": p.text[:500]}, "source_url": p.source_url}
                for i, (p, s) in enumerate(hits, 1)]
    cited = list(dict.fromkeys(re.findall(r"e(\d+)", " ".join(re.findall(r"\[(e\d+(?:\s*[,;]\s*e\d+)*)\]", text)))))
    citations = [e for e in evidence if e["id"][1:] in cited]
    return {"answer": text, "status": "ok" if citations else "uncited", "citations": citations, "evidence": evidence, "path": [],
            "retriever": name, "tool_trace": [{"tool": "vector_search" if name == "vector" else "keyword_search", "params": {"query": question, "k": k}, "status": "ok", "evidence": len(evidence), "outcome": None, "duration_ms": search_ms, "unresolved": []}],
            "outcome": None, "mode": "retrieval", "validation": {"accepted": True, "rejected": [], "note": "not validated: plain retrieval has no entity check, no validator and no refusal rule"}}
