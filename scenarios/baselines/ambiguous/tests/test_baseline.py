from fastapi.testclient import TestClient
from shortcode.app import app


def test_liveness():
    assert TestClient(app).get("/health/live").json() == {"status": "ok"}
