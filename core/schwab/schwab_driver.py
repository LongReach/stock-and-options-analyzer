import asyncio
import math
import os
from datetime import datetime, timezone, timedelta, date
from logging import getLogger
from typing import Optional, List, Tuple, Union, Dict
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from schwab.auth import easy_client
from schwab.client import AsyncClient
from schwab.streaming import StreamClient

from core.base_driver import BaseDriver
from core.common import (
    HistoricalData,
    DataBar,
    RequestedInfoType,
    SecurityDescriptor,
    OptionChainInfo,
    OptionInfo,
    OrderType,
    OrderInfo,
    OrderAction,
    PositionsInfo,
    TradeDescriptor,
    TradesInfo,
    EarningsInfo,
    BarSize,
    MARKETS_TIMEZONE,
)
from core.utils import (
    get_datetime,
    get_datetime_as_str,
    bar_size_to_time,
    current_datetime,
    is_trading_hours,
)
from core.schwab.schwab_driver_requests import SchwabDriverException, LiveStream

# schwab-py stores/refreshes the OAuth token here. This file holds a refresh token, so it's gitignored.
DEFAULT_TOKEN_PATH = "schwab_token.json"

# Roughly the number of regular-session minutes in a US equities trading day (9:30-16:00).
MINUTES_PER_TRADING_DAY = 390


