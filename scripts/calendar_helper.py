import argparse
import asyncio
import textwrap
import traceback
from typing import List, Optional, Tuple

from core.base_driver import BaseDriver
from core.common import BarSize, RequestedInfoType, OptionInfo
from core.option_data_manager import OptionDataManager
from core.options_data import OptionDataException
from core.ib.ib_driver import IBDriver
from core.schwab.schwab_driver import SchwabDriver
from core.utils import (
    current_datetime,
    get_datetime,
    get_datetime_as_str,
    get_best_strike,
    get_full_symbol_name,
    black_scholes_price,
    implied_volatility_from_price,
)

r"""
Analyzes a potential calendar spread (sell a near-dated option, buy a longer-dated one at the same strike and
right) and prints useful metrics: the two expirations/DTEs, the front/back implied-volatility ratio, aggregate
Greeks, net cost, and an estimated maximum profit. It also reports where today's IV ratio sits as a percentile
of the last 20 days, with the historical IV reconstructed from option/underlying price history (works on both
brokers, since neither serves per-option IV history directly).

Setup and usage:
------------------------
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.calendar_helper --help    # full instruction manual with examples
"""

CLIENT_ID = 27
# Standard US equity-option contract multiplier: 1 contract controls 100 shares.
CONTRACT_MULTIPLIER = 100
# How far out (days) to look for expirations, and how many days of history to sample for the IV percentile.
MAX_DAYS_OUT = 730
IV_HISTORY_DAYS = 20
STRIKE_TOLERANCE = 1e-6


async def get_expirations_with_dte(manager: OptionDataManager, symbol: str) -> List[Tuple[str, int]]:
    """Returns [(IB-style expiration, dte), ...] sorted by expiration date (soonest first)."""
    expirations = await manager.get_expirations(symbol, 0, MAX_DAYS_OUT)
    now = current_datetime()
    out = [(exp, (get_datetime(exp) - now).days) for exp in expirations]
    out.sort(key=lambda item: get_datetime(item[0]))
    return out


def closest_by_dte(expirations: List[Tuple[str, int]], desired_dte: int) -> Tuple[str, int]:
    """Picks the (expiration, dte) whose dte is closest to desired_dte."""
    return min(expirations, key=lambda item: abs(item[1] - desired_dte))


def strike_in(strikes: List[float], target: float) -> bool:
    """True if target is present in strikes (within a small float tolerance)."""
    return any(abs(s - target) <= STRIKE_TOLERANCE for s in strikes)


def common_strikes(front_strikes: List[float], back_strikes: List[float]) -> List[float]:
    """Strikes available in both expirations (front-list values), preserving order."""
    return [s for s in front_strikes if strike_in(back_strikes, s)]


def date_close_map(historical_data) -> dict:
    """Maps each bar's date -> close price."""
    bar_dicts = historical_data.get_bar_data_as_dicts()
    timestamps = historical_data.timestamps
    return {timestamps[i].date(): bar_dicts[i]["close"] for i in range(len(bar_dicts))}


async def _daily_closes(driver: BaseDriver, symbol_full: str) -> Tuple[Optional[dict], Optional[str]]:
    """Fetches IV_HISTORY_DAYS daily TRADES bars for a symbol and returns a {date: close} map."""
    hist, error_str = await driver.get_historical_data(
        symbol_full,
        num_bars=IV_HISTORY_DAYS,
        bar_size=BarSize.ONE_DAY,
        request_info_type=RequestedInfoType.TRADES,
    )
    if error_str or hist.is_empty():
        return None, error_str or "no data"
    return date_close_map(hist), None


async def iv_ratio_percentile(
    driver: BaseDriver,
    symbol: str,
    right: str,
    strike: float,
    front_exp: str,
    back_exp: str,
    today_ratio: float,
) -> Tuple[Optional[float], Optional[str]]:
    """
    Computes where today's front/back IV ratio sits as a percentile of the last IV_HISTORY_DAYS days.

    Neither IB nor Schwab serves per-option historical IV, but both serve option and underlying price history.
    So for each historical day we reconstruct each contract's IV by inverting Black-Scholes from that day's
    option close, the underlying close, the strike, and the days-to-expiration as of that day, then take the
    front/back ratio. Works for either broker.

    :return: (percentile 0-100 with a detail string, or (None, reason) if it can't be computed)
    """
    is_call = right == "C"
    front_symbol = get_full_symbol_name(symbol, is_option=True, is_call=is_call, expiration=front_exp, strike=strike)
    back_symbol = get_full_symbol_name(symbol, is_option=True, is_call=is_call, expiration=back_exp, strike=strike)

    front_closes, front_err = await _daily_closes(driver, front_symbol)
    back_closes, back_err = await _daily_closes(driver, back_symbol)
    underlying_closes, underlying_err = await _daily_closes(driver, symbol)
    if front_err or back_err or underlying_err:
        return None, "historical price data not available for these contracts"

    front_exp_date = get_datetime(front_exp).date()
    back_exp_date = get_datetime(back_exp).date()

    ratios = []
    for day in sorted(set(front_closes) & set(back_closes) & set(underlying_closes)):
        spot = underlying_closes[day]
        front_iv = implied_volatility_from_price(front_closes[day], spot, strike, (front_exp_date - day).days, is_call)
        back_iv = implied_volatility_from_price(back_closes[day], spot, strike, (back_exp_date - day).days, is_call)
        if front_iv is not None and back_iv is not None and back_iv > 0.0:
            ratios.append(front_iv / back_iv)

    if not ratios:
        return None, "could not derive historical IV for enough days"

    below_or_equal = sum(1 for r in ratios if r <= today_ratio)
    return 100.0 * below_or_equal / len(ratios), f"derived from {len(ratios)} of the last {IV_HISTORY_DAYS} days"


