# Wdrożenie: backend na Cloud Run, dashboard na Netlify

```
Netlify (statyczny dashboard)  ──HTTPS + token──►  Cloud Run (API + synchronizacja)
                                                     │  ▲
                              Cloud Scheduler ───────┘  │ snapshot bazy DuckDB
                              (codziennie POST /api/sync)│
                                                     Cloud Storage (gs://…/profitability.duckdb)
                                                     Secret Manager (tokeny, klucze)
```

Cloud Run nie ma trwałego dysku i wyłącza instancję, gdy nie ma ruchu. Dlatego:

- baza DuckDB jest pobierana z Cloud Storage przy starcie i zapisywana po każdej synchronizacji i zmianie marż (`GCS_DB_URI`);
- `--max-instances=1` gwarantuje jednego „pisarza” bazy;
- harmonogram w aplikacji jest wyłączony (`SYNC_ENABLED=false`), synchronizację wywołuje **Cloud Scheduler** żądaniem `POST /api/sync?wait=true`, w trakcie którego instancja ma pełne CPU.

Wszystko poniżej działa w projekcie GCP x-kom (tym samym, w którym są włączone Google Ads API i GA4).

## 1. Zmienne

```bash
PROJECT=xkom-projekt-gcp
REGION=europe-central2            # Warszawa
SERVICE=rentownosc-api
BUCKET=${PROJECT}-rentownosc
SA=rentownosc-app@${PROJECT}.iam.gserviceaccount.com   # konto serwisowe aplikacji (to samo, które ma Viewer w GA4)
NETLIFY_URL=https://xkom-rentownosc.netlify.app        # adres dashboardu
gcloud config set project $PROJECT
```

## 2. Jednorazowe przygotowanie

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com

# bucket na snapshot bazy (prywatny, w UE)
gcloud storage buckets create gs://$BUCKET --location=$REGION --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding gs://$BUCKET --member=serviceAccount:$SA --role=roles/storage.objectAdmin

# repozytorium obrazów
gcloud artifacts repositories create rentownosc --repository-format=docker --location=$REGION

# sekrety (wartości wpisujesz interaktywnie, nie trafiają do historii shella)
for S in google-ads-developer-token google-ads-client-secret google-ads-refresh-token api-token; do
  gcloud secrets create $S --replication-policy=automatic
  read -rsp "$S: " V && printf "%s" "$V" | gcloud secrets versions add $S --data-file=- && echo
done
for S in google-ads-developer-token google-ads-client-secret google-ads-refresh-token api-token; do
  gcloud secrets add-iam-policy-binding $S --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
done
```

`api-token` wygeneruj np. `openssl rand -hex 32` — to hasło do dashboardu i dla Cloud Scheduler.

Na Cloud Run konto serwisowe jest przypięte do usługi, więc **plik klucza JSON nie jest potrzebny** — GA4 i BigQuery uwierzytelniają się automatycznie (`GOOGLE_APPLICATION_CREDENTIALS` zostaw puste).

## 3. Budowa i wdrożenie

```bash
IMAGE=$REGION-docker.pkg.dev/$PROJECT/rentownosc/api:$(git rev-parse --short HEAD)
gcloud builds submit --tag $IMAGE

gcloud run deploy $SERVICE --image $IMAGE --region $REGION \
  --service-account $SA \
  --max-instances 1 --min-instances 0 --memory 2Gi --cpu 1 --timeout 3600 \
  --allow-unauthenticated \
  --set-env-vars "APP_MODE=live,DB_PATH=/tmp/profitability.duckdb,GCS_DB_URI=gs://$BUCKET/profitability.duckdb,SYNC_ENABLED=false,SYNC_ON_EMPTY_START=false,CORS_ORIGINS=$NETLIFY_URL,GOOGLE_ADS_CLIENT_ID=XXX.apps.googleusercontent.com,GOOGLE_ADS_LOGIN_CUSTOMER_ID=1234567890,GOOGLE_ADS_CUSTOMER_IDS=1112223333,GA4_SOURCE=data_api,GA4_PROPERTY_ID=123456789,DEFAULT_MARGIN_PCT=10" \
  --set-secrets "GOOGLE_ADS_DEVELOPER_TOKEN=google-ads-developer-token:latest,GOOGLE_ADS_CLIENT_SECRET=google-ads-client-secret:latest,GOOGLE_ADS_REFRESH_TOKEN=google-ads-refresh-token:latest,API_TOKEN=api-token:latest"

API_URL=$(gcloud run services describe $SERVICE --region $REGION --format 'value(status.url)')
curl -s $API_URL/api/health
```

`--allow-unauthenticated` otwiera usługę na poziomie sieci, ale każde wywołanie `/api/*` (poza `/api/health`) wymaga tokenu `API_TOKEN`. Jeśli x-kom wymaga dostępu tylko z VPN/IAP — do ustalenia z IT.

## 4. Pierwsze zasilenie i harmonogram

```bash
TOKEN=$(gcloud secrets versions access latest --secret=api-token)

# historia: paczkami po miesiącu (każde wywołanie czeka na wynik, max 60 min)
curl -s -X POST "$API_URL/api/sync?wait=true" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"date_from":"2026-08-01","date_to":"2026-08-31"}'
# ...powtórz dla kolejnych miesięcy

# codziennie o 6:15 czasu warszawskiego: ostatnie SYNC_LOOKBACK_DAYS dni
gcloud scheduler jobs create http rentownosc-sync --location $REGION \
  --schedule "15 6 * * *" --time-zone "Europe/Warsaw" \
  --uri "$API_URL/api/sync?wait=true" --http-method POST \
  --headers "Authorization=Bearer $TOKEN,Content-Type=application/json" --message-body '{}' \
  --attempt-deadline 30m
```

## 5. Netlify (dashboard)

1. Netlify → **Add new site → Import an existing project → GitHub** → `levelupstudioPL/xkom-campaign-profitability`.
2. Ustawienia builda są w `netlify.toml` (publish: `app/static`), nic nie trzeba wpisywać.
3. **Site configuration → Environment variables:** `API_BASE_URL` = adres z `$API_URL`.
4. Deploy. Przy pierwszym wejściu dashboard poprosi o token (`api-token`).
5. Jeśli adres strony Netlify jest inny niż w `CORS_ORIGINS`, zaktualizuj usługę:
   `gcloud run services update $SERVICE --region $REGION --update-env-vars CORS_ORIGINS=https://twoj-adres.netlify.app`

Dashboard na Netlify nie zawiera żadnych danych — tylko kod strony. Dane przychodzą z API po podaniu tokenu.

## Aktualizacja wersji

```bash
IMAGE=$REGION-docker.pkg.dev/$PROJECT/rentownosc/api:$(git rev-parse --short HEAD)
gcloud builds submit --tag $IMAGE && gcloud run deploy $SERVICE --image $IMAGE --region $REGION
```

Netlify przebudowuje dashboard sam po każdym pushu do `main`.

## Koszt (rząd wielkości)

Cloud Run (scale-to-zero, jedna synchronizacja dziennie + sporadyczny ruch) i Cloud Storage: pojedyncze USD/mies. Google Ads API i GA4 Data API — bez opłat w ramach limitów. BigQuery (jeśli `GA4_SOURCE=bigquery`) — zależnie od wolumenu eksportu GA4.