class SchwabDriver(BaseDriver):
    """
    Wraps the Charles Schwab trading API, providing the same async interface as IBDriver so the rest of the
    system stays broker-agnostic. Built on top of the third-party `schwab-py` library, whose AsyncClient
    handles the OAuth2 dance and automatically refreshes the 30-minute access token under the hood.

    All data returned is in the generic form used throughout `core` (HistoricalData, DataBar, etc.); Schwab-
    and schwab-py-specific classes are only used inside this module.

    Implemented: historical/live OHLC bar data, account positions, trade history, and options data (chain
    info, per-contract identity, and Greeks/price). Order-placement endpoints remain stubbed and raise
    NotImplementedError.

    Note on paper trading: unlike IB, Schwab's developer API has no paper/sandbox environment. Market data is
    available, but any future order support would hit real money.

    Note on implied volatility: it's not possible to get historical implied volatility from Schwab, only IV
    as it currently stands.
    """

    def __init__(self, token_path: str = DEFAULT_TOKEN_PATH):
        """
        Constructor. Reads Schwab credentials from the project's .env file but does not connect; call connect()
        for that.

        :param token_path: where schwab-py persists (and auto-refreshes) the OAuth token
        """
        load_dotenv()
        # Credentials are held in memory only for as long as this driver lives, and never written anywhere
        # other than schwab-py's token file.
        self._api_key: Optional[str] = os.getenv("API_KEY")
        self._app_secret: Optional[str] = os.getenv("APP_SECRET")
        self._callback: Optional[str] = os.getenv("CALLBACK")
        self._token_path = token_path

        self._client: Optional[AsyncClient] = None
        self._connected = False

        # Maps a HistoricalData id to the LiveStream keeping it updated
        self._live_streams: Dict[int, LiveStream] = {}
        self._lock = asyncio.Lock()

        # BarSizes natively supported by Schwab's price-history endpoint, mapped to schwab-py client methods.
        self._history_method_map: Dict[BarSize, str] = {
            BarSize.ONE_MINUTE: "get_price_history_every_minute",
            BarSize.FIVE_MINUTES: "get_price_history_every_five_minutes",
            BarSize.FIFTEEN_MINUTES: "get_price_history_every_fifteen_minutes",
            BarSize.ONE_DAY: "get_price_history_every_day",
            BarSize.ONE_WEEK: "get_price_history_every_week",
        }

        self._logger = getLogger(__file__)

    @staticmethod
    def create(token_path: str = DEFAULT_TOKEN_PATH) -> "BaseDriver":
        """Factory method that creates and returns a SchwabDriver instance as a BaseDriver reference."""
        return SchwabDriver(token_path=token_path)

    def connect(self) -> bool:
        """
        Builds the schwab-py AsyncClient. If a valid token file exists, this is silent; otherwise
        schwab-py opens a browser for the one-time OAuth login. Returns True if the client was created.
        """
        if self.is_connected():
            self._logger.error("SchwabDriver already connected")
            return False

        missing = [
            name
            for name, value in (
                ("API_KEY", self._api_key),
                ("APP_SECRET", self._app_secret),
                ("CALLBACK", self._callback),
            )
            if not value
        ]
        if missing:
            self._logger.error(f"Missing Schwab credential(s) in .env: {', '.join(missing)}")
            return False

        self._logger.info("Creating Schwab client...")
        try:
            # asyncio=True returns an AsyncClient whose request methods are awaitable. The client transparently
            # refreshes the 30-minute access token using the persisted refresh token.
            self._client = easy_client(
                api_key=self._api_key,
                app_secret=self._app_secret,
                callback_url=self._callback,
                token_path=self._token_path,
                asyncio=True,
            )
        except Exception as e:
            self._logger.error(f"Failed to create Schwab client: {e}")
            return False

        self._connected = True
        self._logger.info("Connected to Schwab.")
        return True

    def disconnect(self):
        """Cancels any live streams and closes the underlying HTTP session. Best-effort and safe to call twice."""
        self._logger.info("Disconnecting from Schwab...")
        self._connected = False

        for live in list(self._live_streams.values()):
            if live.task is not None:
                live.task.cancel()
        self._live_streams.clear()

        if self._client is not None:
            client = self._client
            self._client = None
            # close_async_session() is a coroutine; run it however we can from this synchronous method.
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(client.close_async_session())
            except RuntimeError:
                try:
                    asyncio.run(client.close_async_session())
                except Exception:
                    pass

        self._logger.info("Disconnected.")

    def is_connected(self) -> bool:
        """Returns True if the Schwab client has been created."""
        return self._connected and self._client is not None

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
        Requests historical OHLC bar data from Schwab and waits for it before returning.

        If live_data is True, the returned HistoricalData is first seeded with recent bars and then kept updated
        by a background one-minute-candle stream (CHART_EQUITY). The caller must hang onto the object and can
        stop the stream via cancel_historical_data().

        :param symbol_full: stock/ETF ticker (e.g. AAPL or SPY) or an IB-style option (e.g.
            SPY-C-20260721-742.0). Options are supported for request/response history, but not live streaming.
        :param num_bars: how many bars to collect; if 0, start_date determines the range
        :param bar_size: one of the Schwab-supported sizes (1m, 5m, 15m, 1d, 1w)
        :param end_date: end of the range; datetime or IB-style str '20250523 16:00:00 US/Eastern'
        :param start_date: start of the range; datetime or IB-style str
        :param live_data: if True, stream continuing one-minute candle updates into the result (equities only)
        :param request_info_type: only TRADES is supported
        :param regular_trading_hours_only: if True, exclude pre/post-market data
        :param primary_exchange: unused (Schwab resolves the venue); accepted for interface compatibility
        :return: (HistoricalData, error str or None)
        :raises SchwabDriverException: if the bar size isn't supported by Schwab
        """
        if not self.is_connected():
            return HistoricalData(), "Not connected to Schwab"

        descriptor = SecurityDescriptor(symbol_full)
        is_option = descriptor.is_option()
        if request_info_type != RequestedInfoType.TRADES:
            return HistoricalData(), f"SchwabDriver only supports TRADES data, not {request_info_type.name}"
        if is_option and live_data:
            return HistoricalData(), "SchwabDriver does not support live streaming for options"
        if bar_size not in self._history_method_map:
            raise SchwabDriverException(
                f"Bar size {bar_size.name} is not supported by SchwabDriver "
                f"(supported: {', '.join(bs.name for bs in self._history_method_map)})"
            )

        # Schwab's price-history endpoint takes the OSI option symbol for options, or the plain ticker otherwise.
        symbol = self._descriptor_to_osi(descriptor) if is_option else descriptor.ticker
        end_dt = self._to_market_dt(end_date) if end_date is not None else current_datetime()
        if num_bars > 0:
            start_dt = self._estimate_start_datetime(end_dt, bar_size, num_bars)
        elif start_date is not None:
            start_dt = self._to_market_dt(start_date)
        else:
            # Neither num_bars nor start_date given: fetch a single most-recent bar.
            start_dt = self._estimate_start_datetime(end_dt, bar_size, 1)

        self._logger.info(
            f"get_historical_data(), ticker={symbol}, num_bars={num_bars}, bar_size={bar_size.name}, live={live_data}"
        )

        historical_data, error_str = await self._fetch_history(
            symbol, bar_size, start_dt, end_dt, regular_trading_hours_only, num_bars
        )
        if error_str:
            return historical_data, error_str

        if live_data:
            await self._start_live_stream(symbol, historical_data)

        return historical_data, None

    async def cancel_historical_data(self, historical_data: HistoricalData):
        """Cancels the live stream feeding the given HistoricalData (the streaming/live-data case)."""
        async with self._lock:
            live = self._live_streams.pop(historical_data.get_id(), None)
        if live is None:
            self._logger.warning("No live Schwab stream found for this historical data")
            return
        await self._stop_stream(live)

    async def cancel_all_historical_data(self):
        """Cancels all live streams."""
        async with self._lock:
            streams = list(self._live_streams.values())
            self._live_streams.clear()
        for live in streams:
            await self._stop_stream(live)

    async def get_most_recent_data(
        self,
        symbol_full: str,
        bar_size: BarSize = BarSize.ONE_DAY,
        request_info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> Tuple[Optional[Tuple[dict, datetime]], Optional[str]]:
        """
        Gets the most recent bar of data. A returned bar dict includes: "date", "open", "close", "low", "high",
        "volume".

        :param symbol_full: e.g. AAPL or SPY
        :param bar_size: daily, hourly, weekly, etc.
        :param request_info_type: only TRADES is supported
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

    # ---------------------------------------------------
    # Stubs: not implemented in Version One
    # ---------------------------------------------------

    async def get_head_timestamp(
        self,
        ticker: str,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        primary_exchange: Optional[str] = None,
    ) -> Optional[datetime]:
        raise NotImplementedError("SchwabDriver.get_head_timestamp() is not implemented yet")

    async def get_option_info(
        self,
        ticker: str,
        primary_exchange: str = None,
        is_call: bool = False,
        strike: Optional[float] = None,
        expiration: Optional[str] = None,
    ) -> Tuple[List[OptionInfo], Optional[str]]:
        """
        Gets identity information (name, right, strike, expiration) for every option matching the given filters.
        Price and Greeks are NOT fetched here -- call get_greeks() for that, mirroring IBDriver.

        :param ticker: symbol of underlying, e.g. SPY
        :param primary_exchange: unused (Schwab resolves the venue); accepted for interface compatibility
        :param is_call: True for calls, False for puts
        :param strike: if given, restrict to this strike
        :param expiration: if given (IB-style yyyymmdd), restrict to this expiration
        :return: (list of OptionInfo objects, error string or None)
        """
        if not self.is_connected():
            return [], "Not connected to Schwab"

        contract_type = self._contract_type(is_call)
        payload, error_str = await self._request_option_chain(
            ticker, contract_type, strike=strike, expiration=expiration
        )
        if error_str:
            return [], f"Unable to get option info for {ticker}: {error_str}"

        live = is_trading_hours()
        out_list: List[OptionInfo] = []
        for exp_ib, strk, _detail in self._iter_option_details(payload):
            option_info = OptionInfo()
            option_info.full_name = f"{ticker}-{'C' if is_call else 'P'}-{exp_ib}-{self._strike_str(strk)}"
            option_info.is_call = is_call
            option_info.strike = strk
            option_info.expiration = exp_ib
            option_info.set_live(live)
            out_list.append(option_info)

        return out_list, None

    async def get_option_info_single(
        self,
        ticker: str,
        is_call: bool,
        strike: float,
        expiration: str,
        primary_exchange: str = None,
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        """
        Gets identity information for a single option contract.

        :param ticker: symbol of underlying
        :param is_call: True for a call, False for a put
        :param strike: strike price (must be defined)
        :param expiration: expiration date, IB-style yyyymmdd (must be defined)
        :param primary_exchange: unused; accepted for interface compatibility
        :return: (OptionInfo or None, error string or None)
        """
        option_info_list, error_str = await self.get_option_info(
            ticker=ticker, primary_exchange=primary_exchange, is_call=is_call, strike=strike, expiration=expiration
        )
        if error_str:
            return None, f"Unable to get option info, error is: {error_str}"
        if len(option_info_list) == 0:
            return None, f"Could not find matching option for ticker {ticker}, strike {strike}, expiration {expiration}"

        return option_info_list[0], None

    async def get_options_chain_info(
        self, ticker: str, primary_exchange: Optional[str] = None
    ) -> Tuple[Optional[OptionChainInfo], Optional[str]]:
        """
        Gets basic option-chain information (the set of expirations and strikes) for a stock.

        We request only the call side: it spans the same expirations and strike ladder as the puts, and asking
        for both sides at once returns a payload large enough that Schwab rejects it.

        :param ticker: ticker of underlying stock
        :param primary_exchange: unused; accepted for interface compatibility
        :return: (OptionChainInfo or None, error string or None)
        """
        if not self.is_connected():
            return None, "Not connected to Schwab"

        payload, error_str = await self._request_option_chain(ticker, self._contract_type(is_call=True))
        if error_str:
            return None, f"Couldn't get options chain for {ticker}, error is {error_str}"

        chain_info = OptionChainInfo()
        chain_info.underlying = ticker
        chain_info.multiplier = 100
        for exp_ib, strk, detail in self._iter_option_details(payload):
            chain_info.expirations.add(exp_ib)
            chain_info.strikes.add(strk)
            if not chain_info.exchange:
                chain_info.exchange = detail.get("exchangeName", "")

        self._logger.info(
            f"get_options_chain_info() finished for {ticker}: "
            f"{len(chain_info.expirations)} expiration(s), {len(chain_info.strikes)} strike(s)"
        )
        return chain_info, None

    async def get_greeks(
        self, option_info: OptionInfo, primary_exchange: Optional[str] = None
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        """
        Fills in the price, Greeks, implied volatility, volume, and open interest for a particular option and
        returns the same OptionInfo, populated. Mirrors IBDriver.get_greeks().

        :param option_info: option to fetch data for (must have underlying/right/strike/expiration set)
        :param primary_exchange: unused; accepted for interface compatibility
        :return: (populated OptionInfo or None, error string or None)
        """
        if not self.is_connected():
            return None, "Not connected to Schwab"

        underlying_name = option_info.get_underlying_name()
        if underlying_name is None:
            return None, "Underlying not defined"

        self._logger.info(f"Getting Greeks and other info for option {option_info.full_name}")
        payload, error_str = await self._request_option_chain(
            underlying_name,
            self._contract_type(option_info.is_call),
            strike=option_info.strike,
            expiration=option_info.expiration,
        )
        if error_str:
            return None, f"Could not fetch Greeks for {option_info.full_name}, error is: {error_str}"

        detail = next((d for _exp, _strk, d in self._iter_option_details(payload)), None)
        if detail is None:
            return None, f"Could not fetch Greeks, no contract found for {option_info.full_name}"

        option_info.underlying_price = float(payload.get("underlyingPrice", 0.0))
        option_info.price = self._detail_price(detail)
        option_info.delta = self._clean_number(detail.get("delta"))
        option_info.gamma = self._clean_number(detail.get("gamma"))
        option_info.theta = self._clean_number(detail.get("theta"))
        option_info.vega = self._clean_number(detail.get("vega"))
        # Schwab reports volatility as a percentage (e.g. 14.6); IBDriver uses a fraction, so divide by 100.
        option_info.implied_volatility = self._clean_number(detail.get("volatility")) / 100.0
        # for_call must match the option's right or these setters no-op.
        option_info.set_open_interest(int(detail.get("openInterest", 0)), for_call=option_info.is_call)
        option_info.set_volume(int(detail.get("totalVolume", 0)), for_call=option_info.is_call)
        option_info.set_greeks_defined()

        self._logger.info("get_greeks() finished")
        return option_info, None

    async def get_implied_volatility(
        self,
        ticker: str,
        primary_exchange: Optional[str] = None,
    ) -> Optional[float]:
        """
        Returns a current implied-volatility estimate (as a fraction, e.g. 0.18) for a stock/ETF, or None if it
        can't be determined.

        Schwab has no underlying-IV series (unlike IB's OPTION_IMPLIED_VOLATILITY), so we approximate it with
        the IV of the at-the-money option in the nearest expiration: request the call chain, then take the
        contract in the soonest expiration whose strike sits closest to the underlying price. Schwab quotes
        volatility as a percentage, so we divide by 100 to match the fraction convention used elsewhere.

        :param ticker: symbol of underlying, e.g. SPY
        :param primary_exchange: unused (Schwab resolves the venue); accepted for interface compatibility
        :return: implied volatility as a fraction, or None if unavailable
        """
        if not self.is_connected():
            return None

        payload, error_str = await self._request_option_chain(ticker, self._contract_type(is_call=True))
        if error_str or not payload:
            return None

        underlying_price = float(payload.get("underlyingPrice", 0.0))
        if underlying_price <= 0.0:
            return None

        # Sorting candidates by (expiration, |strike - underlying price|) puts the nearest expiration's
        # at-the-money strike first; skip contracts without a usable IV (Schwab's -999 / 0 sentinels).
        best_key = None
        best_iv = None
        for exp_ib, strike, detail in self._iter_option_details(payload):
            iv = self._clean_number(detail.get("volatility")) / 100.0
            if iv <= 0.0:
                continue
            key = (exp_ib, abs(strike - underlying_price))
            if best_key is None or key < best_key:
                best_key = key
                best_iv = iv

        return best_iv

    async def get_fundamentals(self, symbol: str) -> Tuple[Optional[dict], Optional[str]]:
        """
        Fetches Schwab's fundamental data for a single equity/ETF via the /instruments FUNDAMENTAL projection.

        Returns the raw instrument dict Schwab sends back -- identity fields (symbol, description, exchange,
        assetType, cusip) plus a nested "fundamental" object (valuation, profitability, growth, leverage,
        dividend, and trading-stat fields). The dict is passed through unmodified so callers can inspect exactly
        what Schwab provides. This is Schwab-specific and not part of BaseDriver.

        Note: Schwab's proprietary equity ratings (A-F letter grades) are a schwab.com research product and are
        NOT included here -- the Trader API exposes no such field.

        :param symbol: ticker of the underlying, e.g. AAPL
        :return: (instrument dict or None, error string or None)
        """
        if not self.is_connected():
            return None, "Not connected to Schwab"

        projection = self._client.Instrument.Projection.FUNDAMENTAL
        try:
            resp = await self._client.get_instruments(symbol, projection=projection)
        except Exception as e:
            return None, f"Schwab instruments request failed: {e}"

        if resp.status_code != 200:
            return None, f"Schwab instruments request failed ({resp.status_code}): {resp.text}"

        payload = resp.json()
        instruments = payload.get("instruments", []) if isinstance(payload, dict) else []
        if not instruments:
            return None, f"No fundamental data returned for {symbol}"

        # Schwab may return several matches for a search-y symbol; prefer the exact ticker, else the first.
        match = next(
            (item for item in instruments if (item.get("symbol") or "").upper() == symbol.upper()),
            instruments[0],
        )
        return match, None

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
        raise NotImplementedError("SchwabDriver.place_order() is not implemented yet")

    async def change_order(
        self,
        order: OrderInfo,
        action: OrderAction = OrderAction.BUY,
        quantity: int = 0,
        price: float = 0.0,
        order_type: OrderType = OrderType.MARKET,
        parent_order: Optional[OrderInfo] = None,
    ) -> Tuple[OrderInfo, Optional[str]]:
        raise NotImplementedError("SchwabDriver.change_order() is not implemented yet")

    async def cancel_order(self, order_info: OrderInfo):
        raise NotImplementedError("SchwabDriver.cancel_order() is not implemented yet")

    async def cancel_all_orders(self):
        raise NotImplementedError("SchwabDriver.cancel_all_orders() is not implemented yet")

    async def get_positions(self) -> Tuple[PositionsInfo, Optional[str]]:
        """
        Fetches the positions currently held across all accounts linked to this token and returns them in the
        broker-agnostic PositionsInfo form (same as IBDriver.get_positions()).

        Options are keyed by IB-style symbols (e.g. 'SPY-C-20260821-785.0') so they round-trip through the rest
        of the system unchanged. Short positions are reported with a positive quantity and short_position=True,
        matching IBDriver's convention.

        :return: (PositionsInfo, error string or None)
        """
        if not self.is_connected():
            return PositionsInfo(), "Not connected to Schwab"

        self._logger.info("get_positions()")
        try:
            resp = await self._client.get_accounts(fields=self._client.Account.Fields.POSITIONS)
        except Exception as e:
            return PositionsInfo(), f"Schwab positions request failed: {e}"

        if resp.status_code != 200:
            return PositionsInfo(), f"Schwab positions request failed ({resp.status_code}): {resp.text}"

        positions_info = PositionsInfo()
        # get_accounts() returns a list of {"securitiesAccount": {...}} wrappers, one per linked account.
        for account in resp.json():
            securities_account = account.get("securitiesAccount", {})
            for position in securities_account.get("positions", []):
                descriptor, quantity, price, is_short = self._parse_position(position)
                if descriptor is not None:
                    positions_info.set_position(descriptor, quantity, price, is_short)

        self._logger.info(f"get_positions() finished, {len(positions_info.get_positions())} position(s)")
        return positions_info, None

    async def get_trades(
        self, start_dt: datetime, end_dt: Optional[datetime] = None
    ) -> Tuple[TradesInfo, Optional[str]]:
        """
        Gets the trades the account holder made over a date range, across all linked accounts.

        Options are keyed by IB-style symbols (e.g. 'SPY-C-20260821-785.0'); a trade's quantity is signed
        (positive for a buy, negative for a sell), matching TradeDescriptor's convention.

        Note: Schwab only serves transactions from the last 60 days, and requires start_dt to be within that
        window, so older ranges will come back as an error from Schwab.

        :param start_dt: returned trades will be no older than this datetime
        :param end_dt: returned trades will be no newer than this datetime; defaults to the current datetime
        :return: (TradesInfo, error string or None)
        """
        if not self.is_connected():
            return TradesInfo(), "Not connected to Schwab"

        if end_dt is None:
            end_dt = current_datetime()

        self._logger.info(f"get_trades() from {start_dt} to {end_dt}")
        account_hashes, error_str = await self._get_account_hashes()
        if error_str:
            return TradesInfo(), error_str

        trades_info = TradesInfo()
        for account_hash in account_hashes:
            try:
                resp = await self._client.get_transactions(
                    account_hash,
                    start_date=start_dt,
                    end_date=end_dt,
                    transaction_types=self._client.Transactions.TransactionType.TRADE,
                )
            except Exception as e:
                return TradesInfo(), f"Schwab transactions request failed: {e}"

            if resp.status_code != 200:
                return TradesInfo(), f"Schwab transactions request failed ({resp.status_code}): {resp.text}"

            for txn in resp.json():
                trade_dt = self._parse_txn_time(txn.get("time"))
                for item in txn.get("transferItems", []):
                    trade = self._transfer_item_to_trade(item, trade_dt)
                    if trade is not None:
                        trades_info.add_trade(trade)

        self._logger.info(f"get_trades() finished, {len(trades_info.get_trades())} trade(s)")
        return trades_info, None

    async def get_earnings_dates(self, ticker: str) -> Tuple[EarningsInfo, Optional[str]]:
        raise NotImplementedError("SchwabDriver.get_earnings_dates() is not implemented yet")

    # ---------------------------------------------------
    # Private helpers
    # ---------------------------------------------------

    def _parse_position(self, position: dict) -> Tuple[Optional[SecurityDescriptor], int, float, bool]:
        """
        Converts one Schwab position entry into (SecurityDescriptor, quantity, price, is_short).

        Schwab reports long and short holdings in separate quantity fields; we collapse them into a magnitude
        plus a short flag, IB-style. Returns (None, 0, 0.0, False) for anything we can't describe.
        """
        instrument = position.get("instrument", {})
        symbol = instrument.get("symbol", "")
        if not symbol:
            return None, 0, 0.0, False

        long_qty = float(position.get("longQuantity", 0.0))
        short_qty = float(position.get("shortQuantity", 0.0))
        is_short = short_qty > 0
        quantity = int(short_qty if is_short else long_qty)
        price = float(position.get("averagePrice", 0.0))

        if instrument.get("assetType") == "OPTION":
            descriptor = self._option_symbol_to_descriptor(symbol)
        else:
            # Equities, ETFs (COLLECTIVE_INVESTMENT), etc. -- just the ticker.
            descriptor = SecurityDescriptor(symbol)

        return descriptor, quantity, price, is_short

    async def _get_account_hashes(self) -> Tuple[List[str], Optional[str]]:
        """Fetches the account hashes for every account linked to this token (needed for the transactions API)."""
        try:
            resp = await self._client.get_account_numbers()
        except Exception as e:
            return [], f"Schwab account lookup failed: {e}"
        if resp.status_code != 200:
            return [], f"Schwab account lookup failed ({resp.status_code}): {resp.text}"
        accounts = resp.json()
        if not accounts:
            return [], "No Schwab accounts linked to this token"
        return [account["hashValue"] for account in accounts], None

    def _transfer_item_to_trade(self, item: dict, trade_dt: datetime) -> Optional[TradeDescriptor]:
        """
        Converts one transferItem of a Schwab TRADE transaction into a TradeDescriptor, or None if the item is
        not a tradeable security (e.g. the cash/fee CURRENCY legs that accompany every trade).
        """
        instrument = item.get("instrument", {})
        asset_type = instrument.get("assetType")
        symbol = instrument.get("symbol", "")
        if asset_type not in ("OPTION", "EQUITY", "COLLECTIVE_INVESTMENT") or not symbol:
            return None

        if asset_type == "OPTION":
            descriptor = self._option_symbol_to_descriptor(symbol)
        else:
            # Equities, ETFs (COLLECTIVE_INVESTMENT), etc. -- just the ticker.
            descriptor = SecurityDescriptor(symbol)

        trade = TradeDescriptor(descriptor)
        trade.trade_date = trade_dt
        # Schwab's 'amount' is already signed: positive for a buy, negative for a sell.
        trade.quantity = int(item.get("amount", 0))
        trade.price = float(item.get("price") or 0.0)
        return trade

    @staticmethod
    def _parse_txn_time(time_str: Optional[str]) -> datetime:
        """Parses a Schwab transaction timestamp (ISO 8601, UTC) into a market-timezone datetime."""
        if not time_str:
            return current_datetime()
        return datetime.fromisoformat(time_str).astimezone(ZoneInfo(MARKETS_TIMEZONE))

    @staticmethod
    def _option_symbol_to_descriptor(symbol: str) -> SecurityDescriptor:
        """
        Parses a Schwab OSI option symbol (e.g. 'SPY   260821C00785000') into an IB-style SecurityDescriptor
        such as SPY-C-20260821-785.0.

        OSI layout, read from the right: 8 digits of strike (in thousandths), 1 char right (C/P), 6 digits of
        expiration (yymmdd), and the remaining (space-padded) leading characters are the underlying root.
        """
        strike = int(symbol[-8:]) / 1000.0
        right = symbol[-9]
        yymmdd = symbol[-15:-9]
        root = symbol[:-15].strip()
        expiration = "20" + yymmdd  # OSI years are two digits; Schwab options are all 21st-century.
        # Build the descriptor from the IB-style string so symbol_full keeps the trimmed strike (e.g. 785.0).
        return SecurityDescriptor(f"{root}-{right}-{expiration}-{SchwabDriver._strike_str(strike)}")

    @staticmethod
    def _descriptor_to_osi(descriptor: SecurityDescriptor) -> str:
        """
        Builds a Schwab OSI option symbol (e.g. 'SPY   260721C00742000') from an IB-style option descriptor
        (e.g. SPY-C-20260721-742.0). The inverse of _option_symbol_to_descriptor().

        OSI layout: 6-char space-padded root, 6-digit expiration (yymmdd), 1-char right (C/P), then the strike
        in thousandths of a dollar as 8 digits.
        """
        root = descriptor.ticker.ljust(6)
        yymmdd = descriptor.expiration[2:]  # 'yyyymmdd' -> 'yymmdd'
        strike_thousandths = int(round(descriptor.strike * 1000))
        return f"{root}{yymmdd}{descriptor.right}{strike_thousandths:08d}"

    def _contract_type(self, is_call: bool):
        """Returns the schwab-py ContractType enum value for the given right."""
        options = self._client.Options.ContractType
        return options.CALL if is_call else options.PUT

    async def _request_option_chain(
        self,
        ticker: str,
        contract_type,
        strike: Optional[float] = None,
        expiration: Optional[str] = None,
    ) -> Tuple[Optional[dict], Optional[str]]:
        """
        Issues a Schwab option-chain request and returns the parsed JSON payload (or an error string).

        :param ticker: underlying symbol
        :param contract_type: schwab-py ContractType enum (CALL/PUT/ALL)
        :param strike: if given, restrict to this strike
        :param expiration: if given (IB-style yyyymmdd), restrict to that single expiration date
        """
        kwargs = {"contract_type": contract_type}
        if strike is not None:
            kwargs["strike"] = strike
        if expiration is not None:
            exp_date = self._ib_expiration_to_date(expiration)
            kwargs["from_date"] = exp_date
            kwargs["to_date"] = exp_date

        try:
            resp = await self._client.get_option_chain(ticker, **kwargs)
        except Exception as e:
            return None, f"Schwab option chain request failed: {e}"

        if resp.status_code != 200:
            return None, f"Schwab option chain request failed ({resp.status_code}): {resp.text}"
        return resp.json(), None

    @staticmethod
    def _iter_option_details(payload: dict):
        """
        Iterates the option contracts in a Schwab option-chain payload, yielding (IB-style expiration, strike,
        detail dict) tuples across both the call and put expiration maps.
        """
        for map_key in ("callExpDateMap", "putExpDateMap"):
            for exp_key, strike_map in payload.get(map_key, {}).items():
                # exp_key looks like '2026-08-21:36' (date:days-to-expiration).
                exp_ib = exp_key.split(":")[0].replace("-", "")
                for strike_key, detail_list in strike_map.items():
                    for detail in detail_list:
                        yield exp_ib, float(strike_key), detail

    @staticmethod
    def _ib_expiration_to_date(expiration: str) -> date:
        """Converts an IB-style expiration string 'yyyymmdd' to a datetime.date for Schwab's date filters."""
        return date(int(expiration[:4]), int(expiration[4:6]), int(expiration[6:8]))

    @staticmethod
    def _clean_number(value: Optional[float]) -> float:
        """Coerces a Schwab numeric field to float, mapping missing values and the -999 sentinel to 0.0."""
        if value is None:
            return 0.0
        number = float(value)
        # Schwab uses -999 to mean 'not available' (e.g. volatility/Greeks on illiquid or 0-DTE contracts).
        return 0.0 if number <= -999.0 else number

    @staticmethod
    def _detail_price(detail: dict) -> float:
        """Picks a representative option price: mark, falling back to last, then the bid/ask midpoint."""
        mark = detail.get("mark")
        if mark:
            return float(mark)
        last = detail.get("last")
        if last:
            return float(last)
        bid = SchwabDriver._clean_number(detail.get("bid"))
        ask = SchwabDriver._clean_number(detail.get("ask"))
        return (bid + ask) / 2.0

    @staticmethod
    def _strike_str(strike: float) -> str:
        """Formats a strike the IB-style way: trailing zeros trimmed but always at least one decimal (785.0)."""
        text = f"{strike:.4f}".rstrip("0")
        if text.endswith("."):
            text += "0"
        return text

    async def _fetch_history(
        self,
        symbol: str,
        bar_size: BarSize,
        start_dt: datetime,
        end_dt: datetime,
        regular_trading_hours_only: bool,
        num_bars: int,
    ) -> Tuple[HistoricalData, Optional[str]]:
        """Issues the price-history request and converts the returned candles into a HistoricalData object."""
        method = getattr(self._client, self._history_method_map[bar_size])
        try:
            resp = await method(
                symbol,
                start_datetime=start_dt,
                end_datetime=end_dt,
                need_extended_hours_data=not regular_trading_hours_only,
            )
        except Exception as e:
            return HistoricalData(), f"Schwab price history request failed: {e}"

        if resp.status_code != 200:
            return HistoricalData(), f"Schwab price history request failed ({resp.status_code}): {resp.text}"

        payload = resp.json()
        candles = payload.get("candles", [])
        if num_bars > 0:
            # Schwab returns the whole window; keep only the most recent num_bars.
            candles = candles[-num_bars:]

        historical_data = HistoricalData()
        for candle in candles:
            bar, bar_dt = self._candle_to_bar(candle)
            historical_data.add_data(bar, bar_dt)
        return historical_data, None

    async def _start_live_stream(self, symbol: str, historical_data: HistoricalData):
        """Starts the background CHART_EQUITY stream that keeps historical_data updated with new 1-min candles."""
        stream = StreamClient(self._client)
        live = LiveStream(symbol=symbol, historical_data=historical_data)
        live.task = asyncio.create_task(self._run_stream(stream, symbol, historical_data))
        async with self._lock:
            self._live_streams[historical_data.get_id()] = live

    async def _run_stream(self, stream: StreamClient, symbol: str, historical_data: HistoricalData):
        """Logs in, subscribes to the symbol's one-minute candles, and pumps messages until cancelled."""
        try:
            await stream.login()
            stream.add_chart_equity_handler(lambda msg: self._on_chart_equity(msg, symbol, historical_data))
            await stream.chart_equity_subs([symbol])
            while True:
                await stream.handle_message()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._logger.error(f"Schwab stream error for {symbol}: {e}")
        finally:
            try:
                await stream.logout()
            except Exception:
                pass

    async def _stop_stream(self, live: LiveStream):
        """Cancels a stream's background task and waits for it to unwind."""
        if live.task is None:
            return
        live.task.cancel()
        try:
            await live.task
        except asyncio.CancelledError:
            pass

    def _on_chart_equity(self, msg: dict, symbol: str, historical_data: HistoricalData):
        """Handler invoked by schwab-py for each CHART_EQUITY message; updates historical_data in place."""
        for content in msg.get("content", []):
            if content.get("key") != symbol:
                continue
            bar, bar_dt = self._stream_content_to_bar(content)
            if bar is not None:
                historical_data.add_data(bar, bar_dt)

    def _candle_to_bar(self, candle: dict) -> Tuple[DataBar, datetime]:
        """Converts a Schwab price-history candle into a (DataBar, datetime) pair."""
        bar_dt = self._epoch_millis_to_market_dt(candle["datetime"])
        bar = DataBar()
        bar.date = get_datetime_as_str(bar_dt)
        bar.open = float(candle["open"])
        bar.high = float(candle["high"])
        bar.low = float(candle["low"])
        bar.close = float(candle["close"])
        bar.volume = float(candle.get("volume", 0))
        return bar, bar_dt

    def _stream_content_to_bar(self, content: dict) -> Tuple[Optional[DataBar], Optional[datetime]]:
        """Converts a CHART_EQUITY stream content dict into a (DataBar, datetime) pair, or (None, None)."""
        millis = content.get("CHART_TIME_MILLIS")
        close = content.get("CLOSE_PRICE")
        if millis is None or close is None:
            return None, None
        bar_dt = self._epoch_millis_to_market_dt(millis)
        bar = DataBar()
        bar.date = get_datetime_as_str(bar_dt)
        bar.open = float(content.get("OPEN_PRICE", close))
        bar.high = float(content.get("HIGH_PRICE", close))
        bar.low = float(content.get("LOW_PRICE", close))
        bar.close = float(close)
        bar.volume = float(int(content.get("VOLUME", 0)))
        return bar, bar_dt

    @staticmethod
    def _epoch_millis_to_market_dt(millis: Union[int, float]) -> datetime:
        """Converts epoch milliseconds (UTC) to a market-timezone datetime."""
        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).astimezone(ZoneInfo(MARKETS_TIMEZONE))

    @staticmethod
    def _to_market_dt(value: Union[datetime, str]) -> datetime:
        """Normalizes a datetime or IB-style date string to a timezone-aware market-timezone datetime."""
        if isinstance(value, str):
            return get_datetime(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=ZoneInfo(MARKETS_TIMEZONE))
        return value.astimezone(ZoneInfo(MARKETS_TIMEZONE))

    @staticmethod
    def _estimate_start_datetime(end_dt: datetime, bar_size: BarSize, num_bars: int) -> datetime:
        """
        Estimates a start datetime that comfortably spans at least num_bars bars back from end_dt. Schwab returns
        the full window and we trim to the last num_bars, so over-shooting (weekends/holidays) is fine.
        """
        if bar_size == BarSize.ONE_DAY:
            return end_dt - timedelta(days=math.ceil(num_bars * 7 / 5) + 5)
        if bar_size == BarSize.ONE_WEEK:
            return end_dt - timedelta(weeks=num_bars + 2)
        # Intraday: figure out how many trading days it takes to cover the requested minutes, with buffer.
        minutes = num_bars * (bar_size_to_time(bar_size).total_seconds() / 60.0)
        trading_days = math.ceil(minutes / MINUTES_PER_TRADING_DAY) + 1
        calendar_days = math.ceil(trading_days * 7 / 5) + 3
        return end_dt - timedelta(days=calendar_days)
