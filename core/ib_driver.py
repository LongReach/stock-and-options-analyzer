import asyncio
from ibapi.contract import Contract
from ibapi.order import *
from ibapi.common import BarData
from logging import getLogger, basicConfig
import threading
import time
from typing import Optional, Dict, List

from core.ib_wrapper import IBWrapper
from core.utils import wait_for_condition

LIVE_PORT = 4001
SIM_PORT = 4002
NUM_CONNECT_TRIES = 10

class IBDriver:
    """
    This class communicates with IBWrapper, which interfaces directly with TWS. IBDriver relays commands to
    IBWrapper, and receives responses via callbacks.
    """

    def __init__(self, sim_account: bool, client_id: int = 0):
        self._app: Optional[IBWrapper] = None
        self._app_thread: Optional[threading.Thread] = None
        self._sim_account = sim_account
        self._client_id = client_id

        self._historical_data_lock = asyncio.Lock()
        self._historical_data_fetch_complete: bool = True
        self._request_id_to_ticker: Dict[int, str] = {}
        self._request_id_to_bar_data: Dict[int, List[BarData]] = {}

        basicConfig()
        self._logger = getLogger(__file__)

    def connect(self):
        """Attempts to connect to TWS."""
        if self._app:
            self._logger.error("IBWrapper already created")
            return

        self._logger.info("Creating IBWrapper, attempting connection...")
        self._app = IBWrapper()

        port = SIM_PORT if self._sim_account else LIVE_PORT
        self._app.connect("127.0.0.1", port, self._client_id)

        self._app_thread = threading.Thread(target=self._app.run)
        self._app_thread.start()
        time.sleep(1)

        connect_try = NUM_CONNECT_TRIES
        while connect_try > 0:
            if self._app.is_connected():
                self._logger.info("Connected!")
                break
            self._logger.info("Waiting for connection...")
            time.sleep(1.0)
            connect_try -= 1

        self.setup_callbacks()

    def disconnect(self):
        """Triggers disconnect from TWS"""
        self._logger.info('Disconnecting...')
        self._app.disconnect()
        self._logger.info('Disconnected.')

    def setup_callbacks(self):
        """Hooks up callback methods that will receive responses from TWS."""
        self._app.set_historical_data_cb(self._historical_data_cb)
        self._app.set_historical_data_end_response_cb(self._historical_data_end_cb)

    async def get_historical_data(self, ticker: str, num_bars: int):
        """Requests historical data from TWS, and waits for it to arrive."""
        async with self._historical_data_lock:
            req_id = self._app.next_id()
            self._request_id_to_ticker[req_id] = ticker
            self._request_id_to_bar_data[req_id] = []

            self._historical_data_fetch_complete = False
            self._request_historical_data(req_id, num_bars)

            success = await wait_for_condition(lambda: self._historical_data_fetch_complete, timeout=5.0)
            if success:
                print("All done getting historical data.")
            else:
                print("Timed out getting historical data.")

    def _request_historical_data(self, req_id: int, num_bars: int):
        """Sends request for historical data to TWS."""
        ticker = self._request_id_to_ticker[req_id]
        new_contract = self._make_contract(ticker, primary_exchange='NYSE')
        duration_str = str(num_bars) + ' D'
        bar_size_str = "1 day"

        self._logger.info(f"Sending historical data request for: {ticker}, id={req_id}")
        # Request Historical Data
        #     reqId: ID of request
        #     contract: Contract object
        #     endDateTime: Can be '', otherwise 'yyyyMMdd HH:mm:ss {TMZ}'
        #     durationStr: E.g. '5 D' or '60 S'
        #     barSizeSetting: E.g. '15 secs', '1 min', or '1 hour'
        #     whatToShow: kind of info (e.g. 'BID', 'ASK', 'OPTION_IMPLIED_VOLATILITY', 'TRADES'). Some choices won't return volume data.
        #     useRTH: 1 for regular trading hours only, 0 otherwise
        #     formatDate: 1 for human-readable string, 2 for system format
        #     keepUpToDate: True for continuous updates, False otherwise
        #     chartOptions: Internal use only, just send []
        self._app.reqHistoricalData(req_id, new_contract, '', duration_str, bar_size_str, 'TRADES', 1, 2, False, [])
        self._logger.info(f"Completed request {req_id}.")

    def _historical_data_cb(self, req_id: int, in_bar: BarData):
        """
        Receives a single bar of historical data. This function is called multiple times, when multiple bars of data
        are requested.

        :param req_id: --
        :param in_bar: --
        """
        print(f"Got bar of historical data for {req_id}: {in_bar}")
        self._request_id_to_bar_data[req_id].append(in_bar)

    def _historical_data_end_cb(self, req_id: int, start: str, end: str):
        """
        Called when all historical data has been sent, in response to a particular request.
        :param req_id: --
        :param start: --
        :param end: --
        """
        print(f"Historical Data Ended for {req_id}. Started at {start}, ending at {end}")
        self._historical_data_fetch_complete = True

    def _make_contract(self, ticker: str, primary_exchange=None):
        the_contract = Contract()
        the_contract.symbol = ticker
        the_contract.secType = 'STK'
        the_contract.exchange = 'SMART'
        the_contract.currency = 'USD'
        if primary_exchange:
            the_contract.primaryExchange = primary_exchange
        return the_contract
