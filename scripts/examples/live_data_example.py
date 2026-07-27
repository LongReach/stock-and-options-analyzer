import asyncio
from logging import basicConfig, INFO, getLogger

from core.common import HistoricalData, RequestedInfoType
from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver, BarSize
from core.schwab.schwab_driver import SchwabDriver
from core.utils import get_full_symbol_name

"""
An example of how to get recent data bars (price, volatility) from the market, as well as how to get
live, constantly streaming data.
"""

TICKER = "AAPL"
OPTION_UNDERLYING = "SPY"
OPTION_EXPIRATION = "20260821"
OPTION_STRIKE = 750.0
# If False, communicate with TWS app instead of Gateway
USE_GATEWAY = True
CLIENT_ID = 14
BROKER = "IB"


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


def create_driver() -> BaseDriver:
    """Creates the data driver selected by the BROKER constant."""
    if BROKER == "SCHWAB":
        return SchwabDriver.create()
    return IBDriver.create(sim_account=True, client_id=CLIENT_ID)


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="live_data_test.log", level=INFO)
    data_driver: BaseDriver = create_driver()
    try:
        data_driver.connect()

        price_data_five, error_str = await data_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.FIVE_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
        )
        print(f"Five minute bars for {TICKER} (trades) are\n------------------------")
        print_historical_data(price_data_five)

        spy_contract_name = get_full_symbol_name(OPTION_UNDERLYING, True, True, OPTION_EXPIRATION, OPTION_STRIKE)
        price_data_option, error_str = await data_driver.get_historical_data(
            spy_contract_name,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.FIVE_MINUTES,
            request_info_type=RequestedInfoType.TRADES,
        )
        print(f"Five minute bars for {spy_contract_name} (option trades) are\n------------------------")
        print_historical_data(price_data_option)

        iv_data_option, error_str = await data_driver.get_historical_data(
            spy_contract_name,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.FIVE_MINUTES,
            request_info_type=RequestedInfoType.IMPLIED_VOLATILITY,
        )
        if error_str:
            print(f"Error getting historical option IV data for {spy_contract_name}: {error_str}")
        else:
            print(f"Five minute bars for {spy_contract_name} (option implied volatility) are\n------------------------")
            print_historical_data(iv_data_option)

        iv_data, error_str = await data_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.ONE_DAY,
            request_info_type=RequestedInfoType.IMPLIED_VOLATILITY,
        )
        if error_str:
            print(f"Error getting historical IV data for {TICKER}")
        else:
            print(f"One day bars for {TICKER} (implied volatility) are\n------------------------")
        print_historical_data(iv_data)

        hv_data, error_str = await data_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=False,
            bar_size=BarSize.ONE_DAY,
            request_info_type=RequestedInfoType.HISTORICAL_VOLATILITY,
        )
        if error_str:
            print(f"Error getting historical historical IV data (yes, that's a correct statement) for {TICKER}")
        else:
            print(f"One day bars for {TICKER} (historical volatility) are\n------------------------")
        print_historical_data(hv_data)

        price_data_one, error_str = await data_driver.get_historical_data(
            TICKER,
            num_bars=10,
            live_data=True,
            bar_size=BarSize.ONE_MINUTE,
            request_info_type=RequestedInfoType.TRADES,
            regular_trading_hours_only=False,
        )

        print("Now printing live data for one minute bars, stand by... (ctrl-c to end)")
        stop_event = asyncio.Event()
        task1 = asyncio.create_task(print_streaming_data(price_data_one, stop_event))
        task2 = asyncio.create_task(wait_for_keypress(stop_event))

        await asyncio.gather(task1, task2)

    except Exception as ex:
        print(f"Exception: {ex}")

    data_driver.disconnect()


asyncio.run(main())
