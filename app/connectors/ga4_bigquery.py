"""GA4 → BigQuery export — dane zdarzeń bez próbkowania i limitów Data API.

Kampania sesji pochodzi z rekordu session_traffic_source_last_click (dostępny w eksporcie
od połowy 2024). Dla ruchu z Google Ads (auto-tagging) mamy ID i nazwę kampanii,
dla pozostałego — nazwę z utm_campaign albo '(not set)'.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from ..config import Settings

SQL = """
SELECT
  PARSE_DATE('%Y%m%d', event_date) AS date,
  COALESCE(CAST(session_traffic_source_last_click.google_ads_campaign.campaign_id AS STRING), '') AS campaign_id,
  COALESCE(
    session_traffic_source_last_click.google_ads_campaign.campaign_name,
    session_traffic_source_last_click.manual_campaign.campaign_name,
    '(not set)'
  ) AS campaign_name,
  i.item_id,
  ANY_VALUE(i.item_name) AS item_name,
  ANY_VALUE(i.{category_field}) AS item_category,
  COUNTIF(event_name = 'view_item') AS items_viewed,
  SUM(IF(event_name = 'purchase', IFNULL(i.quantity, 0), 0)) AS items_purchased,
  SUM(IF(event_name = 'purchase', IFNULL(i.item_revenue, 0), 0)) AS item_revenue
FROM `{project}.{dataset}.events_*`, UNNEST(items) AS i
WHERE _TABLE_SUFFIX BETWEEN @d_from AND @d_to
  AND event_name IN ('view_item', 'purchase')
  AND i.item_id IS NOT NULL
GROUP BY date, campaign_id, campaign_name, i.item_id
"""

ALLOWED_CATEGORY_FIELDS = {"item_category", "item_category2", "item_category3", "item_category4", "item_category5"}


def _client(s: Settings):
    from google.cloud import bigquery

    project = s.ga4_bq_billing_project or s.ga4_bq_project
    if s.google_application_credentials:
        return bigquery.Client.from_service_account_json(s.google_application_credentials, project=project)
    return bigquery.Client(project=project)


def fetch(s: Settings, d_from: date, d_to: date) -> pd.DataFrame:
    from google.cloud import bigquery

    if s.category_field not in ALLOWED_CATEGORY_FIELDS:
        raise ValueError(f"CATEGORY_FIELD musi być jednym z {sorted(ALLOWED_CATEGORY_FIELDS)}")
    sql = SQL.format(project=s.ga4_bq_project, dataset=s.ga4_bq_dataset, category_field=s.category_field)
    job = _client(s).query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("d_from", "STRING", d_from.strftime("%Y%m%d")),
                bigquery.ScalarQueryParameter("d_to", "STRING", d_to.strftime("%Y%m%d")),
            ]
        ),
    )
    df = job.result().to_dataframe(create_bqstorage_client=False)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for c in ("items_viewed", "items_purchased", "item_revenue"):
        df[c] = df[c].astype(float)
    return df[["date", "campaign_id", "campaign_name", "item_id", "item_name", "item_category", "items_viewed", "items_purchased", "item_revenue"]]


def check(s: Settings) -> str:
    client = _client(s)
    tables = list(client.list_tables(f"{s.ga4_bq_project}.{s.ga4_bq_dataset}", max_results=5))
    return f"BigQuery {s.ga4_bq_project}.{s.ga4_bq_dataset}: dostęp OK ({len(tables)}+ tabel)"
