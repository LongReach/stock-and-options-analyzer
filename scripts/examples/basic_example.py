import asyncio
from logging import basicConfig, INFO, getLogger

from core.common import HistoricalData
from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
Very basic example of collecting bars of market data via IB Gateway. Notice the different bar sizes.
"""

CLIENT_ID = 12


def print_historical_data(bars: HistoricalData):
    for bar in bars.bar_data:
        print(f"{bar}")
    print()


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="test.log", level=INFO)
    data_driver: BaseDriver = IBDriver.create(sim_account=True, client_id=CLIENT_ID)
    try:
        data_driver.connect()

        head_timestamp_dt = await data_driver.get_head_timestamp("SPY")
        if not head_timestamp_dt:
            print("Couldn't find head timestamp for SPY")
        else:
            print(f"Head timestamp for SPY is {get_datetime_as_str(head_timestamp_dt)}")

        results, error_str = await data_driver.get_historical_data("SPY", num_bars=10)
        print("Daily bars for SPY are\n------------------------")
        print_historical_data(results)
        results, error_str = await data_driver.get_historical_data("AAPL", num_bars=32, bar_size=BarSize.ONE_HOUR)
        print("Hourly bars for AAPL are\n------------------------")
        print_historical_data(results)
        results, error_str = await data_driver.get_historical_data("DIA", num_bars=32, bar_size=BarSize.FOUR_HOURS)
        print("Four-hour bars for DIA are\n------------------------")
        print_historical_data(results)
        results, error_str = await data_driver.get_historical_data(
            "GLD",
            num_bars=4,
            bar_size=BarSize.ONE_DAY,
            end_date="20250422 16:00:00 US/Eastern",
        )
        print("Daily bars for GLD are\n------------------------")
        print_historical_data(results)
        results, error_str = await data_driver.get_historical_data(
            "TLT",
            bar_size=BarSize.ONE_DAY,
            end_date="20250404 16:00:00 US/Eastern",
            start_date="20250320 09:30:00 US/Eastern",
        )
        print("Daily bars for TLT are\n------------------------")
        print_historical_data(results)
    except Exception as ex:
        print(f"Exception: {ex}")

    data_driver.disconnect()


asyncio.run(main())
