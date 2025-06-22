import asyncio
from logging import basicConfig, INFO, getLogger
import time
from typing import List, Tuple, Dict, Any

import pandas as pd
from ibapi.common import BarData
from datetime import datetime, timedelta
import argparse
import traceback

from core.common import RequestedInfoType
from core.ib_driver import IBDriver, BarSize
from core.stock_data_manager import StockDataManager
from core.stock_data import StockData
from core.utils import (
    str_to_bar_size,
    get_datetime,
    get_datetime_as_str,
    current_datetime,
)
from app.common import PositionColumn, TradeColumn, column_enum_to_str
from app.dialog import Dialog, MainDialog, PositionDialog, TradeDialog
from app.opt_position_tracker import OptionPositionTracker

position_tracker: OptionPositionTracker = None


def print_df(df):
    if df is None:
        print("ERROR: no dataframe")
        return
    print("Dataframe is:\n---------------")
    print("Head:")
    print(df.head())
    print("Tail:")
    print(df.tail())


def input_trades_new(position_fields: Dict[PositionColumn, Any]):
    """The user has already entered position data, now they must enter trade data"""
    pos_num = position_fields[PositionColumn.POSITION_NUMBER]
    strategy = position_fields[PositionColumn.STRATEGY]
    date_opened = position_fields[PositionColumn.DATE_OPENED]
    if strategy in ["CS", "DS"]:
        dialog = TradeDialog("Short leg" if strategy == "CS" else "Long leg")
        input_fields = {
            TradeColumn.RIGHT: "",
            TradeColumn.EXPIRATION: "",
            TradeColumn.STRIKE: -1.0,
            TradeColumn.NUM_CONTRACTS: 1,
            TradeColumn.OPENING_PRICE: -1.0,
        }
        dialog.set_fields_and_defaults(input_fields)
        dialog.collect_input()
        out_fields_1 = dialog.get_main_fields()
        out_fields_1[TradeColumn.POSITION_NUMBER] = pos_num
        out_fields_1[TradeColumn.DATE_OPENED] = date_opened
        out_fields_1[TradeColumn.DATE_CLOSED] = ""

        input_fields.pop(TradeColumn.RIGHT, None)
        input_fields.pop(TradeColumn.EXPIRATION, None)
        input_fields.pop(TradeColumn.NUM_CONTRACTS, None)
        dialog = TradeDialog("Long leg" if strategy == "CS" else "Short leg")
        dialog.set_fields_and_defaults(input_fields)
        dialog.collect_input()
        out_fields_2 = dialog.get_main_fields()
        out_fields_2[TradeColumn.POSITION_NUMBER] = pos_num
        out_fields_2[TradeColumn.DATE_OPENED] = date_opened
        out_fields_2[TradeColumn.DATE_CLOSED] = ""
        out_fields_2[TradeColumn.RIGHT] = out_fields_1[TradeColumn.RIGHT]
        out_fields_2[TradeColumn.EXPIRATION] = out_fields_1[TradeColumn.EXPIRATION]
        out_fields_2[TradeColumn.NUM_CONTRACTS] = out_fields_1[
            TradeColumn.NUM_CONTRACTS
        ]

        position_tracker.add_trade_row(out_fields_1)
        position_tracker.add_trade_row(out_fields_2)
    if strategy == "IC":
        dialog = TradeDialog("Short bull leg")
        input_fields = {
            TradeColumn.EXPIRATION: "",
            TradeColumn.STRIKE: -1.0,
            TradeColumn.NUM_CONTRACTS: 1,
            TradeColumn.OPENING_PRICE: -1.0,
        }
        dialog.set_fields_and_defaults(input_fields)
        dialog.collect_input()
        out_fields_1 = dialog.get_main_fields()
        out_fields_1[TradeColumn.POSITION_NUMBER] = pos_num
        out_fields_1[TradeColumn.DATE_OPENED] = date_opened
        out_fields_1[TradeColumn.DATE_CLOSED] = ""
        out_fields_1[TradeColumn.RIGHT] = "P"

        input_fields.pop(TradeColumn.EXPIRATION, None)
        input_fields.pop(TradeColumn.NUM_CONTRACTS, None)
        dialog = TradeDialog("Long bull leg")
        dialog.set_fields_and_defaults(input_fields)
        dialog.collect_input()
        out_fields_2 = dialog.get_main_fields()
        out_fields_2[TradeColumn.POSITION_NUMBER] = pos_num
        out_fields_2[TradeColumn.DATE_OPENED] = date_opened
        out_fields_2[TradeColumn.DATE_CLOSED] = ""
        out_fields_2[TradeColumn.RIGHT] = "P"
        out_fields_2[TradeColumn.EXPIRATION] = out_fields_1[TradeColumn.EXPIRATION]
        out_fields_2[TradeColumn.NUM_CONTRACTS] = out_fields_1[
            TradeColumn.NUM_CONTRACTS
        ]

        dialog = TradeDialog("Short bear leg")
        dialog.set_fields_and_defaults(input_fields)
        dialog.collect_input()
        out_fields_3 = dialog.get_main_fields()
        out_fields_3[TradeColumn.POSITION_NUMBER] = pos_num
        out_fields_3[TradeColumn.DATE_OPENED] = date_opened
        out_fields_3[TradeColumn.DATE_CLOSED] = ""
        out_fields_3[TradeColumn.RIGHT] = "C"
        out_fields_3[TradeColumn.EXPIRATION] = out_fields_1[TradeColumn.EXPIRATION]
        out_fields_3[TradeColumn.NUM_CONTRACTS] = out_fields_1[
            TradeColumn.NUM_CONTRACTS
        ]

        dialog = TradeDialog("Long bear leg")
        dialog.set_fields_and_defaults(input_fields)
        dialog.collect_input()
        out_fields_4 = dialog.get_main_fields()
        out_fields_4[TradeColumn.POSITION_NUMBER] = pos_num
        out_fields_4[TradeColumn.DATE_OPENED] = date_opened
        out_fields_4[TradeColumn.DATE_CLOSED] = ""
        out_fields_4[TradeColumn.RIGHT] = "C"
        out_fields_4[TradeColumn.EXPIRATION] = out_fields_1[TradeColumn.EXPIRATION]
        out_fields_4[TradeColumn.NUM_CONTRACTS] = out_fields_1[
            TradeColumn.NUM_CONTRACTS
        ]

        position_tracker.add_trade_row(out_fields_1)
        position_tracker.add_trade_row(out_fields_2)
        position_tracker.add_trade_row(out_fields_3)
        position_tracker.add_trade_row(out_fields_4)


