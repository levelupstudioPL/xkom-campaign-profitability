"""Synchronizacja: pobranie danych z Google Ads i GA4 do DuckDB (idempotentnie per zakres dat)."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from . import db
from .config import Settings, get_settings
from .connectors import demo

log = logging.getLogger("sync")

_running = {"active": False, "since": None}


def status() -> dict:
    return dict(_running)


def _run(source: str, d_from: date, d_to: date, fn) -> int:
    started = datetime.now()
    try:
        n = fn()
        db.log_sync(source, d_from, d_to, n, "ok", "", started, datetime.now())
        log.info("%s %s..%s: %s wierszy", source, d_from, d_to, n)
        return n
    except Exception as e:  # noqa: BLE001 — logujemy każdą porażkę źródła, pozostałe idą dalej
        db.log_sync(source, d_from, d_to, 0, "error", f"{type(e).__name__}: {e}", started, datetime.now())
        log.exception("%s: błąd synchronizacji", source)
        return -1


def _backup() -> None:
    try:
        db.backup_to_gcs()
    except Exception:  # noqa: BLE001
        log.exception("Nie udało się zapisać snapshotu bazy do GCS")


def sync_range(d_from: date, d_to: date, s: Settings | None = None) -> dict:
    s = s or get_settings()
    if _running["active"]:
        raise RuntimeError("Synchronizacja już trwa")
    _running.update(active=True, since=datetime.now().isoformat(timespec="seconds"))
    try:
        db.init_db()
        result = {}
        if s.mode == "demo":
            ads, shop, ga = demo.generate(d_from, d_to)
            result["google_ads_campaigns"] = _run("google_ads_campaigns", d_from, d_to, lambda: db.replace_range("ads_campaign_daily", ads, d_from, d_to))
            result["google_ads_products"] = _run("google_ads_products", d_from, d_to, lambda: db.replace_range("ads_product_daily", shop, d_from, d_to))
            result["ga4"] = _run("ga4_demo", d_from, d_to, lambda: db.replace_range("ga4_item_daily", ga, d_from, d_to))
            if not db.get_margins():
                db.set_margins(demo.DEMO_MARGINS)
            _backup()
            return result

        from .connectors import google_ads

        for cid in s.customer_id_list:
            sub = s.model_copy(update={"google_ads_customer_ids": cid})
            result[f"google_ads_campaigns:{cid}"] = _run(
                "google_ads_campaigns", d_from, d_to,
                lambda sub=sub, cid=cid: db.replace_range("ads_campaign_daily", google_ads.fetch_campaigns(sub, d_from, d_to), d_from, d_to, "AND customer_id = ?", [cid]),
            )
            result[f"google_ads_products:{cid}"] = _run(
                "google_ads_products", d_from, d_to,
                lambda sub=sub, cid=cid: db.replace_range("ads_product_daily", google_ads.fetch_products(sub, d_from, d_to), d_from, d_to, "AND customer_id = ?", [cid]),
            )

        if s.ga4_source == "bigquery":
            from .connectors import ga4_bigquery as ga4
        else:
            from .connectors import ga4_data_api as ga4
        result["ga4"] = _run(f"ga4_{s.ga4_source}", d_from, d_to, lambda: db.replace_range("ga4_item_daily", ga4.fetch(s, d_from, d_to), d_from, d_to))
        _backup()
        return result
    finally:
        _running.update(active=False, since=None)


def sync_recent(s: Settings | None = None) -> dict:
    """Zadanie harmonogramu: nadpisuje ostatnie N dni (dane GA4 dojrzewają do 48 h)."""
    s = s or get_settings()
    d_to = date.today() - timedelta(days=1)
    bounds = db.data_bounds()
    if bounds["max"] is None:
        d_from = d_to - timedelta(days=s.initial_backfill_days - 1)
    else:
        last = date.fromisoformat(bounds["max"])
        d_from = min(last, d_to) - timedelta(days=s.sync_lookback_days - 1)
    return sync_range(d_from, d_to, s)


def sync_backfill(days: int, s: Settings | None = None, chunk_days: int = 31) -> dict:
    """Backfill w paczkach miesięcznych, żeby nie przekraczać limitów API."""
    s = s or get_settings()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    out = {}
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=chunk_days - 1), end)
        out[f"{cur}..{nxt}"] = sync_range(cur, nxt, s)
        cur = nxt + timedelta(days=1)
    return out


def check_connections(s: Settings | None = None) -> list[str]:
    s = s or get_settings()
    if s.mode == "demo":
        return ["APP_MODE=demo — połączenia z Google nie są używane"]
    out = []
    try:
        from .connectors import google_ads

        out += [f"Google Ads OK: {x}" for x in google_ads.check(s)] or ["Google Ads: brak kont w GOOGLE_ADS_CUSTOMER_IDS"]
    except Exception as e:  # noqa: BLE001
        out.append(f"Google Ads BŁĄD: {type(e).__name__}: {e}")
    try:
        if s.ga4_source == "bigquery":
            from .connectors import ga4_bigquery as ga4
        else:
            from .connectors import ga4_data_api as ga4
        out.append(f"GA4 ({s.ga4_source}) OK: {ga4.check(s)}")
    except Exception as e:  # noqa: BLE001
        out.append(f"GA4 ({s.ga4_source}) BŁĄD: {type(e).__name__}: {e}")
    return out
