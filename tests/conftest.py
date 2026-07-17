import os
import tempfile

# Point databases at a writable temp location before app modules import.
_tmp = tempfile.mkdtemp(prefix="aus_test_")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/app.db")
os.environ.setdefault("ORCHESTRATOR_DB", f"{_tmp}/orchestrator.db")
os.environ.setdefault("LLM_PROVIDER", "mock")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())
