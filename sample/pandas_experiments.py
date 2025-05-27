from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)

def main():
    df = pd.DataFrame(columns=["Fruit", "Color", "Rating"])
    df.loc[1] = ["Apple", "red", 7]
    df.loc[2] = ["Orange", "orange", 6]
    df.loc[3] = ["Banana", "yellow", 7.5]
    print("Starting Dataframe:")
    print(df)
    print()

    # Change orange rating
    df.loc[2] = ["Orange", "orange", 6.5]
    print("Changed rating of orange")
    print(df)
    print()

    # Add new items and removed one
    df.loc[4] = ["Pancake", "brown", 5]
    df.loc[5] = ["Grape", "purple", 7]
    df.drop(4, inplace=True)
    print("Added two rows, dropped one")
    print(df)
    print()

    # Get second item by raw index, should be Orange
    print(f"Fruit at raw index 1 {df.iloc[1]["Fruit"]}")

    # Get last item by raw index, should be Grape
    print(f"Fruit at raw index -1 {df.iloc[-1]["Fruit"]}")

    # Get index at raw index 0, should be 1
    print(f"Index at raw index 0 is {df.index[0]}")

    # Get index at raw index -1, should be 5
    print(f"Index at raw index -1 is {df.index[-1]}")

main()