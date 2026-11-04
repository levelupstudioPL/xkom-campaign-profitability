# Rentowność kampanii per produkt (x-kom)

Aplikacja łączy **Google Ads API** i **GA4** (Data API albo eksport BigQuery) z **marżą % per kategoria** i odpowiada na trzy pytania:

| | Pytanie | Metryka |
|---|---|---|
| a) | Ile kosztują kampanie w każdej kategorii? | koszt bezpośredni (Shopping/PMax) + koszt alokowany (pozostałe kampanie) |
| b) | Ile marży kategorii zjada reklama? | koszt ÷ marża brutto, ROAS vs break-even ROAS, wynik po reklamie |
| c) | Ile wydajemy na ruch, żeby sprzedać jedną sztukę produktu? | koszt ruchu / szt. vs marża / szt., status produktu |

Dane są pobierane automatycznie raz dziennie (bez ręcznych eksportów), zapisywane w lokalnej bazie DuckDB i pokazywane w dashboardzie web.

> **Status: MVP.** Moduły konektorów i model są oddzielone od magazynu danych, żeby w kolejnym kroku przenieść tabele do hurtowni ClickHouse (PBI 716688) zamiast utrzymywać osobną bazę.

---

## Szybki start (tryb demo, bez dostępów)

```bash
cp .env.example .env            # APP_MODE=demo
cp config/margins.example.csv config/margins.csv
docker compose up --build
# http://localhost:8000
```

Przy pierwszym starcie aplikacja generuje 90 dni danych syntetycznych.

Bez Dockera:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # lokalnie zmień ścieżki: DB_PATH=data/profitability.duckdb,
                       # MARGINS_SEED_FILE=config/margins.csv, CATEGORY_MAP_FILE=config/category_map.csv
uvicorn app.main:app --reload
pytest
```

---

## Uruchomienie na danych x-kom (APP_MODE=live)

### 1. Dostępy, które trzeba załatwić (najdłuższy element wdrożenia)

| Co | Gdzie | Kto |
|---|---|---|
| **Developer token** Google Ads, poziom min. *Basic access* | Google Ads (konto MCC) → Narzędzia → Centrum API | admin MCC; wniosek o Basic access rozpatrywany przez Google zwykle kilka dni roboczych |
| **Projekt Google Cloud** z włączonymi API: *Google Ads API*, *Google Analytics Data API* (lub *BigQuery API*) | console.cloud.google.com | ktokolwiek z dostępem do GCP |
| **Klient OAuth** typu *Desktop app* (client id + secret) | GCP → APIs & Services → Credentials | j.w. |
| **Użytkownik Google Ads** z dostępem (min. odczyt) do konta x-kom przez MCC | Google Ads → Dostęp i zabezpieczenia | admin konta |
| **Konto serwisowe GCP** (plik JSON) | GCP → IAM → Service accounts | j.w. |
| GA4 Data API: konto serwisowe dodane do property GA4 jako **Viewer** | GA4 → Administracja → Zarządzanie dostępem do usługi | admin GA4 x-kom |
| *lub* BigQuery: role **BigQuery Data Viewer** (dataset `analytics_XXXX`) + **BigQuery Job User** (projekt rozliczeniowy) | GCP IAM | admin GCP x-kom |

### 2. Refresh token Google Ads

```bash
pip install google-auth-oauthlib
python scripts/google_ads_auth.py --client-id XXX --client-secret YYY
# zaloguj się kontem z dostępem do MCC → skopiuj GOOGLE_ADS_REFRESH_TOKEN do .env
```

### 3. Konfiguracja `.env`

```ini
APP_MODE=live
GOOGLE_ADS_DEVELOPER_TOKEN=...
GOOGLE_ADS_CLIENT_ID=...
GOOGLE_ADS_CLIENT_SECRET=...
GOOGLE_ADS_REFRESH_TOKEN=...
GOOGLE_ADS_LOGIN_CUSTOMER_ID=1234567890     # MCC
GOOGLE_ADS_CUSTOMER_IDS=1112223333          # konto(a) x-kom, po przecinku

GA4_SOURCE=data_api                         # albo bigquery
GA4_PROPERTY_ID=123456789
GOOGLE_APPLICATION_CREDENTIALS=/secrets/ga4-service-account.json
# dla BigQuery:
# GA4_BQ_PROJECT=xkom-analytics  GA4_BQ_DATASET=analytics_123456789

DASHBOARD_USER=xkom
DASHBOARD_PASSWORD=...                      # dashboard pokazuje przychody i marże — ustaw hasło
```

Plik konta serwisowego wrzuć do `./secrets/` (montowany read-only, ignorowany przez git).

### 4. Sprawdzenie połączeń i zasilenie historii

```bash
docker compose run --rm app python -m app.cli check          # Google Ads + GA4
docker compose run --rm app python -m app.cli backfill --days 90
docker compose up -d
```

Od tej chwili harmonogram (`SYNC_CRON`, domyślnie 6:15 Europe/Warsaw) codziennie nadpisuje ostatnie `SYNC_LOOKBACK_DAYS` dni. Dane GA4 dojrzewają do 48 h, a Google Ads koryguje koszty wstecz.

### 5. Marże

- Startowe wartości: `config/margins.csv` (`kategoria;marza_proc`), wczytywane przy pierwszym starcie lub `python -m app.cli seed-margins`.
- Edycja na bieżąco: zakładka **Synchronizacja i marże** w dashboardzie.
- Kategorie pochodzą z GA4 (`CATEGORY_FIELD`, domyślnie `item_category`). Jeśli GA4 ma zbyt szczegółowe kategorie, zmapuj je w `config/category_map.csv` (`kategoria_zrodlowa;kategoria_raportowa`).

---

## Jak to liczy

```
Google Ads API ──► ads_campaign_daily   (koszt kampanii / dzień)
               └─► ads_product_daily    (shopping_performance_view: koszt per item_id — Shopping + PMax)
