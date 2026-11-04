"""Warstwa danych — DuckDB (plik lokalny, montowany jako wolumen w Dockerze)."""
from __future__ import annotations

import csv
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS ads_campaign_daily (
    date DATE NOT NULL,
    customer_id VARCHAR NOT NULL,
    campaign_id VARCHAR NOT NULL,
    campaign_name VARCHAR,
    channel_type VARCHAR,
    cost DOUBLE,
    clicks BIGINT,
    impressions BIGINT
);
CREATE TABLE IF NOT EXISTS ads_product_daily (
    date DATE NOT NULL,
    customer_id VARCHAR NOT NULL,
    campaign_id VARCHAR NOT NULL,
    campaign_name VARCHAR,
    item_id VARCHAR NOT NULL,
    product_title VARCHAR,
    product_type_l1 VARCHAR,
    cost DOUBLE,
    clicks BIGINT,
    impressions BIGINT
);
CREATE TABLE IF NOT EXISTS ga4_item_daily (
    date DATE NOT NULL,
    campaign_id VARCHAR,
    campaign_name VARCHAR,
    item_id VARCHAR NOT NULL,
    item_name VARCHAR,
    item_category VARCHAR,
    items_viewed DOUBLE,
    items_purchased DOUBLE,
    item_revenue DOUBLE
);
CREATE TABLE IF NOT EXISTS margins (
    category VARCHAR PRIMARY KEY,
    margin_pct DOUBLE NOT NULL,
    updated_at TIMESTAMP DEFAULT current_timestamp
);
CREATE SEQUENCE IF NOT EXISTS sync_log_seq;
CREATE TABLE IF NOT EXISTS sync_log (
    id BIGINT DEFAULT nextval('sync_log_seq'),
    source VARCHAR,
    date_from DATE,
    date_to DATE,
    rows BIGINT,
    status VARCHAR,
    message VARCHAR,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);
"""

_lock = threading.Lock()


@contextmanager
def connect():
    """Jedno połączenie na operację; zapis serializowany lockiem (DuckDB = jeden writer)."""
    s = get_settings()
    Path(s.db_path).parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        con = duckdb.connect(str(s.db_path))
        try:
            yield con
        finally:
            con.close()


def init_db() -> None:
    with connect() as con:
        con.execute(SCHEMA)
        n = con.execute("SELECT count(*) FROM margins").fetchone()[0]
    if n == 0:
        seed_margins_from_file(get_settings().margins_seed_file)


def replace_range(table: str, df: pd.DataFrame, date_from: date, date_to: date, extra_where: str = "", params: list | None = None) -> int:
    """Idempotentny zapis: kasuje zakres dat (i ew. konto) i wstawia nowe wiersze."""
    with connect() as con:
        con.execute("BEGIN")
        con.execute(
            f"DELETE FROM {table} WHERE date BETWEEN ? AND ? {extra_where}",
            [date_from, date_to, *(params or [])],
        )
        if len(df):
            con.register("incoming", df)
            cols = ", ".join(df.columns)
            con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM incoming")
            con.unregister("incoming")
        con.execute("COMMIT")
    return len(df)


def query_df(sql: str, params: list | None = None) -> pd.DataFrame:
    with connect() as con:
        return con.execute(sql, params or []).df()


def get_margins() -> dict[str, float]:
    df = query_df("SELECT category, margin_pct FROM margins")
    return dict(zip(df.category, df.margin_pct, strict=False))


def set_margins(margins: dict[str, float | None]) -> None:
    with connect() as con:
        for cat, pct in margins.items():
            if pct is None:
                con.execute("DELETE FROM margins WHERE category = ?", [cat])
            else:
                con.execute(
                    "INSERT INTO margins (category, margin_pct) VALUES (?, ?) "
                    "ON CONFLICT (category) DO UPDATE SET margin_pct = excluded.margin_pct, updated_at = now()",
                    [cat, float(pct)],
                )


def seed_margins_from_file(path: Path) -> int:
    if not Path(path).exists():
        return 0
    with open(path, encoding="utf-8") as f:
        sample = f.read(2048)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        rows = list(csv.DictReader(f, dialect=dialect))
    m = {}
    for r in rows:
        cat = (r.get("kategoria") or r.get("category") or "").strip()
        val = (r.get("marza_proc") or r.get("margin_pct") or "").strip().replace(",", ".").replace("%", "")
        if cat and val:
            m[cat] = float(val)
    set_margins(m)
    return len(m)


def log_sync(source: str, date_from: date, date_to: date, rows: int, status: str, message: str, started_at, finished_at) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO sync_log (source, date_from, date_to, rows, status, message, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?)",
            [source, date_from, date_to, rows, status, message[:2000], started_at, finished_at],
        )


def data_bounds() -> dict:
    df = query_df(
        "SELECT min(date) AS dmin, max(date) AS dmax FROM ("
        "SELECT date FROM ads_campaign_daily UNION ALL SELECT date FROM ga4_item_daily)"
    )
    r = df.iloc[0]
    return {"min": None if pd.isna(r.dmin) else str(r.dmin)[:10], "max": None if pd.isna(r.dmax) else str(r.dmax)[:10]}
