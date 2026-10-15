from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    session_id: str | None = Field(None, max_length=64)


class ToolStep(BaseModel):
    tool: str
    params: dict[str, Any]
    status: str
    evidence: int


class AskResponse(BaseModel):
    answer: str
    status: Literal["ok", "insufficient_evidence", "incomplete", "uncited"]
    citations: list[Evidence]
    evidence: list[Evidence]
    path: list[str]
    tool_trace: list[ToolStep]
    unsupported_citations: list[str] = []
    session_id: str | None = None


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


class AboutData(BaseModel):
    snapshot_date: str | None
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
