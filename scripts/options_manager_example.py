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
from core.option_data_manager import OptionDataManager

"""
Run like:
python -m scripts.options_manager_example
"""


def print_df(df):
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head(8))
    print("Tail:")
    print(df.tail(8))


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="options_manager_test.log", level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=2112)
    try:
        ib_driver.connect()
        await asyncio.sleep(1.0)
        options_manager = OptionDataManager()
        options_manager.add_driver(ib_driver)

        print("Getting expirations for SPY...")
        expirations = await options_manager.get_expirations("SPY", 30, 44)
        print(f"Expirations for SPY between 30 and 50 days away are {expirations}")

        print("Getting option chain for SPY...")
        option_data = await options_manager.get_option_chain(
            "SPY", expiration=expirations[-1], right="C"
        )
        # option_data.sort("delta", ascending=True)
        df = option_data.get_dataframe()
        print_df(df)

    except Exception as ex:
        print(f"Exception: {ex}")

    ib_driver.disconnect()


asyncio.run(main())
