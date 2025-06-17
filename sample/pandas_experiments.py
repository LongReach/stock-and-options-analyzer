from typing import Optional, List, Union, Tuple, Any, Dict
import logging
import pandas as pd
from pandas import DataFrame, read_pickle, DatetimeIndex
from datetime import datetime, timedelta

_logger = logging.getLogger(__name__)


def main():
    fruit_df = pd.DataFrame(columns=["Fruit", "Color", "Rating"])
    fruit_df.loc[1] = ["Apple", "red", 7]
    fruit_df.loc[2] = ["Orange", "orange", 6]
    fruit_df.loc[3] = ["Banana", "yellow", 7.5]
    print("Starting Dataframe:")
    print(fruit_df)
    print()

    # Change orange rating
    fruit_df.loc[2] = ["Orange", "orange", 6.5]
    print("Changed rating of orange")
    print(fruit_df)
    print()

    # Add new items and removed one
    fruit_df.loc[4] = ["Pancake", "brown", 5]
    fruit_df.loc[5] = ["Grape", "purple", 7]
    fruit_df.drop(4, inplace=True)
    print("Added two rows, dropped one")
    print(fruit_df)
    print()

    # Get second item by raw index, should be Orange
    print(f"Fruit at raw index 1 {fruit_df.iloc[1]["Fruit"]}")

    # Get last item by raw index, should be Grape
    print(f"Fruit at raw index -1 {fruit_df.iloc[-1]["Fruit"]}")

    # Get index at raw index 0, should be 1
    print(f"Index at raw index 0 is {fruit_df.index[0]}")

    # Get index at raw index -1, should be 5
    print(f"Index at raw index -1 is {fruit_df.index[-1]}")
    print()

    animal_data = {
        "animal": ["cat", "dog", "monkey", "parrot"],
        "legs": [4, 4, 2, 2],
        "date": ["01012020", "05052020", "02022020", "04042020"],
    }
    animal_df = pd.DataFrame(animal_data)
    print("Unsorted DF:")
    print(animal_df)
    print()

    animal_df.sort_values(by="date", inplace=True)
    print("Sorted DF:")
    print(animal_df)
    print()

    print("Second row is:")
    print(animal_df.iloc[1])

    print("1st column in second row is:")
    print(animal_df.iloc[1, 0])


main()
