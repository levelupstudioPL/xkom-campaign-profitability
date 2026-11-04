"""GA4 Data API — wyświetlenia, sprzedaż i przychód produktów per kampania sesji.

Uwaga: metryki z zakresu elementu (itemsViewed, itemsPurchased, itemRevenue) nie są
kompatybilne ze wszystkimi wymiarami kampanii. Przy GA4_CAMPAIGN_DIMENSION=auto
aplikacja pyta API (checkCompatibility) i wybiera pierwszy kompatybilny wymiar.
Gdy żaden nie działa — przełącz GA4_SOURCE=bigquery.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import Settings

METRICS = ["itemsViewed", "itemsPurchased", "itemRevenue"]
CAMPAIGN_NAME_CANDIDATES = ["sessionGoogleAdsCampaignName", "sessionCampaignName", "campaignName"]
CAMPAIGN_ID_CANDIDATES = ["sessionGoogleAdsCampaignId", "sessionCampaignId", "campaignId"]
CATEGORY_DIMS = {
    "item_category": "itemCategory",
    "item_category2": "itemCategory2",
    "item_category3": "itemCategory3",
    "item_category4": "itemCategory4",
    "item_category5": "itemCategory5",
}
PAGE = 100_000


def _client(s: Settings):
    from google.analytics.data_v1beta import BetaAnalyticsDataClient

    if s.google_application_credentials:
        return BetaAnalyticsDataClient.from_service_account_file(s.google_application_credentials)
    return BetaAnalyticsDataClient()


def _compatible(client, prop: str, dims: list[str]) -> bool:
    from google.analytics.data_v1beta.types import CheckCompatibilityRequest, Dimension, Metric

    resp = client.check_compatibility(
        CheckCompatibilityRequest(
            property=prop,
            dimensions=[Dimension(name=d) for d in dims],
            metrics=[Metric(name=m) for m in METRICS],
        )
    )
    bad = [d.dimension_metadata.api_name for d in resp.dimension_compatibilities if d.compatibility.name == "INCOMPATIBLE"]
    bad += [m.metric_metadata.api_name for m in resp.metric_compatibilities if m.compatibility.name == "INCOMPATIBLE"]
    return not bad


def resolve_dimensions(s: Settings, client=None) -> tuple[str, str | None]:
    """Zwraca (wymiar_nazwy_kampanii, wymiar_id_kampanii lub None)."""
    client = client or _client(s)
    prop = f"properties/{s.ga4_property_id}"
    base = ["date", "itemId", "itemName", CATEGORY_DIMS.get(s.category_field, "itemCategory")]

    name_cands = CAMPAIGN_NAME_CANDIDATES if s.ga4_campaign_dimension == "auto" else [s.ga4_campaign_dimension]
    name_dim = next((d for d in name_cands if _compatible(client, prop, base + [d])), None)
    if not name_dim:
        raise RuntimeError(
            "Żaden wymiar kampanii nie jest kompatybilny z metrykami produktów w GA4 Data API "
            f"(sprawdzono: {', '.join(name_cands)}). Ustaw GA4_SOURCE=bigquery."
        )
    id_dim = None
    if s.ga4_campaign_id_dimension:
        id_cands = CAMPAIGN_ID_CANDIDATES if s.ga4_campaign_id_dimension == "auto" else [s.ga4_campaign_id_dimension]
        # ID tylko w parze z odpowiadającym wymiarem nazwy (ten sam zakres)
        if s.ga4_campaign_id_dimension == "auto":
            idx = CAMPAIGN_NAME_CANDIDATES.index(name_dim) if name_dim in CAMPAIGN_NAME_CANDIDATES else -1
            id_cands = [CAMPAIGN_ID_CANDIDATES[idx]] if idx >= 0 else []
        id_dim = next((d for d in id_cands if _compatible(client, prop, base + [name_dim, d])), None)
    return name_dim, id_dim


def fetch(s: Settings, d_from: date, d_to: date) -> pd.DataFrame:
    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    client = _client(s)
    name_dim, id_dim = resolve_dimensions(s, client)
    cat_dim = CATEGORY_DIMS.get(s.category_field, "itemCategory")
    dims = ["date", name_dim] + ([id_dim] if id_dim else []) + ["itemId", "itemName", cat_dim]

    rows, offset = [], 0
    while True:
        resp = client.run_report(
            RunReportRequest(
                property=f"properties/{s.ga4_property_id}",
                date_ranges=[DateRange(start_date=d_from.isoformat(), end_date=d_to.isoformat())],
                dimensions=[Dimension(name=d) for d in dims],
                metrics=[Metric(name=m) for m in METRICS],
                limit=PAGE,
                offset=offset,
                keep_empty_rows=False,
            )
        )
        for r in resp.rows:
            dv = {d: v.value for d, v in zip(dims, r.dimension_values, strict=False)}
            mv = [float(v.value or 0) for v in r.metric_values]
            rows.append(
                {
                    "date": dv["date"],
                    "campaign_id": dv.get(id_dim, "") if id_dim else "",
                    "campaign_name": dv[name_dim],
                    "item_id": dv["itemId"],
                    "item_name": dv["itemName"],
                    "item_category": dv[cat_dim],
                    "items_viewed": mv[0],
                    "items_purchased": mv[1],
                    "item_revenue": mv[2],
                }
            )
        offset += PAGE
        if offset >= resp.row_count:
            break

    cols = ["date", "campaign_id", "campaign_name", "item_id", "item_name", "item_category", "items_viewed", "items_purchased", "item_revenue"]
    df = pd.DataFrame(rows, columns=cols)
    df = df[df.item_id.notna() & (df.item_id != "(not set)")]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.date
    df["campaign_id"] = df["campaign_id"].replace({"(not set)": ""})
    return df


def check(s: Settings) -> str:
    name_dim, id_dim = resolve_dimensions(s)
    return f"property {s.ga4_property_id}: wymiar kampanii = {name_dim}, ID kampanii = {id_dim or 'brak (łączenie po nazwie)'}"
