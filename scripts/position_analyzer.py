import asyncio
from logging import basicConfig, INFO, getLogger
from typing import List, Optional, Tuple

import pandas as pd
import argparse
import textwrap
import traceback

from core.base_driver import BaseDriver
from core.common import SecurityDescriptor, OptionInfo, BarSize, RequestedInfoType
from core.options_data import OptionData, OptionDataException
from core.option_data_manager import OptionDataManager
from core.ib.ib_driver import IBDriver
from core.schwab.schwab_driver import SchwabDriver
from core.utils import current_datetime, calculate_expected_move

r"""
Utility for analyzing a set of open options positions. For each option leg it reports current
price and the Greeks (delta, theta, gamma, vega) alongside the trade details recorded in a CSV,
plus the contracts actually held (Quantity + Quantity Out) and any realized P/L on the closed
portion. A final aggregate row summarizes the filtered legs (net position Greeks, net value,
unrealized P/L, and realized P/L), computed over the contracts actually held. Finally, it prints
the expected next-day (1 std dev) move for each underlying, from the broker's implied volatility.

Setup and usage:
------------------------
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.position_analyzer --help    # full instruction manual with examples
"""

CLIENT_ID = 17
# Standard US equity-option contract multiplier: 1 contract controls 100 shares.
CONTRACT_MULTIPLIER = 100

# CSV column names (current_positions.csv format:
# Position #,Date In,Position Type,Symbol,Quantity,Trade Price,Date Out,Quantity Out,Exit Price)
CSV_POSITION_NUM = "Position #"
CSV_POSITION_TYPE = "Position Type"
CSV_SYMBOL = "Symbol"
CSV_QUANTITY = "Quantity"
CSV_TRADE_PRICE = "Trade Price"
CSV_QUANTITY_OUT = "Quantity Out"
CSV_EXIT_PRICE = "Exit Price"

# Maps the short --position-type argument to the full name stored in the CSV's "Position Type" column.
POSITION_TYPE_MAP = {
    "IC": "Iron Condor",
    "CS": "Credit Spread",
    "DS": "Debit Spread",
    "L": "Naked Long",
    "S": "Naked Short",
    "CAL": "Calendar",
    "DCAL": "Double Calendar",
    "TCAL": "Triple Calendar",
    "DIAG": "Diagonal",
    "DDIAG": "Double Diagonal",
}

# Output column names, in display order
COL_CONTRACT = "Contract"
COL_POSITION_NUM = "Pos #"
COL_POSITION_TYPE = "Pos Type"
COL_QUANTITY = "Qty"
COL_HELD = "Held"
COL_TRADE_PRICE = "Trade Price"
COL_CURRENT_PRICE = "Cur Price"
COL_REALIZED = "Realized"
COL_IV = "IV"
COL_DELTA = "Delta"
COL_THETA = "Theta"
COL_GAMMA = "Gamma"
COL_VEGA = "Vega"

OUTPUT_COLUMNS = [
    COL_CONTRACT,
    COL_POSITION_NUM,
    COL_POSITION_TYPE,
    COL_QUANTITY,
    COL_HELD,
    COL_TRADE_PRICE,
    COL_CURRENT_PRICE,
    COL_REALIZED,
    COL_IV,
    COL_DELTA,
    COL_THETA,
    COL_GAMMA,
    COL_VEGA,
]

_logger = getLogger(__name__)


def load_positions(
    positions_file: str,
    symbol: Optional[str],
    expiration: Optional[str],
    position_num: Optional[int] = None,
    position_type: Optional[str] = None,
) -> pd.DataFrame:
    """
    Loads open positions from a CSV, optionally narrowing the legs by several filters.

    :param positions_file: path to CSV of open positions
    :param symbol: if given, keep only legs whose underlying matches (e.g. "SPY")
    :param expiration: if given, keep only legs with this IB-style expiration (e.g. "20260821")
    :param position_num: if given, keep only legs with this "Position #" value
    :param position_type: if given, keep only legs with this full "Position Type" (e.g. "Iron Condor")
    :return: filtered DataFrame of position rows (original CSV columns)
    """
    df = pd.read_csv(positions_file)

    # Parse each leg's descriptor so we can filter on underlying / expiration.
    descriptors = df[CSV_SYMBOL].apply(SecurityDescriptor)
    df = df.assign(
        _ticker=descriptors.apply(lambda d: d.ticker),
        _expiration=descriptors.apply(lambda d: d.expiration),
    )

    if symbol:
        df = df[df["_ticker"] == symbol]
    if expiration:
        df = df[df["_expiration"] == expiration]
    if position_num is not None:
        df = df[df[CSV_POSITION_NUM] == position_num]
    if position_type:
        df = df[df[CSV_POSITION_TYPE] == position_type]

    return df.reset_index(drop=True)


