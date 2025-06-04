import asyncio
import os
import sys
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict
from ibapi.common import BarData
from datetime import datetime

# module_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'core'))
# sys.path.append(module_path)

from core.common import RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str

"""
Run like:
python -m scripts.options_driver_test
"""


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="options_driver_test.log", level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=17)
    try:
        ib_driver.connect()
        contract_details, error_str = await ib_driver.get_contract_details("SPY")

        print(f"Got {contract_details}, error is {error_str}")
        contract_id = contract_details.contract.conId
        await ib_driver.get_options_chain_info("SPY", contract_id)

        await asyncio.sleep(1.0)

        contract_details, error_str = await ib_driver.get_contract_details(
            "SPY", is_option=True, is_call=True, strike=600.0, expiration="20250627"
        )
        print(f"Got {contract_details}, error is {error_str}")
        full_ticker = ib_driver.get_full_symbol_from_contract_details(contract_details)
        print(f"Full ticker is {full_ticker}")

        data_tup, error_str = await ib_driver.get_most_recent_data(
            full_ticker,
            BarSize.ONE_HOUR,
            request_info_type=RequestedInfoType.ADJUSTED_LAST,
        )
        option_price = 0.0
        if data_tup:
            option_price = data_tup[0]["close"]
        print(f"Option price for {full_ticker} is {option_price}")

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


asyncio.run(main())
