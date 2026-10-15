"""Embed every passage of the RAG baseline corpus with Gemini (cached; resumable). Run in the background: about 30 minutes on the free tier.

Run: uv run --env-file .env python -m eval.embed_corpus
"""
import logging

from eval.baseline_rag import build_corpus
from lineage.agent.embeddings import GeminiEmbedder

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    corpus = build_corpus()
    emb = GeminiEmbedder.from_settings()
    logging.info("corpus: %d passages; already cached: %d", len(corpus), sum(emb._key(p.text[:6000], 'RETRIEVAL_DOCUMENT') in emb.cache for p in corpus))
    emb.embed([p.text for p in corpus], "RETRIEVAL_DOCUMENT")
    logging.info("done: %s", emb.stats)
