import argparse
import asyncio
import textwrap
import traceback
from datetime import datetime
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

--dte-front and --dte-back accept either a DTE integer or an IB-style date (YYYYMMDD); with --double it analyzes
a double calendar, auto-selecting two strikes around the expected move instead of using --strike; with --auto it
prints a table of candidate back expirations (theta ratio, cost, theta-per-dollar, vega, theta/vega) to choose
from and stops.

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
# --auto: how many days past the front expiration to search for the back, and the smallest theta magnitude
# we'll treat as usable when forming a theta ratio.
AUTO_BACK_WINDOW_DAYS = 90
THETA_EPSILON = 1e-9


def parse_dte_or_date(value: str) -> int:
    """
    Interprets a --dte-front / --dte-back argument as either a plain days-to-expiration integer (e.g. 14) or an
    IB-style date (YYYYMMDD, e.g. 20260821). A date is converted to a DTE relative to today (same convention as
    get_expirations_with_dte), so from here on the rest of the code flow only ever sees a DTE value.

    Raises argparse.ArgumentTypeError on malformed input so argparse prints a friendly message.
    """
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        try:
            target = get_datetime(text)
        except TypeError as ex:
            raise argparse.ArgumentTypeError(str(ex))
        return (target - current_datetime()).days
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{value}' is not a valid DTE (integer) or IB-style date (YYYYMMDD, e.g. 20260821)."
        )


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


def calendar_metrics(front: OptionInfo, back: OptionInfo) -> dict:
    """
    Aggregate Greeks and net debit for one calendar (short 1 front, long 1 back). Aggregate = back - front;
    cost is the net debit in dollars for one front + one back contract (x100).
    """
    return {
        "delta": back.delta - front.delta,
        "theta": back.theta - front.theta,
        "gamma": back.gamma - front.gamma,
        "vega": back.vega - front.vega,
        "cost": (back.price - front.price) * CONTRACT_MULTIPLIER,
    }


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
    """Pretty-prints the calendar-spread analysis. Returns this calendar's metrics dict for aggregation."""
    iv_ratio = front.implied_volatility / back.implied_volatility if back.implied_volatility > 0.0 else float("nan")

    metrics = calendar_metrics(front, back)
    total_cost = metrics["cost"]

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
    print(f"  Aggregate delta         : {metrics['delta']:>10.4f}")
    print(f"  Aggregate theta         : {metrics['theta']:>10.4f}")
    print(f"  Aggregate gamma         : {metrics['gamma']:>10.5f}")
    print(f"  Aggregate vega          : {metrics['vega']:>10.4f}")
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
    return metrics


def print_double_header(symbol: str, right: str, info: dict, front_dte: int):
    """Pretty-prints the shared context for a double calendar: spot, expected move, and the two chosen strikes."""
    right_word = "call" if right == "C" else "put"
    spot, move = info["spot"], info["move"]
    strike_low, strike_high = info["strike_low"], info["strike_high"]
    print(f"\nDouble calendar: {symbol} {right_word}s @ strikes {strike_low:g} and {strike_high:g}")
    print("#" * 60)
    print(f"  Underlying price        : {spot:>10.2f}")
    print(f"  ATM straddle (strike {info['atm_strike']:g}) : {move:>10.2f}")
    print(f"  Expected move ({front_dte:>3} DTE) : +/-{move:>8.2f}  ({spot - move:.2f} to {spot + move:.2f})")
    print(f"  Selected strikes        : {strike_low:g} (low)  /  {strike_high:g} (high)")
    print("  Expected move = front-expiration ATM straddle (call + put mid); strikes sit near +/- 1 EM.")


