import logging
import time
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Union

import pandas as pd
import requests

_logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/earnings_data.h5"
_REQUEST_DELAY = 0.5
_NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date="
_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.nasdaq.com",
    "referer": "https://www.nasdaq.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class EarningsManager:
    """
    Scrapes Nasdaq earnings calendar data and caches it to an HDF5 file.
    Only records data for tickers configured via set_tickers().

    This is a cheap and free way to get earnings dates. It's slow, but the dates are then cached on disk
    for quick subsequent access. And it's not necessary to do the scraping/caching process that often.

    Storage: one DataFrame per ticker, keyed by the ticker symbol.
    Columns: company_name, eps, surprise_pct, market_cap, fiscal_quarter_ending,
             consensus_eps, num_estimates. Index: earnings date (DatetimeIndex).
    """

    def __init__(self):
        self._tickers: Set[str] = set()
        self._db_path: str = DEFAULT_DB_PATH

    def set_tickers(self, tickers: List[str]):
        """Set the list of tickers of interest."""
        self._tickers = {t.strip().upper() for t in tickers if t.strip()}

    def set_tickers_from_file(self, file_path: str) -> bool:
        """
        Sets of tickers of interest from a text file
        :param file_path: path to text file containing one symbol per line
        :return: True if successfully loaded file, else False
        """
        tickers = []
        try:
            with open(file_path, "r") as f:
                for line in f:
                    t = line.strip()
                    if t:
                        tickers.append(t)
        except FileNotFoundError:
            return False
        self.set_tickers(tickers)
        return True

    def set_db_path(self, path: str):
        """Set the path to the HDF5 cache file."""
        self._db_path = path

    def scrape(self, start_date: date, end_date: date):
        """
        Scrape the Nasdaq earnings calendar for every day in [start_date, end_date]
        and cache matching rows to the HDF5 file.
        """
        total_days = (end_date - start_date).days + 1
        current = start_date
        day_num = 0

        while current <= end_date:
            day_num += 1
            date_str = current.strftime("%Y-%m-%d")
            print(f"[{day_num}/{total_days}] {date_str} ...", end=" ", flush=True)

            rows = self._fetch_date(date_str)
            matching: Dict[str, dict] = {
                r.get("symbol", "").strip().upper(): r
                for r in rows
                if r.get("symbol", "").strip().upper() in self._tickers
            }

            if matching:
                self._cache_rows(matching, current)
                print(f"{len(matching)} cached.")
            else:
                print("none.")

            current += timedelta(days=1)
            time.sleep(_REQUEST_DELAY)

    def get_past_dates(self, ticker: str) -> List[date]:
        """Return past earnings dates for a ticker, sorted oldest first."""
        today = date.today()
        df = self._load_ticker(ticker.upper())
        if df is None or df.empty:
            return []
        return sorted(ts.date() for ts in df.index if ts.date() < today)

    def get_upcoming_dates(self, ticker: str) -> List[date]:
        """Return upcoming earnings dates for a ticker, sorted soonest first."""
        today = date.today()
        df = self._load_ticker(ticker.upper())
        if df is None or df.empty:
            return []
        return sorted(ts.date() for ts in df.index if ts.date() >= today)

    def get_next_date(self, ticker: str, in_days: bool = False) -> Optional[Union[date, int]]:
        """
        Returns next earnings date if found, or None.

        :param ticker: ticker symbol of stock
        :param in_days: if True, return earnings data as days from now, rather than direct date
        """
        dates = self.get_upcoming_dates(ticker)
        if len(dates) == 0:
            return None
        return (dates[0] - date.today()).days if in_days else dates[0]

    def get_data(self, ticker: str) -> Optional[pd.DataFrame]:
        """Return the full cached DataFrame for a ticker, or None if not present."""
        return self._load_ticker(ticker.upper())

    def get_cached_tickers(self) -> List[str]:
        """Return sorted list of ticker symbols currently stored in the cache."""
        try:
            with pd.HDFStore(self._db_path, mode="r") as store:
                return sorted(k.lstrip("/") for k in store.keys())
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_date(self, date_str: str) -> List[dict]:
        """Fetch one day's earnings rows from the Nasdaq API."""
        try:
            resp = requests.get(_NASDAQ_URL + date_str, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            rows = resp.json().get("data", {}).get("rows") or []
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            _logger.warning(f"Nasdaq fetch failed for {date_str}: {exc}")
            return []

    def _cache_rows(self, rows: Dict[str, dict], earnings_date: date):
        """Merge a dict of {ticker: row} into the HDF5 cache."""
        ts = pd.Timestamp(earnings_date)
        for ticker, row in rows.items():
            new_df = pd.DataFrame(
                [
                    {
                        "company_name": row.get("name", ""),
                        "eps": row.get("eps", ""),
                        "surprise_pct": row.get("surprise", ""),
                        "market_cap": row.get("marketCap", ""),
                        "fiscal_quarter_ending": row.get("fiscalQuarterEnding", ""),
                        "consensus_eps": row.get("epsForecast", ""),
                        "num_estimates": row.get("noOfEsts") or row.get("numOfEsts", ""),
                    }
                ],
                index=pd.DatetimeIndex([ts], name="date"),
            )

            existing = self._load_ticker(ticker)
            if existing is not None and not existing.empty:
                existing = existing[~existing.index.isin([ts])]
                combined = pd.concat([existing, new_df]).sort_index()
            else:
                combined = new_df

            self._save_ticker(ticker, combined)

    def _load_ticker(self, ticker: str) -> Optional[pd.DataFrame]:
        """Load cached DataFrame for a ticker from HDF5. Returns None on miss."""
        try:
            return pd.read_hdf(self._db_path, key=self._h5_key(ticker))
        except Exception:
            return None

    def _save_ticker(self, ticker: str, df: pd.DataFrame):
        """Write DataFrame for a ticker to the HDF5 cache."""
        try:
            df.to_hdf(self._db_path, key=self._h5_key(ticker), complevel=6, complib="zlib")
        except Exception as exc:
            _logger.error(f"Failed to save earnings for {ticker}: {exc}")

    @staticmethod
    def _h5_key(ticker: str) -> str:
        """Map a ticker to a valid HDF5 store key (e.g. BRK-B → BRK_B)."""
        return ticker.upper().replace("-", "_").replace(".", "_")