GA4 ───────────► ga4_item_daily       (kampania sesji × item: wyświetlenia kart, sztuki, przychód)
marże ─────────► margins
                       │
                       ▼
                 app/model.py  →  /api/report  →  dashboard
```

1. **Koszt bezpośredni:** Shopping i Performance Max mają koszt per produkt w `shopping_performance_view`.
2. **Koszt alokowany:** reszta kosztu kampanii (Search, Display, Video, Demand Gen oraz nieproduktowa część PMax) jest dzielona proporcjonalnie do **wyświetleń kart produktu** (`view_item`) z sesji tej kampanii w GA4:
   `koszt_alok(produkt, kampania) = koszt_resztowy × wyświetlenia(produkt, kampania) ÷ Σ wyświetlenia(kampania)`
3. **Łączenie GA4 ↔ Google Ads:** po ID kampanii (auto-tagging), a jeśli brak — po nazwie kampanii.
4. **Nieprzypisane:** kampanie bez żadnego wyświetlenia karty w GA4 — widoczne w zakładce *Kampanie* i w *Jakości danych*.
5. **Marża:** `przychód_netto × marża%(kategoria)`; przychód GA4 jest domyślnie traktowany jako brutto (`GA4_REVENUE_INCLUDES_VAT`).
6. **Baza sprzedaży:** *Atrybuowana* (sprzedaż w sesjach z kampanii Google Ads) albo *Blended* (cała sprzedaż produktu).
7. **Statusy produktu:** Strata (koszt/szt. > marża/szt.), Na granicy (70–100%), Rentowny (< 70%), Brak sprzedaży.

### Znane ograniczenia

- **Marża % per kategoria** uśrednia SKU — produkty niskomarżowe wyglądają lepiej niż w rzeczywistości. Docelowo: marża per SKU z D365.
- **Alokacja wg wyświetleń kart** to model — wiarygodna dla kategorii, orientacyjna dla pojedynczego SKU.
- **GA4 Data API** nie zawsze pozwala łączyć metryki produktów (`itemsViewed`, `itemsPurchased`, `itemRevenue`) z wymiarami kampanii sesji. Przy `GA4_CAMPAIGN_DIMENSION=auto` aplikacja sprawdza kompatybilność (`checkCompatibility`) i wybiera pierwszy działający wymiar (`sessionGoogleAdsCampaignName` → `sessionCampaignName` → `campaignName`). Jeśli żaden nie działa albo raporty mają dużo `(other)`, przełącz `GA4_SOURCE=bigquery`.
- **ID produktu** w Merchant Center i w GA4 musi być to samo. Rozjazdy (wielkość liter, prefiksy) koryguje `ITEM_ID_LOWERCASE` / `ITEM_ID_STRIP_REGEX`; jeśli są, *Jakość danych* pokaże % kosztu z nieznanymi ID.
- Sprzedaż liczona jest z GA4, nie z konwersji Google Ads.

---

## API

| Endpoint | Opis |
|---|---|
| `GET /api/report?date_from&date_to&basis=attr\|all&vat=gross\|net&channel=Search&q&category&status&sort&desc&limit` | KPI, kategorie, produkty, kampanie, jakość danych |
| `GET /api/products.csv`, `GET /api/categories.csv` | pełne eksporty (separator `;`, przecinek dziesiętny — do Excela) |
| `GET/PUT /api/margins` | marże per kategoria |
| `POST /api/sync` (`{date_from, date_to}` opcjonalnie) | ręczna synchronizacja w tle |
| `GET /api/sync/log` | historia synchronizacji |
| `GET /api/health` | stan, zakres danych (bez hasła — do monitoringu) |

CLI: `python -m app.cli check | sync [--from --to] | backfill --days N | seed-margins | report --from --to`

---

## Struktura

```
app/
  config.py              konfiguracja (env)
  db.py                  DuckDB: schemat, zapis idempotentny per zakres dat
  connectors/
    google_ads.py        GAQL: campaign + shopping_performance_view
    ga4_data_api.py      runReport + checkCompatibility
    ga4_bigquery.py      SQL na eksporcie GA4 (session_traffic_source_last_click)
    demo.py              dane syntetyczne
  sync.py                orkiestracja pobierania, backfill w paczkach
  model.py               alokacja kosztu i metryki rentowności
  main.py                FastAPI + harmonogram + basic auth
  cli.py                 polecenia administracyjne
  static/index.html      dashboard
tests/                   testy modelu (przykład liczony ręcznie) i API end-to-end
```

## Roadmapa

1. Uruchomienie na danych x-kom za 1 miesiąc i weryfikacja % kosztu przypisanego do produktów.
2. Marża per SKU (eksport z D365) zamiast marży per kategoria.
3. Przeniesienie tabel `ads_*` / `ga4_*` do ClickHouse (warstwa silver) — `db.py` jako jedyny punkt zmiany.
4. Kolejne źródła kosztu: Meta Ads, Criteo (ten sam model alokacji wg wyświetleń kart).
