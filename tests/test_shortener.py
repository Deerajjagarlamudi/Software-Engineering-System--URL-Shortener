from datetime import datetime, timedelta, timezone


def test_create_and_redirect(client):
    r = client.post("/api/v1/links", json={"target_url": "https://example.com/page"})
    assert r.status_code == 201
    code = r.json()["code"]
    assert len(code) == 7

    r2 = client.get(f"/{code}", follow_redirects=False)
    assert r2.status_code == 307
    assert r2.headers["location"] == "https://example.com/page"


def test_custom_alias_and_idempotency(client):
    body = {"target_url": "https://example.com/a", "custom_alias": "my-link"}
    assert client.post("/api/v1/links", json=body).status_code == 201
    # same alias + same target -> idempotent 201 with same code
    r = client.post("/api/v1/links", json=body)
    assert r.status_code == 201 and r.json()["code"] == "my-link"
    # same alias + different target -> conflict
    r = client.post("/api/v1/links", json={"target_url": "https://other.com", "custom_alias": "my-link"})
    assert r.status_code == 409


def test_invalid_inputs(client):
    assert client.post("/api/v1/links", json={"target_url": "ftp://x.com/f"}).status_code == 400
    assert client.post("/api/v1/links", json={"target_url": "https://x.com", "custom_alias": "a"}).status_code == 400
    assert client.post("/api/v1/links", json={"target_url": "https://x.com", "custom_alias": "api"}).status_code == 400


def test_expiry(client):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = client.post("/api/v1/links", json={"target_url": "https://example.com/e", "expires_at": past})
    code = r.json()["code"]
    assert client.get(f"/{code}", follow_redirects=False).status_code == 410


def test_analytics_and_delete(client):
    code = client.post("/api/v1/links", json={"target_url": "https://example.com/s"}).json()["code"]
    for _ in range(3):
        client.get(f"/{code}", follow_redirects=False)
    stats = client.get(f"/api/v1/links/{code}/analytics").json()
    assert stats["click_count"] == 3
    assert len(stats["recent_clicks"]) == 3

    assert client.delete(f"/api/v1/links/{code}").status_code == 204
    assert client.get(f"/api/v1/links/{code}").status_code == 404


def test_not_found_and_health(client):
    assert client.get("/nope999", follow_redirects=False).status_code == 404
    assert client.get("/health/live").json()["status"] == "ok"
    assert client.get("/health/ready").json()["status"] == "ready"
