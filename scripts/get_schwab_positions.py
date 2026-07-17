import argparse
import asyncio
import csv
import os
from typing import Dict, List, Optional, Tuple

from core.base_driver import BaseDriver
from core.common import PositionDescriptor
from core.schwab.schwab_driver import SchwabDriver

"""
Fetches all of the user's current Schwab positions and pretty-prints them.

With an optional CSV file path, also writes the positions to that file using the same column format as
data/options_trades_2026.csv:

    Position #,Position Type,Symbol,Quantity,Trade Price

Notes on the CSV:
  * Schwab reports flat positions (individual legs), not the logical structures (Iron Condor, Double
    Calendar, ...) that a human groups them into. So "Position Type" can't be inferred here.
  * If the CSV file already exists, the "Position #" and "Position Type" a human has assigned to each symbol
    are preserved; only "Quantity" and "Trade Price" are refreshed from the broker. New symbols (held now but
    not yet in the file) each get a fresh "Position #" and a blank "Position Type" for you to fill in.
    Symbols in the file that are no longer held are dropped (the file tracks currently-held positions).
  * Output rows are ordered by "Position #".
  * "Quantity" is signed: negative for short positions, matching options_trades_2026.csv.

Credentials come from .env (see core/schwab/schwab_driver.py).
"""

# CSV column names, matching data/options_trades_2026.csv exactly.
COL_POSITION_NUM = "Position #"
COL_POSITION_TYPE = "Position Type"
COL_SYMBOL = "Symbol"
COL_QUANTITY = "Quantity"
COL_TRADE_PRICE = "Trade Price"
CSV_COLUMNS = [COL_POSITION_NUM, COL_POSITION_TYPE, COL_SYMBOL, COL_QUANTITY, COL_TRADE_PRICE]


def signed_quantity(position: PositionDescriptor) -> int:
    """Returns the position quantity, negative for a short position (as in options_trades_2026.csv)."""
    return -position.quantity if position.short_position else position.quantity


def print_positions(positions: List[PositionDescriptor]):
    """Pretty-prints the held positions as an aligned table."""
    if not positions:
        print("No open positions.\n")
        return

    header = f"{'Symbol':<28}{'Quantity':>10}{'Trade Price':>14}"
    print(header)
    print("-" * len(header))
    for position in positions:
        print(
            f"{position.security_descriptor.symbol_full:<28}"
            f"{signed_quantity(position):>10}"
            f"{position.price:>14.2f}"
        )
    print(f"\n{len(positions)} position(s).\n")


def load_existing_metadata(csv_path: str) -> Tuple[Dict[str, Tuple[str, str]], int]:
    """
    Reads an existing positions CSV and returns the human-assigned grouping metadata to preserve.

    :param csv_path: path to an existing CSV (may not exist)
    :return: (map of Symbol -> (Position #, Position Type), highest integer Position # seen)
    """
    metadata: Dict[str, Tuple[str, str]] = {}
    max_position_num = 0
    if not os.path.exists(csv_path):
        return metadata, max_position_num

    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            symbol = (row.get(COL_SYMBOL) or "").strip()
            if not symbol:
                continue
            position_num = (row.get(COL_POSITION_NUM) or "").strip()
            position_type = row.get(COL_POSITION_TYPE) or ""
            metadata[symbol] = (position_num, position_type)
            try:
                max_position_num = max(max_position_num, int(position_num))
            except ValueError:
                pass
    return metadata, max_position_num


def build_rows(positions: List[PositionDescriptor], csv_path: str) -> List[dict]:
    """
    Builds the CSV rows for the currently-held positions, preserving any Position #/Position Type already
    recorded in csv_path and assigning fresh Position #s to newly-held symbols. Rows are ordered by Position #.
    """
    metadata, next_position_num = load_existing_metadata(csv_path)
    next_position_num += 1

    rows = []
    for order, position in enumerate(positions):
        symbol = position.security_descriptor.symbol_full
        if symbol in metadata:
            position_num, position_type = metadata[symbol]
        else:
            # Held now but not in the file yet: give it its own new number and a blank type to fill in later.
            position_num, position_type = str(next_position_num), ""
            next_position_num += 1
        rows.append(
            {
                COL_POSITION_NUM: position_num,
                COL_POSITION_TYPE: position_type,
                COL_SYMBOL: symbol,
                COL_QUANTITY: signed_quantity(position),
                # Schwab reports a full-precision average cost; option prices are quoted in cents, so round to
                # 2 decimals to match options_trades_2026.csv.
                COL_TRADE_PRICE: round(position.price, 2),
                "_order": order,  # stable tie-breaker so same-numbered legs keep broker order
            }
        )

    # Order by Position # (numeric where possible), keeping broker order within a group.
    def sort_key(row: dict) -> Tuple[int, int]:
        try:
            return int(row[COL_POSITION_NUM]), row["_order"]
        except ValueError:
            return 10**9, row["_order"]

    rows.sort(key=sort_key)
    return rows


def write_csv(positions: List[PositionDescriptor], csv_path: str):
    """Writes the held positions to csv_path in options_trades_2026.csv format (merging with any existing file)."""
    rows = build_rows(positions, csv_path)
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} position(s) to {csv_path}\n")


async def main(csv_path: Optional[str]):
    driver: BaseDriver = SchwabDriver.create()
    if not driver.connect():
        print("Failed to connect to Schwab. Check credentials in .env and the token file.")
        return

    try:
        positions_info, error_str = await driver.get_positions()
        if error_str:
            print(f"Error getting positions: {error_str}")
            return

        positions = positions_info.get_positions()
        print_positions(positions)
        if csv_path:
            write_csv(positions, csv_path)
    finally:
        driver.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and print current Schwab positions.")
    parser.add_argument(
        "csv_path",
        nargs="?",
        default=None,
        help="optional path to write positions as CSV (options_trades_2026.csv format)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.csv_path))
