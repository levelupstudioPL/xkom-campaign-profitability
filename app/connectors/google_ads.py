"""Google Ads API — koszt kampanii i koszt per produkt (Shopping + Performance Max).

Wymaga:
  * developer token (poziom dostępu min. Basic) z konta MCC,
  * OAuth (client id/secret + refresh token) LUB konto serwisowe z impersonacją,
  * login_customer_id = MCC, customer_ids = konto(a) x-kom.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import Settings

CAMPAIGN_QUERY = """
SELECT
  segments.date,
  campaign.id,
  campaign.name,
  campaign.advertising_channel_type,
  metrics.cost_micros,
  metrics.clicks,
  metrics.impressions
FROM campaign
WHERE segments.date BETWEEN '{d_from}' AND '{d_to}'
  AND metrics.cost_micros > 0
"""

# shopping_performance_view obejmuje kampanie Shopping oraz część produktową Performance Max
PRODUCT_QUERY = """
SELECT
  segments.date,
  campaign.id,
  campaign.name,
  segments.product_item_id,
  segments.product_title,
  segments.product_type_l1,
  metrics.cost_micros,
  metrics.clicks,
  metrics.impressions
FROM shopping_performance_view
WHERE segments.date BETWEEN '{d_from}' AND '{d_to}'
  AND metrics.cost_micros > 0
"""


def _client(s: Settings):
    from google.ads.googleads.client import GoogleAdsClient

    cfg: dict = {
        "developer_token": s.google_ads_developer_token,
        "use_proto_plus": True,
    }
    if s.google_ads_login_customer_id:
        cfg["login_customer_id"] = s.google_ads_login_customer_id.replace("-", "")
    if s.google_ads_json_key_file:
        cfg["json_key_file_path"] = s.google_ads_json_key_file
        if s.google_ads_impersonated_email:
            cfg["impersonated_email"] = s.google_ads_impersonated_email
    else:
        cfg.update(
            client_id=s.google_ads_client_id,
            client_secret=s.google_ads_client_secret,
            refresh_token=s.google_ads_refresh_token,
        )
    return GoogleAdsClient.load_from_dict(cfg)


def _stream(client, customer_id: str, query: str):
    svc = client.get_service("GoogleAdsService")
    for batch in svc.search_stream(customer_id=customer_id, query=query):
        yield from batch.results


def fetch_campaigns(s: Settings, d_from: date, d_to: date) -> pd.DataFrame:
    client = _client(s)
    rows = []
    for cid in s.customer_id_list:
        q = CAMPAIGN_QUERY.format(d_from=d_from.isoformat(), d_to=d_to.isoformat())
        for r in _stream(client, cid, q):
            rows.append(
                {
                    "date": r.segments.date,
                    "customer_id": cid,
                    "campaign_id": str(r.campaign.id),
                    "campaign_name": r.campaign.name,
                    "channel_type": r.campaign.advertising_channel_type.name,
                    "cost": r.metrics.cost_micros / 1_000_000,
                    "clicks": int(r.metrics.clicks),
                    "impressions": int(r.metrics.impressions),
                }
            )
    df = pd.DataFrame(rows, columns=["date", "customer_id", "campaign_id", "campaign_name", "channel_type", "cost", "clicks", "impressions"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def fetch_products(s: Settings, d_from: date, d_to: date) -> pd.DataFrame:
    client = _client(s)
    rows = []
    for cid in s.customer_id_list:
        q = PRODUCT_QUERY.format(d_from=d_from.isoformat(), d_to=d_to.isoformat())
        for r in _stream(client, cid, q):
            rows.append(
                {
                    "date": r.segments.date,
                    "customer_id": cid,
                    "campaign_id": str(r.campaign.id),
                    "campaign_name": r.campaign.name,
                    "item_id": r.segments.product_item_id,
                    "product_title": r.segments.product_title,
                    "product_type_l1": r.segments.product_type_l1,
                    "cost": r.metrics.cost_micros / 1_000_000,
                    "clicks": int(r.metrics.clicks),
                    "impressions": int(r.metrics.impressions),
                }
            )
    cols = ["date", "customer_id", "campaign_id", "campaign_name", "item_id", "product_title", "product_type_l1", "cost", "clicks", "impressions"]
    df = pd.DataFrame(rows, columns=cols)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def check(s: Settings) -> list[str]:
    """Sprawdza dostęp: zwraca listę nazw kont, do których mamy dostęp."""
    client = _client(s)
    out = []
    for cid in s.customer_id_list:
        q = "SELECT customer.id, customer.descriptive_name, customer.currency_code FROM customer LIMIT 1"
        for r in _stream(client, cid, q):
            out.append(f"{r.customer.id} — {r.customer.descriptive_name} ({r.customer.currency_code})")
    return out
