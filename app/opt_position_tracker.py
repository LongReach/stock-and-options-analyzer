import pandas
import pandas as pd
import logging
from typing import List, Dict, Any

from core.option_data_manager import OptionDataManager
from app.common import TradeColumn, PositionColumn, column_enum_to_str

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
        # TODO: I think := is a better way to do this
        num = self._new_position_number
        self._new_position_number += 1
        return num
