from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Union
from datetime import datetime

from core.common import (
    HistoricalData,
    RequestedInfoType,
    OptionChainInfo,
    OptionInfo,
    OrderType,
    OrderInfo,
    OrderAction,
    PositionsInfo,
    TradesInfo,
    EarningsInfo,
)
from core.utils import BarSize


class BaseDriver(ABC):
    """
    Abstract base class for broker drivers. Defines the public interface that all broker-specific
    driver implementations must provide. This allows the rest of the system to be agnostic about
    which brokerage is in use.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Attempts to connect to the brokerage. Returns True if successful."""

    @abstractmethod
    def disconnect(self):
        """Disconnects from the brokerage."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Returns True if currently connected to the brokerage."""

    @abstractmethod
    async def get_historical_data(
        self,
        symbol_full: str,
        num_bars: int = 0,
        bar_size: BarSize = BarSize.ONE_DAY,
        end_date: Optional[Union[datetime, str]] = None,
        start_date: Optional[Union[datetime, str]] = None,
        live_data: bool = False,
        request_info_type: RequestedInfoType = RequestedInfoType.TRADES,
        regular_trading_hours_only: bool = True,
        primary_exchange: Optional[str] = None,
    ) -> Tuple[HistoricalData, Optional[str]]:
        """
        Requests historical bar data and waits for it to arrive before returning. "Historical" is not necessarily
        the best word, as the data returned can be live and continuously updating, though bars other than the
        current one will still be historical.

        :param symbol_full: stock ticker, e.g. AAPL or SPY-C-20250627-600.0
        :param num_bars: how many bars of data to collect; if 0, start_date determines range
        :param bar_size: daily, hourly, weekly, etc.
        :param end_date: end of last bar in range; str format '20250523 16:00:00 US/Eastern'
        :param start_date: start of first bar in range; str format '20250523 09:30:00 US/Eastern'
        :param live_data: if True, data will continue to flow in
        :param request_info_type: type of info to get, e.g. TRADES or IMPLIED_VOLATILITY
        :param regular_trading_hours_only: if True, excludes premarket/extended hours data
        :param primary_exchange: if given, primary exchange to use; if None, use default
        :return: (HistoricalData, error str or None)
        """

    @abstractmethod
    async def cancel_historical_data(self, historical_data: HistoricalData):
        """Cancels a historical data request (e.g. for streaming/live data)."""

    @abstractmethod
    async def cancel_all_historical_data(self):
        """Cancels all active historical data requests."""

    @abstractmethod
    async def get_most_recent_data(
        self,
        symbol_full: str,
        bar_size: BarSize = BarSize.ONE_DAY,
        request_info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> Tuple[Optional[Tuple[dict, datetime]], Optional[str]]:
        """
        Gets the most recent bar of data.

        :param symbol_full: e.g. AAPL or SPY-C-20250627-600.0
        :param bar_size: daily, hourly, weekly, etc.
        :param request_info_type: type of info to get
        :return: ((bar dict, datetime) or None, error string or None)
        """

    @abstractmethod
    async def get_head_timestamp(
        self,
        ticker: str,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        primary_exchange: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        Returns the head timestamp for a particular ticker — the earliest datetime for which
        the brokerage has data.
        """

    @abstractmethod
    async def get_implied_volatility(
        self,
        ticker: str,
        primary_exchange: Optional[str] = None,
    ) -> Optional[float]:
        """
        Returns the most current implied volatility value (as a fraction, e.g. 0.18) for the specified
        stock/ETF, or None if it can't be retrieved.
        """

    @abstractmethod
    async def get_option_info(
        self,
        ticker: str,
        primary_exchange: str = None,
        is_call: bool = False,
        strike: Optional[float] = None,
        expiration: Optional[str] = None,
    ) -> Tuple[List[OptionInfo], Optional[str]]:
        """
        Get information about all options matching the given parameters.

        :param ticker: symbol of underlying
        :param primary_exchange: --
        :param is_call: True if call, False if put
        :param strike: strike price
        :param expiration: expiration date
        :return: (list of OptionInfo, error message or None)
        """

    @abstractmethod
    async def get_option_info_single(
        self,
        ticker: str,
        is_call: bool,
        strike: float,
        expiration: str,
        primary_exchange: str = None,
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        """
        Get information about a particular option contract.

        :param ticker: symbol of underlying
        :param is_call: True if call, False if put
        :param strike: strike price
        :param expiration: expiration date
        :param primary_exchange: --
        :return: (OptionInfo or None, error message or None)
        """

    @abstractmethod
    async def get_options_chain_info(
        self, ticker: str, primary_exchange: Optional[str] = None
    ) -> Tuple[Optional[OptionChainInfo], Optional[str]]:
        """
        Gets basic information about the option chain for a stock.

        :param ticker: ticker of underlying stock
        :param primary_exchange: primary exchange or None
        :return: (OptionChainInfo or None, error string or None)
        """

    @abstractmethod
    async def get_greeks(
        self, option_info: OptionInfo, primary_exchange: Optional[str] = None
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        """
        Gets price, Greeks, volume, and open interest for a particular option.

        :param option_info: info about the option
        :param primary_exchange: if given, primary exchange to use
        :return: (OptionInfo or None, error string or None)
        """

    @abstractmethod
    async def place_order(
        self,
        symbol_full: str,
        primary_exchange: str = None,
        action: OrderAction = OrderAction.BUY,
        quantity: int = 0,
        price: float = 0.0,
        order_type: OrderType = OrderType.MARKET,
        transmit: bool = True,
        parent_order: Optional[OrderInfo] = None,
    ) -> Tuple[OrderInfo, Optional[str]]:
        """
        Places an order with the brokerage.

        :param symbol_full: stock ticker, e.g. AAPL or SPY-C-20250627-600.0
        :param primary_exchange: --
        :param action: buy or sell
        :param quantity: number of shares/contracts
        :param price: desired price (for limit or stop orders)
        :param order_type: market, limit, stop, or stop limit
        :param transmit: if True, transmit this order (and any pending parent orders) to broker
        :param parent_order: parent order for bracket/attached orders
        :return: (OrderInfo, error string or None)
        """

    @abstractmethod
    async def change_order(
        self,
        order: OrderInfo,
        action: OrderAction = OrderAction.BUY,
        quantity: int = 0,
        price: float = 0.0,
        order_type: OrderType = OrderType.MARKET,
        parent_order: Optional[OrderInfo] = None,
    ) -> Tuple[OrderInfo, Optional[str]]:
        """
        Modifies an existing order on the brokerage server.

        :param order: reference to the original order
        :param action: buy or sell
        :param quantity: number of shares/contracts
        :param price: desired price (for limit or stop orders)
        :param order_type: market, limit, stop, or stop limit
        :param parent_order: parent order if applicable
        :return: (OrderInfo, error string or None)
        """

    @abstractmethod
    async def cancel_order(self, order_info: OrderInfo):
        """Cancels an order, given the OrderInfo object."""

    @abstractmethod
    async def cancel_all_orders(self):
        """Cancels all active orders."""

    @abstractmethod
    async def get_positions(self) -> Tuple[PositionsInfo, Optional[str]]:
        """Gets info about positions currently held in the account."""

    @abstractmethod
    async def get_trades(
        self, start_dt: datetime, end_dt: Optional[datetime] = None
    ) -> Tuple[TradesInfo, Optional[str]]:
        """
        Gets trades made by the account holder over a date range.

        :param start_dt: returned trades will be no older than this datetime
        :param end_dt: returned trades will be no newer than this datetime; defaults to the current datetime
        :return: (TradesInfo, error string or None)
        """

    @abstractmethod
    async def get_earnings_dates(self, ticker: str) -> Tuple[EarningsInfo, Optional[str]]:
        """
        Gets past and upcoming earnings dates for a stock.

        :param ticker: stock ticker, e.g. AAPL
        :return: (EarningsInfo, error string or None)
        """
