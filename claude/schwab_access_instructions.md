# Instructions for Claude

## Brief Description of Goals

Currently, I have the class `IBDriver`, which inherits from `BaseDriver` and allows access to the Interactive Brokers API. I want to eventually create a class called `SchwabDriver`, which will live in the folder `core\schwab` and do the same thing with a Schwab account. I've already registered with the Schwab Developer portal and should be ready to get started accessing the API.

## More Detailed Instructions

### Phase One (Complete)

For now, I don't necessarily need to implement `SchwabDriver`. I'd just like a proof-of-concept program that will be in `scripts/examples`. It will get the daily OHLC bars for SPY over the last week and print them out.

I need help with:
* Deciding the best way to access Schwab's APIs. There seem to be well-regarded third-party Python libraries out there. I want an approach that fits best with my current design for `BaseDriver`, not requiring too many modifications to it.
* There's a need, when building software that accesses Schwab's API, to do some token refreshing. I need a tool for that.
* Answering the question of whether I can do paper trading with Schwab, as I can with Interactive Brokers. 

Please present me with a menu of different approaches and the strengths/weaknesses of each.

Phase One will be complete when we get to the point of having a working version of the example script I describe above.

Note that I have my API secret stored in the file `.env`, as `APP_SECRET`. Please don't memorize or store the key anywhere else, because it's private data that I don't want to share with anyone. Just know that that's where it is. The callback is there as `CALLBACK`.

### Phase Two (Complete)

It's time to create Version One of `SchwabDriver`. It will implement Schwab-specific functionality like what already exists for `IBDriver`. 

Only implement the following public functions:
create()
connect()
disconnect()
is_connected()
get_historical_data()
cancel_historical_data()
cancel_all_historical_data()
get_most_recent_data()

Make stub versions of the other public functions seen in `IBDriver`, but have these raise a `NotImplementedError`. `IBDriver` should automatically handle the token-refresh tasks necessary for working with the Schwab API (I mean the 30-minute auto).

Next, make a script in `scripts\examples` called `schwab_driver_example.py`. This will exercise the new driver in a few different ways. Please have it stream some one-minute candle data.

### Phase Three (Complete)

#### Step 1 (Complete)

The class `IBDriver` contains the function `get_positions()`. Please make a function that does the same thing in `SchwabDriver`. Any other public function currently raising a `NotImplementedError` exception can be left as it is.

In `scripts/` make a script called `get_schwab_positions.py`. This script will fetch all of the user's current positions from Schwab and pretty-print them. It will also take an optional argument with a file path for a CSV file. Into this file, it will place a table of currently-held positions. The format should be the same as in the file `D:\CodingProjects\Python\TWS2025\data\options_trades_2026.csv`.

#### Step 2 (Complete)

If the CSV file already exists, don't change the "Position #" or "Position Type" fields for any rows that are already recorded. It's okay to change "Quantity" and "Trade Price", if the information from the broker is different.

Please ensure that the rows are ordered by Position #, once the new or modified CSV is outputted.

You will find that the file `D:\CodingProjects\Python\TWS2025\data\current_positions.csv` is an example of a CSV in which the Position # and Position Type fields are set.

Please update the script, `position_analyzer.py` with a new position type, called "Triple Calendar".

### Phase Four (Complete)

In `ScnwabDriver`, please provide implementations for the following public functions. They should do what their counterparts in `IBDriver` do.

Functions:
get_option_info()
get_option_info_single()
get_options_chain_info()
get_greeks()

Any other public function raising a `NotImplementedError` exception can be left as it is.

The new code will be exercised by making modifications to these scripts:
`scripts/examples/options_driver_example.py`
`scripts/examples/options_manager_example.py`

Notice how each of these files has a constant called `BROKER`. This should be used to determine which driver becomes active. For now, you can set it to "SCHWAB" in each script.

Finally, let's do something similar for `scripts/position_analyzer.py`.

### Phase Five

I've noticed that the class `BarData`, from IB's API, is being used in different parts of the code. It should only be used by code within `core/ib`. For code outside this folder, please use the `DataBar` class. It's now found in `core/common.py`. Please remove any imports of IB-specific libraries that aren't done from withing `core/ib`.
