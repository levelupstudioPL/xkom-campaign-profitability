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
