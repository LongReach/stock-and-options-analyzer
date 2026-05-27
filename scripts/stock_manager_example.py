import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime
import argparse

from core.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager

CLIENT_ID = 17
TEST_DB = "data/test_data.h5"


def print_df(df):
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head())
    print("Tail:")
    print(df.tail())


async def main(mode: int):
    logger = getLogger(__name__)
    basicConfig(filename="test_2.log", level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=CLIENT_ID)
    stock_manager = StockDataManager()
    stock_manager.add_driver(ib_driver)
    stock_manager.set_db_path(TEST_DB)
    try:
        print(f"Running in mode {mode}")
        if mode == 0:
            print("Mode 0: do nothing")
        if mode == 1:
            # Scrape some data for SPY, save it.

            # Notice that 3/29/25 is a weekend
            success, error_str = await stock_manager.scrape_data(
                "SPY", BarSize.ONE_DAY, start_date="20250218", end_date="20250329"
            )
            if not success:
                print(f"Error: {error_str}")
            stock_manager.save_data("SPY", BarSize.ONE_DAY)
            df = stock_manager.get_pandas_df("SPY", BarSize.ONE_DAY)
            print_df(df)
        if mode == 2:
            # Load SPY data

            stock_manager.load_data("SPY", BarSize.ONE_DAY)
            df = stock_manager.get_pandas_df("SPY", BarSize.ONE_DAY)
            print_df(df)
        if mode == 3:
            # Scrape DIA data and save it

            print("Here we go!")
            success, error_str = await stock_manager.scrape_data(
                "DIA", BarSize.ONE_DAY, start_date="19980901", end_date="20250528"
            )
            if not success:
                print(f"Error: {error_str}")
            stock_manager.save_data("DIA", BarSize.ONE_DAY)
            df = stock_manager.get_pandas_df("DIA", BarSize.ONE_DAY)
            print_df(df)
        if mode == 4:
            # Smart scrape of DIA data

            print("Here we go with smart scrape!")
            stock_manager.load_data("DIA", BarSize.ONE_DAY)
            success, error_str = await stock_manager.scrape_data_smart("DIA", BarSize.ONE_DAY, start_date="19700101")
            if not success:
                print(f"Error: {error_str}")
            stock_manager.save_data("DIA", BarSize.ONE_DAY)
            df = stock_manager.get_pandas_df("DIA", BarSize.ONE_DAY)
            print_df(df)
        if mode == 5:
            # Tests being able to load cached data on top of scraped data, keeping both

            stock_manager.set_db_path("data/test_xlk_data.h5")
            print("Initial cache purge.")
            stock_manager.clear_data("XLK", BarSize.ONE_DAY, remove_from_cache=True)
            print("Scraping some XLK data.")
            success, error_str = await stock_manager.scrape_data(
                "XLK", BarSize.ONE_DAY, start_date="20260513", end_date="20260521"
            )
            if not success:
                print(f"Error: {error_str}")
            print("Saving it to cache.")
            stock_manager.save_data("XLK", BarSize.ONE_DAY)
            print("Removing it from memory.")
            stock_manager.clear_data("XLK", BarSize.ONE_DAY)
            print("Scraping new data, overlapping with the cached data.")
            success, error_str = await stock_manager.scrape_data(
                "XLK", BarSize.ONE_DAY, start_date="20260519", end_date="20260526"
            )
            if not success:
                print(f"Error: {error_str}")
            success = stock_manager.load_data("XLK", BarSize.ONE_DAY)
            if not success:
                print(f"Error: {error_str}")
            df = stock_manager.get_pandas_df("XLK", BarSize.ONE_DAY)
            print("Dataframe is:\n", df)
        if mode == 6:
            # Tests streaming of data
            pass

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


parser = argparse.ArgumentParser(description="StockDataManager test")
parser.add_argument("--mode", help="choice are 1 - 6", default=0, type=int)
args = parser.parse_args()

asyncio.run(main(args.mode))
