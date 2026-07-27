import argparse
import asyncio
import json
from typing import Optional

from core.schwab.schwab_driver import SchwabDriver

"""
Dumps Schwab's fundamental data for a single symbol, so you can see exactly which fields Schwab provides.

    python -m scripts.dump_schwab_fundamentals SYMBOL [--json]

By default the "fundamental" object is printed as an aligned key/value table (sorted by field name), under a
short identity header (symbol, description, asset type, exchange). Pass --json to instead dump the full raw
instrument payload (identity + fundamental) as formatted JSON.

Note: Schwab's proprietary equity ratings (the A-F letter grades on schwab.com) are NOT available through the
Trader API and won't appear here.

Credentials come from .env (see core/schwab/schwab_driver.py).
"""


def print_fundamentals(instrument: dict):
    """Pretty-prints the identity header and the fundamental fields as an aligned, sorted table."""
    symbol = instrument.get("symbol", "?")
    print(f"\nFundamentals for {symbol}")
    print("=" * 60)
    for label, key in (
        ("Description", "description"),
        ("Asset type", "assetType"),
        ("Exchange", "exchange"),
        ("CUSIP", "cusip"),
    ):
        if instrument.get(key):
            print(f"  {label:<12}: {instrument[key]}")

    fundamental = instrument.get("fundamental")
    if not fundamental:
        print("\n  (no 'fundamental' object present in the response)")
        return

    print("\nfundamental object")
    print("-" * 60)
    width = max(len(field) for field in fundamental)
    for field in sorted(fundamental):
        print(f"  {field:<{width}}  {fundamental[field]}")
    print(f"\n{len(fundamental)} fundamental field(s).\n")


async def main(symbol: str, as_json: bool):
    driver = SchwabDriver.create()
    if not driver.connect():
        print("Failed to connect to Schwab. Check credentials in .env and the token file.")
        return

    try:
        instrument, error_str = await driver.get_fundamentals(symbol)
        if error_str:
            print(f"Error getting fundamentals for {symbol}: {error_str}")
            return

        if as_json:
            print(json.dumps(instrument, indent=2, sort_keys=True))
        else:
            print_fundamentals(instrument)
    finally:
        driver.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dump Schwab fundamental data for a symbol.")
    parser.add_argument("symbol", help="ticker to fetch fundamentals for, e.g. AAPL")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="dump the full raw instrument payload as formatted JSON instead of a table",
    )
    args = parser.parse_args()
    asyncio.run(main(args.symbol.upper(), args.as_json))