async def collect_leg_data(
    option_manager: OptionDataManager, positions_df: pd.DataFrame
) -> Tuple[OptionData, List[Optional[OptionInfo]]]:
    """
    Fetches current market data (price + Greeks) for every leg in positions_df.

    Uses OptionDataManager to retrieve an OptionInfo per contract and collects them into an
    OptionData object so the results are available as a pandas DataFrame.

    :param option_manager: connected OptionDataManager
    :param positions_df: filtered positions
    :return: (OptionData holding one row per successfully-fetched leg, per-row OptionInfo list
              aligned with positions_df -- None where a fetch failed)
    """
    option_data = OptionData("positions", current_datetime())
    infos: List[Optional[OptionInfo]] = []

    for _, row in positions_df.iterrows():
        descriptor = SecurityDescriptor(row[CSV_SYMBOL])
        try:
            option_info = await option_manager.get_option_info(
                ticker=descriptor.ticker,
                expiration=descriptor.expiration,
                right=descriptor.right,
                strike=descriptor.strike,
            )
        except OptionDataException as ex:
            print(f"WARNING: could not get data for {row[CSV_SYMBOL]}: {ex}")
            option_info = None

        infos.append(option_info)
        if option_info is not None:
            option_data.add_data(option_info)

    return option_data, infos


def _csv_int(pos_row: pd.Series, col: str) -> int:
    """Reads an integer CSV cell, treating a blank/missing/NaN value as 0."""
    value = pos_row.get(col, 0)
    if value == "" or value is None or pd.isna(value):
        return 0
    return int(value)


def _csv_float(pos_row: pd.Series, col: str) -> float:
    """Reads a float CSV cell, treating a blank/missing/NaN value as 0.0."""
    value = pos_row.get(col, 0.0)
    if value == "" or value is None or pd.isna(value):
        return 0.0
    return float(value)


