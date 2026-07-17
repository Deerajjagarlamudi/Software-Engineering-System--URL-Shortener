"""SQLite persistence for workflow runs and audit events."""

import json
import os
import sqlite3
import threading
import time
import uuid

from app.orchestrator.models import AuditEvent, WorkflowRun

DB_PATH = os.environ.get("ORCHESTRATOR_DB", "./data/orchestrator.db")
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, data TEXT, updated_at REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_events ("
        "event_id TEXT PRIMARY KEY, run_id TEXT, at REAL, kind TEXT, detail TEXT)"
    )
    return conn


def save_run(run: WorkflowRun) -> None:
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, data, updated_at) VALUES (?, ?, ?)",
                (run.run_id, json.dumps(run.to_dict()), time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def load_run(run_id: str) -> WorkflowRun | None:
    with _lock:
        conn = _conn()
        try:
            row = conn.execute("SELECT data FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        finally:
            conn.close()
    return WorkflowRun.from_dict(json.loads(row[0])) if row else None


def list_runs() -> list[dict]:
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT data FROM runs ORDER BY updated_at DESC LIMIT 50"
            ).fetchall()
        finally:
            conn.close()
    out = []
    for (data,) in rows:
        d = json.loads(data)
        out.append(
            {
                "run_id": d["run_id"],
                "scenario": d["scenario"],
                "status": d["status"],
                "requirement": d["requirement"][:120],
                "created_at": d["created_at"],
            }
        )
    return out


def all_runs() -> list[WorkflowRun]:
    """Load complete run state for aggregate metrics and restart recovery."""
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute("SELECT data FROM runs ORDER BY updated_at").fetchall()
        finally:
            conn.close()
    return [WorkflowRun.from_dict(json.loads(data)) for (data,) in rows]


def audit(run_id: str, kind: str, detail: dict | None = None) -> AuditEvent:
    ev = AuditEvent(
        event_id=uuid.uuid4().hex,
        run_id=run_id,
        at=time.time(),
        kind=kind,
        detail=detail or {},
    )
    with _lock:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO audit_events (event_id, run_id, at, kind, detail) VALUES (?, ?, ?, ?, ?)",
                (ev.event_id, ev.run_id, ev.at, ev.kind, json.dumps(ev.detail)),
            )
            conn.commit()
        finally:
            conn.close()
    return ev


def audit_trail(run_id: str) -> list[dict]:
    with _lock:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT at, kind, detail FROM audit_events WHERE run_id = ? ORDER BY at",
                (run_id,),
            ).fetchall()
        finally:
            conn.close()
    return [{"at": at, "kind": kind, "detail": json.loads(detail)} for at, kind, detail in rows]
