import asyncio
import math
from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from pandas.core.resample import maybe_warn_args_and_kwargs

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
from core.stock_data import StockData, StockDataException
from core.ib_driver import IBDriver

_logger = logging.getLogger(__name__)


class StockDataManager:
    """
    Keeps track of stock data for any number of symbols (e.g. AAPL)
    """

    BARS_PER_SCRAPE = 200
    TIME_BETWEEN_SCRAPES = 0.2

    def __init__(self):
        self._data_map: Dict[Tuple[str, BarSize, RequestedInfoType], StockData] = {}
        self._ib_driver: Optional[IBDriver] = None
        self._log_to_stdout = False

    def add_driver(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        self._ib_driver.connect()

    def set_log_to_stdout(self, to_stdout: bool):
        self._log_to_stdout = True

    def load_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        filename: Optional[str] = None,
    ) -> bool:
        """
        Creates a StockData object, attempts to load data from disk
        :param symbol: e.g. "AAPL"
        :param bar_size: --
        :param info_type: --
        :param filename: if not given, a filename will be chosen from symbol/bar size
        :return: True if file successfully loaded from disk
        """
        file_str = f" from file {filename}" if filename else ""
        self._log(f"Loading data for {symbol}, {bar_size.name}{file_str}")
        stock_data = self._get_stock_data(
            symbol, bar_size, info_type, add_if_missing=True
        )
        return stock_data.load(filename)

    def save_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
        filename: Optional[str] = None,
    ):
        """
        Creates a StockData object, attempts to load data from disk
        :param symbol: e.g. "AAPL"
        :param bar_size: --
        :param info_type: --
        :param filename: if not given, a filename will be chosen from symbol/bar size
        :return:
        """
        file_str = f" to file {filename}" if filename else ""
        self._log(f"Saving data for {symbol}, {bar_size.name}{file_str}")
        stock_data = self._get_stock_data(symbol, bar_size, info_type)
        if stock_data:
            stock_data.save(filename)

    def clear_data(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ):
        """Clear out any data already loaded"""
        stock_data = self._get_stock_data(
            symbol, bar_size, info_type, add_if_missing=True
        )
        stock_data.clear()

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

        stock_data = self._get_stock_data(
            symbol, bar_size, info_type, add_if_missing=True
        )
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
                start_dt
                if (current_end_dt - interval_delta) < start_dt
                else current_end_dt - interval_delta
            )
            self._log(
                f"Scraping tranch of data from {get_datetime_as_str(current_start_dt)} to {get_datetime_as_str(current_end_dt)}"
            )
            historical_data, error_str = await self._ib_driver.get_historical_data(
                stock_data.symbol,
                bar_size=stock_data.bar_size,
                start_date=current_start_dt,
                end_date=current_end_dt,
                request_info_type=info_type,
            )
            if error_str:
                ret_error_str = error_str
            if historical_data.is_empty():
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
        :param start_date: earliest date for which to scrape data. If not given, don't attempt to scrape data
            that's earlier than data already loaded. If date is earlier than available data, then start from
            there.
        :param end_date: latest date for which to scrape data. If not given, only attempt to scrape data
            that's later than data already loaded if update_recent set.
        :param update_recent: if True, and end_date not set, most recent data not already loaded will be
            scroped.
        :return:
        """
        stock_data = self._get_stock_data(
            symbol, bar_size, info_type, add_if_missing=True
        )

        df = stock_data.get_data_frame()
        if len(df) == 0:
            # There's nothing "smart" we can do here, no data loaded at all
            return await self.scrape_data(
                symbol, bar_size, info_type, start_date, end_date
            )

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
            earliest_data_dt = await self._ib_driver.get_head_timestamp(symbol)
            if start_dt < earliest_data_dt:
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
        stock_data = self._data_map.get(key)
        if stock_data is None and add_if_missing:
            stock_data = StockData(symbol, bar_size, info_type)
            self._add_stock_data(stock_data)
        return stock_data

    def _add_stock_data(self, stock_data: StockData):
        """Adds a StockData object to tracking"""
        key = (stock_data.symbol, stock_data.bar_size, stock_data.info_type)
        self._data_map[key] = stock_data
