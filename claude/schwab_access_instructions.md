# Instructions for Claude

## Brief Description of Goals

Currently, I have the class `IBDriver`, which inherits from `BaseDriver` and allows access to the Interactive Brokers API. I want to eventually create a class called `SchwabDriver`, which will live in the folder `core\schwab` and do the same thing with a Schwab account. I've already registered with the Schwab Developer portal and should be ready to get started accessing the API.

## More Detailed Instructions

### Phase One

For now, I don't necessarily need to implement `SchwabDriver`. I'd just like a proof-of-concept program that will be in `scripts/examples`. It will get the daily OHLC bars for SPY over the last week and print them out.

I need help with:
* Deciding the best way to access Schwab's APIs. There seem to be well-regarded third-party Python libraries out there. I want an approach that fits best with my current design for `BaseDriver`, not requiring too many modifications to it.
* There's a need, when building software that accesses Schwab's API, to do some token refreshing. I need a tool for that.
* Answering the question of whether I can do paper trading with Schwab, as I can with Interactive Brokers. 

Please present me with a menu of different approaches and the strengths/weaknesses of each.

Phase One will be complete when we get to the point of having a working version of the example script I describe above.

Note that I have my API secret stored in the file `.env`, as `APP_SECRET`. Please don't memorize or store the key anywhere else, because it's private data that I don't want to share with anyone. Just know that that's where it is. The callback is there as `CALLBACK`.

### Phase Two

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