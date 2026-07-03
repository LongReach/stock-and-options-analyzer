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


parser = argparse.ArgumentParser(description="Cache Nasdaq earnings calendar data to HDF5")
parser.add_argument("--db", required=True, help="Path to .h5 database file")
parser.add_argument("--file", default=None, required=False, help="Path to text file with one ticker per line")
parser.add_argument("--start", default=None, help="Start date in YYYYMMDD format (default: 2 years ago)")
parser.add_argument("--end", default=None, help="End date in YYYYMMDD format (default: 1 year from now)")
parser.add_argument("--show", help="Show contents of cache. Can be paired with --symbol.", action="store_true")
parser.add_argument("--symbol", default=None, help="If given, show earnings info for particular symbol. Must be paired with --show.")

main(parser)
