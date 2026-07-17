from fastapi.testclient import TestClient
from shortcode.app import app, clicks, links


def test_existing_create_redirect_and_analytics():
    links.clear()
    clicks.clear()
    client = TestClient(app)
    assert client.post("/links", json={"code": "abc", "target_url": "https://example.com"}).status_code == 201
    assert client.get("/abc", follow_redirects=False).status_code == 307
    assert client.get("/links/abc/analytics").json()["click_count"] == 1
