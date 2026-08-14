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

# Every ticker lives in this one HDF5 key. Using a key per ticker costs ~1 MB of HDF5
# file-space allocation per key regardless of how little data it holds, and re-writing
# a key abandons that space permanently -- which is what grew an earlier 960-row cache
# to 1.5 GB. One table-format key holds the same data in well under a megabyte.
_H5_KEY = "earnings"
_DATA_COLUMNS = ["ticker", "date"]

# Scraping a multi-year range takes a while, so checkpoint periodically rather than
# risking the whole run to a crash. Re-writing this single key is cheap (~1 kB of
# growth per flush), so the interval is about limiting lost work, not file size.
_FLUSH_INTERVAL_DAYS = 25

_VALUE_COLUMNS = [
    "company_name",
    "eps",
    "surprise_pct",
    "market_cap",
    "fiscal_quarter_ending",
    "consensus_eps",
    "num_estimates",
]


class EarningsManager:
    """
    Scrapes Nasdaq earnings calendar data and caches it to an HDF5 file.
    Only records data for tickers configured via set_tickers().

    This is a cheap and free way to get earnings dates. It's slow, but the dates are then cached on disk
    for quick subsequent access. And it's not necessary to do the scraping/caching process that often.

    Storage: a single table-format DataFrame under the key "earnings", with `ticker` and
    `date` as indexed data columns.
    Columns: ticker, date, company_name, eps, surprise_pct, market_cap,
             fiscal_quarter_ending, consensus_eps, num_estimates.
    """

    def __init__(self):
        self._tickers: Set[str] = set()
        self._db_path: str = DEFAULT_DB_PATH
        self._cache: Optional[pd.DataFrame] = None

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
        self._cache = None

    def scrape(self, start_date: date, end_date: date):
        """
        Scrape the Nasdaq earnings calendar for every day in [start_date, end_date]
        and cache matching rows to the HDF5 file.

        Rows accumulate in memory and are flushed to disk periodically, so a long run
        costs a handful of writes rather than one per calendar day.
        """
        total_days = (end_date - start_date).days + 1
        current = start_date
        day_num = 0
        pending: List[dict] = []

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

            for ticker, row in matching.items():
                pending.append(self._build_record(ticker, row, current))
            print(f"{len(matching)} cached." if matching else "none.")

            if pending and day_num % _FLUSH_INTERVAL_DAYS == 0:
                self._merge_and_save(pending)
                pending = []

            current += timedelta(days=1)
            time.sleep(_REQUEST_DELAY)

        if pending:
            self._merge_and_save(pending)

    def get_past_dates(self, ticker: str) -> List[date]:
        """Return past earnings dates for a ticker, sorted oldest first."""
        today = date.today()
        return [d for d in self._dates_for(ticker) if d < today]

    def get_upcoming_dates(self, ticker: str) -> List[date]:
        """Return upcoming earnings dates for a ticker, sorted soonest first."""
        today = date.today()
        return [d for d in self._dates_for(ticker) if d >= today]

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
        """
        Return the cached data for a ticker as a date-indexed DataFrame, or None if not
        present. Columns are the value columns only; the ticker is implied.
        """
        df = self._load_cache()
        if df is None or df.empty:
            return None

        subset = df[df["ticker"] == ticker.upper()]
        if subset.empty:
            return None

        subset = subset.sort_values("date")
        result = subset[_VALUE_COLUMNS].copy()
        result.index = pd.DatetimeIndex(subset["date"], name="date")
        return result

    def get_cached_tickers(self) -> List[str]:
        """Return sorted list of ticker symbols currently stored in the cache."""
        df = self._load_cache()
        if df is None or df.empty:
            return []
        return sorted(df["ticker"].unique())

    def migrate_legacy(self, legacy_path: str) -> int:
        """
        Import a cache written in the old one-key-per-ticker layout into this manager's
        (single-key) database. Existing rows for the same ticker/date are overwritten.

        :param legacy_path: path to the old .h5 file
        :return: number of rows imported
        """
        try:
            with pd.HDFStore(legacy_path, mode="r") as store:
                keys = list(store.keys())
        except Exception as exc:
            _logger.error(f"Could not open legacy cache {legacy_path}: {exc}")
            return 0

        records: List[dict] = []
        for key in keys:
            try:
                df = pd.read_hdf(legacy_path, key=key)
            except Exception as exc:
                _logger.warning(f"Skipping legacy key {key}: {exc}")
                continue
            if df is None or df.empty:
                continue

            ticker = key.lstrip("/").upper()
            for ts, row in df.iterrows():
                record = {"ticker": ticker, "date": pd.Timestamp(ts).normalize()}
                for col in _VALUE_COLUMNS:
                    record[col] = str(row.get(col, "") or "")
                records.append(record)

        if records:
            self._merge_and_save(records)
        return len(records)

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

    @staticmethod
    def _build_record(ticker: str, row: dict, earnings_date: date) -> dict:
        """Flatten one Nasdaq calendar row into a record for the cache table."""
        return {
            "ticker": ticker,
            "date": pd.Timestamp(earnings_date),
            "company_name": str(row.get("name", "") or ""),
            "eps": str(row.get("eps", "") or ""),
            "surprise_pct": str(row.get("surprise", "") or ""),
            "market_cap": str(row.get("marketCap", "") or ""),
            "fiscal_quarter_ending": str(row.get("fiscalQuarterEnding", "") or ""),
            "consensus_eps": str(row.get("epsForecast", "") or ""),
            "num_estimates": str(row.get("noOfEsts") or row.get("numOfEsts", "") or ""),
        }

    def _dates_for(self, ticker: str) -> List[date]:
        """Return all cached earnings dates for a ticker, sorted oldest first."""
        df = self._load_cache()
        if df is None or df.empty:
            return []
        subset = df.loc[df["ticker"] == ticker.upper(), "date"]
        return sorted(ts.date() for ts in subset)

    def _merge_and_save(self, records: List[dict]):
        """Merge new records into the cache, de-duplicating on (ticker, date), then save."""
        if not records:
            return

        new_df = pd.DataFrame(records, columns=["ticker", "date"] + _VALUE_COLUMNS)
        existing = self._load_cache()
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined = (
            combined.drop_duplicates(subset=["ticker", "date"], keep="last")
            .sort_values(["ticker", "date"])
            .reset_index(drop=True)
        )
        self._save_cache(combined)

    def _load_cache(self) -> Optional[pd.DataFrame]:
        """Load the whole cache table, memoizing it so repeated lookups don't re-read the file."""
        if self._cache is not None:
            return self._cache
        try:
            df = pd.read_hdf(self._db_path, key=_H5_KEY)
        except Exception:
            return None
        if not isinstance(df, pd.DataFrame):
            return None
        self._cache = df
        return self._cache

    def _save_cache(self, df: pd.DataFrame):
        """Write the whole cache table back to HDF5 as a single table-format key."""
        try:
            df.to_hdf(
                self._db_path,
                key=_H5_KEY,
                mode="a",
                format="table",
                data_columns=_DATA_COLUMNS,
                complevel=6,
                complib="zlib",
            )
            self._cache = df
        except Exception as exc:
            _logger.error(f"Failed to save earnings cache: {exc}")
