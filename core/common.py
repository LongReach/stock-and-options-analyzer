from typing import Dict, List, Tuple
from datetime import datetime

class HistoricalData:

    def __init__(self, bars: List[Dict], datetimes: List[datetime]):
        self.bar_data_list = bars
        self.datetime_list = datetimes

    def is_empty(self):
        return len(self.bar_data_list) == 0

    def get_zipped_lists(self) -> List[Tuple[Dict, datetime]]:
        return list(zip(self.bar_data_list, self.datetime_list))