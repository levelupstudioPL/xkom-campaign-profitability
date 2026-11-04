"""Generuje refresh token OAuth dla Google Ads API.

Użycie (lokalnie, z przeglądarką):
    python scripts/google_ads_auth.py --client-id XXX --client-secret YYY

Klient OAuth: Google Cloud Console → APIs & Services → Credentials → OAuth client ID
(typ „Desktop app”). Zaloguj się kontem, które ma dostęp do MCC x-kom.
Wynik wklej do .env jako GOOGLE_ADS_REFRESH_TOKEN.
"""
import argparse

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    a = ap.parse_args()
    flow = InstalledAppFlow.from_client_config(
        {"installed": {"client_id": a.client_id, "client_secret": a.client_secret,
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}},
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    print("\nGOOGLE_ADS_REFRESH_TOKEN=" + creds.refresh_token)


if __name__ == "__main__":
    main()
