"""FastAPI: API raportowe + dashboard + harmonogram synchronizacji."""
from __future__ import annotations

import base64
import io
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import db, model, sync
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
STATIC = Path(__file__).parent / "static"
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    s = get_settings()
    db.init_db()
    if s.sync_on_empty_start and db.data_bounds()["max"] is None:
        # pierwszy start: backfill w tle (demo: 90 dni danych syntetycznych)
        threading.Thread(target=_safe_sync_recent, daemon=True).start()
    if s.sync_enabled:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        _scheduler = BackgroundScheduler(timezone=s.sync_timezone)
        _scheduler.add_job(_safe_sync_recent, CronTrigger.from_crontab(s.sync_cron, timezone=s.sync_timezone), id="daily_sync", max_instances=1, coalesce=True)
        _scheduler.start()
        log.info("Harmonogram synchronizacji: %s (%s)", s.sync_cron, s.sync_timezone)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


def _safe_sync_recent():
    try:
        sync.sync_recent()
    except Exception:  # noqa: BLE001
        log.exception("Synchronizacja nie powiodła się")


app = FastAPI(title="Rentowność kampanii per produkt", version="0.1.0", lifespan=lifespan)


PUBLIC_PATHS = {"/api/health"}


def _check_auth(header: str) -> bool | None:
    """True = poprawne dane, False = błędne/brak, None = uwierzytelnianie wyłączone."""
    s = get_settings()
    if not (s.api_token or s.dashboard_user):
        return None
    if s.api_token and header.startswith("Bearer "):
        return secrets.compare_digest(header[7:].strip(), s.api_token)
    if s.dashboard_user and header.startswith("Basic "):
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:  # noqa: BLE001
            return False
        return secrets.compare_digest(user, s.dashboard_user) and secrets.compare_digest(pwd, s.dashboard_password)
    return False


@app.middleware("http")
async def auth(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or path in PUBLIC_PATHS:
        return await call_next(request)
    s = get_settings()
    is_api = path.startswith("/api/")
    # Statyczny dashboard (/, /config.js) chroni tylko basic auth; z tokenem strona sama prosi o token
    if not is_api and not s.dashboard_user:
        return await call_next(request)
    if _check_auth(request.headers.get("authorization", "")) is False:
        cross_origin = "origin" in request.headers and request.headers["origin"].rstrip("/") != str(request.base_url).rstrip("/")
        headers = {} if (cross_origin or not s.dashboard_user) else {"WWW-Authenticate": 'Basic realm="rentownosc"'}
        return JSONResponse({"detail": "Brak autoryzacji — podaj token API"}, status_code=401, headers=headers)
    return await call_next(request)


# CORS dodany jako ostatni = najbardziej zewnętrzny (obsługuje preflight przed auth)
_cors = get_settings().cors_origin_list
if _cors:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["Content-Disposition"],
        max_age=3600,
    )


def _params(date_from: date | None, date_to: date | None, basis: str, vat: str | None, channel: str) -> model.Params:
    b = db.data_bounds()
    if b["max"] is None:
        raise HTTPException(409, "Brak danych — trwa pierwsza synchronizacja lub nie skonfigurowano źródeł.")
    d_to = date_to or date.fromisoformat(b["max"])
    d_from = date_from or max(date.fromisoformat(b["min"]), d_to - timedelta(days=29))
    if d_from > d_to:
        raise HTTPException(400, "date_from > date_to")
    gross = None if vat in (None, "") else vat == "gross"
    return model.Params(d_from, d_to, "all" if basis == "all" else "attr", gross, channel or "")


@app.get("/api/health")
def health():
    s = get_settings()
    return {"status": "ok", "mode": s.mode, "ga4_source": s.ga4_source, "data": db.data_bounds(), "sync": sync.status()}


