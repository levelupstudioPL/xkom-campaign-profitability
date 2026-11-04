"""Konfiguracja aplikacji — wszystko z env / pliku .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Tryb pracy ---------------------------------------------------------
    # demo   = dane syntetyczne, bez połączeń z Google (do testów i prezentacji)
    # live   = Google Ads API + GA4 (Data API lub BigQuery)
    mode: Literal["demo", "live"] = Field("demo", alias="APP_MODE")

    db_path: Path = Field(Path("data/profitability.duckdb"), alias="DB_PATH")
    margins_seed_file: Path = Field(Path("config/margins.csv"), alias="MARGINS_SEED_FILE")
    category_map_file: Path = Field(Path("config/category_map.csv"), alias="CATEGORY_MAP_FILE")

    # --- Google Ads API -----------------------------------------------------
    google_ads_developer_token: str = Field("", alias="GOOGLE_ADS_DEVELOPER_TOKEN")
    google_ads_client_id: str = Field("", alias="GOOGLE_ADS_CLIENT_ID")
    google_ads_client_secret: str = Field("", alias="GOOGLE_ADS_CLIENT_SECRET")
    google_ads_refresh_token: str = Field("", alias="GOOGLE_ADS_REFRESH_TOKEN")
    # Konto menedżera (MCC), przez które jest dostęp — bez myślników
    google_ads_login_customer_id: str = Field("", alias="GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    # Konto(a) reklamowe x-kom, rozdzielone przecinkiem — bez myślników
    google_ads_customer_ids: str = Field("", alias="GOOGLE_ADS_CUSTOMER_IDS")
    # Alternatywa dla OAuth: konto serwisowe (plik JSON) + adres użytkownika do impersonacji
    google_ads_json_key_file: str = Field("", alias="GOOGLE_ADS_JSON_KEY_FILE")
    google_ads_impersonated_email: str = Field("", alias="GOOGLE_ADS_IMPERSONATED_EMAIL")

    # --- GA4 ----------------------------------------------------------------
    ga4_source: Literal["data_api", "bigquery"] = Field("data_api", alias="GA4_SOURCE")
    ga4_property_id: str = Field("", alias="GA4_PROPERTY_ID")
    # Plik konta serwisowego (Viewer w GA4 / BigQuery Data Viewer + Job User)
    google_application_credentials: str = Field("", alias="GOOGLE_APPLICATION_CREDENTIALS")
    # Wymiar kampanii w Data API. "auto" = sprawdź kompatybilność i wybierz pierwszy działający
    ga4_campaign_dimension: str = Field("auto", alias="GA4_CAMPAIGN_DIMENSION")
    ga4_campaign_id_dimension: str = Field("auto", alias="GA4_CAMPAIGN_ID_DIMENSION")
    ga4_bq_project: str = Field("", alias="GA4_BQ_PROJECT")
    ga4_bq_dataset: str = Field("", alias="GA4_BQ_DATASET")  # np. analytics_123456789
    ga4_bq_billing_project: str = Field("", alias="GA4_BQ_BILLING_PROJECT")

    # --- Model --------------------------------------------------------------
    ga4_revenue_includes_vat: bool = Field(True, alias="GA4_REVENUE_INCLUDES_VAT")
    vat_rate: float = Field(0.23, alias="VAT_RATE")
    default_margin_pct: float = Field(10.0, alias="DEFAULT_MARGIN_PCT")
    # Normalizacja ID produktu (Merchant Center vs GA4): lower / strip prefix
    item_id_lowercase: bool = Field(True, alias="ITEM_ID_LOWERCASE")
    item_id_strip_regex: str = Field("", alias="ITEM_ID_STRIP_REGEX")  # np. ^pl_|_pl$
    # Którego poziomu kategorii GA4 użyć: item_category .. item_category5
    category_field: str = Field("item_category", alias="CATEGORY_FIELD")
    loss_threshold: float = Field(1.0, alias="STATUS_LOSS_THRESHOLD")
    edge_threshold: float = Field(0.7, alias="STATUS_EDGE_THRESHOLD")

    # --- Synchronizacja ----------------------------------------------------
    sync_enabled: bool = Field(True, alias="SYNC_ENABLED")
    sync_cron: str = Field("15 6 * * *", alias="SYNC_CRON")
    sync_timezone: str = Field("Europe/Warsaw", alias="SYNC_TIMEZONE")
    # Ile ostatnich dni nadpisywać przy każdej synchronizacji (opóźnienia GA4 / korekty Ads)
    sync_lookback_days: int = Field(3, alias="SYNC_LOOKBACK_DAYS")
    initial_backfill_days: int = Field(90, alias="INITIAL_BACKFILL_DAYS")

    # --- Dostęp do dashboardu ----------------------------------------------
    dashboard_user: str = Field("", alias="DASHBOARD_USER")
    dashboard_password: str = Field("", alias="DASHBOARD_PASSWORD")

    @property
    def customer_id_list(self) -> list[str]:
        return [c.strip().replace("-", "") for c in self.google_ads_customer_ids.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
