"""
Caches Nasdaq earnings calendar data for a list of stocks of interest.

Run `python -m scripts.cache_earnings --help` for the full instruction manual.
"""

import argparse
import os
import textwrap
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


def show_contents(earnings_manager: EarningsManager):
    """Prints name of every ticker cached by EarningsManager"""
    tickers = earnings_manager.get_cached_tickers()
    if not tickers:
        print("Cache is empty.")
        return
    print(f"Cached tickers ({len(tickers)}):")
    for t in tickers:
        print(f"  {t}")


def show_data(earnings_manager: EarningsManager, symbol: str):
    """
    Pretty-prints the data cached by EarningsManager for a particular stock. If data can't be found, prints a message
    saying so.
    """
    symbol = symbol.upper()
    df = earnings_manager.get_data(symbol)
    if df is None or df.empty:
        print(f"No cached data for {symbol}.")
        return

    display = df.rename(
        columns={
            "company_name": "Company",
            "eps": "EPS",
            "surprise_pct": "Surprise",
            "market_cap": "Mkt Cap",
            "fiscal_quarter_ending": "FQ End",
            "consensus_eps": "Cons EPS",
            "num_estimates": "#Ests",
        }
    )
    display.index = display.index.strftime("%Y-%m-%d")
    display.index.name = "Date"

    print(f"{symbol} — {len(df)} entries")
    print("=" * 80)
    print(display.to_string())


def migrate(earnings_manager: EarningsManager, legacy_path: str, db_path: str):
    """Imports a cache written in the old one-key-per-ticker layout into the new single-key one."""
    if os.path.abspath(legacy_path) == os.path.abspath(db_path):
        print("--migrate source and --db must be different files.")
        return

    print(f"Importing {legacy_path} -> {db_path} ...")
    rows = earnings_manager.migrate_legacy(legacy_path)
    if not rows:
        print("Nothing imported.")
        return

    old_mb = os.path.getsize(legacy_path) / 1024**2
    new_mb = os.path.getsize(db_path) / 1024**2
    print(f"Imported {rows} rows for {len(earnings_manager.get_cached_tickers())} tickers.")
    print(f"{old_mb:.1f} MB -> {new_mb:.2f} MB")


def main(parser: argparse.ArgumentParser):
    today = date.today()
    args = parser.parse_args()

    start_date = _parse_date(args.start) if args.start else today - timedelta(days=365 * 2)
    end_date = _parse_date(args.end) if args.end else today + timedelta(days=365)

    manager = EarningsManager()
    manager.set_db_path(args.db)

    if args.show:
        if args.symbol:
            show_data(manager, args.symbol)
        else:
            show_contents(manager)
    elif args.migrate:
        migrate(manager, args.migrate, args.db)
    elif args.file:
        tickers = _load_tickers(args.file)
        if not tickers:
            print("No tickers loaded. Exiting.")
            return

        print(f"Loaded {len(tickers)} tickers from {args.file}")
        print(f"Date range: {start_date} to {end_date} ({(end_date - start_date).days + 1} days)")

        manager.set_tickers(tickers)
        manager.scrape(start_date, end_date)
        print("Done.")
    else:
        print("Nothing to do.")


parser = argparse.ArgumentParser(
    prog="python -m scripts.cache_earnings",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=textwrap.dedent("""\
        Cache Nasdaq earnings calendar data to an HDF5 file.

        Scrapes earnings dates for a list of tickers over a date range and stores them
        for use by other tools (e.g. the earnings filters in iv_finder).
        """),
    epilog=textwrap.dedent("""\
        Examples:
          # Scrape earnings for a list of tickers (default range: 2 years back to 1 year ahead)
          python -m scripts.cache_earnings --db data/earnings_data.h5 --file data/optionable.txt

          # Scrape a specific date range
          python -m scripts.cache_earnings --db data/earnings_data.h5 --file data/optionable.txt --start 20240101 --end 20261231

          # List every ticker currently in the cache
          python -m scripts.cache_earnings --db data/earnings_data.h5 --show

          # Show cached earnings entries for a single symbol
          python -m scripts.cache_earnings --db data/earnings_data.h5 --show --symbol SPY

          # Convert a cache written by an older version into the compact layout
          python -m scripts.cache_earnings --db data/earnings_slim.h5 --migrate data/earnings_data.h5

        Notes:
          * --start / --end take dates in YYYYMMDD format.
          * --file is required to scrape; --show reads the existing cache instead.
          * --symbol only applies together with --show.
          * --migrate writes into --db, which must be a different file from the source.
        """),
)
parser.add_argument("--db", required=True, help="Path to .h5 database file")
parser.add_argument("--file", default=None, required=False, help="Path to text file with one ticker per line")
parser.add_argument("--start", default=None, help="Start date in YYYYMMDD format (default: 2 years ago)")
parser.add_argument("--end", default=None, help="End date in YYYYMMDD format (default: 1 year from now)")
parser.add_argument("--show", help="Show contents of cache. Can be paired with --symbol.", action="store_true")
parser.add_argument(
    "--symbol", default=None, help="If given, show earnings info for particular symbol. Must be paired with --show."
)
parser.add_argument(
    "--migrate", default=None, help="Path to an old-format .h5 file to import into --db (no re-scraping needed)"
)

main(parser)
