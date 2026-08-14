import asyncio
from importlib.metadata import metadata
from logging import basicConfig, INFO, getLogger
from typing import List, Tuple, Dict, Optional
from enum import IntEnum

import pandas
from datetime import timedelta
import argparse
import textwrap
import traceback

from core.common import RequestedInfoType, ScrapeLevel
from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager
from core.stock_data import StockData
from core.utils import str_to_bar_size, get_datetime_as_str, current_datetime, get_bars_between_times, bar_size_to_time

r"""
Utility for collecting price (or other) data for a particular security, in bar form, then caching it to disk.

Setup and usage:
------------------------
d:
cd CodingProjects\Python\TWS2025
conda activate options_2025_1
python -m scripts.cache_data --help    # full instruction manual with examples
"""

CLIENT_ID = 13
DB_PATH = "data/market_data.h5"
NUM_ACCEPTABLE_BARS = 200
GENERAL_ACCEPTABLE_RECENCY = {
    BarSize.ONE_MINUTE: 1,
    BarSize.TWO_MINUTES: 1,
    BarSize.FIVE_MINUTES: 1,
    BarSize.FIFTEEN_MINUTES: 1,
    BarSize.ONE_HOUR: 1,
    BarSize.FOUR_HOURS: 1,
    BarSize.ONE_DAY: 2,
    BarSize.ONE_WEEK: 1,
    BarSize.ONE_MONTH: 1,
}
MAX_ERRORS_ALLOWED = 10


def print_df(df):
    if df is None:
        print("ERROR: no dataframe")
        return
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head())
    print("Tail:")
    print(df.tail())


async def do_cache(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size: BarSize,
    info_type: RequestedInfoType,
    scrape_level: ScrapeLevel,
) -> Tuple[Optional[pandas.DataFrame], Optional[str]]:
    """
    Scrapes and caches data for a single stock or ETF.

    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type: type of chart data, e.g. trades or implied volatility
    :param scrape_level: full, limited, recent, or none
    :return:
    """
    if scrape_level == ScrapeLevel.FULL:
        # Go all the way back
        start_date = "19700101"
    elif scrape_level == ScrapeLevel.LIMITED:
        # Go 200 bars back only
        start_date = get_datetime_as_str(current_datetime() - bar_size_to_time(bar_size) * 200, date_only=True)
    else:
        # Recent only
        start_date = ""

    df = None
    error_msg: Optional[str] = None
    try:
        # We'll be using cached data
        stock_manager.load_data(symbol, bar_size, info_type)

        if scrape_level != ScrapeLevel.NONE:
            success, error_str = await stock_manager.scrape_data_smart(
                symbol, bar_size, info_type, start_date=start_date, update_recent=True
            )
            if not success:
                error_msg = error_str
            stock_manager.save_data(symbol, bar_size, info_type)
        df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
        stock_manager.unload_data(symbol, bar_size, info_type)
    except Exception as ex:
        print(f"Exception: {ex}")
        print(traceback.format_exc())
        error_msg = f"Exception: {error_msg}"

    return df, error_msg


async def show_cache_contents(stock_manager: StockDataManager, bar_size_str: str, info_type_str: str):
    """Prints information about data currently in cache"""
    match_bar_size = str_to_bar_size(bar_size_str) if bar_size_str else None
    match_info_type = StockData.get_info_type(info_type_str) if info_type_str else None

    stock_manager.set_log_to_stdout(False)
    data_driver: Optional[BaseDriver] = stock_manager.driver
    keys: List[str] = stock_manager.get_cached_keys()
    print("Cache contents\n======================================")
    for key in keys:
        symbol, bar_size, info_type = StockDataManager.get_key_elements(key)
        if match_bar_size and bar_size != match_bar_size:
            continue
        if match_info_type and info_type != match_info_type:
            continue

        metadata = stock_manager.get_metadata(symbol, bar_size, info_type)
        if metadata is None:
            print(f"No metadata for {key}")
            continue

        current_dt = current_datetime()
        num_bars, earliest_dt, latest_dt, head_dt = metadata
        bars_then_to_now = get_bars_between_times(latest_dt, current_datetime(), bar_size)

        earliest_date_str = get_datetime_as_str(earliest_dt)
        latest_date_str = get_datetime_as_str(latest_dt)
        out_line = f"Have entry for {key}, num bars: {num_bars}, earliest data: {earliest_date_str}, latest date: {latest_date_str}"
        if bars_then_to_now > 1:
            out_line += f" (newer data exists)"
        head_dt = StockDataManager.get_adjusted_head_timestamp(head_dt, bar_size)
        if head_dt is not None and earliest_dt < head_dt:
            out_line += f", available data begins at: {get_datetime_as_str(head_dt)} (not matched)"
        elif head_dt is None:
            out_line += ", no head timestamp established"
        print(out_line)

        stock_manager.unload_data(symbol, bar_size, info_type)


