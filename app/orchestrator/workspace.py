"""Isolated git workspace per run: apply patches, checkpoint, rollback.

Generated changes never touch the primary repository; they land in a
temporary git repo and only an approved release would promote them.
"""
import os
import subprocess
import tempfile

ALLOWED_COMMANDS = {"git", "python", "pytest"}


class WorkspaceError(Exception):
    pass


def _git(workspace: str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise WorkspaceError(f"git {' '.join(args)} failed: {result.stderr[:300]}")
    return result.stdout.strip()


def create_workspace(run_id: str) -> str:
    ws = tempfile.mkdtemp(prefix=f"run_{run_id}_")
    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "orchestrator@local")
    _git(ws, "config", "user.name", "orchestrator")
    with open(os.path.join(ws, ".gitkeep"), "w") as f:
        f.write("")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "initial")
    return ws


def apply_files(workspace: str, files: dict[str, str], message: str) -> str:
    """Write files (path -> content), commit, return checkpoint sha."""
    for rel_path, content in files.items():
        norm = os.path.normpath(rel_path)
        if norm.startswith("..") or os.path.isabs(norm):
            raise WorkspaceError(f"path escapes workspace: {rel_path}")
        full = os.path.join(workspace, norm)
        os.makedirs(os.path.dirname(full) or workspace, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-q", "-m", message)
    return _git(workspace, "rev-parse", "HEAD")


def rollback(workspace: str, checkpoint_sha: str) -> None:
    _git(workspace, "reset", "--hard", checkpoint_sha)


def head(workspace: str) -> str:
    return _git(workspace, "rev-parse", "HEAD")


def list_files(workspace: str) -> list[str]:
    return [f for f in _git(workspace, "ls-files").splitlines() if f != ".gitkeep"]


def read_file(workspace: str, rel_path: str) -> str:
    norm = os.path.normpath(rel_path)
    if norm.startswith("..") or os.path.isabs(norm):
        raise WorkspaceError("path escapes workspace")
    with open(os.path.join(workspace, norm)) as f:
        return f.read()