def build_output_dataframe(positions_df: pd.DataFrame, infos: List[Optional[OptionInfo]]) -> pd.DataFrame:
    """
    Combines the CSV position details with fetched per-contract market data into one numeric
    DataFrame -- one row per leg -- ready for aggregation and display.

    Two derived per-leg quantities are added:
      * "Held": the number of contracts ACTUALLY held now, = Quantity + Quantity Out (signed; can be 0 once a
        leg is fully closed). All market-value/Greek aggregation uses this, not the original entry Quantity.
      * "Realized": dollars of realized P/L on the closed portion, = -Quantity Out * (Exit Price - Trade Price)
        * contract multiplier. This is sign-correct for both long and short legs and is 0 until an exit is
        recorded.

    :param positions_df: filtered positions (CSV columns)
    :param infos: OptionInfo per leg (aligned with positions_df), None where unavailable
    :return: DataFrame with OUTPUT_COLUMNS
    """
    rows = []
    for (_, pos_row), info in zip(positions_df.iterrows(), infos):
        quantity = _csv_int(pos_row, CSV_QUANTITY)
        quantity_out = _csv_int(pos_row, CSV_QUANTITY_OUT)
        trade_price = _csv_float(pos_row, CSV_TRADE_PRICE)
        exit_price = _csv_float(pos_row, CSV_EXIT_PRICE)

        held = quantity + quantity_out
        realized = -quantity_out * (exit_price - trade_price) * CONTRACT_MULTIPLIER

        rows.append(
            {
                COL_CONTRACT: pos_row[CSV_SYMBOL],
                COL_POSITION_NUM: pos_row[CSV_POSITION_NUM],
                COL_POSITION_TYPE: pos_row[CSV_POSITION_TYPE],
                COL_QUANTITY: quantity,
                COL_HELD: held,
                COL_TRADE_PRICE: trade_price,
                COL_CURRENT_PRICE: info.price if info else float("nan"),
                COL_REALIZED: realized,
                COL_IV: info.implied_volatility if info else float("nan"),
                COL_DELTA: info.delta if info else float("nan"),
                COL_THETA: info.theta if info else float("nan"),
                COL_GAMMA: info.gamma if info else float("nan"),
                COL_VEGA: info.vega if info else float("nan"),
            }
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def build_aggregate_row(df: pd.DataFrame) -> Tuple[dict, float, float]:
    """
    Computes the aggregate row for a set of option legs.

    All market-value and Greek aggregation is done on the contracts ACTUALLY HELD ("Held" = Quantity +
    Quantity Out), so fully-closed legs (Held == 0) drop out naturally.

    Standard rules for combining option positions:
      * Position Greek = sum over legs of (per-contract Greek * held * contract multiplier).
        Held is negative for short legs, so shorts subtract as expected.
      * Aggregate trade/current "price" is expressed as net dollars: sum(price * held * multiplier).
        A positive value is a net debit (cash paid); a negative value is a net credit (cash
        received).
      * Realized P/L is summed straight from the per-leg "Realized" dollars (closed portion).
      * Implied volatility does not aggregate meaningfully across strikes, so it is left blank.

    :param df: per-leg output DataFrame (OUTPUT_COLUMNS)
    :return: (aggregate row dict keyed by OUTPUT_COLUMNS, unrealized P/L in dollars, realized P/L in dollars)
    """
    held = df[COL_HELD]
    mult = CONTRACT_MULTIPLIER

    net_trade = (df[COL_TRADE_PRICE] * held * mult).sum()
    net_current = (df[COL_CURRENT_PRICE] * held * mult).sum()
    unrealized_pl = net_current - net_trade
    realized_pl = df[COL_REALIZED].sum()

    aggregate = {
        COL_CONTRACT: "AGGREGATE",
        COL_POSITION_NUM: "",
        COL_POSITION_TYPE: "",
        COL_QUANTITY: df[COL_QUANTITY].sum(),
        COL_HELD: held.sum(),
        COL_TRADE_PRICE: net_trade,
        COL_CURRENT_PRICE: net_current,
        COL_REALIZED: realized_pl,
        COL_IV: float("nan"),
        COL_DELTA: (df[COL_DELTA] * held * mult).sum(),
        COL_THETA: (df[COL_THETA] * held * mult).sum(),
        COL_GAMMA: (df[COL_GAMMA] * held * mult).sum(),
        COL_VEGA: (df[COL_VEGA] * held * mult).sum(),
    }
    return aggregate, unrealized_pl, realized_pl


def _fmt(value, decimals: int) -> str:
    """Formats a numeric cell, rendering NaN/empty as a dash."""
    if value == "" or value is None:
        return ""
    try:
        if pd.isna(value):
            return "-"
    except (TypeError, ValueError):
        return str(value)
    if value == 0:  # normalize -0.0 so it prints as 0.00, not -0.00
        value = 0.0
    return f"{value:.{decimals}f}"


def format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Returns a copy of df with each column formatted to a readable string."""
    decimals = {
        COL_TRADE_PRICE: 2,
        COL_CURRENT_PRICE: 2,
        COL_REALIZED: 2,
        COL_IV: 4,
        COL_DELTA: 4,
        COL_THETA: 4,
        COL_GAMMA: 5,
        COL_VEGA: 4,
    }
    display = pd.DataFrame(columns=OUTPUT_COLUMNS)
    for col in OUTPUT_COLUMNS:
        if col in decimals:
            display[col] = df[col].apply(lambda v, d=decimals[col]: _fmt(v, d))
        elif col in (COL_QUANTITY, COL_HELD):
            display[col] = df[col].apply(lambda v: "" if v == "" else str(int(v)))
        else:
            display[col] = df[col].astype(str)
    return display


def print_analysis(df: pd.DataFrame, aggregate: dict, unrealized_pl: float, realized_pl: float):
    """Pretty-prints the per-leg table, the aggregate row, and a summary."""
    # Format legs and aggregate together so the columns align in a single table.
    combined = pd.concat([df, pd.DataFrame([aggregate], columns=OUTPUT_COLUMNS)], ignore_index=True)
    display = format_for_display(combined)
    lines = display.to_string(index=False).splitlines()
    table_width = max(len(line) for line in lines)

    print("\nPer-leg analysis")
    print("=" * table_width)
    # All rows except the last are individual legs; the last row is the aggregate.
    for line in lines[:-1]:
        print(line)
    print("-" * table_width)
    print(lines[-1])
    print("=" * table_width)

    print("\nPosition summary")
    print("-" * 40)
    print(f"  Net premium (debit +/credit -): {aggregate[COL_TRADE_PRICE]:>12.2f}")
    print(f"  Current net value             : {aggregate[COL_CURRENT_PRICE]:>12.2f}")
    print(f"  Unrealized P/L                : {unrealized_pl:>12.2f}")
    print(f"  Realized P/L                  : {realized_pl:>12.2f}")
    print(f"  Total P/L (real + unreal)     : {realized_pl + unrealized_pl:>12.2f}")
    print(f"  Position delta                : {aggregate[COL_DELTA]:>12.4f}")
    print(f"  Position theta                : {aggregate[COL_THETA]:>12.4f}")
    print(f"  Position gamma                : {aggregate[COL_GAMMA]:>12.4f}")
    print(f"  Position vega                 : {aggregate[COL_VEGA]:>12.4f}")
    print()
    print("Note: 'Held' = contracts actually held now (Quantity + Quantity Out); market value and Greeks")
    print("      aggregate over Held. Aggregate Trade/Cur Price are net dollars (price * held * 100);")
    print("      aggregate Greeks are position Greeks (per-contract Greek * held * 100). Per-leg 'Realized'")
    print("      is closed-portion P/L in dollars. Quantities: '-' = short.")
    print()


async def print_expected_moves(data_driver: BaseDriver, tickers: List[str]):
    """
    Prints each underlying's expected 1-day move (one standard deviation), using the broker's current implied
    volatility and most recent price: expected move = price * IV * sqrt(1 / 365) (see calculate_expected_move).

    :param data_driver: connected broker driver
    :param tickers: distinct underlying symbols to report on
    """
    print("\nExpected next-day move (1 std dev)")
    print("-" * 60)
    for ticker in tickers:
        implied_vol = await data_driver.get_implied_volatility(ticker)
        recent, error_str = await data_driver.get_most_recent_data(
            ticker, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.TRADES
        )
        if implied_vol is None or implied_vol <= 0.0 or recent is None:
            print(f"  {ticker:<6} expected move unavailable (implied volatility or price missing)")
            continue
        price = recent[0]["close"]
        move = calculate_expected_move(price, implied_vol, 1)
        print(
            f"  {ticker:<6} price {price:>10.2f}  IV {implied_vol:>7.4f}  "
            f"move +/-{move:>7.2f}  ({price - move:.2f} to {price + move:.2f})"
        )
    print()


async def main(parser: argparse.ArgumentParser):
    """Top-level function: unpacks arguments, fetches data, and prints the analysis."""
    args = parser.parse_args()

    basicConfig(filename="position_analyzer.log", level=INFO)

    # A broker must be chosen explicitly; there's no sensible default.
    if not args.ib and not args.schwab:
        print("No broker specified. Pass --ib (Interactive Brokers) or --schwab (Schwab).")
        return

    # Resolve the short --position-type code (e.g. "IC") to the full CSV name (e.g. "Iron Condor").
    position_type = POSITION_TYPE_MAP[args.position_type] if args.position_type else None

    positions_df = load_positions(
        args.positions_file,
        args.symbol,
        args.expiration,
        position_num=args.position_num,
        position_type=position_type,
    )
    if len(positions_df) == 0:
        print("No positions match the given filters.")
        return

    print(f"Analyzing {len(positions_df)} option leg(s) from {args.positions_file}")
    if args.symbol:
        print(f"  Filtered to underlying: {args.symbol}")
    if args.expiration:
        print(f"  Filtered to expiration: {args.expiration}")
    if args.position_num is not None:
        print(f"  Filtered to position #: {args.position_num}")
    if position_type:
        print(f"  Filtered to position type: {position_type} ({args.position_type})")

    print("Please wait...")
    option_manager = OptionDataManager()
    if args.schwab:
        data_driver = SchwabDriver.create()
        connect_hint = "Check your Schwab credentials in .env and the token file, then try again."
    else:
        data_driver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
        connect_hint = "Make sure IB Gateway or TWS is running and logged in to the paper/sim account, then try again."
    option_manager.add_driver(data_driver)

    if not data_driver.is_connected():
        print(f"Could not connect to the broker. {connect_hint}")
        data_driver.disconnect()
        return

    try:
        _, infos = await collect_leg_data(option_manager, positions_df)
        output_df = build_output_dataframe(positions_df, infos)
        aggregate, unrealized_pl, realized_pl = build_aggregate_row(output_df)
        print_analysis(output_df, aggregate, unrealized_pl, realized_pl)

        # Expected next-day move for each distinct underlying among the filtered legs.
        tickers = sorted({SecurityDescriptor(symbol).ticker for symbol in positions_df[CSV_SYMBOL]})
        await print_expected_moves(data_driver, tickers)
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())
    finally:
        data_driver.disconnect()


def build_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.position_analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            Analyze a set of open options positions read from a CSV.

            For each option leg the tool reports the current per-contract price, implied volatility,
            and Greeks (delta, theta, gamma, vega) next to the trade details from the CSV, along with
            the contracts actually held (Quantity + Quantity Out) and realized P/L on any closed
            portion. A final aggregate row summarizes the filtered legs as a whole: net position
            Greeks, net value, unrealized profit/loss, and realized profit/loss.

            A broker must be selected with --ib (Interactive Brokers; requires IB Gateway or TWS
            running locally, paper/sim account) or --schwab (Charles Schwab; requires credentials
            in .env).
            """),
        epilog=textwrap.dedent("""\
            Examples:
              # Analyze every open position in the CSV, using Interactive Brokers for market data
              python -m scripts.position_analyzer --positions-file .\\data\\options_trades_2026.csv --ib

              # Same, but using Schwab for market data
              python -m scripts.position_analyzer --positions-file .\\data\\options_trades_2026.csv --schwab

              # Narrow to a single underlying
              python -m scripts.position_analyzer --positions-file .\\data\\options_trades_2026.csv --ib --symbol SPY

              # Narrow to a single underlying and expiration
              python -m scripts.position_analyzer --positions-file .\\data\\options_trades_2026.csv --ib --symbol QQQ --expiration 20260821

              # Narrow to one position number, or to all positions of a given type
              python -m scripts.position_analyzer --positions-file .\\data\\options_trades_2026.csv --ib --position-num 2
              python -m scripts.position_analyzer --positions-file .\\data\\options_trades_2026.csv --ib --position-type IC

            Notes:
              * The CSV must have columns: 'Position #', 'Date In', 'Position Type', 'Symbol',
                'Quantity', 'Trade Price', 'Date Out', 'Quantity Out', 'Exit Price' (the
                current_positions.csv format). Symbols are IB-style, e.g. SPY-C-20260821-800.0.
              * A negative Quantity indicates a short (sold) leg. 'Quantity Out' accumulates the
                signed closing trades, so contracts still held = Quantity + Quantity Out.
            """),
    )
    parser.add_argument(
        "--positions-file",
        help="Path to a CSV containing open positions.",
        required=True,
        type=str,
    )
    broker_group = parser.add_mutually_exclusive_group()
    broker_group.add_argument(
        "--ib",
        help="Use Interactive Brokers for market data (requires IB Gateway or TWS running locally).",
        action="store_true",
    )
    broker_group.add_argument(
        "--schwab",
        help="Use Charles Schwab for market data (requires Schwab credentials in .env).",
        action="store_true",
    )
    parser.add_argument(
        "--symbol",
        help="Narrow analysis to options sharing this underlying, e.g. SPY or QQQ.",
        required=False,
        default=None,
        type=str,
    )
    parser.add_argument(
        "--expiration",
        help="Narrow analysis to options with this IB-style expiration, e.g. 20260821.",
        required=False,
        default=None,
        type=str,
    )
    parser.add_argument(
        "--position-num",
        help="Narrow analysis to legs with this 'Position #' from the CSV, e.g. 2.",
        required=False,
        default=None,
        type=int,
    )
    parser.add_argument(
        "--position-type",
        help="Narrow analysis to legs of this position type. "
        + ", ".join(f"{code}={name}" for code, name in POSITION_TYPE_MAP.items())
        + ".",
        required=False,
        default=None,
        choices=list(POSITION_TYPE_MAP.keys()),
        type=str,
    )
    return parser


if __name__ == "__main__":
    asyncio.run(main(build_parser()))
