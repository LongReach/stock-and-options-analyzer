import asyncio
from logging import basicConfig, INFO, getLogger
import time

from core.ib_driver import IBDriver

async def main():
    logger = getLogger(__name__)
    basicConfig(filename='test.log', level=INFO)
    logger.info("Logging should work")
    ib_driver = IBDriver(sim_account=True, client_id=12)
    ib_driver.connect()

    await ib_driver.get_historical_data("SPY", 10)

    ib_driver.disconnect()

asyncio.run(main())