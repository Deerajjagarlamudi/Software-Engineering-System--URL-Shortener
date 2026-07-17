"""Isolated, scenario-seeded Git workspaces with idempotent checkpoints."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ALLOWED_COMMANDS = {"python", "pytest"}
BASELINES = Path(__file__).resolve().parents[2] / "scenarios" / "baselines"


class WorkspaceError(Exception):
    pass


def _git(workspace: str, *args: str, allow_noop: bool = False) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if allow_noop and ("nothing to commit" in combined or "no changes added" in combined):
            return head(workspace)
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr[:300]}")
    return result.stdout.strip()


def create_workspace(run_id: str, scenario: str) -> str:
    source = BASELINES / scenario
    if not source.is_dir():
        raise WorkspaceError(f"scenario baseline not found: {scenario}")
    ws = tempfile.mkdtemp(prefix=f"run_{run_id}_")
    shutil.copytree(source, ws, dirs_exist_ok=True)
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "orchestrator@local")
    _git(ws, "config", "user.name", "orchestrator")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "scenario baseline")
    return ws


def _safe_path(workspace: str, rel_path: str) -> Path:
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkspaceError(f"path escapes workspace: {rel_path}")
    root = Path(workspace).resolve()
    full = root.joinpath(candidate)
    parent = full.parent.resolve()
    if root != parent and root not in parent.parents:
        raise WorkspaceError(f"path escapes workspace through symlink: {rel_path}")
    return full


def apply_changes(
    workspace: str,
    writes: dict[str, str],
    deletes: list[str] | None,
    message: str,
) -> tuple[str, bool]:
    """Apply writes/deletes and return (revision, changed). No-op changes are valid."""
    before = head(workspace)
    for rel_path, content in sorted(writes.items()):
        full = _safe_path(workspace, rel_path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    for rel_path in sorted(deletes or []):
        full = _safe_path(workspace, rel_path)
        if full.is_dir():
            raise WorkspaceError(f"refusing directory deletion: {rel_path}")
        if full.exists() or full.is_symlink():
            full.unlink()
    _git(workspace, "add", "-A")
    status = _git(workspace, "status", "--porcelain")
    if not status:
        return before, False
    _git(workspace, "commit", "-q", "-m", message)
    return head(workspace), True


def rollback(workspace: str, checkpoint_sha: str) -> None:
    _git(workspace, "reset", "--hard", checkpoint_sha)
    _git(workspace, "clean", "-fd")


def head(workspace: str) -> str:
    return _git(workspace, "rev-parse", "HEAD")


def list_files(workspace: str) -> list[str]:
    return [f for f in _git(workspace, "ls-files").splitlines() if f]


def read_file(workspace: str, rel_path: str) -> str:
    return _safe_path(workspace, rel_path).read_text()


def context_snapshot(workspace: str, max_files: int = 20, max_chars: int = 40_000) -> dict:
    files = list_files(workspace)
    selected: dict[str, str] = {}
    used = 0
    for path in files[:max_files]:
        try:
            text = read_file(workspace, path)
        except (OSError, UnicodeDecodeError):
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        selected[path] = text[:remaining]
        used += len(selected[path])
    return {"manifest": files, "sources": selected, "revision": head(workspace)}


def run_allowed(
    workspace: str, command: list[str], timeout: int = 120
) -> subprocess.CompletedProcess:
    if not command or command[0] not in ALLOWED_COMMANDS:
        raise WorkspaceError("command is not allowlisted")
    return subprocess.run(command, cwd=workspace, capture_output=True, text=True, timeout=timeout)
