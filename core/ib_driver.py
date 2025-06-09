import asyncio
import copy
import math

from _decimal import Decimal
from idlelib.window import add_windows_to_menu

from core.utils import current_datetime
from ibapi.contract import Contract, ContractDetails
from ibapi.client import EClient
from ibapi.order import *
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString, TickerId
from ibapi.ticktype import TickType
from ibapi.wrapper import EWrapper, OrderId
from logging import getLogger, basicConfig
import threading
import time
from typing import Optional, Dict, List, Tuple, Union, Set
from enum import Enum, auto
from datetime import datetime, timedelta

from core.common import (
    HistoricalData,
    RequestedInfoType,
    SecurityDescriptor,
    OptionChainInfo,
    OptionInfo,
)
from core.utils import (
    wait_for_condition,
    get_datetime,
    get_datetime_as_str,
    BarSize,
    is_trading_hours,
)
from core.ib_driver_requests import (
    ContractDetailsRequest,
    OptionChainInfoRequest,
    OptionRequest,
    BarDataRequest,
    IBDriverException,
)
from core.ib_wrapper import IBWrapper

LIVE_PORT = 4001
SIM_PORT = 4002
NUM_CONNECT_TRIES = 10
HISTORICAL_DATA_TIMEOUT = 10.0
OPTIONS_DATA_TIMEOUT = 8.0


