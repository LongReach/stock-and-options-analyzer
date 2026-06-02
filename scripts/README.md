# scripts

## What's Here?

The `examples/` folder contains scripts that demonstrate how to use the core API.

The scripts outside of `examples/` are for launching command-line applications.

## Usage 

Run scripts here from root folder like:

```powershell
# Or whatever your environment is
conda activate options_2025_1

# Like on of the following
python -m scripts.cache_data --symbol SPY --info-only
python -m scripts.example.place_order_example
```
## Applications

`cache_data.py`

A tool for caching stock market data on the local drive. The `StockDataManager` class is designed to prefer cached market data over that sourced directly from the broker's API, which can be *VERY* slow when large amounts of data are requested at once.

-----------

`iv_finder.py`

A tool for finding stocks with high or low implied volatility, relative to recent history. This ability is essential to strategies that involve selling or buying options. The user can then investigate the options chains for specific stocks, receiving a table of the best choices, along with a prediction of the expected one-standard-deviation move.

-----------

`missile_launcher.py`

Launches the GuidedMissile application, a command-line tool intended to be used in conjunction with Interactive Broker's Trader Workstation. GuidedMissile is for daytrading; it automatically sets entry and exit points for long or short positions, with appropriate sizing. Take-profit limit orders are automatically set as well.

As the name might lead one to guess, GuidedMissile requires the user to "press the button". It won't initiate any new position without a direct command from a human. However, it handles the "targeting" itself. Since stocks can move very fast on a minute-to-minute chart, the less a user has to fumble around with a clunky graphical interface, with the potential of entering an extra zero by mistake, the better. Of course, the tool is only useful if the user is viewing charts on Trader Workstation; the user will need to set those up before the market opens.

-----------

`options_position_tracker.py`

A tool for keeping tracking of options positions the user is currently in. Needs refactoring. For now, don't try to use it.

## Examples

The scripts in `examples/` demonstrate various ways of using the API.