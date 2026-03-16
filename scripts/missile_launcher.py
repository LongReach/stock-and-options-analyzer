import asyncio
from logging import basicConfig, INFO, getLogger
import time
from time import sleep
from typing import List, Tuple, Dict, Optional
from ibapi.common import BarData
from datetime import datetime

from core.ib_driver import IBDriver, BarSize
from core.utils import get_datetime_as_str
from guided_missile.guided_missile_app import GuidedMissile

"""
Entry point for GuidedMissile application. Run like:

python -m scripts.missile_launcher
"""

CLIENT_ID = 19


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="guided_missile.log", level=INFO)
    ib_driver = IBDriver(sim_account=True, client_id=CLIENT_ID, gateway_connection=False)

    try:
        ib_driver.connect()
    except Exception as ex:
        print(f"Exception: {ex}")
        return

    guided_missile_app = GuidedMissile(ib_driver)
    task1 = asyncio.create_task(guided_missile_app.run_loop())
    task2 = asyncio.create_task(guided_missile_app.input_loop())

    await asyncio.gather(task1, task2)

    ib_driver.disconnect()


asyncio.run(main())
