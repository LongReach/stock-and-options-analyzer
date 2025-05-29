from ibapi.client import EClient
from ibapi.wrapper import EWrapper, OrderId
from ibapi.common import BarData, SetOfString, SetOfFloat, intMaxString
from ibapi.contract import ContractDetails
from ibapi.contract import Contract
from datetime import datetime
from logging import getLogger
from typing import Optional, List, Callable, Any


class IBWrapper(EWrapper, EClient):
    """
    This class, along with IBDriver, is used to communicate with TWS. It handles responses to commands and
    requests send to TWS. It overrides several methods that are part of EWrapper for this purpose.
    """

    def __init__(self):
        EClient.__init__(self, self)
        self.order_id: Optional[OrderId] = None
        self.historical_data_cb: Optional[Callable[[int, BarData, bool], None]] = None
        self.historical_data_end_cb: Optional[Callable[[int, str, str], None]] = None
        self.head_stamp_cb: Optional[Callable[[int, str], None]] = None
        self.contract_details_cb: Optional[Callable[[int, ContractDetails], None]] = None
        self.contract_details_end_cb: Optional[Callable[[int], None]] = None
        self.error_cb: Optional[Callable[[int, int, str, Any], None]] = None
        self._logger = getLogger(__file__)

    def is_connected(self):
        """Returns True if a connection with TWS has been achieved"""
        return self.order_id is not None

    def set_historical_data_cb(self, cb: Callable[[int, BarData, bool], None]):
        """Sets callback to receive incoming historical data"""
        self.historical_data_cb = cb

    def set_historical_data_end_response_cb(self, cb: Callable[[int, str, str], None]):
        """Sets callback to receive message about end of incoming historical data"""
        self.historical_data_end_cb = cb

    def set_head_timestamp_cb(self, cb: Callable[[int, str], None]):
        """Sets callback to receive info about earliest available data"""
        self.head_stamp_cb = cb

    def set_error_cb(self, cb: Callable[[int, int, str, Any], None]):
        """Sets callback to receive message about error"""
        self.error_cb = cb

    def set_contract_details_cb(self, cb: Callable[[int, ContractDetails], None]):
        self.contract_details_cb = cb

    def set_contract_details_end_cb(self, cb: Callable[[int], None]):
        self.contract_details_end_cb = cb

    def next_id(self):
        """Returns next order ID, advancing the counter."""
        self.order_id += 1
        return self.order_id

    # ---------------------------------------------------
    # Callbacks
    # ---------------------------------------------------

    def nextValidId(self, order_id: OrderId):
        """
        Called by TWS when a valid order ID is established. We are not properly connected until we have one.
        Overrides method in EWrapper.
        """
        super().nextValidId(order_id)
        self.order_id = order_id

    def historicalData(self, req_id: int, bar: BarData):
        """
        Called by TWS when a bar of historical data comes in. Not called for updates to historical data (for
        current bar), only for the data that's actually in the past.

        Overrides method in EWrapper.

        :param req_id: request ID
        :param bar: info about bar of data
        """
        super().historicalData(req_id, bar)
        self.historical_data_cb(req_id, bar, False)

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
        self.historical_data_cb(req_id, bar, True)

    def historicalDataEnd(self, req_id: int, start: str, end: str):
        """
        Called by TWS when all the historical data requested has arrived.

        :param req_id: request ID
        :param start: date of first bar of data
        :param end: date of last bar of data
        """
        super().historicalDataEnd(req_id, start, end)
        self.historical_data_end_cb(req_id, start, end)

    def headTimestamp(self, req_id: int, head_time_stamp: str):
        super().headTimestamp(req_id, head_time_stamp)
        self.head_stamp_cb(req_id, head_time_stamp)

    def securityDefinitionOptionParameter(self, req_id: int, exchange: str, underlying_con_id: int,
                                          trading_class: str, multiplier: str,
                                          expirations: SetOfString,
                                          strikes: SetOfFloat):
        super().securityDefinitionOptionParameter(req_id, exchange, underlying_con_id, trading_class, multiplier, expirations, strikes)
        print("SecurityDefinitionOptionParameter.",
            "ReqId:", req_id, "Exchange:", exchange, "Underlying conId:", intMaxString(underlying_con_id),
            "TradingClass:", trading_class, "Multiplier:", multiplier,
            "Expirations:", expirations, "Strikes:", str(strikes))

    def contractDetails(self, req_id: int, contract_details: ContractDetails):
        super().contractDetails(req_id, contract_details)
        self.contract_details_cb(req_id, contract_details)

    def contractDetailsEnd(self, req_id: int):
        super().contractDetailsEnd(req_id)
        self.contract_details_end_cb(req_id)

    def error(self, req_id: int, error_code: int, error_string: str, advanced_order_reject_json=""):
        """Called by TWS when there's an error with a request."""
        super().error(req_id, error_code, error_string, advanced_order_reject_json)
        self.error_cb(req_id, error_code, error_string, advanced_order_reject_json)
