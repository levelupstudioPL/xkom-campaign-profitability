"""Model rentowności: koszt kampanii → produkt → kategoria, marża i koszt ruchu na sztukę.

Zasady (patrz README → Metodologia):
  1. Shopping / PMax: koszt per produkt wprost z shopping_performance_view.
  2. Reszta kosztu kampanii (Search, Display, Video, Demand Gen, nieproduktowa część PMax)
     dzielona proporcjonalnie do wyświetleń kart produktu (view_item) z sesji tej kampanii w GA4.
  3. Kampanie bez wyświetleń kart w GA4 → „Nieprzypisane”.
  4. Marża = przychód netto × marża % kategorii.
"""
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import db
from .config import Settings, get_settings

CHANNEL_LABEL = {
    "SEARCH": "Search",
    "SHOPPING": "Shopping",
    "PERFORMANCE_MAX": "Performance Max",
    "DISPLAY": "Display",
    "VIDEO": "Video",
    "DEMAND_GEN": "Demand Gen",
    "DISCOVERY": "Demand Gen",
}
TYPE_ORDER = ["Search", "Shopping", "Performance Max", "Display", "Video", "Demand Gen", "Inne"]
NON_PAID = re.compile(r"^\(|organic|direct|referral|newsletter|e-?mail|not set|not provided", re.I)
UNASSIGNED = "Nieprzypisane"
NO_CATEGORY = "Bez kategorii"


@dataclass
class Params:
    date_from: date
    date_to: date
    basis: str = "attr"  # attr = sprzedaż z sesji kampanii Google Ads; all = cała sprzedaż produktu
    revenue_gross: bool | None = None  # None = z konfiguracji
    channel: str = ""  # filtr typu kampanii (etykieta z TYPE_ORDER)


def _pln(x: float) -> str:
    return f"{x:,.0f}".replace(",", "\u00a0") + " zł"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}".replace(".", ",") + "%"


def _norm_name(s: pd.Series) -> pd.Series:
    return s.fillna("").astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)


def _norm_id(s: pd.Series, cfg: Settings) -> pd.Series:
    x = s.fillna("").astype(str).str.strip()
    if cfg.item_id_lowercase:
        x = x.str.lower()
    if cfg.item_id_strip_regex:
        x = x.str.replace(cfg.item_id_strip_regex, "", regex=True)
    return x


def _category_map(path: Path) -> dict[str, str]:
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as f:
        sample = f.read(2048)
        f.seek(0)
        rows = list(csv.DictReader(f, dialect=csv.Sniffer().sniff(sample, delimiters=";,\t")))
    return {(r.get("kategoria_zrodlowa") or "").strip(): (r.get("kategoria_raportowa") or "").strip() for r in rows if r.get("kategoria_zrodlowa")}


def load_frames(p: Params) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = [p.date_from, p.date_to]
    camp = db.query_df(
        """SELECT campaign_id, arg_max(campaign_name, date) AS campaign_name, arg_max(channel_type, date) AS channel_type,
                  sum(cost) AS cost, sum(clicks) AS clicks
           FROM ads_campaign_daily WHERE date BETWEEN ? AND ? GROUP BY campaign_id""", rng)
    prod = db.query_df(
        """SELECT campaign_id, arg_max(campaign_name, date) AS campaign_name, item_id,
                  any_value(product_title) AS product_title, any_value(product_type_l1) AS product_type_l1,
                  sum(cost) AS cost, sum(clicks) AS clicks
           FROM ads_product_daily WHERE date BETWEEN ? AND ? GROUP BY campaign_id, item_id""", rng)
    ga = db.query_df(
        """SELECT coalesce(campaign_id, '') AS campaign_id, coalesce(campaign_name, '(not set)') AS campaign_name, item_id,
                  arg_max(item_name, items_viewed) AS item_name, arg_max(item_category, items_viewed) AS item_category,
                  sum(items_viewed) AS views, sum(items_purchased) AS units, sum(item_revenue) AS revenue
           FROM ga4_item_daily WHERE date BETWEEN ? AND ? GROUP BY 1, 2, 3""", rng)
    return camp, prod, ga


