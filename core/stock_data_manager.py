from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

from core.utils import BarSize, bar_size_to_str, str_to_bar_size
from core.stock_data import StockData
from core.ib_driver import IBDriver

_logger = logging.getLogger(__name__)

class StockDataManager:

    def __init__(self):
        self._data_map: Dict[str, Dict[BarSize, StockData]] = {}
        self._ib_driver: Optional[IBDriver] = None

    def add_driver(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        self._ib_driver.connect()

    def load_data(self, symbol: str, bar_size: BarSize):
        """
        Creates a StockData object, attempts to load data from disk
        :param symbol:
        :param bar_size:
        :return:
        """
        stock_data = self._get_stock_data(symbol, bar_size, add_if_missing=True)
        stock_data.load()

    async def scrape_data(self, symbol: str, bar_size: BarSize, start_date: str = "", end_date: str = ""):
        if not self._ib_driver:
            return
        stock_data = self._get_stock_data(symbol, bar_size, add_if_missing=True)
        results, error_str = await self._ib_driver.get_historical_data(stock_data.symbol, bar_size=stock_data.bar_size, start_date=start_date, end_date=end_date)
        for results_tup in results:
            stock_data.add_data(results_tup[0], results_tup[1])

    def _get_stock_data(self, symbol: str, bar_size: BarSize, add_if_missing: bool = False) -> Optional[StockData]:
        bar_size_dict = self._data_map.get(symbol)
        stock_data = None
        if bar_size_dict:
            stock_data = bar_size_dict.get(bar_size)
        if stock_data is None and add_if_missing:
            stock_data = StockData(symbol, bar_size)
            self._add_stock_data(stock_data)
        return stock_data

    def _add_stock_data(self, stock_data: StockData):
        bar_size_dict = self._data_map.get(stock_data.symbol)
        if not bar_size_dict:
            bar_size_dict = {}
            self._data_map[stock_data.symbol] = bar_size_dict
        bar_size_dict[stock_data.bar_size] = stock_data
