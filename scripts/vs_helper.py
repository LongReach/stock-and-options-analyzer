import asyncio
from importlib.metadata import metadata
from logging import basicConfig, INFO, getLogger
from typing import List, Tuple, Dict, Optional, Callable
from enum import IntEnum
import pandas
from datetime import timedelta
import argparse
import traceback

from core.stock_data_manager import StockDataManager
from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver
from core.common import BarSize, RequestedInfoType
from core.utils import str_to_bar_size
from core.indicators import macd_indicator, rsi_indicator, stochastic_indicator, max, min, crossover

"""
A tool for scanning through weekly charts to find good candidates for vertical spreads trades.
For now, this is just a prototype, scanning for indications of a likely move in a particular direction.
It should be run on a Friday, close to the end of a week.
"""

CLIENT_ID = 21
DB_PATH = "data/market_data.h5"


def test_type_1(price_data_df: pandas.DataFrame) -> int:
    """Scores stock for short call spread potential"""
    stoch_df = stochastic_indicator(price_data_df, k_length=10, k_smoothing=4, d_smoothing=4)
    rsi_df = rsi_indicator(price_data_df, rsi_length=14, ma_length=14)
    macd_df = macd_indicator(price_data_df, fast_length=12, slow_length=26, signal_length=9)

    score = 0
    # Test one: is %k for stoch above 80 in last four bars?
    max_val, idx = max(stoch_df.iloc[-4:], "k")
    score += 1 if max_val >= 80.0 else 0
    # Test two: is there %k crossoover?
    score += 1 if crossover(stoch_df, "k", 80.0, -1.0) else 0
    # Test three: is RSI above 70 in last four bars?
    max_val, idx = max(rsi_df.iloc[-4:], "rsi")
    score += 1 if max_val >= 70.0 else 0
    # Test four: has MACD histogram crossed zero line?
    score += 1 if crossover(macd_df, "histogram", 0.0, -1.0) else 0
    return score


def test_type_2(price_data_df: pandas.DataFrame) -> int:
    """Scores stock for short put spread potential"""
    stoch_df = stochastic_indicator(price_data_df, k_length=10, k_smoothing=4, d_smoothing=4)
    rsi_df = rsi_indicator(price_data_df, rsi_length=14, ma_length=14)
    macd_df = macd_indicator(price_data_df, fast_length=12, slow_length=26, signal_length=9)

    score = 0
    # Test one: is %k for stoch below 20 in last four bars?
    max_val, idx = max(stoch_df.iloc[-4:], "k")
    score += 1 if max_val <= 20.0 else 0
    # Test two: is there %k crossoover?
    score += 1 if crossover(stoch_df, "k", 20.0, 1.0) else 0
    # Test three: is RSI below 30 in last four bars?
    max_val, idx = max(rsi_df.iloc[-4:], "rsi")
    score += 1 if max_val <= 30.0 else 0
    # Test four: has MACD histogram crossed zero line?
    score += 1 if crossover(macd_df, "histogram", 0.0, 1.0) else 0
    return score


def test_type_3(price_data_df: pandas.DataFrame) -> int:
    """Scores stock for iron condor potential"""
    stoch_df = stochastic_indicator(price_data_df, k_length=10, k_smoothing=4, d_smoothing=4)
    rsi_df = rsi_indicator(price_data_df, rsi_length=14, ma_length=14)
    macd_df = macd_indicator(price_data_df, fast_length=12, slow_length=26, signal_length=9)

    score = 0
    # Test one: is there %k crossoover?
    score += 1 if crossover(stoch_df, "k", 50.0, -1.0) else 0
    score += 1 if crossover(stoch_df, "k", 50.0, 1.0) else 0
    # Test two: does RSI cross center line?
    score += 1 if crossover(rsi_df, "rsi", 50.0, -1.0) else 0
    score += 1 if crossover(rsi_df, "rsi", 50.0, 1.0) else 0
    return score


def do_analysis(price_data_df: pandas.DataFrame, test_type: int) -> int:
    """
    Runs a test to see how well the stock/ETF scores

    :param price_data_df: dataframe full of recent price data
    :param test_type: which test
    :return: score
    """

    function_lookup: Dict[int, Callable[[pandas.DataFrame], int]] = {
        1: test_type_1,
        2: test_type_2,
        3: test_type_3,
    }

    scan_func = function_lookup.get(test_type)
    if scan_func:
        return scan_func(price_data_df)

    return 0


async def perform_scan(stock_manager: StockDataManager, test_type: int, bar_size: BarSize, cache_only: bool):
    """
    Scan through stock data, looking for stocks that score well for a particular type of test. A list of candidates
    will be printed.

    :param stock_manager: StockDataManager instance. It's strongly recommended that as much data as possible
        already be cached.
    :param test_type: which test to run (a little hacky for now)
    :param bar_size: daily or weekly bars
    :param cache_only: if True, no price data will be scraped from web. Only cached data will be used.
    """

    score_map: Dict[str, int] = {}

    keys = stock_manager.get_cached_keys()
    for key in keys:
        symbol, stored_bar_size, info_type = stock_manager.get_key_elements(key)
        # We are only interested in trade data of the specified bar size
        if stored_bar_size != bar_size or info_type != RequestedInfoType.TRADES:
            continue

        recent_trades_df = None
        stock_manager.load_data(symbol, bar_size, info_type)
        if not cache_only:
            await stock_manager.scrape_data_smart(symbol, bar_size, info_type, update_recent=True)
        df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
        if len(df) <= 100:
            print(f"Not enough data for {symbol}, skipping...")
            skip_analysis = True
        else:
            recent_trades_df = df.iloc[-100:, :]
            skip_analysis = False
        stock_manager.unload_data(symbol, bar_size, info_type)

        if not skip_analysis:
            print(f"Analyzing {symbol}...")
            score = do_analysis(recent_trades_df, test_type)
            if score > 0:
                score_map[symbol] = score

    sorted_data = dict(sorted(score_map.items(), key=lambda item: item[1], reverse=True))
    print("Scores:\n========================================================")
    for symbol, score in sorted_data.items():
        print(f"Score for symbol {symbol} is {score}")


async def main(parser: argparse.ArgumentParser):
    """Top-level function, unpacks arguments and calls functions that do the work"""
    args = parser.parse_args()

    logger = getLogger(__name__)
    basicConfig(filename="cache_data.log", level=INFO)
    stock_manager = StockDataManager()
    data_driver: Optional[BaseDriver] = None
    if not args.cache_only:
        data_driver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
        success = stock_manager.add_driver(data_driver)
        if not success:
            print("Error connecting to broker data")
            return
    stock_manager.set_log_to_stdout(True)
    stock_manager.set_db_path(DB_PATH)

    try:
        bar_size = BarSize.ONE_WEEK if args.bar_size is None else str_to_bar_size(args.bar_size)
        await perform_scan(stock_manager, args.test_type, bar_size, args.cache_only)
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())

    if data_driver:
        data_driver.disconnect()


parser = argparse.ArgumentParser(description="Tool for finding potential vertical spread opportunities")
parser.add_argument(
    "--test-type", help="Scan type (only 1 - 3 are supported for now)", required=True, default=1, type=int
)
parser.add_argument("--cache-only", help="Do no data scraping from broker", action="store_true")
parser.add_argument(
    "--bar-size",
    help="bar size, e.g. 1m, 1h, 1d, etc.",
    required=False,
    default=None,
    type=str,
)

asyncio.run(main(parser))
