import asyncio
from logging import basicConfig, INFO, getLogger
import time
from time import sleep
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

from core.common import HistoricalData, RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str


def print_historical_data(bars: HistoricalData):
    for bar in bars.bar_data:
        print(f"{bar}")
    print()


async def print_streaming_data(price_data: HistoricalData, stop_event: asyncio.Event):
    while not stop_event.is_set():
        await asyncio.sleep(2.0)
        bar, dt = price_data.get_current_bar()
        print(f"Bar for {dt} is {bar}")

    print("Loop stopped.")


async def wait_for_keypress(stop_event: asyncio.Event):
    # Run blocking input() in a separate thread
    await asyncio.to_thread(input, "Press ENTER to stop...\n")
    stop_event.set()


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="live_data_test.log", level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=45)
    try:
        ib_driver.connect()

        price_data_five, error_str = await ib_driver.get_historical_data(
            "SPY",
            num_bars=10,
            live_data=False,
            bar_size=BarSize.FIVE_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
        )
        print("Five minute bars for SPY (trades) are\n------------------------")
        print_historical_data(price_data_five)

        iv_data, error_str = await ib_driver.get_historical_data(
            "SPY",
            num_bars=10,
            live_data=False,
            bar_size=BarSize.ONE_DAY,
            request_info_type=RequestedInfoType.IMPLIED_VOLATILITY,
        )
        print("One day bars for SPY (implied volatility) are\n------------------------")
        print_historical_data(iv_data)

        hv_data, error_str = await ib_driver.get_historical_data(
            "SPY",
            num_bars=10,
            live_data=False,
            bar_size=BarSize.ONE_DAY,
            request_info_type=RequestedInfoType.HISTORICAL_VOLATILITY,
        )
        print(
            "One day bars for SPY (historical volatility) are\n------------------------"
        )
        print_historical_data(hv_data)

        price_data_two, error_str = await ib_driver.get_historical_data(
            "SPY",
            num_bars=10,
            live_data=True,
            bar_size=BarSize.TWO_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
            regular_trading_hours_only=False
        )


    except Exception as ex:
        print(f"Exception: {ex}")

    print("Now printing live data for two minute bars, stand by... (ctrl-c to end)")
    stop_event = asyncio.Event()
    task1 = asyncio.create_task(print_streaming_data(price_data_two, stop_event))
    task2 = asyncio.create_task(wait_for_keypress(stop_event))

    await asyncio.gather(task1, task2)

    ib_driver.disconnect()


asyncio.run(main())
