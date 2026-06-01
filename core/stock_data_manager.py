import asyncio
import math
from typing import Optional, List, Union, Tuple, Any, Dict
from threading import Lock
import logging
import pandas as pd
from datetime import datetime, timedelta

from core.common import HistoricalData, RequestedInfoType
from core.utils import (
    BarSize,
    bar_size_to_str,
    str_to_bar_size,
    bar_size_to_time,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
    non_naive_datetime,
)
from core.stock_data import StockData, StockDataException, DB_PATH
from core.ib_driver import IBDriver

_logger = logging.getLogger(__name__)


class StockDataManager:
    """
    Keeps track of stock data for any number of symbols (e.g. AAPL)
    """

    BARS_PER_SCRAPE = 50
    TIME_BETWEEN_SCRAPES = 0.2

    def __init__(self):
        self._data_map: Dict[Tuple[str, BarSize, RequestedInfoType], StockData] = {}
        self._ib_driver: Optional[IBDriver] = None
        self._log_to_stdout = False
        self._db_path: str = DB_PATH

        self._map_lock: Lock = Lock()  # Controls access to self._data_map
        self._cache_lock: Lock = Lock()  # Controls access to the cache DB

    def add_driver(self, ib_driver: IBDriver) -> bool:
        """Adds driver for connecting to brokerage, returns True if connection successful"""
        self._ib_driver = ib_driver
        self._ib_driver.connect()
        return self._ib_driver.is_connected()

    @property
    def driver(self) -> Optional[IBDriver]:
        return self._ib_driver

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
        Creates a StockData object, attempts to load data from the HDF5 database.
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
        self._log(
            f"Scraping data for {symbol}, {bar_size.name}, {info_type.name}. start_date='{start_date}', end_date='{end_date}'"
        )
        if not self._ib_driver:
            raise StockDataException("No driver set")

        stock_data = self._get_stock_data(symbol, bar_size, info_type, add_if_missing=True)
        if start_date == "":
            raise StockDataException("Need start date for data scraping")
        start_dt = get_datetime(start_date)

        if end_date == "":
            end_dt = current_datetime()
        else:
            end_dt = get_datetime(end_date)

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

            async def _get_historical_data() -> Tuple[Optional[HistoricalData], Optional[str]]:
                # This function is experimental. I'm trying different approaches to deal with non-responses from broker
                # Might put the following back later:
                # exchanges = [None, "NYSE", "NASDAQ"]
                exchanges = [None]
                for primary_ex in exchanges:
                    _historical_data, _error_str = await self._ib_driver.get_historical_data(
                        stock_data.symbol,
                        bar_size=stock_data.bar_size,
                        start_date=current_start_dt,
                        end_date=current_end_dt,
                        request_info_type=info_type,
                        primary_exchange=primary_ex,
                    )
                    if _error_str is None or "Timed out" not in _error_str:
                        return _historical_data, _error_str
                return None, "StockDataManager: timed out with all exchanges"

            historical_data, error_str = await _get_historical_data()
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
        oldest_dt: datetime = df.iloc[0]["date"].to_pydatetime()
        oldest_dt = non_naive_datetime(oldest_dt)
        # Newest date for which there's data
        newest_dt = df.iloc[-1]["date"].to_pydatetime()
        newest_dt = non_naive_datetime(newest_dt)

        if start_date == "":
            start_dt = None
        else:
            start_dt = get_datetime(start_date)
            earliest_data_dt = await self._ib_driver.get_head_timestamp(symbol, info_type)
            if earliest_data_dt is not None and start_dt < earliest_data_dt:
                start_dt = earliest_data_dt

        # Scrape data that's older than already-loaded data
        if start_dt is not None and start_dt < oldest_dt:
            success, error_str = await self.scrape_data(
                symbol, bar_size, info_type, start_date, get_datetime_as_str(oldest_dt)
            )
            if not success:
                return success, error_str

        if end_date == "":
            end_dt = current_datetime() if update_recent else None
        else:
            end_dt = get_datetime(end_date)

        # Scrape data that's newer than already-loaded data
        if end_dt is not None and end_dt > newest_dt:
            success, error_str = await self.scrape_data(
                symbol,
                bar_size,
                info_type,
                get_datetime_as_str(newest_dt + bar_size_to_time(bar_size)),
                get_datetime_as_str(end_dt),
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
    ) -> Optional[Tuple[int, datetime, datetime]]:
        """
        Gets the metadata. If stock data for requested series is loaded in memory, compute metadata from that.
        If not loaded in memory, attempt to load metadata from DB.

        :param symbol: ticker symbol
        :param bar_size: --
        :param info_type: --
        :return: (num bars, start date, end date) or None, if metadata not in memory and can't be loaded
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