def print_double_summary(metrics_low: dict, metrics_high: dict):
    """Pretty-prints the combined position across both calendars of a double calendar."""
    print("\nCombined double calendar (both strikes)")
    print("=" * 60)
    print(f"  Aggregate delta         : {metrics_low['delta'] + metrics_high['delta']:>10.4f}")
    print(f"  Aggregate theta         : {metrics_low['theta'] + metrics_high['theta']:>10.4f}")
    print(f"  Aggregate gamma         : {metrics_low['gamma'] + metrics_high['gamma']:>10.5f}")
    print(f"  Aggregate vega          : {metrics_low['vega'] + metrics_high['vega']:>10.4f}")
    print(f"  Total cost (net debit)  : {metrics_low['cost'] + metrics_high['cost']:>10.2f}")
    print()
    print("Notes: combined Greeks and cost are the sum of both calendars (one contract each, x100). The per-")
    print("       strike max profits shown above occur at different underlying prices and are not achievable")
    print("       simultaneously, so they are intentionally not summed here.")
    print()


def print_auto_back_table(auto_info: dict):
    """
    Pretty-prints the --auto table of candidate back expirations. Each row is a one-front/one-back calendar
    evaluated at the reference strike; the tool does not choose for the user, it just lays out the trade-offs.
    """
    ref = auto_info["ref_strike"]
    header = (
        f"  {'Back exp':<10}{'DTE':>5}{'Theta ratio':>13}{'Net cost':>11}{'Agg theta':>11}"
        f"{'Theta/$':>10}{'Vega':>10}{'Theta/Vega':>12}"
    )
    front_str = get_datetime_as_str(auto_info["front_exp"], date_only=True)
    print(f"\nCandidate back expirations for a calendar at strike {ref:g}")
    print(f"Front (sold) leg fixed at {front_str} ({auto_info['front_dte']} DTE)")
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for c in sorted(auto_info["candidates"], key=lambda item: item["dte"]):
        exp_str = get_datetime_as_str(c["exp"], date_only=True)
        print(
            f"  {exp_str:<10}{c['dte']:>5}{c['theta_ratio']:>13.2f}{c['cost']:>11.2f}{c['agg_theta']:>11.4f}"
            f"{c['theta_per_dollar']:>10.4f}{c['vega']:>10.4f}{c['theta_vega_ratio']:>12.4f}"
        )
    print()
    print("  All columns are for one front + one back contract at the reference strike. Theta ratio =")
    print("  |front theta| / |back theta|; Theta/$ = daily dollar theta per dollar of net debit; Vega and")
    print("  Agg theta are (back - front); Theta/Vega = aggregate theta per unit of aggregate vega. The theta")
    print(f"  ratio rises with back DTE (search capped at front + {AUTO_BACK_WINDOW_DAYS} days); pick the back")
    print("  expiration that best fits your own theta / cost / vega trade-off.")


async def analyze_strike(
    driver: BaseDriver,
    manager: OptionDataManager,
    symbol: str,
    right: str,
    strike: float,
    front_exp: str,
    front_dte: int,
    back_exp: str,
    back_dte: int,
) -> Optional[dict]:
    """
    Fetches both legs for one strike, prints the calendar analysis, and returns the calendar's metrics dict
    (for aggregation in double-calendar mode). Returns None if either leg can't be fetched.
    """
    front = await manager.get_option_info(symbol, front_exp, right, strike)
    back = await manager.get_option_info(symbol, back_exp, right, strike)
    if front is None or back is None:
        return None

    # Where today's IV ratio sits over the last 20 days (IV reconstructed from price history; either broker).
    percentile, percentile_detail = None, None
    if back.implied_volatility > 0.0:
        today_ratio = front.implied_volatility / back.implied_volatility
        percentile, percentile_detail = await iv_ratio_percentile(
            driver, symbol, right, strike, front_exp, back_exp, today_ratio
        )

    return print_analysis(symbol, right, strike, front, front_dte, back, back_dte, percentile, percentile_detail)


