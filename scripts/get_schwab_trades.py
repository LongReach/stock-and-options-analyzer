import argparse
import asyncio
from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

from core.base_driver import BaseDriver
from core.common import TradeDescriptor, MARKETS_TIMEZONE
from core.schwab.schwab_driver import SchwabDriver
from core.utils import current_datetime

"""
Fetches the account holder's Schwab trades over a date range and pretty-prints them.

    python -m scripts.get_schwab_trades [START] [END]

START and END are dates (YYYY-MM-DD or YYYYMMDD). If START is omitted, today is used. If END is omitted, the
current datetime is used.

Note: Schwab only serves transactions from the last 60 days, so START can't be much older than that.

Credentials come from .env (see core/schwab/schwab_driver.py).
"""


def parse_date(text: str) -> datetime:
    """Parses a YYYY-MM-DD or YYYYMMDD date into a market-timezone datetime at midnight."""
    digits = text.replace("-", "")
    try:
        naive = datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date '{text}'; expected YYYY-MM-DD or YYYYMMDD.")
    return naive.replace(tzinfo=ZoneInfo(MARKETS_TIMEZONE))


def signed_quantity_str(trade: TradeDescriptor) -> str:
    """Returns the quantity with an explicit sign, so buys (+) and sells (-) read at a glance."""
    return f"{trade.quantity:+d}"


def print_trades(trades: List[TradeDescriptor], start_dt: datetime, end_dt: datetime):
    """Pretty-prints the trades as an aligned table, most recent first."""
    print(f"Trades from {start_dt:%Y-%m-%d %H:%M %Z} to {end_dt:%Y-%m-%d %H:%M %Z}\n")
    if not trades:
        print("No trades in this range.\n")
        return

    header = f"{'Trade Time':<20}{'Symbol':<28}{'Quantity':>10}{'Price':>12}"
    print(header)
    print("-" * len(header))
    # Most recent first.
    for trade in sorted(trades, key=lambda t: t.trade_date, reverse=True):
        print(
            f"{trade.trade_date:%Y-%m-%d %H:%M}    "
            f"{trade.security_descriptor.symbol_full:<28}"
            f"{signed_quantity_str(trade):>10}"
            f"{trade.price:>12.2f}"
        )
    print(f"\n{len(trades)} trade(s).\n")


async def main(start_dt: datetime, end_dt: datetime):
    driver: BaseDriver = SchwabDriver.create()
    if not driver.connect():
        print("Failed to connect to Schwab. Check credentials in .env and the token file.")
        return

    try:
        trades_info, error_str = await driver.get_trades(start_dt, end_dt)
        if error_str:
            print(f"Error getting trades: {error_str}")
            return
        print_trades(trades_info.get_trades(), start_dt, end_dt)
    finally:
        driver.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and print Schwab trades over a date range.")
    parser.add_argument(
        "start_date",
        nargs="?",
        default=None,
        type=parse_date,
        help="start date (YYYY-MM-DD or YYYYMMDD); defaults to today",
    )
    parser.add_argument(
        "end_date",
        nargs="?",
        default=None,
        type=parse_date,
        help="end date (YYYY-MM-DD or YYYYMMDD); defaults to the current datetime",
    )
    args = parser.parse_args()

    # Default start = midnight today (market time); default end = right now.
    now = current_datetime()
    start = args.start_date if args.start_date is not None else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = args.end_date if args.end_date is not None else now
    asyncio.run(main(start, end))
