from fastapi.testclient import TestClient

from ultron_eyes.config import Config
from ultron_eyes.store import Store
from ultron_eyes.web import create_app


def client(tmp_path, **kw):
    cfg = Config(nvr_host="x", nvr_user="u", nvr_password="p", **kw)
    return TestClient(create_app(cfg, Store(tmp_path)), base_url="http://127.0.0.1:8765")


def test_local_host_allowed(tmp_path):
    assert client(tmp_path).get("/api/stats").status_code == 200


def test_dns_rebinding_host_rejected(tmp_path):
    r = client(tmp_path).get("/api/stats", headers={"host": "evil.example.com"})
    assert r.status_code == 400


def test_cross_site_post_blocked(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/events/1/review", json={"correct": True},
               headers={"origin": "http://evil.example.com", "sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert c.post("/api/events/1/review", json={"correct": True}).status_code == 404  # same-origin passes the check


def test_token_required_when_set(tmp_path):
    c = client(tmp_path, web_token="s3cret")
    assert c.get("/api/stats").status_code == 401
    assert c.get("/api/stats", headers={"x-ultron-token": "s3cret"}).status_code == 200
