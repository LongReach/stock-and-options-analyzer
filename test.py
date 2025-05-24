import asyncio
from logging import basicConfig, INFO, getLogger
import time

from core.ib_driver import IBDriver, BarSize


async def main():
    logger = getLogger(__name__)
    basicConfig(filename='test.log', level=INFO)
    logger.info("Logging should work")
    ib_driver = IBDriver(sim_account=True, client_id=12)
    try:
        ib_driver.connect()

        await ib_driver.get_historical_data("SPY", 10)
        await ib_driver.get_historical_data("AAPL", 32, bar_size=BarSize.ONE_HOUR)
    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()

asyncio.run(main())