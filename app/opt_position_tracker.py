import pandas
import pandas as pd
import logging
from typing import List, Dict, Any, Optional

from core.option_data_manager import OptionDataManager
from app.common import (
    TradeColumn,
    PositionColumn,
    column_enum_to_str,
    PositionTrackerException,
)

_logger = logging.getLogger(__name__)


class OptionPositionTracker:

    def __init__(self, set_name: str):
        self._set_name = set_name
        self._trade_column_name_map: Dict[str, int] = {
            column_enum_to_str(col): col.value for col in TradeColumn
        }
        self._position_column_name_map: Dict[str, int] = {
            column_enum_to_str(col): col.value for col in PositionColumn
        }
        self._trades_df = pandas.DataFrame(
            columns=[key for key in self._trade_column_name_map.keys()]
        )
        self._positions_df = pandas.DataFrame(
            columns=[key for key in self._position_column_name_map.keys()]
        )
        self._new_position_number = 0

    def load(self):
        filenames = [
            f"{self._set_name}_opt_trades.zip",
            f"{self._set_name}_opt_positions.zip",
        ]
        load_success = True
        for i, filename in enumerate(filenames):
            path = f"./data/{filename}"
            try:
                _logger.info(f"Attempting to load pickle {path}")
                if i == 0:
                    self._trades_df = pd.read_pickle(path)
                else:
                    self._positions_df = pd.read_pickle(path)
                    self._new_position_number = len(self._positions_df)
            except:
                _logger.warning(f"Couldn't load file {filename}")
                load_success = False
        return load_success

    def save(self):
        filenames = [
            f"{self._set_name}_opt_trades.zip",
            f"{self._set_name}_opt_positions.zip",
        ]
        save_success = True
        for i, filename in enumerate(filenames):
            path = f"./data/{filename}"
            try:
                _logger.info(f"Attempting to save pickle {path}")
                if i == 0:
                    self._trades_df.to_pickle(path)
                else:
                    self._positions_df.to_pickle(path)
            except:
                _logger.warning(f"Couldn't save file {filename}")
                save_success = False
        return save_success

    def get_and_increment_new_position_number(self):
        """Returns an integer that can be used as fresh position number"""
        # TODO: I think := is a better way to do this
        num = self._new_position_number
        self._new_position_number += 1
        return num

    def add_position_row(self, fields_dict: Dict[PositionColumn, Any]):
        """
        Adds a row defining a new position to pandas dataframe
        :param fields_dict: dictionary representing the row
        """
        # Copy with fields enumerations replaced by strings
        row_copy = {
            column_enum_to_str(field): val for field, val in fields_dict.items()
        }
        self._positions_df.loc[len(self._positions_df)] = row_copy

    def add_trade_row(self, fields_dict: Dict[TradeColumn, Any]):
        """
        Adds a row defining a new trade to pandas dataframe
        :param fields_dict: dictionary representing the row
        """
        # These fields must be filled in with 0s
        auto_fields = [
            TradeColumn.LAST_PRICE,
            TradeColumn.IV,
            TradeColumn.DELTA,
            TradeColumn.THETA,
            TradeColumn.GAMMA,
            TradeColumn.VEGA,
        ]
        for field in auto_fields:
            fields_dict[field] = 0.0
        # Copy with fields enumerations replaced by strings
        row_copy = {
            column_enum_to_str(field): val for field, val in fields_dict.items()
        }
        self._trades_df.loc[len(self._trades_df)] = row_copy

    def get_position_rows(
            self, position_num: int = -1, is_open: Optional[bool] = None
    ) -> pd.DataFrame:
        """
        Gets pandas Dataframe containing a set of selected position rows
        :param position_num: if given, filter for rows with this pos num
        :param is_open: if not None, filter for rows with open or closed positions
        :return:
        """
        filtered_df = self._positions_df
        if position_num != -1:
            filtered_df = filtered_df[
                filtered_df[column_enum_to_str(PositionColumn.POSITION_NUMBER)]
                == position_num
                ]
        if is_open is not None:
            if is_open:
                filtered_df = filtered_df[
                    filtered_df[column_enum_to_str(PositionColumn.DATE_CLOSED)] == ""
                    ]
            else:
                filtered_df = filtered_df[
                    filtered_df[column_enum_to_str(PositionColumn.DATE_CLOSED)] != ""
                    ]
        return filtered_df

    def get_position_row(self, position_num: int = -1) -> Dict[str, Any]:
        """Gets a single position row, returns as dict"""
        df = self.get_position_rows(position_num=position_num)
        if len(df) == 0:
            raise PositionTrackerException(
                f"No positions with position number {position_num}"
            )
        if len(df) > 1:
            raise PositionTrackerException(
                f"Multiple with position number {position_num}"
            )
        return df.iloc[0].to_dict()

    def get_trade_rows(self, position_num: int = -1):
        """Gets trade rows according to filtering params given"""
        filtered_df = self._trades_df
        if position_num != -1:
            filtered_df = filtered_df[
                filtered_df[column_enum_to_str(TradeColumn.POSITION_NUMBER)]
                == position_num
                ]
        return filtered_df
