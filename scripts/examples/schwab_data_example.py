import asyncio
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from schwab.auth import easy_client
from schwab.client import AsyncClient

"""
Phase One proof-of-concept for Schwab API access.

Fetches the daily OHLC bars for SPY over roughly the last week and prints them.
This is intentionally standalone (no SchwabDriver / BaseDriver yet) so we can
confirm end-to-end authentication and data retrieval work.

Credentials are read from the project's .env file:
    API_KEY      -> Schwab "App Key"    (public identifier)
    APP_SECRET   -> Schwab "App Secret" (confidential)
    CALLBACK     -> the Callback URL registered on the Schwab Developer portal
                    (must match the portal exactly, e.g. https://127.0.0.1:8182)

On first run a browser window opens for the Schwab OAuth consent flow. After you
log in and approve, schwab-py writes an auto-refreshing token to TOKEN_PATH. The
30-minute access token is then refreshed transparently on subsequent runs; a full
re-login is only needed once every 7 days (Schwab's refresh-token lifetime).
"""

# schwab-py stores/refreshes the OAuth token here. Gitignored — contains a refresh token.
TOKEN_PATH = "schwab_token.json"

SYMBOL = "SPY"
DAYS_BACK = 7


def _load_credentials() -> tuple[str, str, str]:
    """Reads Schwab credentials from .env and fails loudly if any are missing."""
    load_dotenv()
    api_key = os.getenv("API_KEY")
    app_secret = os.getenv("APP_SECRET")
    callback = os.getenv("CALLBACK")

    missing = [
        name for name, value in (("API_KEY", api_key), ("APP_SECRET", app_secret), ("CALLBACK", callback)) if not value
    ]
    if missing:
        raise SystemExit(
            f"Missing required .env variable(s): {', '.join(missing)}.\n"
            "Expected API_KEY (App Key), APP_SECRET (App Secret), and CALLBACK (registered callback URL)."
        )
    return api_key, app_secret, callback


def _print_candles(symbol: str, payload: dict):
    candles = payload.get("candles", [])
    if not candles:
        print(f"No candles returned for {symbol}. Raw payload: {payload}")
        return

    print(f"Daily OHLC bars for {symbol} (last {DAYS_BACK} days)")
    print("-" * 64)
    print(f"{'Date':<12}{'Open':>10}{'High':>10}{'Low':>10}{'Close':>10}{'Volume':>14}")
    for c in candles:
        # Schwab returns the candle timestamp as epoch milliseconds.
        dt = datetime.fromtimestamp(c["datetime"] / 1000, tz=timezone.utc)
        print(
            f"{dt:%Y-%m-%d}  "
            f"{c['open']:>9.2f}{c['high']:>10.2f}{c['low']:>10.2f}{c['close']:>10.2f}{c['volume']:>14,}"
        )
    print()


async def main():
    api_key, app_secret, callback = _load_credentials()

    # easy_client tries TOKEN_PATH first; if it's missing/expired it runs the interactive
    # login flow (opens a browser). asyncio=True returns an awaitable AsyncClient.
    client: AsyncClient = easy_client(
        api_key=api_key,
        app_secret=app_secret,
        callback_url=callback,
        token_path=TOKEN_PATH,
        asyncio=True,
    )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=DAYS_BACK)

    resp = await client.get_price_history_every_day(
        SYMBOL,
        start_datetime=start,
        end_datetime=end,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Schwab API request failed ({resp.status_code}): {resp.text}")

    _print_candles(SYMBOL, resp.json())


if __name__ == "__main__":
    asyncio.run(main())