def print_analysis(
    symbol: str,
    right: str,
    strike: float,
    front: OptionInfo,
    front_dte: int,
    back: OptionInfo,
    back_dte: int,
    percentile: Optional[float],
    percentile_detail: Optional[str],
):
    """Pretty-prints the calendar-spread analysis."""
    iv_ratio = front.implied_volatility / back.implied_volatility if back.implied_volatility > 0.0 else float("nan")

    # Calendar = short 1 front, long 1 back: aggregate = back - front.
    agg_delta = back.delta - front.delta
    agg_theta = back.theta - front.theta
    agg_gamma = back.gamma - front.gamma
    agg_vega = back.vega - front.vega

    # Net debit for one front + one back contract.
    total_cost = (back.price - front.price) * CONTRACT_MULTIPLIER

    # Estimated max profit: at front expiration with the underlying at the strike, the front expires worthless
    # and the back still has (back_dte - front_dte) days of life. Value the back with Black-Scholes at that
    # point (spot = strike, its current IV) and subtract the debit paid.
    back_value_at_front_expiry = black_scholes_price(
        strike, strike, back.implied_volatility, back_dte - front_dte, is_call=(right == "C")
    )
    max_profit = back_value_at_front_expiry * CONTRACT_MULTIPLIER - total_cost

    right_word = "call" if right == "C" else "put"
    print(f"\nCalendar spread: {symbol} {right_word} @ strike {strike:g}")
    print("=" * 60)
    print(f"  Front (sold) : {get_datetime_as_str(front.expiration, date_only=True)}  ({front_dte} DTE)")
    print(f"  Back (bought): {get_datetime_as_str(back.expiration, date_only=True)}  ({back_dte} DTE)")
    print()
    print(f"  Front IV                : {front.implied_volatility:>10.4f}")
    print(f"  Back IV                 : {back.implied_volatility:>10.4f}")
    print(f"  IV ratio (front / back) : {iv_ratio:>10.4f}")
    if percentile is not None:
        print(f"  IV ratio percentile     : {percentile:>9.1f}%  ({percentile_detail})")
    elif percentile_detail is not None:
        print(f"  IV ratio percentile     : unavailable ({percentile_detail})")
    print()
    print(f"  Aggregate delta         : {agg_delta:>10.4f}")
    print(f"  Aggregate theta         : {agg_theta:>10.4f}")
    print(f"  Aggregate gamma         : {agg_gamma:>10.5f}")
    print(f"  Aggregate vega          : {agg_vega:>10.4f}")
    print()
    print(f"  Front price / Back price: {front.price:.2f} / {back.price:.2f}")
    print(f"  Total cost (net debit)  : {total_cost:>10.2f}")
    print(f"  Estimated max profit    : {max_profit:>10.2f}")
    print()
    print("Notes: aggregate Greeks are (back - front), i.e. long 1 back and short 1 front. Total cost and max")
    print("       profit are dollars for one front + one back contract (x100). Max profit is a Black-Scholes")
    print("       estimate at front expiration with the underlying at the strike and the back's current IV.")
    print("       The IV-ratio percentile reconstructs each day's IV from option/underlying closes via")
    print("       Black-Scholes inversion (European, no dividends), so it's an approximation.")
    print()


