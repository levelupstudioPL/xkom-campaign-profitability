"""Test end-to-end w trybie demo: synchronizacja → raport → marże → CSV."""
from datetime import date

from fastapi.testclient import TestClient


def test_demo_end_to_end():
    from app import sync
    from app.main import app

    sync.sync_range(date(2026, 10, 1), date(2026, 10, 31))
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["data"]["min"] == "2026-10-01"
        r = c.get("/api/report", params={"date_from": "2026-10-01", "date_to": "2026-10-31"}).json()
        assert r["totals"]["ads_cost"] > 0
        assert any(cat["category"] == "Laptopy" for cat in r["categories"])
        assert r["products"] and r["products_total"] >= len(r["products"])
        # koszt nie ginie: suma kategorii = koszt kampanii (± nadwyżka kosztu produktowego)
        assert abs(sum(cat["cost"] for cat in r["categories"]) - r["totals"]["total_cost"]) < 1
        m = c.put("/api/margins", json={"margins": {"Laptopy": 9.5}}).json()
        assert m["margins"]["Laptopy"] == 9.5
        csv = c.get("/api/products.csv", params={"date_from": "2026-10-01", "date_to": "2026-10-31"})
        assert csv.status_code == 200 and "Laptop" in csv.content.decode("utf-8-sig")
        log = c.get("/api/sync/log").json()
        assert log["log"] and all(entry["status"] == "ok" for entry in log["log"])


def test_basic_auth(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "admin")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "tajne")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/margins").status_code == 401
        assert c.get("/api/margins", auth=("admin", "tajne")).status_code == 200
        assert c.get("/api/health").status_code == 200


def _reload_app(monkeypatch, **env):
    import importlib

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from app.config import get_settings

    get_settings.cache_clear()
    import app.main

    return importlib.reload(app.main).app


def test_bearer_token_and_cors_for_netlify(monkeypatch):
    origin = "https://xkom-rentownosc.netlify.app"
    app = _reload_app(monkeypatch, API_TOKEN="t0ken", CORS_ORIGINS=origin)
    with TestClient(app) as c:
        # strona i config.js są publiczne (dane nie)
        assert c.get("/").status_code == 200
        assert "apiBase" in c.get("/config.js").text
        r = c.get("/api/margins", headers={"Origin": origin})
        assert r.status_code == 401 and "www-authenticate" not in r.headers
        assert r.headers["access-control-allow-origin"] == origin
        assert c.get("/api/margins", headers={"Authorization": "Bearer zly"}).status_code == 401
        assert c.get("/api/margins", headers={"Authorization": "Bearer t0ken"}).status_code == 200
        pre = c.options("/api/report", headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
        assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == origin
        evil = c.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in evil.headers


def test_sync_wait_for_cloud_scheduler(monkeypatch):
    app = _reload_app(monkeypatch, API_TOKEN="t0ken", SYNC_ON_EMPTY_START="false")
    with TestClient(app) as c:
        r = c.post("/api/sync?wait=true", json={"date_from": "2026-10-01", "date_to": "2026-10-03"}, headers={"Authorization": "Bearer t0ken"})
        assert r.status_code == 200, r.text
        assert r.json()["failed"] == []
        assert c.get("/api/health").json()["data"]["max"] is not None
