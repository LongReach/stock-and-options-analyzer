import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
Option contract

        contract = Contract()
        contract.symbol = "GOOG"
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = "20190315"
        contract.strike = 1180
        contract.right = "C"
        contract.multiplier = "100"
        #! [optcontract_us]
        return contract
"""

async def main():
    logger = getLogger(__name__)
    basicConfig(filename='options_driver_test.log', level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=17)
    try:
        ib_driver.connect()
        contract_details, error_str = await ib_driver.get_contract_details("SPY")

        print(f"Got {contract_details}, error is {error_str}")
        contract_id = contract_details.contract.conId
        await ib_driver.get_options_chain_info("SPY", contract_id)

        await asyncio.sleep(5.0)

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()

asyncio.run(main())