async def show_single_stock_data(stock_manager: StockDataManager, symbol: str, bar_size_str: str, info_type_str: str):
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)

    print(f"Loading data for {symbol}...")
    result = stock_manager.load_data(symbol, bar_size, info_type)
    if not result:
        print(f"Could not load data for symbol {symbol}")
        return

    num_bars, start_dt, end_dt, head_dt = stock_manager.get_metadata(symbol, bar_size, info_type)
    print(f"Metadata: num_bars {num_bars}, start datetime {start_dt}, end datetime {end_dt}, head timestamp {head_dt}")

    df = stock_manager.get_pandas_df(symbol, bar_size, info_type)
    print(f"\nDataframe: {df}")


async def cache_single_stock(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size_str: str,
    info_type_str: str,
    force_update: bool,
    limited_scrape: bool,
):
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :param force_update: force the scraping of most recent data
    :param limited_scrape: if True, only scrape about 200 bars into past
    :return:
    """
    print(f"Scraping {info_type_str} data for {symbol}, {bar_size_str}\n======================================")

    await _cache_multiple_stocks_impl(
        stock_manager, [symbol], bar_size_str, info_type_str, force_update, limited_scrape
    )


async def cache_multiple_stocks(
    stock_manager: StockDataManager,
    file_path: str,
    bar_size_str: str,
    info_type_str: str,
    force_update: bool,
    limited_scrape: bool,
):
    """
    Scrapes and caches data for a single stock or ETF.
    :param stock_manager:  --
    :param file_path: path of file containing list of ticker symbols
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :param force_update: force the scraping of most recent data
    :param limited_scrape: only scrape 200 bars into the past
    :return:
    """
    print(
        f"\nCaching multiple stocks: file path = {file_path}, bar size = {bar_size_str}, info type = {info_type_str}, force update = {force_update}"
    )
    print("-----------------------------------------------------------------------------")

    symbols: List[str] = []
    try:
        with open(file_path, "r") as file:
            for line in file:
                symbol = line.strip()
                symbols.append(symbol)
    except FileNotFoundError:
        print(f"Could not find file {file_path}")
        return

    await _cache_multiple_stocks_impl(stock_manager, symbols, bar_size_str, info_type_str, force_update, limited_scrape)


async def remove_single_stock(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size_str: str,
    info_type_str: str,
):
    """
    Removes a single stock from cache.

    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    """
    print(f"Removing stock from cache: symbol is {symbol}, bar size is {bar_size_str}, info type is {info_type_str}")
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)
    stock_manager.clear_data(symbol, bar_size, info_type, remove_from_cache=True)


async def remove_multiple_stocks(
    stock_manager: StockDataManager,
    file_path: str,
    bar_size_str: str,
    info_type_str: str,
):
    """
    Removes a single stock from cache.

    :param stock_manager:  --
    :param file_path: path to file containing list of stocks to remove
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    """
    print(f"Removing multiple stocks from cache, given in file {file_path}")
    symbols: List[str] = []
    try:
        with open(file_path, "r") as file:
            for line in file:
                symbol = line.strip()
                symbols.append(symbol)
    except FileNotFoundError:
        print(f"Could not find file {file_path}")

    for symbol in symbols:
        await remove_single_stock(stock_manager, symbol, bar_size_str, info_type_str)


async def clean_single_stock(
    stock_manager: StockDataManager,
    symbol: str,
    bar_size_str: str,
    info_type_str: str,
    num_bars: int,
) -> Tuple[bool, Optional[str]]:
    """
    Deletes the last N bars of cached stock data.

    :param stock_manager:  --
    :param symbol: stock or ETF ticker, e.g. SPY
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :param num_bars: number of bars to redo
    :return: (True if success, error message or None)
    """
    print(
        f"Deleting last {num_bars} of stock from cache: symbol is {symbol}, bar size is {bar_size_str}, info type is {info_type_str}"
    )
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)
    success = stock_manager.load_data(symbol, bar_size, info_type)
    if not success:
        return False, f"Failed to load data for {symbol}"
    stock_manager.remove_data(symbol, bar_size, info_type=info_type, num_bars=num_bars)
    stock_manager.save_data(symbol, bar_size, info_type)
    stock_manager.unload_data(symbol, bar_size, info_type)
    return True, None


async def clean_multiple_stocks(
    stock_manager: StockDataManager,
    file_path: str,
    bar_size_str: str,
    info_type_str: str,
    num_bars: int,
):
    """Same idea as redo_single_stock(), but for multiple stocks."""
    print(f"Cleaning multiple stocks in cache, given in file {file_path}")
    symbols: List[str] = []
    try:
        with open(file_path, "r") as file:
            for line in file:
                symbol = line.strip()
                symbols.append(symbol)
    except FileNotFoundError:
        print(f"Could not find file {file_path}")

    error_count = 0
    for symbol in symbols:
        success, error_msg = await clean_single_stock(stock_manager, symbol, bar_size_str, info_type_str, num_bars)
        if not success:
            print(f"Error with {symbol}: {error_msg}")
            error_count += 1
            if error_count >= MAX_ERRORS_ALLOWED:
                print("Too many errors, ending redo procedure")
                break


async def _cache_multiple_stocks_impl(
    stock_manager: StockDataManager,
    symbols: List[str],
    bar_size_str: str,
    info_type_str: str,
    force_update: bool,
    limited_scrape: bool,
):
    """
    Scrapes and caches data for a set of stocks/ETFs
    :param stock_manager:  --
    :param symbols: list of ticker symbols
    :param bar_size_str: timeframe of data, e.g. 1 day, 1 minute, etc.
    :param info_type_str: type of chart data, e.g. trades or implied volatility
    :param force_update: force the scraping of most recent data
    :param limited_scrape: only scrape 200 bars into the past
    :return:
    """
    bar_size = str_to_bar_size(bar_size_str)
    info_type = StockData.get_info_type(info_type_str)

    symbols_with_error: Dict[Tuple[str, RequestedInfoType], str] = {}

    # Scraping must occur if latest cached data is more than acceptable_recency days in the past
    acceptable_recency = 0 if force_update else GENERAL_ACCEPTABLE_RECENCY[bar_size]

    error_count: int = 0
    for symbol in symbols:
        metadata = stock_manager.get_metadata(symbol, bar_size, info_type)
        num_bars = 0
        if metadata is not None:
            current_dt = current_datetime()
            num_bars, earliest_dt, latest_dt, head_dt = metadata
            bars_missing_until_now = get_bars_between_times(latest_dt, current_dt, bar_size)
            # If we don't have a head timestamp, we should scrape
            if head_dt is not None:
                if limited_scrape:
                    if num_bars >= NUM_ACCEPTABLE_BARS and bars_missing_until_now <= acceptable_recency:
                        # No need to cache. Data is fresh enough
                        print(
                            f"Data scrape unnecessary for {symbol}. Have {num_bars} bars of data ending on {latest_dt}. Head timestamp is {head_dt}."
                        )
                        continue
                else:
                    # Does earliest data we actually have precede/match adjusted head timestamp?
                    adjusted_head_dt = StockDataManager.get_adjusted_head_timestamp(head_dt, bar_size)
                    head_timestamp_matched = earliest_dt <= adjusted_head_dt
                    if head_timestamp_matched and bars_missing_until_now < acceptable_recency:
                        print(
                            f"Data scrape unnecessary for {symbol}. Have {num_bars} bars of data beginning on {earliest_dt} and ending on {latest_dt}"
                        )
                        continue
                    else:
                        print(
                            f"Need data scrape for {symbol}. Head timestamp {head_dt} preceded/matched by {earliest_dt}: {head_timestamp_matched}, data recency {bars_missing_until_now}."
                        )

        scrape_level = ScrapeLevel.LIMITED if limited_scrape else ScrapeLevel.FULL
        if force_update and num_bars >= NUM_ACCEPTABLE_BARS:
            scrape_level = ScrapeLevel.RECENT

        print(f"Scraping and caching data for {symbol}...")
        df, error_message = await do_cache(
            stock_manager=stock_manager,
            symbol=symbol,
            bar_size=bar_size,
            info_type=info_type,
            scrape_level=scrape_level,
        )
        if error_message is not None:
            print(f"Error for symbol {symbol}: {error_message}")
            symbols_with_error[(symbol, info_type)] = error_message
            error_count += 1
            if error_count > MAX_ERRORS_ALLOWED:
                print("Too many errors, stopping program.")
                break
        print(f"Done with {symbol}")

    if len(symbols_with_error) > 0:
        print("Caching had errors with:")
        for key, error_msg in symbols_with_error.items():
            symbol, info_type = key
            info_type_str = StockData.get_info_type_str(info_type)
            print(f"{symbol}, {info_type_str}: {error_msg}")

    print()


async def main(parser: argparse.ArgumentParser):
    """Top-level function, unpacks arguments and calls functions that do the work"""
    args = parser.parse_args()

    logger = getLogger(__name__)
    basicConfig(filename="cache_data.log", level=INFO)
    stock_manager = StockDataManager()
    data_driver: Optional[BaseDriver] = None
    if not args.show:
        data_driver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
        success = stock_manager.add_driver(data_driver)
        if not success:
            print("Error connecting to broker data")
            return
    stock_manager.set_log_to_stdout(True)
    stock_manager.set_db_path(args.db)

    try:
        if args.show:
            if args.symbol:
                await show_single_stock_data(stock_manager, args.symbol, args.bar_size, args.info_type)
            else:
                await show_cache_contents(stock_manager, args.bar_size, args.info_type)
        elif args.remove:
            bar_size = args.bar_size or "1d"
            info_type = args.info_type or "tr"
            if args.symbol:
                await remove_single_stock(stock_manager, args.symbol, bar_size, info_type)
            elif args.file:
                await remove_multiple_stocks(stock_manager, args.file, bar_size, info_type)
            else:
                print("No file or symbol given.")
        elif args.clean > 0:
            if args.symbol:
                await clean_single_stock(stock_manager, args.symbol, args.bar_size, args.info_type, args.clean)
            else:
                await clean_multiple_stocks(stock_manager, args.file, args.bar_size, args.info_type, args.clean)
        elif args.symbol:
            bar_size = args.bar_size or "1d"
            info_type = args.info_type or "tr"
            await cache_single_stock(stock_manager, args.symbol, bar_size, info_type, args.update, args.limited)
        elif args.file:
            bar_size = args.bar_size or "1d"
            info_type = args.info_type or "tr"
            await cache_multiple_stocks(stock_manager, args.file, bar_size, info_type, args.update, args.limited)
    except asyncio.CancelledError:
        print("Program cancelled by user.")
    except Exception as ex:
        print(f"Got exception: {ex}")
        print(traceback.format_exc())

    if data_driver:
        data_driver.disconnect()


parser = argparse.ArgumentParser(
    prog="python -m scripts.cache_data",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description=textwrap.dedent("""\
        Collect price (or other) data for a security, in bar form, and cache it to disk.

        Building the cache from scratch can take a long time, on the order of half an
        hour, and often requires multiple runs of this program, due to data-throttling
        timeouts from Interactive Brokers. However, once the cache exists, updating it
        is relatively quick.

        Requires IB Gateway or TWS running locally (defaults to the paper/sim account).
        """),
    epilog=textwrap.dedent("""\
        Examples:
          # Build data cache for price data
          python -m scripts.cache_data --file .\\data\\optionable.txt --db .\\data\\market_data.h5

          # Build data cache for the iv_finder tool
          python -m scripts.cache_data --file .\\data\\optionable.txt --db .\\data\\iv_data.h5 --info-type iv --limited

          # Cache a single symbol
          python -m scripts.cache_data --symbol SPY --bar-size 1d --db .\\data\\market_data.h5

          # See what is currently in a cache
          python -m scripts.cache_data --db .\\data\\iv_data.h5 --info-type iv --show

        Notes:
          * bar-size accepts values like 1m, 2m, 5m, 15m, 1h, 4h, 1d, 1w, 1mo (default: 1d).
          * info-type accepts tr (trades), iv, hv, al (default: tr).
          * Use --symbol for one ticker or --file for a list; --file is required to
            cache/remove multiple tickers at once.
        """),
)
parser.add_argument("--symbol", help="Ticker symbol", required=False, default=None, type=str)
parser.add_argument(
    "--file",
    help="File containing list of ticker symbols. Required for caching multiple tickers at once.",
    required=False,
    default=None,
    type=str,
)
parser.add_argument("--db", help="Path to database file", required=False, default=DB_PATH, type=str)
parser.add_argument(
    "--bar-size",
    help="bar size, e.g. 1m, 1h, 1d, etc.",
    required=False,
    default=None,
    type=str,
)
parser.add_argument(
    "--info-type",
    help="type of info, e.g. tr, iv, hv, al",
    required=False,
    default=None,
    type=str,
)
parser.add_argument(
    "--show",
    help="Show what data is in cache. Use --bar-size or --info-type to narrow it down. Use --symbol to show info for single stock.",
    action="store_true",
)
parser.add_argument(
    "--remove",
    help="Remove symbol(s) from cache. Requires --symbol for single symbol, --file for list of symbols to remove.",
    action="store_true",
)
parser.add_argument(
    "--clean",
    help="Deletes last N bars of symbol(s) in cache. Requires --symbol for single symbol, --file for list of symbols to clean.",
    required=False,
    default=0,
    type=int,
)
parser.add_argument(
    "--update",
    help="Forces the getting of most recent data. Works when caching single or multiple stocks.",
    action="store_true",
)
parser.add_argument("--limited", help="Perform limited scrape, going only 200 bars into past", action="store_true")

asyncio.run(main(parser))