def compute(p: Params, cfg: Settings | None = None, frames=None, margins: dict[str, float] | None = None) -> dict:
    cfg = cfg or get_settings()
    camp, prod, ga = frames if frames is not None else load_frames(p)
    margins = margins if margins is not None else db.get_margins()
    gross = cfg.ga4_revenue_includes_vat if p.revenue_gross is None else p.revenue_gross
    vat_div = 1 + cfg.vat_rate if gross else 1.0
    cat_map = _category_map(cfg.category_map_file)

    camp = camp.copy()
    prod = prod.copy()
    ga = ga.copy()
    camp["campaign_id"] = camp["campaign_id"].astype(str)
    prod["campaign_id"] = prod["campaign_id"].astype(str)
    ga["campaign_id"] = ga["campaign_id"].fillna("").astype(str)
    camp["type"] = camp["channel_type"].map(CHANNEL_LABEL).fillna("Inne")

    # Kampanie obecne tylko w raporcie produktowym (np. inne konto) → dopisz jako Shopping
    missing = prod.loc[~prod.campaign_id.isin(camp.campaign_id)]
    if len(missing):
        extra = missing.groupby("campaign_id").agg(campaign_name=("campaign_name", "first"), cost=("cost", "sum"), clicks=("clicks", "sum")).reset_index()
        extra["channel_type"], extra["type"] = "SHOPPING", "Shopping"
        camp = pd.concat([camp, extra], ignore_index=True)

    prod["item"] = _norm_id(prod["item_id"], cfg)
    ga["item"] = _norm_id(ga["item_id"], cfg)

    # Łączenie GA4 → kampania Google Ads: po ID (auto-tagging), w drugiej kolejności po nazwie
    ids = set(camp.campaign_id)
    name_to_id = dict(zip(_norm_name(camp.campaign_name), camp.campaign_id, strict=False))
    by_id = ga.campaign_id.where(ga.campaign_id.isin(ids))
    ga["ads_cid"] = by_id.fillna(_norm_name(ga.campaign_name).map(name_to_id))

    all_types = [t for t in TYPE_ORDER if t in set(camp["type"])]
    if p.channel:
        camp = camp[camp["type"] == p.channel]
    ctype = dict(zip(camp.campaign_id, camp["type"], strict=False))

    # --- katalog produktów ------------------------------------------------
    cat_ga = ga.sort_values("views", ascending=False).groupby("item").agg(name=("item_name", "first"), category=("item_category", "first"))
    cat_ads = prod.groupby("item").agg(name=("product_title", "first"), category=("product_type_l1", "first"), raw_id=("item_id", "first"))
    catalog = cat_ga.combine_first(cat_ads[["name", "category"]])
    raw_ids = pd.concat([ga[["item", "item_id"]], prod[["item", "item_id"]]]).drop_duplicates("item").set_index("item")["item_id"]
    catalog["raw_id"] = raw_ids.reindex(catalog.index)
    catalog["category"] = catalog["category"].replace({"": np.nan, "(not set)": np.nan})
    catalog["category"] = catalog["category"].map(lambda c: cat_map.get(c, c) if isinstance(c, str) else c)
    catalog["has_ga4"] = catalog.index.isin(cat_ga.index)

    # --- koszt bezpośredni (Shopping/PMax) -----------------------------------
    prod_f = prod[prod.campaign_id.isin(ctype)].copy()
    prod_f["type"] = prod_f.campaign_id.map(ctype)
    direct_by_c = prod_f.groupby("campaign_id").cost.sum()
    camp["direct"] = camp.campaign_id.map(direct_by_c).fillna(0.0)
    camp["resid"] = (camp.cost - camp.direct).clip(lower=0)

    # --- alokacja reszty wg wyświetleń kart produktu --------------------------
    gac = ga[ga.ads_cid.isin(ctype)].copy()
    gac["type"] = gac.ads_cid.map(ctype)
    views_c = gac.groupby("ads_cid").views.sum()
    camp["views"] = camp.campaign_id.map(views_c).fillna(0.0)
    resid_c = camp.set_index("campaign_id").resid
    gac["alloc"] = np.where(
        gac.ads_cid.map(views_c) > 0,
        gac.ads_cid.map(resid_c) * gac.views / gac.ads_cid.map(views_c).replace(0, np.nan),
        0.0,
    )
    gac["alloc"] = gac["alloc"].fillna(0.0)
    unalloc = camp[(camp.resid > 0.005) & (camp.views <= 0)]

    # --- poziom produktu ---------------------------------------------------
    cost_direct = prod_f.groupby("item").cost.sum()
    cost_alloc = gac.groupby("item").alloc.sum()
    by_type = pd.concat([
        prod_f.groupby(["item", "type"]).cost.sum(),
        gac.groupby(["item", "type"]).alloc.sum(),
    ]).groupby(level=[0, 1]).sum().unstack(fill_value=0.0)
    attr = gac.groupby("item").agg(units_attr=("units", "sum"), rev_attr=("revenue", "sum"), views_paid=("views", "sum"))
    tot = ga.groupby("item").agg(units_all=("units", "sum"), rev_all=("revenue", "sum"))
    clicks = prod_f.groupby("item").clicks.sum()

    idx = catalog.index
    it = pd.DataFrame(index=idx)
    it["id"] = catalog.raw_id.fillna(pd.Series(idx, index=idx))
    it["name"] = catalog.name
    it["category"] = catalog.category.fillna(NO_CATEGORY)
    it["has_ga4"] = catalog.has_ga4
    it["cost_direct"] = cost_direct.reindex(idx).fillna(0.0)
    it["cost_alloc"] = cost_alloc.reindex(idx).fillna(0.0)
    it["cost"] = it.cost_direct + it.cost_alloc
    it = it.join(attr).join(tot)
    it["clicks"] = clicks.reindex(idx).fillna(0)
    for c in ("units_attr", "rev_attr", "views_paid", "units_all", "rev_all"):
        it[c] = it[c].fillna(0.0)
    it["rev_attr"] /= vat_div
    it["rev_all"] /= vat_div

    def margin_for(cat):
        return margins.get(cat, cfg.default_margin_pct) / 100

    it["margin_pct"] = it.category.map(margin_for)
    it["units"] = it.units_attr if p.basis == "attr" else it.units_all
    it["revenue"] = it.rev_attr if p.basis == "attr" else it.rev_all
    it["margin"] = it.revenue * it.margin_pct
    it["avg_price"] = it.rev_all / it.units_all.replace(0, np.nan)
    it["margin_unit"] = it.avg_price * it.margin_pct
    it["cost_unit"] = it.cost / it.units.replace(0, np.nan)
    it["result_unit"] = it.margin_unit - it.cost_unit
    it["eaten"] = it.cost / it.margin.replace(0, np.nan)
    it["result"] = it.margin - it.cost
    it["status"] = np.select(
        [it.cost <= 0, it.units <= 0, it.cost_unit > cfg.loss_threshold * it.margin_unit, it.cost_unit > cfg.edge_threshold * it.margin_unit],
        ["—", "Brak sprzedaży", "Strata", "Na granicy"],
        default="Rentowny",
    )
    for t in TYPE_ORDER:
        it["t_" + t] = by_type[t].reindex(idx).fillna(0.0) if t in by_type.columns else 0.0

    # --- kategorie ---------------------------------------------------------
    agg = {c: "sum" for c in ["cost", "cost_direct", "cost_alloc", "revenue", "margin", "units"] + ["t_" + t for t in TYPE_ORDER]}
    cats = it.groupby("category").agg(agg)
    cats["products_with_cost"] = it[it.cost > 0].groupby("category").size().reindex(cats.index).fillna(0).astype(int)
    cats["margin_pct"] = cats.index.map(margin_for)
    cats["has_margin"] = cats.index.map(lambda c: c in margins)
    cats["unassigned"] = False
    if len(unalloc):
        row = {c: 0.0 for c in cats.columns}
        row.update(cost=unalloc.resid.sum(), margin_pct=np.nan, has_margin=True, unassigned=True, products_with_cost=0)
        for t, v in unalloc.groupby("type").resid.sum().items():
            row["t_" + t] = v
        cats.loc[UNASSIGNED] = row
    cats["eaten"] = cats.cost / cats.margin.replace(0, np.nan)
    cats["roas"] = np.where(cats.unassigned, np.nan, cats.revenue / cats.cost.replace(0, np.nan))
    cats["breakeven_roas"] = 1 / cats.margin_pct.replace(0, np.nan)
    cats["result"] = cats.margin - cats.cost
    cats["cost_unit"] = cats.cost / cats.units.replace(0, np.nan)
    total_cost = float(cats.cost.sum())
    cats["share"] = cats.cost / total_cost if total_cost else 0.0
    cats["status"] = np.select(
        [cats.unassigned, cats.units <= 0, cats.eaten > cfg.loss_threshold, cats.eaten > cfg.edge_threshold],
        ["—", "Brak sprzedaży", "Strata", "Na granicy"], default="Rentowny")

    real = cats[~cats.unassigned]
    allocated = total_cost - float(unalloc.resid.sum())
    T = dict(
        ads_cost=float(camp.cost.sum()),
        total_cost=total_cost,
        allocated_cost=allocated,
        allocated_share=allocated / total_cost if total_cost else None,
        revenue=float(real.revenue.sum()),
        margin=float(real.margin.sum()),
        units=float(real.units.sum()),
    )
    T["roas"] = T["revenue"] / total_cost if total_cost else None
    T["eaten"] = total_cost / T["margin"] if T["margin"] else None
    T["result"] = T["margin"] - total_cost
    T["cost_unit"] = allocated / T["units"] if T["units"] else None
    T["avg_margin_pct"] = T["margin"] / T["revenue"] if T["revenue"] else None
    T["direct_share"] = float(real.cost_direct.sum()) / total_cost if total_cost else None
    T["alloc_share"] = float(real.cost_alloc.sum()) / total_cost if total_cost else None
    loss = it[it.status.isin(["Strata", "Brak sprzedaży"])]
    T["loss_cost"], T["loss_count"] = float(loss.cost.sum()), int(len(loss))

    # --- jakość danych -----------------------------------------------------
    dq = []
    if len(unalloc):
        dq.append({"level": "warn", "text": f"{_pln(unalloc.resid.sum())} kosztu bez wyświetleń kart produktu w GA4 (Nieprzypisane): " + ", ".join(unalloc.campaign_name.astype(str).head(8))})
    unmatched = ga[ga.ads_cid.isna() & ~ga.campaign_name.astype(str).str.match(NON_PAID) & (ga.campaign_id != "")]
    if len(unmatched):
        names = unmatched.groupby("campaign_name").views.sum().sort_values(ascending=False)
        dq.append({"level": "warn", "text": f"{len(names)} kampanii Google Ads z GA4 bez kosztu w Google Ads w tym okresie (inne konto? brak w GOOGLE_ADS_CUSTOMER_IDS?): " + ", ".join(names.index[:5].astype(str))})
    direct_total = float(prod_f.cost.sum())
    if direct_total > 0:
        orphan = prod_f[~prod_f.item.isin(cat_ga.index)].cost.sum()
        if orphan / direct_total > 0.02:
            dq.append({"level": "warn", "text": f"{_pct(orphan / direct_total)} kosztu produktowego Shopping/PMax dotyczy ID nieobecnych w GA4 — sprawdź zgodność ID (ITEM_ID_LOWERCASE / ITEM_ID_STRIP_REGEX)."})
    no_margin = real[(~real.has_margin) & (real.cost > 0)].index.tolist()
    if no_margin:
        dq.append({"level": "info", "text": f"Kategorie bez marży (użyto domyślnej {cfg.default_margin_pct:g}%): " + ", ".join(map(str, no_margin[:10])) + ("…" if len(no_margin) > 10 else "")})
    if NO_CATEGORY in real.index and real.loc[NO_CATEGORY, "cost"] > 0:
        dq.append({"level": "info", "text": f"Produkty bez kategorii z kosztem {_pln(real.loc[NO_CATEGORY, 'cost'])}."})
    if total_cost:
        dq.append({"level": "info", "text": f"Koszt przypisany wprost (Shopping/PMax): {_pct(T['direct_share'])}, alokowany wg wyświetleń kart: {_pct(T['alloc_share'])}."})

    used_types = [t for t in TYPE_ORDER if cats["t_" + t].sum() > 0]
    camp_out = camp.assign(unallocated=camp.campaign_id.isin(unalloc.campaign_id))[
        ["campaign_id", "campaign_name", "type", "cost", "direct", "resid", "views", "unallocated"]
    ].sort_values("cost", ascending=False)

    return {
        "params": {"date_from": str(p.date_from), "date_to": str(p.date_to), "basis": p.basis, "revenue_gross": gross, "channel": p.channel},
        "totals": T,
        "types": used_types,
        "all_types": all_types,
        "categories": _records(cats.reset_index().rename(columns={"index": "category"})),
        "products": it.reset_index(drop=True),
        "campaigns": _records(camp_out),
        "data_quality": dq,
    }


def _clean(v):
    if isinstance(v, (np.floating, float)):
        return None if (math.isnan(v) or math.isinf(v)) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in r.items()} for r in df.to_dict(orient="records")]


PRODUCT_COLUMNS = ["id", "name", "category", "cost", "cost_direct", "cost_alloc", "views_paid", "clicks", "units", "units_attr", "units_all",
                   "revenue", "avg_price", "margin_pct", "margin_unit", "cost_unit", "result_unit", "eaten", "margin", "result", "status", "has_ga4"]


def products_records(products: pd.DataFrame, q: str = "", category: str = "", status: str = "", sort: str = "result", desc: bool = False, limit: int = 2000) -> tuple[list[dict], int]:
    df = products[(products.cost > 0) | (products.units > 0)]
    if q:
        ql = q.lower()
        df = df[df.name.fillna("").str.lower().str.contains(ql, regex=False) | df.id.astype(str).str.lower().str.contains(ql, regex=False)]
    if category:
        df = df[df.category == category]
    if status:
        df = df[df.status == status]
    n = len(df)
    if sort in df.columns:
        df = df.sort_values(sort, ascending=not desc, na_position="last")
    return _records(df[PRODUCT_COLUMNS].head(limit)), n