class IBDriver(IBWrapper):
    """
    This class permits communication with Interactive Brokers. Commands go out via functions in EClient (a parent
    of this class) and responses come back to the callbacks in IBWrapper, which passes the information on
    to functions in this class.

    This is an async interface, meant to abstract away the threaded-ness of EClient and the need for callers to think
    about IB's callback-based communication framework. Note how the function get_historical_data() waits for all data
    to arrive before returning a result.
    """

    def __init__(self, sim_account: bool, client_id: int = 0):
        super().__init__()
        self._app_thread: Optional[threading.Thread] = None
        self._sim_account = sim_account
        self._client_id = client_id

        # Maps request ID to BarDataRequest, which receives arriving data
        self._request_bardata_objects: Dict[int, BarDataRequest] = {}
        # Maps a request ID to a ContractDetailsRequest object
        self._request_contractdetail_objects: Dict[int, ContractDetailsRequest] = {}
        self._request_optionchain_objects: Dict[int, OptionChainInfoRequest] = {}
        self._request_option_objects: Dict[int, OptionRequest] = {}

        # Maps head timestamp request ID to symbol
        self._head_timestamp_map: Dict[int, str] = {}
        # Maps symbol to head timestamp
        self._symbol_to_head_timestamp: Dict[str, str] = {}

        # For synchronizing changes to self._request_objects
        self._lock = asyncio.Lock()

        self._bar_size_map: Dict[BarSize:str] = {
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
        if self.is_connected():
            self._logger.error("IBDriver already connected")
            return False

        self._logger.info("Attempting connection...")

        port = SIM_PORT if self._sim_account else LIVE_PORT
        super().connect("127.0.0.1", port, self._client_id)

        self._app_thread = threading.Thread(target=self.run)
        self._app_thread.start()
        time.sleep(1)

        connect_try = NUM_CONNECT_TRIES
        while connect_try > 0:
            if self.is_connected():
                self._logger.info("Connected!")
                break
            self._logger.info("Waiting for connection...")
            time.sleep(1.0)
            connect_try -= 1
        if connect_try == 0:
            self._logger.error("Couldn't connect to IB server.")
            return False

        return True

    def disconnect(self):
        """Triggers disconnect from TWS"""
        self._logger.info("Disconnecting...")
        super().disconnect()
        self._logger.info("Disconnected.")

    def is_connected(self):
        """Returns True if a connection with TWS has been achieved"""
        return self.request_id is not None

    def next_id(self):
        """Returns next request ID, advancing the counter."""
        self.request_id += 1
        return self.request_id

    async def get_historical_data(
        self,
        symbol_full: str,
        num_bars: int = 0,
        bar_size: BarSize = BarSize.ONE_DAY,
        end_date: Optional[Union[datetime, str]] = None,
        start_date: Optional[Union[datetime, str]] = None,
        live_data: bool = False,
        request_info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> Tuple[HistoricalData, Optional[str]]:
        """
        Requests historical data from TWS, and waits for it to arrive before returning results. Each dict of returned bar
        data includes fields: "date", "open", "close", "low", "high", "volume".

        Note: incomplete historical data might be returned; check results for error string

        :param symbol_full: stock ticker, e.g. AAPL or SPY-C-20250627-600.0
        :param num_bars: how many bars of data to collect. If not given (0), then start_state will be used
        :param bar_size: daily, hourly, weekly, etc.
        :param end_date: if given, should mark end of last bar in range. If str, format is like '20250523 14:00:00 US/Eastern'.
        :param start_date: if given, should mark start of first bar in range. If str, format is like '20250523 09:30:00 US/Eastern'.
        :param live_data: if True, data will continue to flow in
        :param request_info_type: type of info to get, e.g. TRADES or IMPLIED_VOLATILITY
        :return: (HistoricalData, error str -- if any encountered)
        :raises IBDriverException: if data request can't be fulfilled
        """
        async with self._lock:
            req_id = self.next_id()
            ticker_desc = SecurityDescriptor(symbol_full)
            req_obj = self._request_bardata_objects[req_id] = BarDataRequest(
                ticker_desc
            )

        self._logger.info(
            f"get_historical_data(), ticker={symbol_full}, num_bars={num_bars}, bar_size={bar_size.name}"
        )
        req_obj.data_fetch_complete = False
        if start_date is not None:
            req_obj.earliest_permitted_dt = (
                start_date
                if isinstance(start_date, datetime)
                else get_datetime(start_date)
            )
            start_date = (
                get_datetime_as_str(start_date)
                if isinstance(start_date, datetime)
                else start_date
            )
        else:
            start_date = ""
        if end_date is not None:
            end_date = (
                get_datetime_as_str(end_date)
                if isinstance(end_date, datetime)
                else end_date
            )
        else:
            end_date = ""
        try:
            self._request_historical_data(
                req_id,
                bar_size,
                num_bars,
                end_date,
                start_date,
                live_data,
                request_info_type,
            )
        except Exception as e:
            raise IBDriverException(
                f"Failure with historical data request, exception was {e}"
            )

        timed_out = not await wait_for_condition(
            lambda: req_obj.data_fetch_complete, timeout=HISTORICAL_DATA_TIMEOUT
        )
        ret_error_str = None
        if req_obj.has_error():
            ret_error_str = f"Error getting historical data. Error code is {req_obj.last_error_code}, error string is {req_obj.last_error_string}"
            self._logger.error(ret_error_str)
        elif timed_out:
            ret_error_str = "Timed out getting historical data."
            self._logger.error(ret_error_str)
        else:
            self._logger.info("get_historical_data() finished")

        async with self._lock:
            if not live_data:
                self._request_bardata_objects.pop(req_id, None)

        return req_obj.historical_data, ret_error_str

    async def get_most_recent_data(
        self,
        symbol_full: str,
        bar_size: BarSize = BarSize.ONE_DAY,
        request_info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> Tuple[Optional[Tuple[dict, datetime]], Optional[str]]:
        """
        Gets the most recent bar of data.

        Note: don't use daily bars if you're getting data for an option

        :param symbol_full: e.g. AAPL or SPY-C-20250627-600.0
        :param bar_size: daily, hourly, weekly, etc.
        :param request_info_type: type of info to get, e.g. TRADES or IMPLIED_VOLATILITY
        :return: ((bar dict, datetime) or None, error string or None)
        """
        historical_data, error_str = await self.get_historical_data(
            symbol_full,
            bar_size=bar_size,
            request_info_type=request_info_type,
            num_bars=5,
        )
        ret_tuple = None
        if not historical_data.is_empty():
            bar_data_dicts = historical_data.get_bar_data_as_dicts()
            ret_tuple = (bar_data_dicts[-1], historical_data.timestamps[-1])
        return ret_tuple, error_str

    async def get_head_timestamp(self, ticker: str) -> Optional[datetime]:
        """
        Returns the head timestamp for a particular ticker, i.e. the earliest datetime for which
        IB has data.
        """
        async with self._lock:
            req_id_for_head_timestamp = self.next_id()
            self._head_timestamp_map[req_id_for_head_timestamp] = ticker

        new_contract = self._make_contract(ticker, primary_exchange="NYSE")
        try:
            self._request_head_timestamp(req_id_for_head_timestamp, new_contract)
        except Exception as e:
            raise IBDriverException(
                f"Failure with head timestamp request, exception was {e}"
            )

        def _head_timestamp_available():
            return self._symbol_to_head_timestamp.get(ticker) is not None

        timed_out = not await wait_for_condition(
            _head_timestamp_available, timeout=HISTORICAL_DATA_TIMEOUT
        )
        result = None
        if not timed_out:
            result = get_datetime(self._symbol_to_head_timestamp[ticker])

        async with self._lock:
            self._head_timestamp_map.pop(req_id_for_head_timestamp, None)

        return result

    async def get_contract_details(
        self,
        ticker: str,
        primary_exchange: str = None,
        is_option: bool = False,
        is_call: bool = False,
        strike: Optional[float] = None,
        expiration: Optional[str] = None,
    ) -> Tuple[List[ContractDetails], Optional[str]]:
        """
        Returns an IB ContractDetails object for given ticker.

        :param ticker: ticker for stock, or underlying, if option
        :param primary_exchange: --
        :param is_option: True if option
        :param is_call: True if option is a call, False if put
        :param strike: strike price of option
        :param expiration: expiration date, in IB format
        :return: (list of ContractDetails, error string or None)
        """
        async with self._lock:
            req_id = self.next_id()
            req_obj = self._request_contractdetail_objects[req_id] = (
                ContractDetailsRequest()
            )

        contract = self._make_contract(
            ticker, primary_exchange, is_option, is_call, strike, expiration
        )
        req_obj.data_fetch_complete = False
        self.reqContractDetails(req_id, contract)

        timed_out = not await wait_for_condition(
            lambda: req_obj.data_fetch_complete, timeout=HISTORICAL_DATA_TIMEOUT
        )
        ret_error_str = None
        if req_obj.has_error():
            ret_error_str = f"Error getting contract details. Error code is {req_obj.last_error_code}, error string is {req_obj.last_error_string}"
            self._logger.error(ret_error_str)
        elif timed_out:
            ret_error_str = "Timed out getting contract details."
            self._logger.error(ret_error_str)
        else:
            self._logger.info("get_contract_details() finished")
        ret_list = req_obj.get_best_list()

        async with self._lock:
            self._request_contractdetail_objects.pop(req_id, None)

        return ret_list, ret_error_str

    async def get_contract_details_single(
        self,
        ticker: str,
        primary_exchange: str = None,
    ) -> Tuple[Optional[ContractDetails], Optional[str]]:
        """
        Gets a single ContractDetails object. Useful only for CDs on stocks, not options.
        :param ticker:
        :param primary_exchange:
        :return:
        """
        cd_list, error_str = await self.get_contract_details(ticker, primary_exchange)
        if len(cd_list) == 0:
            return (
                None,
                f"Couldn't find contract details for ticker {ticker}, primary exchange {primary_exchange}. Error was {error_str}",
            )
        return cd_list[0], error_str

    async def get_options_chain_info(
        self, contract_details: ContractDetails
    ) -> Tuple[Optional[OptionChainInfo], Optional[str]]:
        """
        Gets basic information about the option chain for a stock. Strikes and expiration dates
        are the most useful data returned.

        :param contract_details: ContractDetails for a stock
        :return: (OptionChainInfo or None, error string or None)
        """
        ticker = contract_details.contract.symbol

        async with self._lock:
            req_id = self.next_id()
            req_obj = self._request_optionchain_objects[req_id] = (
                OptionChainInfoRequest(ticker)
            )

        underlying_contract_id = contract_details.contract.conId
        req_obj.data_fetch_complete = False
        self.reqSecDefOptParams(req_id, ticker, "", "STK", underlying_contract_id)

        timed_out = not await wait_for_condition(
            lambda: req_obj.data_fetch_complete, timeout=HISTORICAL_DATA_TIMEOUT
        )
        ret_error_str = None
        if req_obj.has_error():
            ret_error_str = f"Error getting option chain info. Error code is {req_obj.last_error_code}, error string is {req_obj.last_error_string}"
            self._logger.error(ret_error_str)
        elif timed_out:
            ret_error_str = "Timed out getting option chain info."
            self._logger.error(ret_error_str)
        else:
            self._logger.info("get_options_chain_info() finished")

        option_info = req_obj.get_best_option_chain_info()

        async with self._lock:
            self._request_optionchain_objects.pop(req_id, None)

        return option_info, ret_error_str

    async def get_greeks(
        self, contract_details: ContractDetails
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        """
        Gets all the useful information for a particular option (price, strike, expiration, Greeks, volume, open
        interest, etc.)

        For more info, see: https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#available-tick-types

        :param contract_details: CD of option for which Greeks are wanted
        :return: (OptionInfo or None, error string or None)
        """
        if contract_details.contract.secType != "OPT":
            return None, "Contract not for an option"

        full_ticker = self.get_full_symbol_from_contract_details(contract_details)
        self._logger.info(f"Getting Greeks and other info for option {full_ticker}")

        async with self._lock:
            req_id = self.next_id()
            req_obj = self._request_option_objects[req_id] = OptionRequest()

        req_obj.option_info.full_name = full_ticker
        req_obj.option_info.is_call = contract_details.contract.right == "C"
        req_obj.option_info.strike = contract_details.contract.strike
        req_obj.option_info.expiration = (
            contract_details.contract.lastTradeDateOrContractMonth
        )
        req_obj.option_info.set_live(is_trading_hours())
        req_obj.data_fetch_complete = False

        option_contract = contract_details.contract

        await self._set_market_data_type(is_trading_hours())
        # 100 and 101 are for volume and open interest, respectively
        self.reqMktData(req_id, option_contract, "100,101", False, False, [])

        timed_out = not await wait_for_condition(
            lambda: req_obj.option_info.is_defined(), timeout=OPTIONS_DATA_TIMEOUT
        )
        ret_error_str = None
        if req_obj.has_error():
            ret_error_str = f"Error getting option. Error code is {req_obj.last_error_code}, error string is {req_obj.last_error_string}"
            self._logger.error(ret_error_str)
        elif timed_out:
            ret_error_str = "Timed out getting option."
            self._logger.error(ret_error_str)
        else:
            self._logger.info("get_greeks() finished")
        req_obj.data_fetch_complete = True
        # Cancel the request, so ticks don't keep coming in
        self.cancelMktData(req_id)

        async with self._lock:
            self._request_option_objects.pop(req_id, None)

        return req_obj.option_info, ret_error_str

    @staticmethod
    def get_full_symbol_from_contract_details(contract_details: ContractDetails) -> str:
        """
        Given a ContractDetails object, return a full symbol name, e.g. "SPY" or "SPY-C-20250627-600.0" (if option)
        """
        contract = contract_details.contract
        if contract.secType == "OPT":
            return f"{contract.symbol}-{contract.right}-{contract.lastTradeDateOrContractMonth}-{contract.strike}"

        return contract.symbol

    # ---------------------------------------------------
    # Private methods
    # ---------------------------------------------------

    def _request_historical_data(
        self,
        req_id: int,
        bar_size: BarSize,
        num_bars: int = 0,
        end_date_time: str = "",
        start_date_time: str = "",
        live_data: bool = False,
        request_info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ):
        """
        Sends request for historical data to TWS.

        For more info, see: https://interactivebrokers.github.io/tws-api/historical_bars.html
        """
        ticker_desc = self._request_bardata_objects[req_id].ticker_desc
        new_contract = self._make_contract(
            ticker_desc.ticker,
            primary_exchange=None,
            is_option=ticker_desc.is_opt,
            is_call=ticker_desc.is_call(),
            strike=ticker_desc.strike,
            expiration=ticker_desc.expiration,
        )
        bar_size_str = self._bar_size_map[bar_size]

        if num_bars == 0 and start_date_time == "":
            # Need one of these defined
            num_bars = 1

        duration_str = "1 D"
        if num_bars > 0:
            if bar_size == BarSize.ONE_MINUTE:
                duration_str = str(num_bars * 60) + " S"
            elif bar_size == BarSize.FIVE_MINUTES:
                duration_str = str(num_bars * 60 * 5) + " S"
            elif bar_size == BarSize.ONE_HOUR:
                days = int(math.ceil(num_bars / 8))
                duration_str = str(days) + " D"
            elif bar_size == BarSize.FOUR_HOURS:
                days = int(math.ceil(num_bars / 2))
                duration_str = str(days) + " D"
            elif bar_size == BarSize.ONE_DAY:
                duration_str = str(num_bars) + " D"
            elif bar_size == BarSize.ONE_WEEK:
                duration_str = str(num_bars) + " W"
            else:
                duration_str = str(num_bars) + " D"
        else:
            # Figure out duration from start and end date
            end_dt = (
                current_datetime()
                if end_date_time == ""
                else get_datetime(end_date_time)
            )
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
            f"Sending historical data request for: {ticker_desc.symbol_full}, id={req_id}, bar_size={bar_size_str}, duration={duration_str}"
        )
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
        self.reqHistoricalData(
            req_id,
            new_contract,
            end_date_time,
            duration_str,
            bar_size_str,
            request_info_type.value,
            1,
            1,
            live_data,
            [],
        )
        self._logger.info(f"Completed request {req_id}.")

    def _historical_data_cb(self, req_id: int, in_bar: BarData, real_time: bool):
        """
        Receives a single bar of historical data. This function is called multiple times, when multiple bars of data
        are requested.

        :param req_id: applicable request
        :param in_bar: --
        :param real_time: True if this is a real-time update, for current bar
        """
        req_obj = self._request_bardata_objects.get(req_id)
        if req_obj:
            dt = get_datetime(in_bar.date)
            if (
                req_obj.earliest_permitted_dt is None
                or dt >= req_obj.earliest_permitted_dt
            ):
                req_obj.add_or_update_bar(in_bar, allow_update=real_time)

    def _historical_data_end_cb(self, req_id: int, start: str, end: str):
        """
        Called when all historical data has been sent, in response to a particular request.
        :param req_id: applicable request
        :param start: --
        :param end: --
        """
        self._logger.info(
            f"Historical Data Ended for {req_id}. Started at {start}, ending at {end}"
        )
        req_obj = self._request_bardata_objects.get(req_id)
        if req_obj:
            req_obj.data_fetch_complete = True

    def _request_head_timestamp(self, req_id: int, contract: Contract):
        """Requests head timestamp (datetime of earliest bar) from TWS"""
        # Request Head Timestamp
        #     reqId: ID of request
        #     contract: Contract object
        #     whatToShow: kind of info (e.g. 'BID', 'ASK', 'OPTION_IMPLIED_VOLATILITY', 'TRADES'). Some choices won't return volume data.
        #     useRTH: 1 for regular trading hours only, 0 otherwise
        #     formatDate: 1 for human-readable string, 2 for system format
        self.reqHeadTimeStamp(req_id, contract, "TRADES", 1, 1)

    def _head_timestamp_cb(self, req_id: int, start: str):
        """
        Called when info about earliest timestamp for particular security has been sent.
        :param req_id: applicable request
        :param start: the earliest timestamp
        """
        symbol = self._head_timestamp_map.get(req_id)
        if not symbol:
            return
        self._symbol_to_head_timestamp[symbol] = start

    def _contract_details_cb(self, req_id: int, contract_details: ContractDetails):
        """Called when a ContractDetails object has arrived"""
        req_obj = self._request_contractdetail_objects.get(req_id)
        if req_obj:
            req_obj.add_contract_details(contract_details)

    def _contract_details_end_cb(self, req_id: int):
        """Called when ALL ContractDetails objects have arrived, in response to last request"""
        req_obj = self._request_contractdetail_objects.get(req_id)
        if req_obj:
            req_obj.data_fetch_complete = True

    def _option_chain_cb(
        self,
        req_id: int,
        exchange: str,
        underlying_con_id: int,
        trading_class: str,
        multiplier: str,
        expirations: Set,
        strikes: Set,
    ):
        """
        Called when info about options for a particular security arrived.
        :param req_id: request ID
        :param exchange: the exchange supplying the info, e.g. "SMART" or "BOX"
        :param underlying_con_id: contract ID for underlying security
        :param trading_class: name of underlying symbol, e.g. SPY
        :param multiplier: usually 100, as is standard for options
        :param expirations: set of expirations
        :param strikes: set of strikes
        """
        req_obj = self._request_optionchain_objects.get(req_id)
        if req_obj:
            option_chain_info = OptionChainInfo()
            option_chain_info.exchange = exchange
            option_chain_info.underlying = trading_class
            option_chain_info.multiplier = int(multiplier)
            option_chain_info.expirations = copy.copy(expirations)
            option_chain_info.strikes = copy.copy(strikes)
            req_obj.add_option_chain_info(option_chain_info)

    def _option_chain_end_cb(self, req_id: int):
        """Called when ALL option chain info has been sent"""
        req_obj = self._request_optionchain_objects.get(req_id)
        if req_obj:
            req_obj.data_fetch_complete = True

    def _tick_option_computation_cb(
        self,
        req_id: TickerId,
        tick_type: TickType,
        tick_attrib: int,
        implied_vol: float,
        delta: float,
        opt_price: float,
        pv_dividend: float,
        gamma: float,
        vega: float,
        theta: float,
        underlying_price: float,
    ):
        """
        Called when info about an option's "Greeks" arrives. We only want to use it if tick type is 13.
        See: https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#available-tick-types
        """
        req_obj = self._request_option_objects.get(req_id)
        if req_obj and tick_type == 13:
            req_obj.option_info.implied_volatility = implied_vol
            req_obj.option_info.delta = delta
            req_obj.option_info.price = opt_price
            req_obj.option_info.gamma = gamma
            req_obj.option_info.vega = vega
            req_obj.option_info.theta = theta
            req_obj.option_info.underlying_price = underlying_price
            req_obj.option_info.set_greeks_defined()

    def _tick_size_cb(self, req_id: TickerId, tick_type: TickType, size: Decimal):
        """
        Called when info about an option's open interest or volume arrives. We only want to use it if tick
        type is 8 or 27 - 30.
        See: https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#available-tick-types
        """
        req_obj = self._request_option_objects.get(req_id)
        if req_obj:
            if int(tick_type) == 27:
                req_obj.option_info.set_open_interest(int(size), for_call=True)
            if int(tick_type) == 28:
                req_obj.option_info.set_open_interest(int(size), for_call=False)
            if int(tick_type) == 29:
                req_obj.option_info.set_volume(int(size), for_call=True)
            if int(tick_type) == 30:
                req_obj.option_info.set_volume(int(size), for_call=False)
            if int(tick_type) == 8:
                req_obj.option_info.set_volume(
                    int(size), for_call=req_obj.option_info.is_call
                )

    def _error_cb(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        advanced_order_reject_json="",
    ):
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
            err_out = (
                "Error (ignorable): code is "
                + str(error_code)
                + ", string is "
                + error_string
            )
            self._logger.info(err_out)
        else:
            err_out = (
                "Error: code is " + str(error_code) + ", string is " + error_string
            )
            self._logger.warning(err_out)
            req_obj = self._request_bardata_objects.get(req_id)
            if not req_obj:
                req_obj = self._request_contractdetail_objects.get(req_id)
            if not req_obj:
                req_obj = self._request_optionchain_objects.get(req_id)
            if req_obj:
                req_obj.last_error_code = error_code
                req_obj.last_error_string = error_string

    def _make_contract(
        self,
        ticker: str,
        primary_exchange: str = None,
        is_option: bool = False,
        is_call: bool = False,
        strike: Optional[float] = None,
        expiration: Optional[str] = None,
    ) -> Contract:
        """Makes and returns an IB Contract"""

        """
        Option contract example:

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
        the_contract = Contract()
        the_contract.symbol = ticker
        the_contract.secType = "STK"
        the_contract.exchange = "SMART"
        the_contract.currency = "USD"
        if is_option:
            the_contract.secType = "OPT"
            the_contract.right = "C" if is_call else "P"
            if strike is not None:
                the_contract.strike = int(strike)
            if expiration is not None:
                the_contract.lastTradeDateOrContractMonth = expiration
            the_contract.multiplier = "100"
        if primary_exchange:
            the_contract.primaryExchange = primary_exchange
        return the_contract

    async def _set_market_data_type(self, live: bool = True):
        """Call before calling reqMktData() to set whether live or frozen data"""
        self.reqMarketDataType(1 if live else 2)
