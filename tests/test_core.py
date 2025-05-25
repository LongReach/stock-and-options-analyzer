from ibapi.common import BarData
from datetime import datetime
import pytest
from typing import List, Dict

from core.utils import get_datetime, get_datetime_as_str
from core.ib_driver import BarDataRequest


def test_datetime_conversion():
    """Tests that timestamps can be converted back and forth between IB format and datetime"""

    ib_timestamp_1 = "20250520 13:00:00 US/Eastern"
    ib_timestamp_2 = "20250523"

    dt_1 = get_datetime(ib_timestamp_1)
    assert ib_timestamp_1 == get_datetime_as_str(dt_1)

    dt_2 = get_datetime(ib_timestamp_2)
    assert "20250523 09:30:00 US/Eastern" == get_datetime_as_str(dt_2)

    bad_ib_timestamp = "2025523"
    with pytest.raises(TypeError):
        get_datetime(bad_ib_timestamp)

    bad_ib_timestamp = "20250537"
    with pytest.raises(TypeError) as ex:
        get_datetime(bad_ib_timestamp)
    assert "TypeError('Bad day value of 37 in IB date 20250537')" in str(ex)

def test_bar_request_class():

    # Bar data not entirely in proper order, with two duplicate entries (20250513)
    bar_info_list: List[Dict] = [
        {"date": "20250512", "open": "581.49", "close": "582.99", "low": "577.04", "high": "583.0", "volume": "47256818"},
        {"date": "20250514", "open": "587.83", "close": "587.59", "low": "585.53", "high": "588.98", "volume": "41691962"},
        {"date": "20250515", "open": "585.56", "close": "590.46", "low": "585.09", "high": "590.97", "volume": "45789629"},
        {"date": "20250516", "open": "591.25", "close": "594.2", "low": "589.28", "high": "594.5", "volume": "37450157"},
        {"date": "20250519", "open": "588.1", "close": "594.85", "low": "588.09", "high": "595.54", "volume": "41787527"},
        {"date": "20250523", "open": "575.98", "close": "579.11", "low": "575.6", "high": "581.82", "volume": "45476396"},
        {"date": "20250520", "open": "593.11", "close": "592.85", "low": "589.6", "high": "594.05", "volume": "39355455"},
        {"date": "20250513", "open": "583.46", "close": "586.84", "low": "582.84", "high": "587.00", "volume": "40068411"},
        {"date": "20250513", "open": "583.46", "close": "586.84", "low": "582.84", "high": "588.00", "volume": "40068412"},
        {"date": "20250521", "open": "588.47", "close": "582.86", "low": "581.82", "high": "592.58", "volume": "62934210"},
        {"date": "20250513", "open": "583.46", "close": "586.84", "low": "582.84", "high": "589.08", "volume": "40068413"},
        {"date": "20250522", "open": "582.66", "close": "583.09", "low": "581.4", "high": "586.62", "volume": "47837028"},
    ]

    bar_data_request = BarDataRequest("SPY")
    for bar_info in bar_info_list:
        bar_data = BarData()
        bar_data.date = bar_info["date"]
        bar_data.open = bar_info["open"]
        bar_data.close = bar_info["close"]
        bar_data.high = bar_info["high"]
        bar_data.low = bar_info["low"]
        bar_data.volume = bar_info["volume"]
        bar_data_request.add_or_update_bar(bar_data)

    # There should be 10 entries, not 12
    assert len(bar_data_request.bar_data) == 10

    # Make sure last entry is what we'd expect
    assert bar_data_request.bar_data[-1].date == "20250523"

    # Make sure third entry is what we'd expect
    assert bar_data_request.bar_data[2].date == "20250514"

    # Make sure second entry has expected high
    assert bar_data_request.bar_data[1].high == "589.08"
