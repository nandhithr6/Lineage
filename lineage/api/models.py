from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Evidence(BaseModel):
    id: str
    kind: str
    type: str
    from_: str | None = Field(None, alias="from")
    to: str | None = None
    props: dict[str, Any] = {}
    source_url: str | None = None
    tool: str | None = None

    model_config = {"populate_by_name": True}


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    graph: str
    llm_configured: bool
    detail: str | None = None
    embeddings: bool = True  # false in the slim build: no local embedding model, so no vector search and the contrast toggle uses keyword retrieval


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    session_id: str | None = Field(None, max_length=64)
    mode: Literal["graph", "retrieval"] = "graph"  # "retrieval" = the plain vector-retrieval baseline (no tools, no graph) for the side-by-side contrast


class SaveNoteRequest(BaseModel):
    answer_id: str = Field(min_length=6, max_length=64)
    session_id: str = Field(min_length=6, max_length=64)


class ToolStep(BaseModel):
    tool: str
    params: dict[str, Any]  # the arguments the model passed
    status: str
    evidence: int  # rows (evidence items) the tool returned
    given: dict[str, Any] | None = None  # what the model passed, when validation normalised it (e.g. letter case)
    outcome: str | None = None  # answered_with_path | not_in_graph | unknown_entity
    duration_ms: int | None = None
    unresolved: list[dict[str, Any]] = []  # names the graph did not know, with "did you mean" text


class AskResponse(BaseModel):
    answer: str
    status: Literal["ok", "insufficient_evidence", "incomplete", "uncited"]
    citations: list[Evidence]
    evidence: list[Evidence]
    path: list[str]
    tool_trace: list[ToolStep]
    unsupported_citations: list[str] = []
    session_id: str | None = None
    outcome: Literal["answered_with_path", "answered_from_memory", "not_in_graph", "unknown_entity"] | None = "answered_with_path"  # None in retrieval mode: there is no graph path to judge
    mode: Literal["graph", "retrieval"] = "graph"
    retriever: str | None = None  # retrieval mode: "vector" (FalkorDB index + local embeddings) or "tfidf" (the slim build)
    validation: dict[str, Any] = {"accepted": True, "rejected": []}  # the answer validator's verdict; when not accepted, `draft` holds the withheld model text
    answer_id: str | None = None  # server-side handle of this answer, so a validated one can be saved as a note without the client sending any text
    can_save: bool = False
    memory: list[dict[str, Any]] = []  # this session's notes about the entities in the question, each re-verified against the graph just now
    cached: bool = False  # true when this is a saved answer for a demo question (same evidence the tools returned when it was saved)
    cached_at: str | None = None
    snapshot_id: str | None = None


class FindingSummary(BaseModel):
    id: str
    key: str
    kind: str
    severity: Literal["finding", "minor", "review"]
    message: str
    page_title: str | None
    page_url: str | None
    section: str | None
    claimed_stage: str | None
    claimed_since: str | None
    gate: str
    link_confidence: float | None
    link_method: str | None
    source_url: str | None


class FindingList(BaseModel):
    total: int
    counts: dict[str, int]
    findings: list[FindingSummary]


class FindingDetail(BaseModel):
    key: str
    status: str
    path: list[str]
    evidence: list[Evidence]
    sections: dict[str, Any]


class FixResponse(BaseModel):
    finding_key: str
    status: Literal["ok", "not_applicable", "insufficient_evidence"]
    note: str
    diff: str | None = None
    explanation: str | None = None
    evidence: list[Evidence] = []
    reviewers: list[dict[str, Any]] = []
    drafted_by: str | None = None


class Node(BaseModel):
    id: str
    label: str
    props: dict[str, Any]


class Edge(BaseModel):
    source: str
    target: str
    type: str
    props: dict[str, Any] = {}


class EntityResponse(BaseModel):
    node: Node
    neighbors: list[Node]
    edges: list[Edge]
    truncated: bool = False


class SnapshotInfo(BaseModel):
    id: str  # the snapshot date, usable as ?since=
    snapshot_date: str
    age_days: int
    sources: list[dict[str, Any]]
    is_current: bool


class SnapshotList(BaseModel):
    current: SnapshotInfo
    previous: list[SnapshotInfo]


class ChangesSince(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: dict[str, Any] = Field(alias="from")
    to: dict[str, Any]
    summary: dict[str, int]
    findings: dict[str, list[dict[str, Any]]]
    gates: list[dict[str, Any]]
    labels: list[dict[str, Any]]
    keps: dict[str, Any]
    ownership: dict[str, Any]
    caveats: list[str]


class SchemaNode(BaseModel):
    label: str
    count: int


class SchemaEdge(BaseModel):
    source: str
    type: str
    target: str
    count: int


class SchemaResponse(BaseModel):
    nodes: list[SchemaNode]
    edges: list[SchemaEdge]


class AboutData(BaseModel):
    snapshot_date: str | None
    age_days: int | None = None
    sources: list[dict[str, Any]]
    counts: dict[str, int]
    detector: dict[str, int]
    manual_links: int
    limitations: list[str]


class OwnerRow(BaseModel):
    handle: str
    role: str
    via_team: str | None
    declared_at: str
    hops: int
    source_url: str | None
    valid_from_release: int | None = None
    valid_to_release: int | None = None


class OwnersResponse(BaseModel):
    repo: str
    path: str
    as_of: str
    as_of_num: int
    declared_at: list[str]
    owners: list[OwnerRow]
    available_releases: list[str]
    note: str | None = None


class ChangesResponse(BaseModel):
    entity: str
    kind: Literal["gate", "path"]
    from_release: str
    to_release: str
    added: list[OwnerRow] = []
    removed: list[OwnerRow] = []
    unchanged: int = 0
    gate_stage_then: list[str] = []
    gate_stage_now: list[str] = []
    source_url: str | None = None


class ExpertRow(BaseModel):
    handle: str
    recency_weighted_score: float
    prs_reviewed: int
    approvals: int
    last_active: str
    listed_approver: bool
    example_pr: str


class ExpertsResponse(BaseModel):
    repo: str
    path: str
    months: int
    experts: list[ExpertRow]
    listed_and_active: list[str]
    listed_but_inactive: list[str]
    active_but_not_listed: list[str]
    coverage_note: str


class OrphanRow(BaseModel):
    path: str
    listed_approvers: int
    active_people: int
    active_but_not_listed: list[str]


class SearchResponse(BaseModel):
    query: str
    status: Literal["ok", "empty", "insufficient_evidence", "unavailable"]
    evidence: list[Evidence]
    path: list[str]
    note: str | None = None


class DormantRow(BaseModel):
    path: str
    listed_approvers: int


class GapRow(BaseModel):
    path: str
    listed_approvers: int
    active_approvers: int
    inactive_approvers: int
    inactive_share: float
    active_people: int
    other_active_people: int
    no_activity_data: bool
    inactive_examples: list[str]


class KeyReviewer(BaseModel):
    rank: int
    handle: str
    score: float
    authors_served: int
    reviews: int
    approver_of_folders: int
    listed_approver: bool


class InsightsResponse(BaseModel):
    months: int
    ownership_gaps: list[GapRow]
    orphaned: list[OrphanRow]
    dormant: list[DormantRow]
    key_reviewers: list[KeyReviewer]
    notes: list[str]
