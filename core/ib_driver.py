import asyncio
import math

from ibapi.contract import Contract
from ibapi.order import *
from ibapi.common import BarData
from logging import getLogger, basicConfig
import threading
import time
from typing import Optional, Dict, List, Tuple, Union
from enum import Enum, auto
from datetime import datetime, timedelta

from core.ib_wrapper import IBWrapper
from core.utils import wait_for_condition, get_datetime, get_datetime_as_str, BarSize

LIVE_PORT = 4001
SIM_PORT = 4002
NUM_CONNECT_TRIES = 10

class IBDriverException(Exception):
    pass


class BarDataRequest:
    """For tracking an in-progress bar data request and capturing data returned so far"""

    def __init__(self, ticker: str):
        self.ticker: str = ticker
        # Bar data, oldest to newest
        self.bar_data: List[BarData] = []
        # One timestamp for each entry in bar_data
        self.timestamps: List[datetime] = []
        # Error triggered by request, if any
        self.last_error_code: int = -1
        self.last_error_string: str = ""
        # False while data fetch still in progress
        self.data_fetch_complete: bool = True
        # Discard any bar data older than this, if set
        self.earliest_permitted_dt: Optional[datetime] = None

    def has_error(self):
        """Returns True if request triggered an error"""
        return self.last_error_code != -1

    def add_or_update_bar(self, bar_data: BarData, allow_update: bool = False):
        """
        Adds a new bar of data to that received so far. We don't necessarily expect bars to arrive
        in sequential order, so we must take timestamps into account to keep them in order. Also,
        a bar's data might replace an existing bar, i.e. if the bar is actively trading right now
        and we're receiving updates on it.

        :param bar_data: --
        :param allow_update: TBD
        """
        bar_dt = get_datetime(bar_data.date)

        def _replace_bar_data(existing: BarData, new: BarData):
            existing.low = new.low
            existing.high = new.high
            existing.open = new.open
            existing.volume = new.volume

        # Go backwards through the list, insert received bar after first encountered existing bar
        # that it's newer than.
        insert_idx = 0
        for idx in range(len(self.bar_data) - 1, -1, -1):
            compare_bar = self.bar_data[idx]
            compare_dt = self.timestamps[idx]
            if compare_dt < bar_dt:
                # Want to insert AFTER this index
                insert_idx = idx + 1
                break
            if compare_dt == bar_dt:
                # Simply replace data
                _replace_bar_data(compare_bar, bar_data)
                return

        self.bar_data.insert(insert_idx, bar_data)
        self.timestamps.insert(insert_idx, bar_dt)

    def get_bar_data_as_dicts(self):
        ret_bars = [{"date": bar.date, "open": bar.open, "close": bar.close, "low": bar.low, "high": bar.high,
                     "volume": float(bar.volume)} for bar in self.bar_data]
        return ret_bars


