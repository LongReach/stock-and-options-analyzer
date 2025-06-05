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
python -m scripts.options_driver_example
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
        option_info, error_str = await ib_driver.get_options_chain_info(
            "SPY", contract_id
        )
        print(f"Get options info from exchange {option_info.exchange}")
        exp_list = sorted(option_info.expirations)
        print(f"Expirations are {exp_list}")
        strike_list = sorted(option_info.strikes)
        print(f"Strikes are {strike_list}")

        await asyncio.sleep(1.0)

        contract_details, error_str = await ib_driver.get_contract_details(
            "SPY", is_option=True, is_call=True, strike=600.0, expiration="20250620"
        )
        option_info, error_str = await ib_driver.get_greeks(contract_details)
        print(f"Option info is: {option_info.to_dict()}")

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


asyncio.run(main())
