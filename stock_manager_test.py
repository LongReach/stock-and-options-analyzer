import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime
import argparse

from core.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager


async def main(mode: int):
    logger = getLogger(__name__)
    basicConfig(filename='test_2.log', level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=13)
    stock_manager = StockDataManager()
    stock_manager.add_driver(ib_driver)
    try:
        print(f"Running in mode {mode}")
        if mode == 1:
            # Notice that 3/29/25 is a weekend
            success, error_str = await stock_manager.scrape_data("SPY", BarSize.ONE_DAY, start_date="20250218", end_date="20250329")
            if not success:
                print(f"Error: {error_str}")
            stock_manager.save_data("SPY", BarSize.ONE_DAY, "SPY-1d-test.zip")
            df = stock_manager.get_pandas_df("SPY", BarSize.ONE_DAY)
            print("Dataframe is:\n---------------")
            print("Head:")
            print(df.head())
            print("Tail:")
            print(df.tail())
        if mode == 2:
            stock_manager.load_data("SPY", BarSize.ONE_DAY, "SPY-1d-test.zip")
            df = stock_manager.get_pandas_df("SPY", BarSize.ONE_DAY)
            print("Dataframe is:\n---------------")
            print("Head:")
            print(df.head())
            print("Tail:")
            print(df.tail())
    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()

parser = argparse.ArgumentParser(description='StockDataManager test')
parser.add_argument("--mode", help="choice are 1, 2", default=1, type=int)
args = parser.parse_args()

asyncio.run(main(args.mode))