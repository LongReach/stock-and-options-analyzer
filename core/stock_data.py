from typing import Any, Dict
import logging
import pandas as pd
from datetime import datetime

from core.utils import BarSize, bar_size_to_str, non_naive_datetime
from core.common import RequestedInfoType

_logger = logging.getLogger(__name__)

DB_PATH = "data/market_data.h5"


class StockDataException(Exception):
    pass


class StockData:
    """
    A wrapper for a pandas Dataframe, which holds bars of price data.

    Columns: date (datetime), open, close, low, high, volume
    Indexed by: human-readable date-time
    """

    def __init__(
        self,
        symbol: str,
        bar_size: BarSize,
        info_type: RequestedInfoType = RequestedInfoType.TRADES,
    ):
        self._symbol = symbol
        self._bar_size = bar_size
        self._info_type = info_type
        self._price_and_vol_df: pd.DataFrame = pd.DataFrame(columns=["date", "open", "close", "low", "high", "volume"])
        self._loaded_from_cache: bool = False

    def add_data(self, bar: Dict[str, Any], date: datetime):
        """
        Adds a bar of data to StockData object. Once added, this object can be saved to disk.

        :param bar: dict of open, close, low, high, volume data
        :param date: datetime at which bar begins
        """
        # Make sure the date is in the right timezone
        date = non_naive_datetime(date)
        date_str = self._get_readable_date(date)
        df = self._price_and_vol_df
        df.loc[date_str] = [
            date,
            float(bar["open"]),
            float(bar["close"]),
            float(bar["low"]),
            float(bar["high"]),
            float(bar["volume"]),
        ]

    def finalize_data(self):
        """Call when all data has been added. Puts data into proper order."""
        self._price_and_vol_df.sort_values(by="date", inplace=True)

    def get_data_frame(self):
        """Returns pandas Dataframe"""
        return self._price_and_vol_df

    def load_from_db(self, db_path: str = DB_PATH, preserve_existing_data: bool = True) -> bool:
        """
        Loads data from the HDF5 database.
        :param db_path: path to the HDF5 file
        :param preserve_existing_data: if True, keep any data that was already scraped
        :return: True if data was loaded successfully
        """
        original_df = self._price_and_vol_df

        key = self.db_key
        try:
            _logger.info(f"Loading from DB {db_path}, key={key}")
            self._price_and_vol_df = pd.read_hdf(db_path, key=key)
        except:
            _logger.warning(f"Couldn't load key {key} from {db_path}")
            return False

        for idx in range(len(self._price_and_vol_df)):
            self._price_and_vol_df.iloc[idx, 0] = non_naive_datetime(self._price_and_vol_df.iloc[idx]["date"])

        if preserve_existing_data and len(original_df) > 0:
            self._price_and_vol_df = pd.concat([original_df, self._price_and_vol_df])
            # Remove rows with duplicate labels
            self._price_and_vol_df.drop_duplicates(inplace=True)
            self.finalize_data()

        self._loaded_from_cache = True
        return True

    def save_to_db(self, db_path: str = DB_PATH) -> bool:
        """
        Saves data to the HDF5 database.
        :param db_path: path to the HDF5 file
        :return: True if data was saved successfully
        """
        key = self.db_key
        try:
            _logger.info(f"Saving to DB {db_path}, key={key}")
            self._price_and_vol_df.to_hdf(db_path, key=key, complevel=6, complib="zlib")
        except:
            _logger.warning(f"Couldn't save key {key} to {db_path}")
            return False

        self._loaded_from_cache = True
        return True

    def clear(self):
        """Make new, empty dataframe"""
        self._price_and_vol_df: pd.DataFrame = pd.DataFrame(columns=["date", "open", "close", "low", "high", "volume"])
        self._loaded_from_cache = False

    def delete_from_db(self, db_path: str = DB_PATH) -> bool:
        """
        Removes this series from the HDF5 database.
        :param db_path: path to the HDF5 file
        :return: True if the key was found and removed
        """
        key = self.db_key
        try:
            with pd.HDFStore(db_path) as store:
                if f"/{key}" in store:
                    store.remove(key)
                    _logger.info(f"Deleted key {key} from {db_path}")
                    self.clear()
                    return True
                _logger.warning(f"Key {key} not found in {db_path}")
                return False
        except Exception:
            _logger.warning(f"Couldn't delete key {key} from {db_path}")
            return False

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def bar_size(self) -> BarSize:
        return self._bar_size

    @property
    def info_type(self) -> RequestedInfoType:
        return self._info_type

    @property
    def db_key(self) -> str:
        """Returns the HDF5 store key for this series, e.g. 'SPY_1d_tr'"""
        raw = f"{self._symbol}_{bar_size_to_str(self._bar_size)}_{StockData.get_info_type_str(self._info_type)}"
        return raw.replace(".", "_")

    @staticmethod
    def get_info_type_str(info_type: RequestedInfoType) -> str:
        """Convert a RequestedInfoType to a simple string"""
        _map: Dict[RequestedInfoType, str] = {
            RequestedInfoType.TRADES: "tr",
            RequestedInfoType.IMPLIED_VOLATILITY: "iv",
            RequestedInfoType.HISTORICAL_VOLATILITY: "hv",
            RequestedInfoType.ADJUSTED_LAST: "al",
        }
        result = _map.get(info_type)
        if not result:
            raise StockDataException(f"Couldn't convert {info_type.name} to str")
        return result

    @staticmethod
    def get_info_type(info_type_str: str) -> RequestedInfoType:
        """Convert a simple string to a RequestedInfoType"""
        _map: Dict[str, RequestedInfoType] = {
            "tr": RequestedInfoType.TRADES,
            "iv": RequestedInfoType.IMPLIED_VOLATILITY,
            "hv": RequestedInfoType.HISTORICAL_VOLATILITY,
            "al": RequestedInfoType.ADJUSTED_LAST,
        }
        result = _map.get(info_type_str)
        if not result:
            raise StockDataException(f"Couldn't convert string {info_type_str} to RequestedInfoType")
        return result

    def _get_readable_date(self, dt: datetime):
        """Converts a datetime into a human-readable string"""
        if self._bar_size in [BarSize.ONE_DAY, BarSize.ONE_WEEK]:
            return f"{dt.month:02}/{dt.day:02}/{dt.year:04}"
        else:
            return f"{dt.month:02}/{dt.day:02} {dt.hour:02}:{dt.minute:02}"