class IBDriver:
    """
    This class communicates with IBWrapper, which interfaces directly with TWS. IBDriver relays commands to
    IBWrapper, and receives responses via callbacks.

    This is an async interface, meant to abstract away the threaded-ness of IBWrapper. Note how the function
    get_historical_data() waits for all data to arrive before returning a result.
    """

    def __init__(self, sim_account: bool, client_id: int = 0):
        self._app: Optional[IBWrapper] = None
        self._app_thread: Optional[threading.Thread] = None
        self._sim_account = sim_account
        self._client_id = client_id

        # Maps request ID to BarDataRequest, which receives arriving data
        self._request_objects: Dict[int, BarDataRequest] = {}

        # For synchronizing changes to self._request_objects
        self._lock = asyncio.Lock()

        self._bar_size_map: Dict[BarSize: str] = {
            BarSize.ONE_MINUTE: "1 min",
            BarSize.FIVE_MINUTES: "5 mins",
            BarSize.ONE_HOUR: "1 hour",
            BarSize.FOUR_HOURS: "4 hours",
            BarSize.ONE_DAY: "1 day",
            BarSize.ONE_WEEK: "7 days",
        }

        self._logger = getLogger(__file__)

    def connect(self) -> bool:
        """Attempts to connect to TWS. Returns True if successful."""
        if self._app:
            self._logger.error("IBWrapper already created")
            return False

        self._logger.info("Creating IBWrapper, attempting connection...")
        self._app = IBWrapper()
        # Set this one before connecting
        self._app.set_error_cb(self._error_cb)

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
        if connect_try == 0:
            self._logger.error("Couldn't connect to IB server.")
            return False

        self.setup_callbacks()
        return True

    def disconnect(self):
        """Triggers disconnect from TWS"""
        self._logger.info('Disconnecting...')
        self._app.disconnect()
        self._logger.info('Disconnected.')

    def setup_callbacks(self):
        """Hooks up callback methods that will receive responses from TWS."""
        self._app.set_historical_data_cb(self._historical_data_cb)
        self._app.set_historical_data_end_response_cb(self._historical_data_end_cb)

    async def get_historical_data(self, ticker: str, num_bars: int = 0, bar_size: BarSize = BarSize.ONE_DAY,
                                  end_date: Optional[Union[datetime, str]] = None,
                                  start_date: Optional[Union[datetime, str]] = None) -> Tuple[List[Tuple[Dict, datetime]], Optional[str]]:
        """
        Requests historical data from TWS, and waits for it to arrive before returning results. Each dict of returned bar
        data includes fields: "date", "open", "close", "low", "high", "volume".

        Note: incomplete historical data might be returned; check results for error string

        :param ticker: stock ticker, e.g. AAPL
        :param num_bars: how many bars of data to collect. If not given (0), then start_state will be used
        :param bar_size: daily, hourly, weekly, etc.
        :param end_date: if given, should mark end of last bar in range. If str, format is like '20250523 14:00:00 US/Eastern'.
        :param start_date: if given, should mark start of first bar in range. If str, format is like '20250523 09:30:00 US/Eastern'.
        :return: (list of (IB Broker BarData as dict, datetime), error str -- if any encountered)
        :raises IBDriverException: if data request can't be fulfilled
        """
        async with self._lock:
            req_id = self._app.next_id()
            req_obj = self._request_objects[req_id] = BarDataRequest(ticker)

        self._logger.info(f"get_historical_data(), ticker={ticker}, num_bars={num_bars}, bar_size={bar_size.name}")
        req_obj.data_fetch_complete = False
        if start_date is not None:
            req_obj.earliest_permitted_dt = start_date if isinstance(start_date, datetime) else get_datetime(start_date)
            start_date = get_datetime_as_str(start_date) if isinstance(start_date, datetime) else start_date
        else:
            start_date = ''
        if end_date is not None:
            end_date = get_datetime_as_str(end_date) if isinstance(end_date, datetime) else end_date
        else:
            end_date = ''
        try:
            self._request_historical_data(req_id, bar_size, num_bars, end_date, start_date)
        except Exception as e:
            raise IBDriverException(f"Failure with historical data request, exception was {e}")

        timed_out = not await wait_for_condition(lambda: req_obj.data_fetch_complete, timeout=5.0)
        ret_error_str = None
        if req_obj.has_error():
            ret_error_str = f"Error getting historical data. Error code is {req_obj.last_error_code}, error string is {req_obj.last_error_string}"
            self._logger.error(ret_error_str)
        elif timed_out:
            ret_error_str = "Timed out getting historical data."
            self._logger.error(ret_error_str)
        else:
            self._logger.info("get_historical_data() finished")
        ret_bars = req_obj.get_bar_data_as_dicts()
        ret_dts = req_obj.timestamps

        async with self._lock:
            self._request_objects.pop(req_id, None)

        return list(zip(ret_bars, ret_dts)), ret_error_str

    def _request_historical_data(self, req_id: int, bar_size: BarSize, num_bars: int = 0, end_date_time: str = '', start_date_time: str = ''):
        """
        Sends request for historical data to TWS.

        For more info, see: https://interactivebrokers.github.io/tws-api/historical_bars.html
        """
        ticker = self._request_objects[req_id].ticker
        new_contract = self._make_contract(ticker, primary_exchange='NYSE')
        bar_size_str = self._bar_size_map[bar_size]

        if num_bars == 0 and start_date_time == '':
            # Need one of these defined
            num_bars = 1

        duration_str = "1 D"
        if num_bars > 0:
            if bar_size == BarSize.ONE_MINUTE:
                duration_str = str(num_bars * 60) + ' S'
            elif bar_size == BarSize.FIVE_MINUTES:
                duration_str = str(num_bars * 60 * 5) + ' S'
            elif bar_size == BarSize.ONE_HOUR:
                days = int(math.ceil(num_bars / 8))
                duration_str = str(days) + ' D'
            elif bar_size == BarSize.FOUR_HOURS:
                days = int(math.ceil(num_bars / 2))
                duration_str = str(days) + ' D'
            elif bar_size == BarSize.ONE_DAY:
                duration_str = str(num_bars) + ' D'
            elif bar_size == BarSize.ONE_WEEK:
                duration_str = str(num_bars) + ' W'
            else:
                duration_str = str(num_bars) + ' D'
        else:
            # Figure out duration from start and end date
            end_dt = datetime.now() if end_date_time == '' else get_datetime(end_date_time)
            start_dt = get_datetime(start_date_time)
            diff = end_dt - start_dt
            if diff.days > 0:
                if diff.days > 30:
                    weeks = int(math.ceil(diff.days / 7))
                    duration_str = f"{weeks} W"
                else:
                    duration_str = f"{diff.days} D"
            else:
                duration_str = f"{diff.seconds} S"

        self._logger.info(
            f"Sending historical data request for: {ticker}, id={req_id}, bar_size={bar_size_str}, duration={duration_str}")
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
        self._app.reqHistoricalData(req_id, new_contract, end_date_time, duration_str, bar_size_str, 'TRADES', 1, 1,
                                    False, [])
        self._logger.info(f"Completed request {req_id}.")

    def _historical_data_cb(self, req_id: int, in_bar: BarData, real_time: bool):
        """
        Receives a single bar of historical data. This function is called multiple times, when multiple bars of data
        are requested.

        :param req_id: applicable request
        :param in_bar: --
        :param real_time: True if this is a real-time update, for current bar
        """
        req_obj = self._request_objects.get(req_id)
        if req_obj:
            dt = get_datetime(in_bar.date)
            if req_obj.earliest_permitted_dt is None or dt >= req_obj.earliest_permitted_dt:
                req_obj.add_or_update_bar(in_bar, allow_update=real_time)

    def _historical_data_end_cb(self, req_id: int, start: str, end: str):
        """
        Called when all historical data has been sent, in response to a particular request.
        :param req_id: applicable request
        :param start: --
        :param end: --
        """
        self._logger.info(f"Historical Data Ended for {req_id}. Started at {start}, ending at {end}")
        req_obj = self._request_objects.get(req_id)
        if req_obj:
            req_obj.data_fetch_complete = True

    def _error_cb(self, req_id: int, error_code: int, error_string: str, advanced_order_reject_json=""):
        """
        Called when there's an error.

        :param req_id: applicable request
        :param error_code: integer code
        :param error_string: error description
        :param advanced_order_reject_json: ??
        :return:
        """
        # errors to ignore
        ignore_errors = {202}
        # errors to downgrade from warning to info (less noise in output)
        info_errors = {2103, 2104, 2106, 2158}
        if error_code in ignore_errors:
            # canceled order, we can ignore
            pass
        elif error_code in info_errors:
            err_out = "Error (ignorable): code is " + str(error_code) + ", string is " + error_string
            self._logger.info(err_out)
        else:
            err_out = "Error: code is " + str(error_code) + ", string is " + error_string
            self._logger.warning(err_out)
            req_obj = self._request_objects.get(req_id)
            if req_obj:
                req_obj.last_error_code = error_code
                req_obj.last_error_string = error_string

    def _make_contract(self, ticker: str, primary_exchange=None):
        """Makes and returns an IB Contract"""
        the_contract = Contract()
        the_contract.symbol = ticker
        the_contract.secType = 'STK'
        the_contract.exchange = 'SMART'
        the_contract.currency = 'USD'
        if primary_exchange:
            the_contract.primaryExchange = primary_exchange
        return the_contract
