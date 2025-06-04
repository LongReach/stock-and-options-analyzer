import asyncio
import copy
import math

from ibapi.contract import Contract, ContractDetails
from ibapi.client import EClient
from ibapi.order import *
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString
from ibapi.wrapper import EWrapper, OrderId
from logging import getLogger, basicConfig
import threading
import time
from typing import Optional, Dict, List, Tuple, Union, Set
from enum import Enum, auto
from datetime import datetime, timedelta

from core.common import HistoricalData, RequestedInfoType, SecurityDescriptor
from core.utils import wait_for_condition, get_datetime, get_datetime_as_str, BarSize
from core.ib_driver_requests import (
    ContractDetailsRequest,
    OptionChainInfoRequest,
    BarDataRequest,
    IBDriverException,
)

LIVE_PORT = 4001
SIM_PORT = 4002
NUM_CONNECT_TRIES = 10
HISTORICAL_DATA_TIMEOUT = 10.0


class IBDriver(EWrapper, EClient):
    """
    This class extends IB's EWrapper and EClient, allowing communication to and from TWS. Commands go out
    via functions in EClient and responses come back to the callbacks inherited from EWrapper.

    This is an async interface, meant to abstract away the threaded-ness of IBWrapper. Note how the function
    get_historical_data() waits for all data to arrive before returning a result.
    """

    def __init__(self, sim_account: bool, client_id: int = 0):
        EClient.__init__(self, self)
        self.request_id: Optional[OrderId] = None
        self._app_thread: Optional[threading.Thread] = None
        self._sim_account = sim_account
        self._client_id = client_id

        # Maps request ID to BarDataRequest, which receives arriving data
        self._request_bardata_objects: Dict[int, BarDataRequest] = {}
        # Maps a request ID to a ContractDetailsRequest object
        self._request_contractdetail_objects: Dict[int, ContractDetailsRequest] = {}
        self._request_optionchain_objects: Dict[int, OptionChainInfoRequest] = {}

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
        Gets the most recent bar of data
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
    ) -> Tuple[Optional[ContractDetails], Optional[str]]:
        """
        Returns an IB ContractDetails object for given ticker
        :param ticker: ticker for stock, or underlying, if option
        :param primary_exchange: --
        :param is_option: True if option
        :param is_call: True if call, False if put
        :param strike: strike price of option
        :param expiration: expiration date, in IB format
        :return: (ContractDetails or None, error string or None)
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
        ret_cd = req_obj.details_list[0] if len(req_obj.details_list) > 0 else None

        async with self._lock:
            self._request_contractdetail_objects.pop(req_id, None)

        return ret_cd, ret_error_str

    def get_full_symbol_from_contract_details(
        self, contract_details: ContractDetails
    ) -> str:
        """
        Given a ContractDetails object, return a full symbol name, e.g. "SPY" or "SPY-C-20250627-600.0" (if option)
        """
        contract = contract_details.contract
        if contract.secType == "OPT":
            return f"{contract.symbol}-{contract.right}-{contract.lastTradeDateOrContractMonth}-{contract.strike}"

        return contract.symbol

    async def get_options_chain_info(self, ticker: str, underlying_contract_id: int):
        async with self._lock:
            req_id = self.next_id()
            req_obj = self._request_optionchain_objects[req_id] = (
                OptionChainInfoRequest(ticker)
            )

        req_obj.data_fetch_complete = False
        print("**** get_options_chain_info()")
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

        exp_list = sorted(req_obj.expirations)
        print(f"Expirations are {exp_list}")
        strike_list = sorted(req_obj.strikes)
        print(f"Strikes are {strike_list}")

        async with self._lock:
            self._request_optionchain_objects.pop(req_id, None)

    # ---------------------------------------------------
    # Callbacks
    # ---------------------------------------------------

    def nextValidId(self, request_id: OrderId):
        """
        Called by TWS when a valid request ID is established. We are not properly connected until we have one.
        This will be the ID used for the first request, with each subsequent request incrementing it by one.
        "OrderId" seems to a misnaming.

        Overrides method in EWrapper.
        """
        super().nextValidId(request_id)
        self.request_id = request_id

    def historicalData(self, req_id: int, bar: BarData):
        """
        Called by TWS when a bar of historical data comes in. Not called for updates to historical data (for
        current bar), only for the data that's actually in the past.

        Overrides method in EWrapper.

        :param req_id: request ID
        :param bar: info about bar of data
        """
        super().historicalData(req_id, bar)
        self._historical_data_cb(req_id, bar, False)

    def historicalDataUpdate(self, req_id: int, bar: BarData):
        """
        Called by TWS when a bar of updated historical data comes in, i.e. for the current bar. Not called when
        we're fetching past historical data only, with no updates. The updates happen rapidly, many times over the
        course of a bar. High, low, and close can change. The date always matches the start of a bar.

        Overrides method in EWrapper.

        :param req_id: request ID
        :param bar: info about bar of data
        """
        super().historicalDataUpdate(req_id, bar)
        print(f"**** Live data for {req_id} is {bar}")
        self._historical_data_cb(req_id, bar, True)

    def historicalDataEnd(self, req_id: int, start: str, end: str):
        """
        Called by TWS when all the historical data requested has arrived.

        :param req_id: request ID
        :param start: date of first bar of data
        :param end: date of last bar of data
        """
        super().historicalDataEnd(req_id, start, end)
        self._historical_data_end_cb(req_id, start, end)

    def headTimestamp(self, req_id: int, head_time_stamp: str):
        super().headTimestamp(req_id, head_time_stamp)
        self._head_timestamp_cb(req_id, head_time_stamp)

    def securityDefinitionOptionParameter(
        self,
        req_id: int,
        exchange: str,
        underlying_con_id: int,
        trading_class: str,
        multiplier: str,
        expirations: SetOfString,
        strikes: SetOfFloat,
    ):
        super().securityDefinitionOptionParameter(
            req_id,
            exchange,
            underlying_con_id,
            trading_class,
            multiplier,
            expirations,
            strikes,
        )
        self._option_chain_cb(
            req_id,
            exchange,
            underlying_con_id,
            trading_class,
            multiplier,
            set(expirations),
            set(strikes),
        )
        # print("SecurityDefinitionOptionParameter.",
        #    "ReqId:", req_id, "Exchange:", exchange, "Underlying conId:", intMaxString(underlying_con_id),
        #    "TradingClass:", trading_class, "Multiplier:", multiplier,
        #    "Expirations:", expirations, "Strikes:", str(strikes))

    def securityDefinitionOptionParameterEnd(self, req_id: int):
        super().securityDefinitionOptionParameterEnd(req_id)
        self._option_chain_end_cb(req_id)

    def contractDetails(self, req_id: int, contract_details: ContractDetails):
        super().contractDetails(req_id, contract_details)
        self._contract_details_cb(req_id, contract_details)

    def contractDetailsEnd(self, req_id: int):
        super().contractDetailsEnd(req_id)
        self._contract_details_end_cb(req_id)

    def error(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        advanced_order_reject_json="",
    ):
        """Called by TWS when there's an error with a request."""
        super().error(req_id, error_code, error_string, advanced_order_reject_json)
        self._error_cb(req_id, error_code, error_string, advanced_order_reject_json)

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
                datetime.now() if end_date_time == "" else get_datetime(end_date_time)
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
        req_obj = self._request_contractdetail_objects.get(req_id)
        if req_obj:
            req_obj.details_list.append(contract_details)

    def _contract_details_end_cb(self, req_id: int):
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
        req_obj = self._request_optionchain_objects.get(req_id)
        if req_obj:
            req_obj.expirations = copy.copy(expirations)
            req_obj.strikes = copy.copy(strikes)

    def _option_chain_end_cb(self, req_id: int):
        req_obj = self._request_optionchain_objects.get(req_id)
        if req_obj:
            req_obj.data_fetch_complete = True

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
        the_contract = Contract()
        the_contract.symbol = ticker
        the_contract.secType = "STK"
        the_contract.exchange = "SMART"
        the_contract.currency = "USD"
        if is_option:
            the_contract.secType = "OPT"
            the_contract.right = "C" if is_call else "P"
            the_contract.strike = int(strike)
            the_contract.lastTradeDateOrContractMonth = expiration
            the_contract.multiplier = "100"
        if primary_exchange:
            the_contract.primaryExchange = primary_exchange
        return the_contract
