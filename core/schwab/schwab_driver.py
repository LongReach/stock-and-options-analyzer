import asyncio
import math
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from logging import getLogger
from typing import Optional, List, Tuple, Union, Dict
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from ibapi.common import BarData

from schwab.auth import easy_client
from schwab.client import AsyncClient
from schwab.streaming import StreamClient

from core.base_driver import BaseDriver
from core.common import (
    HistoricalData,
    RequestedInfoType,
    SecurityDescriptor,
    OptionChainInfo,
    OptionInfo,
    OrderType,
    OrderInfo,
    OrderAction,
    PositionsInfo,
    EarningsInfo,
    BarSize,
    MARKETS_TIMEZONE,
)
from core.utils import (
    get_datetime,
    get_datetime_as_str,
    bar_size_to_time,
    current_datetime,
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

    All data returned is in the generic form used throughout `core` (HistoricalData, BarData, etc.); Schwab-
    and schwab-py-specific classes are only used inside this module.

    Version One scope: historical/live OHLC bar data. Options, orders, and account/positions endpoints are
    stubbed and raise NotImplementedError.

    Note on paper trading: unlike IB, Schwab's developer API has no paper/sandbox environment. Market data is
    available, but any future order support would hit real money.
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

        :param symbol_full: stock/ETF ticker, e.g. AAPL or SPY. Options are not supported in Version One.
        :param num_bars: how many bars to collect; if 0, start_date determines the range
        :param bar_size: one of the Schwab-supported sizes (1m, 5m, 15m, 1d, 1w)
        :param end_date: end of the range; datetime or IB-style str '20250523 16:00:00 US/Eastern'
        :param start_date: start of the range; datetime or IB-style str
        :param live_data: if True, stream continuing one-minute candle updates into the result
        :param request_info_type: only TRADES is supported
        :param regular_trading_hours_only: if True, exclude pre/post-market data
        :param primary_exchange: unused (Schwab resolves the venue); accepted for interface compatibility
        :return: (HistoricalData, error str or None)
        :raises SchwabDriverException: if the bar size isn't supported by Schwab
        """
        if not self.is_connected():
            return HistoricalData(), "Not connected to Schwab"

        descriptor = SecurityDescriptor(symbol_full)
        if descriptor.is_option():
            return HistoricalData(), "SchwabDriver does not support options data yet"
        if request_info_type != RequestedInfoType.TRADES:
            return HistoricalData(), f"SchwabDriver only supports TRADES data, not {request_info_type.name}"
        if bar_size not in self._history_method_map:
            raise SchwabDriverException(
                f"Bar size {bar_size.name} is not supported by SchwabDriver "
                f"(supported: {', '.join(bs.name for bs in self._history_method_map)})"
            )

        symbol = descriptor.ticker
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
        raise NotImplementedError("SchwabDriver.get_option_info() is not implemented yet")

    async def get_option_info_single(
        self,
        ticker: str,
        is_call: bool,
        strike: float,
        expiration: str,
        primary_exchange: str = None,
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        raise NotImplementedError("SchwabDriver.get_option_info_single() is not implemented yet")

    async def get_options_chain_info(
        self, ticker: str, primary_exchange: Optional[str] = None
    ) -> Tuple[Optional[OptionChainInfo], Optional[str]]:
        raise NotImplementedError("SchwabDriver.get_options_chain_info() is not implemented yet")

    async def get_greeks(
        self, option_info: OptionInfo, primary_exchange: Optional[str] = None
    ) -> Tuple[Optional[OptionInfo], Optional[str]]:
        raise NotImplementedError("SchwabDriver.get_greeks() is not implemented yet")

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

    def _candle_to_bar(self, candle: dict) -> Tuple[BarData, datetime]:
        """Converts a Schwab price-history candle into an (ibapi BarData, datetime) pair."""
        bar_dt = self._epoch_millis_to_market_dt(candle["datetime"])
        bar = BarData()
        bar.date = get_datetime_as_str(bar_dt)
        bar.open = float(candle["open"])
        bar.high = float(candle["high"])
        bar.low = float(candle["low"])
        bar.close = float(candle["close"])
        bar.volume = Decimal(str(candle.get("volume", 0)))
        return bar, bar_dt

    def _stream_content_to_bar(self, content: dict) -> Tuple[Optional[BarData], Optional[datetime]]:
        """Converts a CHART_EQUITY stream content dict into an (ibapi BarData, datetime) pair, or (None, None)."""
        millis = content.get("CHART_TIME_MILLIS")
        close = content.get("CLOSE_PRICE")
        if millis is None or close is None:
            return None, None
        bar_dt = self._epoch_millis_to_market_dt(millis)
        bar = BarData()
        bar.date = get_datetime_as_str(bar_dt)
        bar.open = float(content.get("OPEN_PRICE", close))
        bar.high = float(content.get("HIGH_PRICE", close))
        bar.low = float(content.get("LOW_PRICE", close))
        bar.close = float(close)
        bar.volume = Decimal(str(int(content.get("VOLUME", 0))))
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
