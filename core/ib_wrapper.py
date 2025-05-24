from ibapi.client import EClient
from ibapi.wrapper import EWrapper, OrderId
from ibapi.common import BarData
from ibapi.contract import ContractDetails
from ibapi.contract import Contract
from datetime import datetime
from logging import getLogger
from typing import Optional, List, Callable

# This class handles callbacks that come in from TWS, as a response to requests sent to TWS. It overrides several
# functions that are part of EWrapper for this purpose
class IBWrapper(EWrapper, EClient):
    """
    This class, along with IBDriver, is used to communicate with TWS. It handles responses to commands and
    requests send to TWS.
    """

    def __init__(self):
        EClient.__init__(self, self)
        self.order_id: Optional[OrderId] = None
        self.historical_data_cb: Optional[Callable[[int, BarData], None]] = None
        self.historical_data_end_cb: Optional[Callable[[int, str, str], None]] = None
        self._logger = getLogger(__file__)

    def is_connected(self):
        """Returns True if a connection with TWS has been achieved"""
        return self.order_id is not None

    def set_historical_data_cb(self, cb: Callable[[int, BarData], None]):
        """Sets callback to receive incoming historical data"""
        self.historical_data_cb = cb

    def set_historical_data_end_response_cb(self, cb: Callable[[int, str, str], None]):
        """Sets callback to receive message about end of incoming historical data"""
        self.historical_data_end_cb = cb

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
        self.historical_data_cb(req_id, bar)

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
        self.historical_data_cb(req_id, bar)

    def historicalDataEnd(self, req_id: int, start: str, end: str):
        """
        Called by TWS when all the historical data requested has arrived.

        :param req_id: request ID
        :param start: date of first bar of data
        :param end: date of last bar of data
        """
        super().historicalDataEnd(req_id, start, end)
        self.historical_data_end_cb(req_id, start, end)

    def error(self, req_id, error_code: int, error_string: str, advanced_order_reject_json=""):
        """Called when there's an error with a request."""
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