@app.get("/api/report")
def report(
    date_from: date | None = None, date_to: date | None = None, basis: str = "attr", vat: str | None = None, channel: str = "",
    q: str = "", category: str = "", status: str = "", sort: str = "result", desc: bool = False, limit: int = Query(1000, le=20000),
):
    p = _params(date_from, date_to, basis, vat, channel)
    r = model.compute(p)
    products, n = model.products_records(r.pop("products"), q, category, status, sort, desc, limit)
    r["products"], r["products_total"] = products, n
    r["bounds"] = db.data_bounds()
    return JSONResponse(r)


@app.get("/api/products.csv")
def products_csv(date_from: date | None = None, date_to: date | None = None, basis: str = "attr", vat: str | None = None, channel: str = ""):
    p = _params(date_from, date_to, basis, vat, channel)
    rows, _ = model.products_records(model.compute(p)["products"], limit=10**7)
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, sep=";", decimal=",", index=False)
    name = f"rentownosc_produktow_{p.date_from}_{p.date_to}.csv"
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/categories.csv")
def categories_csv(date_from: date | None = None, date_to: date | None = None, basis: str = "attr", vat: str | None = None, channel: str = ""):
    p = _params(date_from, date_to, basis, vat, channel)
    buf = io.StringIO()
    pd.DataFrame(model.compute(p)["categories"]).to_csv(buf, sep=";", decimal=",", index=False)
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="rentownosc_kategorii_{p.date_from}_{p.date_to}.csv"'})


class MarginsIn(BaseModel):
    margins: dict[str, float | None]


@app.get("/api/margins")
def get_margins():
    s = get_settings()
    cats = db.query_df("SELECT DISTINCT item_category AS c FROM ga4_item_daily WHERE item_category IS NOT NULL AND item_category NOT IN ('', '(not set)')")
    cmap = model._category_map(s.category_map_file)
    found = sorted({cmap.get(c, c) for c in cats.c})
    return {"default_margin_pct": s.default_margin_pct, "margins": db.get_margins(), "categories": found}


@app.put("/api/margins")
def put_margins(body: MarginsIn):
    for k, v in body.margins.items():
        if v is not None and not (0 <= v <= 100):
            raise HTTPException(400, f"Marża dla {k} poza zakresem 0–100")
    db.set_margins(body.margins)
    sync._backup()
    return {"ok": True, "margins": db.get_margins()}


class SyncIn(BaseModel):
    date_from: date | None = None
    date_to: date | None = None


@app.post("/api/sync")
def trigger_sync(body: SyncIn | None = None, wait: bool = False):
    """wait=true — synchronicznie (Cloud Scheduler na Cloud Run: CPU tylko w trakcie żądania)."""
    body = body or SyncIn()
    if sync.status()["active"]:
        raise HTTPException(409, "Synchronizacja już trwa")
    if body.date_from and body.date_to:
        def target():
            return sync.sync_range(body.date_from, body.date_to)
    else:
        target = sync.sync_recent
    if wait:
        result = target()
        failed = [k for k, v in result.items() if v == -1]
        return JSONResponse({"result": result, "failed": failed}, status_code=500 if failed else 200)

    def bg():
        try:
            target()
        except Exception:  # noqa: BLE001
            log.exception("Synchronizacja nie powiodła się")

    threading.Thread(target=bg, daemon=True).start()
    return {"started": True}


@app.get("/api/sync/log")
def sync_log(limit: int = 30):
    df = db.query_df("SELECT * FROM sync_log ORDER BY id DESC LIMIT ?", [limit])
    df = df.astype(object).where(pd.notna(df), None)
    return {"status": sync.status(), "log": [{k: (str(v) if v is not None and not isinstance(v, (int, float, str)) else v) for k, v in r.items()} for r in df.to_dict(orient="records")]}


@app.get("/config.js")
def config_js():
    # Przy serwowaniu z backendu API jest pod tym samym adresem. Na Netlify plik generuje build (netlify.toml).
    return Response("window.APP_CONFIG = { apiBase: '' };\n", media_type="application/javascript")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
