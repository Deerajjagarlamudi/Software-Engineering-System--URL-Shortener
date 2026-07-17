"""Workflow domain model: runs, nodes, artifacts, approvals, audit events.

State is a plain dataclass tree serialized to JSON and persisted in SQLite,
so a run can be inspected, resumed, and audited across process restarts.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class NodeStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    INVALIDATED = "invalidated"


class RunStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


@dataclass
class Artifact:
    artifact_id: str
    node_id: str
    kind: str                      # requirement|architecture|plan|patch|tests|docs|security|release
    content: dict[str, Any]
    version: int = 1
    produced_by: str = ""          # agent name
    lineage: list[str] = field(default_factory=list)  # upstream artifact ids
    content_hash: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class Node:
    node_id: str
    agent: str                     # agent registry key
    depends_on: list[str] = field(default_factory=list)
    approval_required: bool = False  # human gate AFTER this node completes
    max_retries: int = 2
    attempts: int = 0
    status: NodeStatus = NodeStatus.PENDING
    artifact_id: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    approval: dict[str, Any] | None = None  # {approved, rationale, actor, at}


@dataclass
class AuditEvent:
    event_id: str
    run_id: str
    at: float
    kind: str                      # node_started|node_completed|node_failed|retry|approval_requested|...
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRun:
    run_id: str
    requirement: str
    scenario: str                  # greenfield|brownfield|ambiguous
    status: RunStatus = RunStatus.RUNNING
    nodes: dict[str, Node] = field(default_factory=dict)
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)  # cross-stage shared context
    checkpoints: list[str] = field(default_factory=list)   # git commit shas
    workspace: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    metrics: dict[str, Any] = field(default_factory=lambda: {
        "retries": 0, "rollbacks": 0, "approvals": 0, "failures": 0,
    })

    # ---- helpers ----
    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        for nid, n in d["nodes"].items():
            n["status"] = self.nodes[nid].status.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "WorkflowRun":
        run = WorkflowRun(
            run_id=d["run_id"], requirement=d["requirement"], scenario=d["scenario"],
            status=RunStatus(d["status"]), context=d.get("context", {}),
            checkpoints=d.get("checkpoints", []), workspace=d.get("workspace"),
            created_at=d.get("created_at", time.time()), finished_at=d.get("finished_at"),
            metrics=d.get("metrics", {}),
        )
        for nid, nd in d.get("nodes", {}).items():
            nd = dict(nd)
            nd["status"] = NodeStatus(nd["status"])
            run.nodes[nid] = Node(**nd)
        for aid, ad in d.get("artifacts", {}).items():
            run.artifacts[aid] = Artifact(**ad)
        return run

    def downstream_of(self, node_id: str) -> set[str]:
        """Transitive descendants of a node."""
        out: set[str] = set()
        frontier = [node_id]
        while frontier:
            cur = frontier.pop()
            for nid, n in self.nodes.items():
                if cur in n.depends_on and nid not in out:
                    out.add(nid)
                    frontier.append(nid)
        return out


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"
