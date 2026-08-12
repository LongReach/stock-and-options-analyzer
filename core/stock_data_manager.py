import asyncio
from typing import Optional, List, Tuple, Dict
from threading import Lock
import logging
import pandas as pd
from datetime import datetime, timedelta

from core.common import HistoricalData, RequestedInfoType
from core.utils import (
    BarSize,
    str_to_bar_size,
    bar_size_to_time,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
    non_naive_datetime,
    align_datetime_to_bar_boundary,
)
from core.stock_data import StockData, StockDataException, DB_PATH
from core.base_driver import BaseDriver

_logger = logging.getLogger(__name__)


class StockDataManager:
    """
    Keeps track of stock data for any number of symbols (e.g. AAPL). Data is cached in an .h5 file on disk. For each
    symbol, the database can hold bars of various specific durations (e.g. daily, weekly, five-minute, etc.), for
    various types of data (trade price, implied volatility, etc.). There are several scrape() functions for pulling
    data from the broker, if not yet present in cache.

    The database also contains metadata, entries that, for each symbol, bar size, data type combo, provide a quick
    lookup for number of bars in cache, start date of cached data, end date of cached data, head timestamp)
    """

    # How many bars to fetch per tranch of scraped data
    BARS_PER_SCRAPE = 50
    # Seconds to pause between tranches
    TIME_BETWEEN_SCRAPES = 0.2
    # When getting recent data, smart_scrape() will redo the last n bars in the cache. This is because some bars in the
    # cached data might have been captured midway through the day or week.
    BARS_TO_REDO = 5

    def __init__(self):
        self._data_map: Dict[Tuple[str, BarSize, RequestedInfoType], StockData] = {}
        self._data_driver: Optional[BaseDriver] = None
        self._log_to_stdout = False
        self._db_path: str = DB_PATH

        self._map_lock: Lock = Lock()  # Controls access to self._data_map
        self._cache_lock: Lock = Lock()  # Controls access to the cache DB

    def add_driver(self, data_driver: BaseDriver) -> bool:
        """Adds driver for connecting to brokerage, returns True if connection successful"""
        self._data_driver = data_driver
        self._data_driver.connect()
        return self._data_driver.is_connected()

    @property
    def driver(self) -> Optional[BaseDriver]:
        return self._data_driver

    def set_log_to_stdout(self, to_stdout: bool):
        self._log_to_stdout = to_stdout

    def set_db_path(self, db_path: str):
        self._db_path = db_path

    def load_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> bool:
        """
        Creates a StockData object, attempts to load data from the HDF5 database. Also

        :param symbol: e.g. "AAPL"
        :param bar_size: --
        :param info_type: --
        :return: True if data was successfully loaded
        """
        self._log(f"Loading data for {symbol}, {bar_size.name} from {self._db_path}")
        stock_data = self._get_stock_data(symbol, bar_size, info_type, add_if_missing=True)
        with self._cache_lock:
            return stock_data.load_from_db(self._db_path)

    async def load_data_async(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> bool:
        """
        Runs load_data() asynchronously. Good for being able to stream in data as a background task.

        :param symbol: e.g. "AAPL"
        :param bar_size: --
        :param info_type: --
        :return: True if data was successfully loaded
        """
        return await asyncio.to_thread(self.load_data, symbol, bar_size, info_type)

    def unload_data(self, symbol: str, bar_size: BarSize, info_type: RequestedInfoType = RequestedInfoType.TRADES):
        """
        Unload StockData object from memory. Doesn't affect data saved in cache.
        :param symbol: e.g. "AAPL"
        :param bar_size: --
        :param info_type: --
        """
        key = (symbol, bar_size, info_type)
        with self._map_lock:
            self._data_map.pop(key, None)

    def save_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ):
        """
        Saves data to the HDF5 database.
        :param symbol: e.g. "AAPL"
        :param bar_size: --
        :param info_type: --
        """
        self._log(f"Saving data for {symbol}, {bar_size.name} to {self._db_path}")
        stock_data = self._get_stock_data(symbol, bar_size, info_type)
        if stock_data:
            with self._cache_lock:
                stock_data.save_to_db(self._db_path)

    def clear_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        remove_from_cache: bool = False,
    ):
        """Clear out any data already loaded. If remove_from_cache is True, also deletes from the database."""
        stock_data = self._get_stock_data(symbol, bar_size, info_type, add_if_missing=True)
        stock_data.clear()
        if remove_from_cache:
            with self._cache_lock:
                stock_data.delete_from_db(self._db_path)

    async def scrape_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        start_date: str = "",
        end_date: str = "",
    ) -> Tuple[bool, str]:
        """
        Scrapes data from online source, completely replacing any data already in memory.

        :param symbol: ticker symbol
        :param bar_size: --
        :param info_type: --
        :param start_date: earliest date for which to get data
        :param end_date: data should be no newer than this date. If not given, use current datetime.
        :return: (success, error string)
        """
        if not self._data_driver:
            raise StockDataException("No driver set")

        stock_data = self._get_stock_data(symbol, bar_size, info_type, add_if_missing=True)
        # Make sure the head time is set
        meta_data = stock_data.get_metadata()
        _, _, _, head_dt = meta_data
        if head_dt is None:
            head_dt = await self._data_driver.get_head_timestamp(symbol, info_type)
            head_dt = align_datetime_to_bar_boundary(head_dt, bar_size)
            stock_data.head_date = head_dt

        info_str = f"Scraping data for {symbol}, {bar_size.name}, {info_type.name}. start_date='{start_date}', end_date='{end_date}'"
        if head_dt is not None:
            info_str += f", head_timestamp='{head_dt}'"
        self._log(info_str)

        if start_date == "":
            raise StockDataException("Need start date for data scraping")
        start_dt = get_datetime(start_date)
        start_dt = align_datetime_to_bar_boundary(start_dt, bar_size) - bar_size_to_time(bar_size)

        if end_date == "":
            end_dt = current_datetime()
            # This is a bit hacky, but IB is more reliable about returning the very most recent data if we don't
            # give it an end datetime.
            first_scrape_no_end_date = True
        else:
            end_dt = get_datetime(end_date)
            first_scrape_no_end_date = False
        end_dt = align_datetime_to_bar_boundary(end_dt, bar_size)

        # Work backwards through time, getting BARS_PER_SCRAPE at a time. We're doing this because IB can refuse requests for
        # too much data at once.
        current_end_dt = end_dt
        interval_delta = bar_size_to_time(bar_size) * self.BARS_PER_SCRAPE
        ret_error_str = None
        while current_end_dt > start_dt:
            current_start_dt = (
                start_dt if (current_end_dt - interval_delta) < start_dt else current_end_dt - interval_delta
            )
            self._log(
                f"Scraping tranch of data from {get_datetime_as_str(current_start_dt)} to {get_datetime_as_str(current_end_dt)}"
            )

            async def _get_historical_data(_no_end_date: bool) -> Tuple[Optional[HistoricalData], Optional[str]]:
                # This function is experimental. I'm trying different approaches to deal with non-responses from broker

                end_dt_to_use = None if _no_end_date else current_end_dt

                # Might put the following back later:
                # exchanges = [None, "NYSE", "NASDAQ"]
                exchanges = [None]
                for primary_ex in exchanges:
                    _historical_data, _error_str = await self._data_driver.get_historical_data(
                        stock_data.symbol,
                        bar_size=stock_data.bar_size,
                        start_date=current_start_dt,
                        end_date=end_dt_to_use,
                        request_info_type=info_type,
                        primary_exchange=primary_ex,
                    )
                    if _error_str is None or "Timed out" not in _error_str:
                        return _historical_data, _error_str
                return None, "StockDataManager: timed out with all exchanges"

            historical_data, error_str = await _get_historical_data(first_scrape_no_end_date)
            # Turn it off after first use
            first_scrape_no_end_date = False
            if error_str:
                self._log(f"Got error while scraping: {error_str}", level=logging.ERROR)
                ret_error_str = error_str
            if historical_data is None or historical_data.is_empty():
                break
            for results_tup in historical_data.get_zipped_lists():
                stock_data.add_data(results_tup[0], results_tup[1])
            current_end_dt -= interval_delta
            await asyncio.sleep(self.TIME_BETWEEN_SCRAPES)

        stock_data.finalize_data()
        return ret_error_str is None, ret_error_str

    async def scrape_data_smart(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        start_date: str = "",
        end_date: str = "",
        update_recent: bool = False,
    ) -> Tuple[bool, str]:
        """
        Like scrape_data(), but avoids looking online for data already loaded, if loaded data overlaps with specified
        date range.

        :param symbol: ticker symbol
        :param bar_size: --
        :param info_type: --
        :param start_date: earliest date for which to scrape data. If date is earlier than available data from broker
            side, then start from the earliest date for which data exists. If not given, don't attempt to scrape data
            that's earlier than data already loaded.
        :param end_date: latest date for which to scrape data. If not given, only attempt to scrape data
            that's later than data already loaded if update_recent set.
        :param update_recent: if True, and end_date not set, most recent data not already loaded will be
            scroped.
        :return:
        """
        stock_data = self._get_stock_data(symbol, bar_size, info_type, add_if_missing=True)

        df = stock_data.get_data_frame()
        if len(df) == 0:
            # There's nothing "smart" we can do here, no data loaded at all. So, we just scrape it all from broker.
            self._log("No data cached locally, scrape_data_smart() must scrape it all")
            return await self.scrape_data(symbol, bar_size, info_type, start_date, end_date)

        # Oldest date for which there's data
        oldest_dt_in_cache: datetime = df.iloc[0]["date"].to_pydatetime()
        oldest_dt_in_cache = non_naive_datetime(oldest_dt_in_cache)
        # Newest date for which there's data
        newest_dt_in_cache = df.iloc[-1]["date"].to_pydatetime()
        newest_dt_in_cache = non_naive_datetime(newest_dt_in_cache)

        # First, focus on obtaining data that's older than currently cached data
        # ------------------------------------------
        if start_date == "":
            start_dt = None
        else:
            start_dt = get_datetime(start_date)
            head_timestamp_dt = await self._data_driver.get_head_timestamp(symbol, info_type)
            adjusted_head_timestamp_dt = self.get_adjusted_head_timestamp(head_timestamp_dt, bar_size)
            if adjusted_head_timestamp_dt is not None and start_dt < adjusted_head_timestamp_dt:
                # The start datetime precedes the adjust head timestamp, so move the start forward in time to
                # match it.
                start_dt = adjusted_head_timestamp_dt
                start_date = get_datetime_as_str(start_dt)

        # Scrape data that's older than already-loaded data
        if start_dt is not None and start_dt < oldest_dt_in_cache:
            success, error_str = await self.scrape_data(
                symbol, bar_size, info_type, start_date, get_datetime_as_str(oldest_dt_in_cache)
            )
            if not success:
                return success, error_str

        # Second, focus on obtaining data that's newer than currently cached data
        # ------------------------------------------

        if end_date == "":
            end_dt = None
        else:
            end_dt = get_datetime(end_date)

        # Scrape data that's newer than already-loaded data
        if (end_dt is not None and end_dt > newest_dt_in_cache) or update_recent:
            end_date_str = "" if end_dt is None else get_datetime_as_str(end_dt)
            success, error_str = await self.scrape_data(
                symbol,
                bar_size,
                info_type,
                start_date=get_datetime_as_str(newest_dt_in_cache - bar_size_to_time(bar_size) * self.BARS_TO_REDO),
                end_date=end_date_str,
            )
            if not success:
                return success, error_str

        return True, ""

    def get_pandas_df(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ) -> Optional[pd.DataFrame]:
        """Get the pandas dataframe for particular stock data."""
        stock_data = self._get_stock_data(symbol, bar_size, info_type)
        if stock_data is None:
            return None
        return stock_data.get_data_frame()

    def get_metadata(
        self, symbol: str, bar_size: BarSize, info_type: RequestedInfoType = RequestedInfoType.TRADES
    ) -> Optional[Tuple[int, datetime, datetime, Optional[datetime]]]:
        """
        Gets the metadata. If stock data for requested series is loaded in memory, compute metadata from that.
        If not loaded in memory, attempt to load metadata from DB.

        Note on head timestamp / head date: this is the earliest datetime for which data is available from the broker.
        If not known, will be a datetime mapping to 1/1/2200. It's best not to try to scrape data beginning at the head
        date itself. Use get_adjusted_head_timestamp() to find the best date for earliest scraping, once the head date
        is obtained from the broker.

        :param symbol: ticker symbol
        :param bar_size: --
        :param info_type: --
        :return: (num bars, start date, end date, head timestamp) or None, if metadata not in memory and can't be loaded
        """
        stock_data = self._get_stock_data(symbol, bar_size, info_type)
        if stock_data is not None:
            return stock_data.get_metadata()
        stock_data = self._get_stock_data(symbol, bar_size, info_type, add_if_missing=True)
        loaded_metadata = stock_data.load_metadata_from_db(self._db_path)
        if loaded_metadata:
            return stock_data.get_metadata()
        return None

    def get_cached_keys(self) -> List[str]:
        """
        Returns the list of series keys present in the local database, e.g. ['SPY_1d_tr', 'AAPL_1d_tr'].
        Returns an empty list if the database file does not exist.
        """
        with self._cache_lock:
            try:
                with pd.HDFStore(self._db_path, mode="r") as store:
                    results = [key.lstrip("/") for key in store.keys()]
            except:
                return []
        return [result for result in results if "_meta" not in result]

    @staticmethod
    def get_key_elements(key: str) -> Tuple[str, BarSize, RequestedInfoType]:
        parts = key.split("_")
        symbol = parts[0]
        bar_size = str_to_bar_size(parts[1])
        info_type = StockData.get_info_type(parts[2])
        return symbol, bar_size, info_type

    @staticmethod
    def get_adjusted_head_timestamp(head_timestamp_dt: Optional[datetime], bar_size: BarSize) -> Optional[datetime]:
        """
        The 'adjusted' head timestamp is simply four weeks after the head timestamp. The idea is that we don't need
        to go all the way back to the very start of a ticker's history. Sometimes the broker gets confused if we
        request data from the beginning, even if the head timestamp would seem to suggest that it's there.

        :param head_timestamp_dt: the actual head timestamp, as gotten from broker, or None
        :param bar_size: 1d, 1w, etc.
        :return: adjusted head timestamp or None
        """
        if head_timestamp_dt is None:
            return None
        return head_timestamp_dt + timedelta(days=28)

    async def get_iv_rank(
        self, symbol: str, cache_only: bool = False, acceptable_recency: int = 0
    ) -> Tuple[float, float, Optional[str]]:
        """
        Gets IV rank for a particular stock.

        :param symbol: ticker
        :param cache_only: if True, only get data from cache
        :param acceptable_recency: how many days in the past most recent IV data can be to be usable
        :return: (IV rank as %, latest IV, error msg)
        """
        num_acceptable_bars = 200

        if cache_only:
            start_date = ""
        else:
            start_date = get_datetime_as_str(current_datetime() - timedelta(days=365), date_only=True)

        df = None
        error_msg: Optional[str] = None
        try:
            # We'll be using cached data
            self.load_data(symbol, BarSize.ONE_DAY, RequestedInfoType.IMPLIED_VOLATILITY)

            scrape_needed = not cache_only
            if scrape_needed:
                # See if metadata is recent enough
                metadata = self.get_metadata(symbol, BarSize.ONE_DAY, RequestedInfoType.IMPLIED_VOLATILITY)
                if metadata:
                    num_bars, start_dt, end_dt, _ = metadata
                    if (current_datetime() - end_dt).days <= acceptable_recency:
                        scrape_needed = False

            if scrape_needed:
                success, error_str = await self.scrape_data_smart(
                    symbol,
                    BarSize.ONE_DAY,
                    RequestedInfoType.IMPLIED_VOLATILITY,
                    start_date=start_date,
                    update_recent=True,
                )
                if not success:
                    error_msg = error_str
                self.save_data(symbol, BarSize.ONE_DAY, RequestedInfoType.IMPLIED_VOLATILITY)
            df = self.get_pandas_df(symbol, BarSize.ONE_DAY, RequestedInfoType.IMPLIED_VOLATILITY)
            self.unload_data(symbol, BarSize.ONE_DAY, RequestedInfoType.IMPLIED_VOLATILITY)
        except KeyboardInterrupt:
            raise
        except StockDataException as ex:
            return 0, 0, f"StockDataException {ex}"
        except Exception as ex:
            return 0, 0, f"Exception {ex}"

        if df is None or error_msg is not None:
            return 0, 0, f"Error finding IV rank for {symbol}, is: {error_msg}"
        if len(df) < num_acceptable_bars:
            return 0, 0, f"Not enough data found for {symbol}"

        most_recent_dt = df.iloc[-1]["date"].to_pydatetime()
        if (current_datetime() - most_recent_dt).days > acceptable_recency:
            return 0, 0, f"Data found for {symbol} not recent enough"

        lowest_iv = 1000000.0
        highest_iv = 0.0
        total_bars = len(df)
        start_bar = total_bars - 200
        start_bar = 0 if start_bar < 0 else start_bar
        for idx in range(start_bar, total_bars):
            iv = df.iloc[idx]["close"]
            lowest_iv = min(iv, lowest_iv)
            highest_iv = max(iv, highest_iv)
        latest_iv = df.iloc[-1]["close"]
        rank = (latest_iv - lowest_iv) / (highest_iv - lowest_iv)
        rank *= 100.0

        return rank, latest_iv, None

    async def get_most_recent_price(
        self, symbol: str, bar_size: BarSize = BarSize.ONE_DAY
    ) -> Tuple[float, Optional[str]]:
        """
        Gets last trading price for security from broker.

        :param symbol: ticker of security
        :param bar_size: one-day by default
        :return: (price, error message)
        """
        recent_data, error_msg = await self.driver.get_most_recent_data(symbol, bar_size, RequestedInfoType.TRADES)
        if recent_data is None or error_msg is not None:
            return 0, f"Error getting underlying price for symbol {symbol}"
        return recent_data[0]["close"], None

    def _log(self, message: str, level: int = logging.INFO):
        if self._log_to_stdout and level == logging.INFO:
            print(message)

        _logger.log(level, message)

    def _get_stock_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        add_if_missing: bool = False,
    ) -> Optional[StockData]:
        """
        Return StockData object.
        :param symbol: --
        :param bar_size: --
        :param info_type: --
        :param add_if_missing: if True, create new StockData object, if it doesn't exist already.
        :return:
        """
        key = (symbol, bar_size, info_type)
        with self._map_lock:
            stock_data = self._data_map.get(key)
        if stock_data is None and add_if_missing:
            stock_data = StockData(symbol, bar_size, info_type)
            self._add_stock_data(stock_data)
        return stock_data

    def _add_stock_data(self, stock_data: StockData):
        """Adds a StockData object to tracking"""
        key = (stock_data.symbol, stock_data.bar_size, stock_data.info_type)
        with self._map_lock:
            self._data_map[key] = stock_data
