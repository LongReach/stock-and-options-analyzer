import asyncio
import copy
import math

from _decimal import Decimal
from ibapi.contract import Contract, ContractDetails
from ibapi.client import EClient
from ibapi.order import *
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString, TickerId
from ibapi.ticktype import TickType
from ibapi.wrapper import EWrapper, OrderId, OrderState, Execution
from logging import getLogger, basicConfig
import threading
import time
from typing import Optional, Dict, List, Tuple, Union, Set, Callable, Any
from enum import Enum, auto, IntEnum
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


class CallbackID(IntEnum):
    """
    For identifying the various callbacks that can be triggered by responses from IB
    """
    HISTORICAL_DATA_CB = 0
    HISTORICAL_DATA_END_CB = 1
    HEAD_TIMESTAMP_CB = 2
    CONTRACT_DETAILS_CB = 3
    CONTRACT_DETAILS_END_CB = 4
    OPTION_CHAIN_CB = 5
    OPTION_CHAIN_END_CB = 6
    TICK_OPTION_COMPUTATION_CB = 7
    TICK_SIZE_CB = 8
    ERROR_CB = 9


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

        # For storing callbacks that handle responses from IB and go back to functions in IBDriver
        self._callback_map: Dict[CallbackID, Callable[..., Any]] = {}

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
        self._verify_callback(CallbackID.HISTORICAL_DATA_CB)
        self._callback_map[CallbackID.HISTORICAL_DATA_CB](req_id, bar, False)

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
        self._verify_callback(CallbackID.HISTORICAL_DATA_CB)
        self._callback_map[CallbackID.HISTORICAL_DATA_CB](req_id, bar, True)

    def historicalDataEnd(self, req_id: int, start: str, end: str):
        """
        Called by TWS when all the historical data requested has arrived.

        Response for reqHistoricalData()

        :param req_id: request ID
        :param start: date of first bar of data
        :param end: date of last bar of data
        """
        super().historicalDataEnd(req_id, start, end)
        self._verify_callback(CallbackID.HISTORICAL_DATA_END_CB)
        self._callback_map[CallbackID.HISTORICAL_DATA_END_CB](req_id, start, end)

    def headTimestamp(self, req_id: int, head_time_stamp: str):
        """
        Called by TWS when head timestamp for a particular security's data has arrived.

        Response for reqHeadTimeStamp()

        :param req_id: request ID
        :param head_time_stamp: datetime in IB format
        :return:
        """
        super().headTimestamp(req_id, head_time_stamp)
        self._verify_callback(CallbackID.HEAD_TIMESTAMP_CB)
        self._callback_map[CallbackID.HEAD_TIMESTAMP_CB](req_id, head_time_stamp)

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
        self._verify_callback(CallbackID.OPTION_CHAIN_CB)
        self._callback_map[CallbackID.OPTION_CHAIN_CB](req_id,
                                                       exchange,
                                                       underlying_con_id,
                                                       trading_class,
                                                       multiplier,
                                                       set(expirations),
                                                       set(strikes)
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
        self._verify_callback(CallbackID.OPTION_CHAIN_END_CB)
        self._callback_map[CallbackID.OPTION_CHAIN_END_CB](req_id)

    def contractDetails(self, req_id: int, contract_details: ContractDetails):
        """
        Called by TWS when contractDetails have arrived (each exchange sends their own)

        Response for reqContractDetails()

        :param req_id: request ID
        :param contract_details: ContractDetails object
        """
        super().contractDetails(req_id, contract_details)
        self._verify_callback(CallbackID.CONTRACT_DETAILS_CB)
        self._callback_map[CallbackID.CONTRACT_DETAILS_CB](req_id, contract_details)

    def contractDetailsEnd(self, req_id: int):
        """
        Called by TWS when ALL contractDetails have arrived

        Response for reqContractDetails()

        :param req_id: request ID
        """
        super().contractDetailsEnd(req_id)
        self._verify_callback(CallbackID.CONTRACT_DETAILS_END_CB)
        self._callback_map[CallbackID.CONTRACT_DETAILS_END_CB](req_id)

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
        self._verify_callback(CallbackID.TICK_OPTION_COMPUTATION_CB)
        self._callback_map[CallbackID.TICK_OPTION_COMPUTATION_CB](req_id,
                                                                  tick_type,
                                                                  tick_attrib,
                                                                  implied_vol,
                                                                  delta,
                                                                  opt_price,
                                                                  pv_dividend,
                                                                  gamma,
                                                                  vega,
                                                                  theta,
                                                                  underlying_price)

    def tickSize(self, req_id: TickerId, tick_type: TickType, size: Decimal):
        """
        Called by TWS when volume or open interest info about an option comes in. Response to reqMktData().
        """
        super().tickSize(req_id, tick_type, size)
        # print(f"tickSize: req_id={req_id}, tick_type={tick_type}, size={size}")
        self._verify_callback(CallbackID.TICK_SIZE_CB)
        self._callback_map[CallbackID.TICK_SIZE_CB](req_id, tick_type, size)

    def orderStatus(
            self,
            orderId: OrderId,
            status: str,
            filled: Decimal,
            remaining: Decimal,
            avgFillPrice: float,
            permId: int,
            parentId: int,
            lastFillPrice: float,
            clientId: int,
            whyHeld: str,
            mktCapPrice: float,
    ):
        """
        This event is called whenever the status of an order changes. It is also fired after reconnecting to TWS if the
        client has any open orders.

        :param orderId: The order ID that was specified previously in the call to placeOrder()
        :param status: The order status. Possible values include:
            PendingSubmit - indicates that you have transmitted the order, but have not  yet received confirmation that
                it has been accepted by the order destination. NOTE: This order status is not sent by TWS and should be
                explicitly set by the API developer when an order is submitted.
            PendingCancel - indicates that you have sent a request to cancel the order but have not yet received cancel
                confirmation from the order destination. At this point, your order is not confirmed canceled. You may
                still receive an execution while your cancellation request is pending. NOTE: This order status is
                not sent by TWS and should be explicitly set by the API developer when an order is canceled.
            PreSubmitted - indicates that a simulated order type has been accepted by the IB system and that this order
                has yet to be elected. The order is held in the IB system until the election criteria are met. At that
                time, the order is transmitted to the order destination as specified.
            Submitted - indicates that your order has been accepted at the order destination and is working.
            Cancelled - indicates that the balance of your order has been confirmed canceled by the IB system. This
                could occur unexpectedly when IB or the destination has rejected your order.
            Filled - indicates that the order has been completely filled.
            Inactive - indicates that the order has been accepted by the system (simulated orders) or an exchange
                (native orders) but that currently the order is inactive due to system, exchange or other issues.
        :param filled: Specifies the number of shares that have been executed.
        :param remaining: Specifies the number of shares still outstanding.
        :param avgFillPrice: The average price of the shares that have been executed. This parameter is valid only if
            the filled parameter value is greater than zero. Otherwise, the price parameter will be zero.
        :param permId: The TWS id used to identify orders. Remains the same over TWS sessions.
        :param parentId: The order ID of the parent order, used for bracket and auto trailing stop orders.
        :param lastFillPrice: The last price of the shares that have been executed. This parameter is valid only if the
            filled parameter value is greater than zero. Otherwise, the price parameter will be zero.
        :param clientId: The ID of the client (or TWS) that placed the order. Note that TWS orders have a fixed
            clientId and orderId of 0 that distinguishes them from API orders.
        :param whyHeld: This field is used to identify an order held when TWS is trying to locate shares for a short
            sell. The value used to indicate this is 'locate'.
        :param mktCapPrice:
        :return:
        """
        pass

    def openOrder(
            self, orderId: OrderId, contract: Contract, order: Order, orderState: OrderState
    ):
        """
        This function is called to feed in open orders.

        :param orderId: The order ID assigned by TWS. Use to cancel or update TWS order.
        :param contract: The Contract class attributes describe the contract.
        :param order: The Order class gives the details of the open order.
        :param orderState: The orderState class includes attributes used for both pre and post trade margin and commission data.
        :return:
        """
        pass

    def openOrderEnd(self):
        """This is called at the end of a given request for open orders."""

        pass

    def execDetails(self, reqId: int, contract: Contract, execution: Execution):
        """This event is fired when the reqExecutions() functions is invoked, or when an order is filled."""

        pass

    def execDetailsEnd(self, reqId: int):
        """This function is called once all executions have been sent to a client in response to reqExecutions()."""

        pass

    def error(
            self,
            req_id: int,
            error_code: int,
            error_string: str,
            advanced_order_reject_json="",
    ):
        """Called by TWS when there's an error with a request."""
        super().error(req_id, error_code, error_string, advanced_order_reject_json)
        self._verify_callback(CallbackID.ERROR_CB)
        self._callback_map[CallbackID.ERROR_CB](req_id, error_code, error_string, advanced_order_reject_json)

    def set_callback(self, cb_id: CallbackID, callback: Callable[..., Any]):
        self._callback_map[cb_id] = callback

    def _verify_callback(self, cb_id: CallbackID):
        cb = self._callback_map.get(cb_id)
        if cb is None:
            raise IBDriverException(f"Callback {id} is not set.")
