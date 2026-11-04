import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_MODE", "demo")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.duckdb"))
    monkeypatch.setenv("MARGINS_SEED_FILE", str(tmp_path / "none.csv"))
    monkeypatch.setenv("CATEGORY_MAP_FILE", str(tmp_path / "none_map.csv"))
    monkeypatch.setenv("SYNC_ENABLED", "false")
    monkeypatch.setenv("SYNC_ON_EMPTY_START", "false")
    monkeypatch.setenv("DASHBOARD_USER", "")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    os.environ.pop("DASHBOARD_USER", None)