def print_pos_row(row: Dict[str, Any]):
    print(f"Position: {row[column_enum_to_str(PositionColumn.POSITION_NUMBER)]}")
    print("------------------")
    print(f"Ticker: {row[column_enum_to_str(PositionColumn.TICKER)]}")
    print(f"Strategy: {row[column_enum_to_str(PositionColumn.STRATEGY)]}")
    print(f"Date opened: {row[column_enum_to_str(PositionColumn.DATE_OPENED)]}")
    date_closed = row[column_enum_to_str(PositionColumn.DATE_CLOSED)]
    if date_closed != "":
        print(f"Date closed: {date_closed}")


def print_trade_rows(rows: pd.DataFrame):
    drop_columns = [TradeColumn.POSITION_NUMBER]
    drop_columns = [column_enum_to_str(dc) for dc in drop_columns]
    modified_df = rows.drop(columns=drop_columns)
    print("------------------")
    print(modified_df)


async def run():
    while True:
        # This dialog asks the user to make a choice about what action to take, e.g.
        # new position, show position, etc.
        dialog = MainDialog()
        dialog.collect_input()
        other_fields = dialog.get_other_fields()
        if other_fields["choice"] == "new position":
            pos_num = position_tracker.get_and_increment_new_position_number()
            current_date = get_datetime_as_str(current_datetime())
            input_dict = {
                PositionColumn.STRATEGY: "",
                PositionColumn.TICKER: "",
                PositionColumn.DATE_OPENED: current_date,
            }
            dialog = PositionDialog(f"Position {pos_num}")
            dialog.set_fields_and_defaults(input_dict)
            dialog.collect_input()
            position_fields = dialog.get_main_fields()
            position_fields[PositionColumn.POSITION_NUMBER] = pos_num
            position_fields[PositionColumn.DATE_CLOSED] = ""
            position_tracker.add_position_row(position_fields)
            # Now the user must input the opened trades for the position
            input_trades_new(position_fields)
        elif other_fields["choice"] == "show position":
            position_number = other_fields["position number"]
            pos_dict = position_tracker.get_position_row(position_num=position_number)
            trades_df = position_tracker.get_trade_rows(position_num=position_number)
            print_pos_row(pos_dict)
            print_trade_rows(trades_df)
        elif other_fields["choice"] == "show positions":
            pos_df = position_tracker.get_position_rows()
            print(pos_df)
        elif other_fields["choice"] == "exit":
            position_tracker.save()
            break


async def main(
    set_name: str,
):
    global position_tracker

    logger = getLogger(__name__)
    basicConfig(filename="options_position_tracker.log", level=INFO)

    position_tracker = OptionPositionTracker(set_name)
    position_tracker.load()
    await run()


parser = argparse.ArgumentParser(description="Tool for tracking options positions")
parser.add_argument("--set", help="name of a set of positions", required=True, type=str)
args = parser.parse_args()

asyncio.run(main(args.set))
