"""
Caches Nasdaq earnings calendar data for a list of stocks of interest.

Run like:
    python -m scripts.cache_earnings --db data/earnings_data.h5 --file data/optionable.txt
    python -m scripts.cache_earnings --db data/earnings_data.h5 --file data/optionable.txt --start 20240101 --end 20261231
"""

import argparse
from datetime import date, timedelta

from core.earnings_manager import EarningsManager


def _parse_date(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _load_tickers(file_path: str):
    tickers = []
    try:
        with open(file_path, "r") as f:
            for line in f:
                t = line.strip()
                if t:
                    tickers.append(t)
    except FileNotFoundError:
        print(f"File not found: {file_path}")
    return tickers


def main(parser: argparse.ArgumentParser):
    today = date.today()
    args = parser.parse_args()

    start_date = _parse_date(args.start) if args.start else today - timedelta(days=365 * 2)
    end_date = _parse_date(args.end) if args.end else today + timedelta(days=365)

    tickers = _load_tickers(args.file)
    if not tickers:
        print("No tickers loaded. Exiting.")
        return

    print(f"Loaded {len(tickers)} tickers from {args.file}")
    print(f"Date range: {start_date} to {end_date} ({(end_date - start_date).days + 1} days)")

    manager = EarningsManager()
    manager.set_db_path(args.db)
    manager.set_tickers(tickers)
    manager.scrape(start_date, end_date)
    print("Done.")


parser = argparse.ArgumentParser(description="Cache Nasdaq earnings calendar data to HDF5")
parser.add_argument("--db", required=True, help="Path to .h5 database file")
parser.add_argument("--file", required=True, help="Path to text file with one ticker per line")
parser.add_argument("--start", default=None, help="Start date in YYYYMMDD format (default: 2 years ago)")
parser.add_argument("--end", default=None, help="End date in YYYYMMDD format (default: 1 year from now)")

main(parser)