async def choose_double_strikes(
    driver: BaseDriver,
    manager: OptionDataManager,
    symbol: str,
    front_exp: str,
    shared_strikes: List[float],
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Picks the two strikes for a double calendar: one near spot - expected_move (from the strikes at or below
    spot) and one near spot + expected_move (from the strikes at or above spot).

    The expected move is the FRONT-expiration ATM straddle price (see OptionDataManager.get_atm_straddle_move).
    That is the market's own priced move to the front expiration and is what platforms like thinkorswim
    approximate, so it tracks their expected-move figure more closely than the closed-form
    spot * IV * sqrt(DTE/365) estimate (which reads ~10-15% higher).

    :return: ({"spot", "atm_strike", "move", "strike_low", "strike_high"}, None) or (None, error_str)
    """
    recent, price_err = await driver.get_most_recent_data(
        symbol, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.TRADES
    )
    if recent is None or price_err:
        return None, f"Could not get the underlying price for {symbol} to place the double-calendar strikes."
    spot = recent[0]["close"]

    move, atm_strike, straddle_err = await manager.get_atm_straddle_move(symbol, front_exp)
    if straddle_err or move <= 0.0:
        return None, f"Could not size the expected move for {symbol}: {straddle_err or 'no straddle price'}"

    at_or_below = [s for s in shared_strikes if s <= spot]
    at_or_above = [s for s in shared_strikes if s >= spot]
    strike_low = get_best_strike(at_or_below or shared_strikes, spot - move)
    strike_high = get_best_strike(at_or_above or shared_strikes, spot + move)

    if abs(strike_low - strike_high) <= STRIKE_TOLERANCE:
        return None, "Could not find two distinct strikes around the expected move for a double calendar."

    return {
        "spot": spot,
        "atm_strike": atm_strike,
        "move": move,
        "strike_low": strike_low,
        "strike_high": strike_high,
    }, None


async def gather_auto_back_candidates(
    driver: BaseDriver,
    manager: OptionDataManager,
    symbol: str,
    right: str,
    front_exp: str,
    front_dte: int,
    front_dt: datetime,
    front_strikes: List[float],
    expirations: List[Tuple[str, int]],
    strike_hint: Optional[float],
) -> Tuple[Optional[dict], Optional[str]]:
    """
    Builds the --auto table of candidate back expirations. For each expiration after the front (within
    AUTO_BACK_WINDOW_DAYS of it) it evaluates a one-front/one-back calendar at a reference strike and records
    the metrics a trader weighs when choosing a back: theta ratio (|front theta| / |back theta|), net cost,
    aggregate theta, theta-per-dollar (daily dollar theta per dollar of net debit), aggregate vega, and the
    theta/vega ratio. It does NOT pick a winner -- the user reads the table and decides.

    The reference strike is the user's --strike if it exists on the front expiration, otherwise the front ATM
    strike; the relative comparison across backs is nearly strike-independent.

    :return: ({"ref_strike", "candidates"}, None) or (None, error_str)
    """
    recent, price_err = await driver.get_most_recent_data(
        symbol, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.TRADES
    )
    if recent is None or price_err:
        return None, f"Could not get the underlying price for {symbol} to build the back-expiration table."
    spot = recent[0]["close"]

    if strike_hint is not None and strike_in(front_strikes, strike_hint):
        ref_strike = strike_hint
    else:
        ref_strike = get_best_strike(front_strikes, spot)

    front = await manager.get_option_info(symbol, front_exp, right, ref_strike)
    if front is None or abs(front.theta) <= THETA_EPSILON:
        return None, f"Could not get a usable front-option theta for {symbol} to build the back-expiration table."

    candidates = []
    for exp, dte in expirations:
        if get_datetime(exp) <= front_dt or dte > front_dte + AUTO_BACK_WINDOW_DAYS:
            continue
        back_strikes, _ = await manager.get_strikes(symbol, exp, right)
        if not strike_in(back_strikes, ref_strike):
            continue
        back = await manager.get_option_info(symbol, exp, right, ref_strike)
        if back is None or abs(back.theta) <= THETA_EPSILON:
            continue
        metrics = calendar_metrics(front, back)
        cost, agg_theta, vega = metrics["cost"], metrics["theta"], metrics["vega"]
        candidates.append(
            {
                "exp": exp,
                "dte": dte,
                "theta_ratio": abs(front.theta) / abs(back.theta),
                "cost": cost,
                "agg_theta": agg_theta,
                # Daily dollar theta (x100) per dollar of net debit.
                "theta_per_dollar": (
                    (agg_theta * CONTRACT_MULTIPLIER / cost) if abs(cost) > STRIKE_TOLERANCE else float("nan")
                ),
                "vega": vega,
                "theta_vega_ratio": (agg_theta / vega) if abs(vega) > THETA_EPSILON else float("nan"),
            }
        )

    if not candidates:
        return None, "Could not evaluate any back expirations for --auto (no matching strike with theta)."

    return {
        "ref_strike": ref_strike,
        "front_exp": front_exp,
        "front_dte": front_dte,
        "candidates": candidates,
    }, None


async def run(
    driver: BaseDriver,
    manager: OptionDataManager,
    symbol: str,
    right: str,
    strike_arg: Optional[float],
    dte_front: int,
    dte_back: Optional[int],
    double: bool,
    auto: bool,
) -> Optional[str]:
    """Does the analysis. Returns an error string to print, or None on success."""
    expirations = await get_expirations_with_dte(manager, symbol)
    if not expirations:
        return f"Could not find any option expirations for {symbol}."

    # Front expiration: closest available to the requested front DTE.
    front_exp, front_dte = closest_by_dte(expirations, dte_front)
    front_dt = get_datetime(front_exp)
    front_strikes, _ = await manager.get_strikes(symbol, front_exp, right)

    # --auto: only present a table of candidate back expirations and stop; the user picks one and re-runs.
    if auto:
        strike_hint = None if double else strike_arg
        auto_info, error_str = await gather_auto_back_candidates(
            driver, manager, symbol, right, front_exp, front_dte, front_dt, front_strikes, expirations, strike_hint
        )
        if error_str:
            return error_str
        print_auto_back_table(auto_info)
        return None

    # Back expiration: closest to a requested DTE, or the first available expiration after the front.
    if dte_back is not None:
        back_exp, back_dte = closest_by_dte(expirations, dte_back)
    else:
        later = [(exp, dte) for exp, dte in expirations if get_datetime(exp) > front_dt]
        if not later:
            return "Could not find a back-dated expiration after the front-dated expiration."
        back_exp, back_dte = later[0]

    if get_datetime(back_exp) <= front_dt:
        return "The back-dated option's expiration does not come after the front-dated option's expiration."

    back_strikes, _ = await manager.get_strikes(symbol, back_exp, right)
    shared = common_strikes(front_strikes, back_strikes)
    if not shared:
        return "Could not find matching strikes for the front- and back-dated options."

    if double:
        # Double calendar: --strike is ignored; strikes are chosen around the expected move.
        if strike_arg is not None:
            print("Note: --strike is ignored when --double is used.")
        info, error_str = await choose_double_strikes(driver, manager, symbol, front_exp, shared)
        if error_str:
            return error_str
        print_double_header(symbol, right, info, front_dte)
        metrics = []
        for strike in (info["strike_low"], info["strike_high"]):
            result = await analyze_strike(
                driver, manager, symbol, right, strike, front_exp, front_dte, back_exp, back_dte
            )
            if result is None:
                return "Could not find matching strikes for the front- and back-dated options."
            metrics.append(result)
        print_double_summary(metrics[0], metrics[1])
        return None

    # Single calendar: determine the strike, requiring it to exist for both expirations.
    if strike_arg is not None:
        if not strike_in(front_strikes, strike_arg):
            return f"The specified strike {strike_arg:g} does not exist for the front-dated {symbol} option."
        if not strike_in(back_strikes, strike_arg):
            return "Could not find matching strikes for the front- and back-dated options."
        strike = strike_arg
    else:
        recent, price_err = await driver.get_most_recent_data(
            symbol, bar_size=BarSize.ONE_DAY, request_info_type=RequestedInfoType.TRADES
        )
        if recent is None or price_err:
            return f"Could not get the underlying price for {symbol} to choose an at-the-money strike."
        strike = get_best_strike(shared, recent[0]["close"])

    result = await analyze_strike(driver, manager, symbol, right, strike, front_exp, front_dte, back_exp, back_dte)
    if result is None:
        return "Could not find matching strikes for the front- and back-dated options."
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
        error_str = await run(
            driver,
            manager,
            args.symbol.upper(),
            right,
            args.strike,
            args.dte_front,
            args.dte_back,
            args.double,
            args.auto,
        )
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

            --dte-front and --dte-back each accept either a days-to-expiration integer (e.g. 14) or an
            IB-style date (YYYYMMDD, e.g. 20260821); a date is converted to a DTE relative to today.

            With --double, a double calendar is analyzed instead: --strike is ignored and two strikes are
            chosen around the +/- 1 expected-move boundaries (the expected move is the front expiration's
            ATM straddle price).

            With --auto, the tool prints a table of candidate back expirations -- theta ratio, net cost,
            aggregate theta, theta-per-dollar, vega, and theta/vega -- and stops, letting you pick the back
            that best fits your trade-off. The front still snaps to --dte-front; --dte-back is unused.

            A broker must be selected with --ib (Interactive Brokers; requires IB Gateway or TWS running
            locally, paper/sim account) or --schwab (Charles Schwab; requires credentials in .env).
            """),
        epilog=textwrap.dedent("""\
            Examples:
              # ATM call calendar on SPY: ~7-DTE front, next expiration after it as the back (IB)
              python -m scripts.calendar_helper --ib --symbol SPY --right C --dte-front 7

              # Put calendar with explicit strike and back DTE (Schwab)
              python -m scripts.calendar_helper --schwab --symbol QQQ --right P --strike 440 --dte-front 14 --dte-back 45

              # Front/back given as explicit expiration dates instead of DTEs (IB)
              python -m scripts.calendar_helper --ib --symbol SPY --right C --dte-front 20260821 --dte-back 20260918

              # Double call calendar: strikes auto-selected around the expected move (Schwab)
              python -m scripts.calendar_helper --schwab --symbol QQQ --right C --dte-front 14 --dte-back 45 --double

              # Print a table of candidate back expirations to choose from; ~14-DTE front (IB)
              python -m scripts.calendar_helper --ib --symbol SPY --right C --dte-front 14 --auto
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
        "--strike",
        type=float,
        default=None,
        help="Strike to use; if omitted, the closest-to-ATM strike is chosen. Ignored when --double is used.",
    )
    parser.add_argument(
        "--dte-front",
        required=True,
        type=parse_dte_or_date,
        help="Front (sold) expiration as a DTE integer (e.g. 14) or IB-style date (YYYYMMDD, e.g. 20260821); "
        "the closest available expiration is used.",
    )
    parser.add_argument(
        "--dte-back",
        type=parse_dte_or_date,
        default=None,
        help="Back (bought) expiration as a DTE integer or IB-style date (YYYYMMDD); the closest available is "
        "used. If omitted, the first expiration after the front is used.",
    )
    parser.add_argument(
        "--double",
        action="store_true",
        help="Analyze a double calendar: --strike is ignored and two strikes are chosen around the expected move.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Print a table of candidate back expirations (theta ratio, net cost, agg theta, theta-per-dollar, "
        "vega, theta/vega) for you to choose from, then stop. The front snaps to --dte-front; --dte-back is unused.",
    )
    return parser


if __name__ == "__main__":
    asyncio.run(main(build_parser().parse_args()))