async def run(
    driver: BaseDriver,
    manager: OptionDataManager,
    symbol: str,
    right: str,
    strike_arg: Optional[float],
    dte_front: int,
    dte_back: Optional[int],
) -> Optional[str]:
    """Does the analysis. Returns an error string to print, or None on success."""
    expirations = await get_expirations_with_dte(manager, symbol)
    if not expirations:
        return f"Could not find any option expirations for {symbol}."

    # Front expiration: closest available to the requested front DTE.
    front_exp, front_dte = closest_by_dte(expirations, dte_front)
    front_dt = get_datetime(front_exp)

    # Back expiration: closest to requested back DTE, or the first expiration after the front if not specified.
    if dte_back is not None:
        back_exp, back_dte = closest_by_dte(expirations, dte_back)
    else:
        later = [(exp, dte) for exp, dte in expirations if get_datetime(exp) > front_dt]
        if not later:
            return "Could not find a back-dated expiration after the front-dated expiration."
        back_exp, back_dte = later[0]

    if get_datetime(back_exp) <= front_dt:
        return "The back-dated option's expiration does not come after the front-dated option's expiration."

    # Determine the strike, requiring it to exist for both expirations.
    front_strikes, _ = await manager.get_strikes(symbol, front_exp, right)
    back_strikes, _ = await manager.get_strikes(symbol, back_exp, right)

    if strike_arg is not None:
        if not strike_in(front_strikes, strike_arg):
            return f"The specified strike {strike_arg:g} does not exist for the front-dated {symbol} option."
        if not strike_in(back_strikes, strike_arg):
            return "Could not find matching strikes for the front- and back-dated options."
        strike = strike_arg
    else:
        shared = common_strikes(front_strikes, back_strikes)
        if not shared:
            return "Could not find matching strikes for the front- and back-dated options."
        recent, price_err = await driver.get_most_recent_data(
            symbol, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.TRADES
        )
        if recent is None or price_err:
            return f"Could not get the underlying price for {symbol} to choose an at-the-money strike."
        strike = get_best_strike(shared, recent[0]["close"])

    # Fetch price + Greeks + IV for both legs.
    front = await manager.get_option_info(symbol, front_exp, right, strike)
    back = await manager.get_option_info(symbol, back_exp, right, strike)
    if front is None or back is None:
        return "Could not find matching strikes for the front- and back-dated options."

    # Where today's IV ratio sits over the last 20 days (IV reconstructed from price history; either broker).
    percentile, percentile_detail = None, None
    if back.implied_volatility > 0.0:
        today_ratio = front.implied_volatility / back.implied_volatility
        percentile, percentile_detail = await iv_ratio_percentile(
            driver, symbol, right, strike, front_exp, back_exp, today_ratio
        )

    print_analysis(symbol, right, strike, front, front_dte, back, back_dte, percentile, percentile_detail)
    return None


async def main(args: argparse.Namespace):
    """Validates arguments, connects to the broker, and runs the analysis."""
    if not args.ib and not args.schwab:
        print("No broker specified. Pass --ib (Interactive Brokers) or --schwab (Schwab).")
        return

    right = args.right.upper()
    if right not in ("P", "C"):
        print(f"Invalid --right '{args.right}'. Use 'P' for put or 'C' for call.")
        return

    if args.schwab:
        driver = SchwabDriver.create()
        connect_hint = "Check your Schwab credentials in .env and the token file, then try again."
    else:
        driver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
        connect_hint = "Make sure IB Gateway or TWS is running and logged in to the paper/sim account."

    manager = OptionDataManager()
    manager.add_driver(driver)
    if not driver.is_connected():
        print(f"Could not connect to the broker. {connect_hint}")
        driver.disconnect()
        return

    try:
        error_str = await run(driver, manager, args.symbol.upper(), right, args.strike, args.dte_front, args.dte_back)
        if error_str:
            print(error_str)
    except OptionDataException as ex:
        print(f"Could not complete the analysis: {ex}")
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())
    finally:
        driver.disconnect()


def build_parser() -> argparse.ArgumentParser:
    """Builds the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.calendar_helper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            Analyze a potential calendar spread: sell a near-dated option and buy a longer-dated one at the
            same strike and right. Reports the two expirations/DTEs, the front/back implied-volatility ratio,
            aggregate Greeks, net cost, and an estimated maximum profit. Also reports where today's IV ratio
            sits as a percentile of the last 20 days (historical IV reconstructed from price history; works on
            IB or Schwab).

            A broker must be selected with --ib (Interactive Brokers; requires IB Gateway or TWS running
            locally, paper/sim account) or --schwab (Charles Schwab; requires credentials in .env).
            """),
        epilog=textwrap.dedent("""\
            Examples:
              # ATM call calendar on SPY: ~7-DTE front, next expiration after it as the back (IB)
              python -m scripts.calendar_helper --ib --symbol SPY --right C --dte-front 7

              # Put calendar with explicit strike and back DTE (Schwab)
              python -m scripts.calendar_helper --schwab --symbol QQQ --right P --strike 440 --dte-front 14 --dte-back 45
            """),
    )
    broker_group = parser.add_mutually_exclusive_group()
    broker_group.add_argument(
        "--ib", action="store_true", help="Use Interactive Brokers (IB Gateway/TWS running locally)."
    )
    broker_group.add_argument("--schwab", action="store_true", help="Use Charles Schwab (credentials in .env).")
    parser.add_argument("--symbol", required=True, help="Underlying stock/ETF ticker, e.g. SPY.")
    parser.add_argument("--right", required=True, help="Option right: 'P' for put or 'C' for call.")
    parser.add_argument(
        "--strike", type=float, default=None, help="Strike to use; if omitted, the closest-to-ATM strike is chosen."
    )
    parser.add_argument(
        "--dte-front",
        required=True,
        type=int,
        help="Days to expiration for the front (sold) option; the closest available is used.",
    )
    parser.add_argument(
        "--dte-back",
        type=int,
        default=None,
        help="Days to expiration for the back (bought) option; the closest available is used. If omitted, the first expiration after the front is used.",
    )
    return parser


if __name__ == "__main__":
    asyncio.run(main(build_parser().parse_args()))
