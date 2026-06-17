import asyncio
from logging import basicConfig, INFO, getLogger

from core.base_driver import BaseDriver
from core.ib.ib_driver import IBDriver
from guided_missile.guided_missile_app import GuidedMissile

"""
Entry point for GuidedMissile application. Run like:

python -m scripts.missile_launcher
"""

CLIENT_ID = 19


async def main():
    logger = getLogger(__name__)
    basicConfig(filename="guided_missile.log", level=INFO)
    data_driver: BaseDriver = IBDriver.create(sim_account=True, client_id=CLIENT_ID, gateway_connection=False)

    try:
        data_driver.connect()
    except Exception as ex:
        print(f"Exception: {ex}")
        return

    guided_missile_app = GuidedMissile(data_driver)
    task1 = asyncio.create_task(guided_missile_app.run_loop())
    task2 = asyncio.create_task(guided_missile_app.input_loop())

    await asyncio.gather(task1, task2)

    data_driver.disconnect()


asyncio.run(main())
