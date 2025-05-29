import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str


async def main():
    logger = getLogger(__name__)
    basicConfig(filename='options_driver_test.log', level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=17)
    try:
        ib_driver.connect()
        contract_details, error_str = await ib_driver.get_contract_details("SPY")

        print(f"Got {contract_details}, error is {error_str}")

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()

asyncio.run(main())