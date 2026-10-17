"""Lineage as an MCP server: the curated READ-ONLY tools, returning the same evidence bundles (ids e1, e2, ... and commit-pinned source URLs) the agent gets.

There are no write tools and no free-form Cypher: each tool is one fixed, parameterised query (lineage/agent/tools.py). Every call is read-only against FalkorDB.
An MCP client (Claude Desktop, Claude Code, an IDE, the MCP Inspector) can therefore ask Lineage for evidence and cite [e1], [e2] ... itself.

    uv run --env-file .env python -m lineage.mcp_server                       # stdio (desktop clients)
    uv run --env-file .env python -m lineage.mcp_server --http --port 8765    # streamable HTTP at http://127.0.0.1:8765/mcp
The deploy image also serves streamable HTTP at /mcp on the same port as the UI and API.
"""
from __future__ import annotations

import argparse
import logging
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from lineage.agent.tools import ToolBox
from lineage.config import load_settings
from lineage.graph.client import connect

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
INSTRUCTIONS = (
    "Lineage knows when Kubernetes documentation is out of date and proves every answer. Every tool returns an evidence bundle: a list of evidence items with ids (e1, e2, ...), "
    "each with a commit-pinned source_url, plus a status of ok, empty or insufficient_evidence and an OUTCOME: answered_with_path (evidence found), not_in_graph (the name exists but nothing "
    "connects to it: never read that as 'nothing is affected') or unknown_entity (the graph does not know a name you passed; 'unresolved' lists close names as text, never as facts). "
    "State only facts that appear in the evidence, cite the ids, and say 'insufficient evidence' when a tool returns that status. Translate jargon: SIG = team, KEP = design decision, OWNERS = ownership map. Lineage only drafts; it never writes anywhere."
)


@lru_cache
def toolbox() -> ToolBox:
    s = load_settings()
    return ToolBox(connect(s).select_graph(s.falkordb_graph))


def _call(name: str, **args: Any) -> dict[str, Any]:
    return toolbox().call(name, {k: v for k, v in args.items() if v is not None})


def build_server(name: str = "lineage", streamable_http_path: str = "/mcp") -> MCPServer:  # noqa: ARG001 (path is set when the app is created)
    mcp = MCPServer(name, instructions=INSTRUCTIONS, version="0.1.0")

    @mcp.tool(annotations=READ_ONLY)
    def drift_findings(severity: str | None = None, sig: str | None = None, limit: int = 20) -> dict[str, Any]:
        """List active drift findings and review items, most severe first. severity: finding | minor | review. sig: filter by owning team, e.g. sig-storage."""
        return _call("drift_findings", severity=severity, sig=sig, limit=limit)

    @mcp.tool(annotations=READ_ONLY)
    def explain_finding(query: str) -> dict[str, Any]:
        """Full trace for one finding: the docs claim, the feature gate it is about, the gate's stage history, the design decision (KEP), the team and suggested reviewers. query = part of the page path (e.g. 'resource-quotas') or a finding key."""
        return _call("explain_finding", query=query)

    @mcp.tool(annotations=READ_ONLY)
    def gate_timeline(gate: str) -> dict[str, Any]:
        """The stage (alpha, beta, stable, deprecated) of a feature gate in every release, with the source of each fact."""
        return _call("gate_timeline", gate=gate)

    @mcp.tool(annotations=READ_ONLY)
    def doc_claims(page_or_section: str, limit: int = 25) -> dict[str, Any]:
        """The maturity labels on a docs page or section (e.g. 'Beta since v1.10') and the feature gate each is linked to, with the link's confidence and method."""
        return _call("doc_claims", page_or_section=page_or_section, limit=limit)

    @mcp.tool(annotations=READ_ONLY)
    def owners_of(path: str, repo: str = "kubernetes", as_of: str | None = None) -> dict[str, Any]:
        """Who approves or reviews a folder or file (inheritance and teams resolved). repo: kubernetes | website. as_of: a release like 1.30 (kubernetes history is recorded at 1.30-1.37 and latest); other releases return insufficient_evidence."""
        return _call("owners_of", path=path, repo=repo, as_of=as_of)

    @mcp.tool(annotations=READ_ONLY)
    def decision_lineage(kep_or_gate: str) -> dict[str, Any]:
        """The design decision (KEP) behind a feature gate or topic: status, owning team, authors, approvers, replaced or related KEPs, the pull requests that implement it and the tickets they close."""
        return _call("decision_lineage", kep_or_gate=kep_or_gate)

    @mcp.tool(annotations=READ_ONLY)
    def suggest_reviewers(query: str) -> dict[str, Any]:
        """Who should review the fix for a stale-docs finding and why: docs approvers (can merge) and domain experts (can check the facts), each reason with its source. query = part of the docs page path or a finding key."""
        return _call("suggest_reviewers", query=query)

    @mcp.tool(annotations=READ_ONLY)
    def experts_for(path: str, repo: str = "kubernetes", months: int = 12) -> dict[str, Any]:
        """Who actually reviews a folder (recency-weighted review counts over the last N months) and whether OWNERS lists them. Covers sig-storage, sig-node and sig-apps pull requests only."""
        return _call("experts_for", path=path, repo=repo, months=months)

    @mcp.tool(annotations=READ_ONLY)
    def what_changed(entity: str, from_release: str, to_release: str, repo: str = "kubernetes") -> dict[str, Any]:
        """What changed between two releases: for a feature gate, its stage; for a folder, who gained or lost an approver/reviewer role. Ownership is recorded at 1.30-1.37 and latest."""
        return _call("what_changed", entity=entity, from_release=from_release, to_release=to_release, repo=repo)

    @mcp.tool(annotations=READ_ONLY)
    def orphaned_components(months: int = 12) -> dict[str, Any]:
        """Folders whose listed approvers were all inactive for N months while other people kept working there (the risk case). Folders with no activity at all are dormant and not listed."""
        return _call("orphaned_components", months=months)

    @mcp.tool(annotations=READ_ONLY)
    def path_between(a: str, b: str, max_hops: int = 4) -> dict[str, Any]:
        """How two things in the graph are connected, hop by hop (FalkorDB algo.SPpaths over ownership, membership, design-decision and docs-structure links). a and b can each be a feature gate, person, team, SIG, KEP, folder or docs page. max_hops is capped at 5."""
        return _call("path_between", a=a, b=b, max_hops=max_hops)

    return mcp


TOOL_NAMES = ["drift_findings", "explain_finding", "gate_timeline", "doc_claims", "owners_of", "decision_lineage", "suggest_reviewers", "experts_for", "what_changed", "orphaned_components",
              "path_between"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # stdio uses stdout for the protocol: logs go to stderr by default
    server = build_server()
    if args.http:
        import uvicorn

        uvicorn.run(server.streamable_http_app(host=args.host), host=args.host, port=args.port, log_level="info")
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
