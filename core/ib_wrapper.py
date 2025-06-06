import asyncio
import copy
import math

from _decimal import Decimal
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

LIVE_PORT = 4001
SIM_PORT = 4002
NUM_CONNECT_TRIES = 10
HISTORICAL_DATA_TIMEOUT = 10.0


class IBWrapper(EWrapper, EClient):
    """
    This class extends IB's EWrapper and EClient, allowing communication to and from TWS. Commands go out
    via functions in EClient and responses come back to the callbacks inherited from EWrapper.

    This class is meant to do little except implement the callbacks that receive data from TWS.
    Most of the work is done by this class's child, IBDriver.
    """

    def __init__(self):
        EClient.__init__(self, self)
        self.request_id: Optional[OrderId] = None

        self._logger = getLogger(__file__)

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

    def marketDataType(self, req_id: TickerId, market_data_type: int):
        """
        Called by TWS to report on market data type being used (1 = live, 2 = frozen)
        """
        self._logger.info(f"Market data type for {req_id} is {market_data_type}")

    def historicalData(self, req_id: int, bar: BarData):
        """
        Called by TWS when a bar of historical data comes in. Not called for updates to historical data (for
        current bar), only for the data that's actually in the past.

        Response for reqHistoricalData()
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

        Response for reqHistoricalData()
        Overrides method in EWrapper.

        :param req_id: request ID
        :param bar: info about bar of data
        """
        super().historicalDataUpdate(req_id, bar)
        self._historical_data_cb(req_id, bar, True)

    def historicalDataEnd(self, req_id: int, start: str, end: str):
        """
        Called by TWS when all the historical data requested has arrived.

        Response for reqHistoricalData()

        :param req_id: request ID
        :param start: date of first bar of data
        :param end: date of last bar of data
        """
        super().historicalDataEnd(req_id, start, end)
        self._historical_data_end_cb(req_id, start, end)

    def headTimestamp(self, req_id: int, head_time_stamp: str):
        """
        Called by TWS when head timestamp for a particular security's data has arrived.

        Response for reqHeadTimeStamp()

        :param req_id: request ID
        :param head_time_stamp: datetime in IB format
        :return:
        """
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
        """
        Called by TWS when info about options for a particular security arrived.
        This allows user to find out about expirations/strikes available for
        a particular option.

        Response for reqSecDefOptParams()

        :param req_id: request ID
        :param exchange: the exchange supplying the info, e.g. "SMART" or "BOX"
        :param underlying_con_id: contract ID for underlying security
        :param trading_class: name of underlying symbol, e.g. SPY
        :param multiplier: usually 100, as is standard for options
        :param expirations: set of expirations
        :param strikes: set of strikes
        """
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
        #   "ReqId:", req_id, "Exchange:", exchange, "Underlying conId:", intMaxString(underlying_con_id),
        #   "TradingClass:", trading_class, "Multiplier:", multiplier,
        #   "Expirations:", expirations, "Strikes:", str(strikes))

    def securityDefinitionOptionParameterEnd(self, req_id: int):
        """
        Called by TWS when *ALL* info about options for a particular security has arrived.

        Response for reqSecDefOptParams()

        :param req_id: request ID
        """
        super().securityDefinitionOptionParameterEnd(req_id)
        self._option_chain_end_cb(req_id)

    def contractDetails(self, req_id: int, contract_details: ContractDetails):
        """
        Called by TWS when contractDetails have arrived (each exchange sends their own)

        Response for reqContractDetails()

        :param req_id: request ID
        :param contract_details: ContractDetails object
        """
        super().contractDetails(req_id, contract_details)
        self._contract_details_cb(req_id, contract_details)

    def contractDetailsEnd(self, req_id: int):
        """
        Called by TWS when ALL contractDetails have arrived

        Response for reqContractDetails()

        :param req_id: request ID
        """
        super().contractDetailsEnd(req_id)
        self._contract_details_end_cb(req_id)

    def tickOptionComputation(
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
        Called by TWS when info about an option comes in. Response to reqMktData().
        """
        super().tickOptionComputation(
            req_id,
            tick_type,
            tick_attrib,
            implied_vol,
            delta,
            opt_price,
            pv_dividend,
            gamma,
            vega,
            theta,
            underlying_price,
        )
        # print(
        #    f"tickOptionComputation: req_id={req_id}, tick_type={tick_type}, tick_attrib={tick_attrib}, opt_price={opt_price}, underlying_price={underlying_price}, delta={delta}, theta={theta}, IV={implied_vol}"
        # )
        self._tick_option_computation_cb(
            req_id,
            tick_type,
            tick_attrib,
            implied_vol,
            delta,
            opt_price,
            pv_dividend,
            gamma,
            vega,
            theta,
            underlying_price,
        )

    def tickSize(self, req_id: TickerId, tick_type: TickType, size: Decimal):
        """
        Called by TWS when volume or open interest info about an option comes in. Response to reqMktData().
        """
        super().tickSize(req_id, tick_type, size)
        # print(f"tickSize: req_id={req_id}, tick_type={tick_type}, size={size}")
        self._tick_size_cb(req_id, tick_type, size)

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

    def _historical_data_cb(self, req_id: int, in_bar: BarData, real_time: bool):
        """
        Receives a single bar of historical data. This function is called multiple times, when multiple bars of data
        are requested.

        :param req_id: applicable request
        :param in_bar: --
        :param real_time: True if this is a real-time update, for current bar
        """
        pass

    def _historical_data_end_cb(self, req_id: int, start: str, end: str):
        """
        Called when all historical data has been sent, in response to a particular request.
        :param req_id: applicable request
        :param start: --
        :param end: --
        """
        pass

    def _head_timestamp_cb(self, req_id: int, start: str):
        """
        Called when info about earliest timestamp for particular security has been sent.
        :param req_id: applicable request
        :param start: the earliest timestamp
        """
        pass

    def _contract_details_cb(self, req_id: int, contract_details: ContractDetails):
        """Called when a ContractDetails object has arrived"""
        pass

    def _contract_details_end_cb(self, req_id: int):
        """Called when ALL ContractDetails objects have arrived, in response to last request"""
        pass

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
        pass

    def _option_chain_end_cb(self, req_id: int):
        """Called when ALL option chain info has been sent"""
        pass

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
        pass

    def _tick_size_cb(self, req_id: TickerId, tick_type: TickType, size: Decimal):
        """
        Called when info about an option's open interest or volume arrives. We only want to use it if tick
        type is 8 or 27 - 30.
        See: https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#available-tick-types
        """
        pass

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
        pass
