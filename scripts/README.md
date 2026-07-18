# scripts

## What's Here?

The `examples/` folder contains scripts that demonstrate how to use the core API.

The scripts outside of `examples/` are for launching command-line applications.

## Usage 

Run scripts here from root folder like:

```powershell
# Or whatever your environment is
conda activate options_2025_1

# Like one of the following
python -m scripts.cache_data --symbol SPY --info-only
python -m scripts.example.place_order_example
```
## Applications

`cache_data.py`

A tool for caching stock market data on the local drive. The `StockDataManager` class is designed to prefer cached market data over that sourced directly from the broker's API, which can be *VERY* slow when large amounts of data are requested at once.

-----------

`cache_earnings.py`

A tool for collecting earnings dates for stocks of interest and caching them.

-----------

`get_schwab_positions.py`

A tool for gathering the user's position data from Schwab and putting it into a positions CSV file that the user can further edit.

-----------

`get_schwab_trades.py`

A tool for gathering the user's trade/transaction records from Schwab and updating a positions CSV file.

-----------

`iv_finder.py`

A tool for finding stocks with high or low implied volatility, relative to recent history. This ability is essential to strategies that involve selling or buying options. The user can then investigate the options chains for specific stocks, receiving a table of the best choices, along with a prediction of the expected one-standard-deviation move.

-----------

`missile_launcher.py`

Launches the GuidedMissile application, a command-line tool intended to be used in conjunction with Interactive Broker's Trader Workstation. GuidedMissile is for daytrading; it automatically sets entry and exit points for long or short positions, with appropriate sizing. Take-profit limit orders are automatically set as well.

As the name might lead one to guess, GuidedMissile requires the user to "press the button". It won't initiate any new position without a direct command from a human. However, it handles the "targeting" itself. Since stocks can move very fast on a minute-to-minute chart, the less a user has to fumble around with a clunky graphical interface, with the potential of entering an extra zero by mistake, the better. Of course, the tool is only useful if the user is viewing charts on Trader Workstation; the user will need to set those up before the market opens.

-----------

`position_analyzer.py`

A tool for analyzing options positions recorded in a positions CSV file. The tool helps the user determine what adjustments or hedging might be necessary, based on values like delta, gamma, theta, etc.

-----------

`vs_helper.py`

A tool for determining when it might be a good idea to open a vertical spread. For now, functionality is limited.

## Examples

The scripts in `examples/` demonstrate various ways of using the API.

## Recommended Workflow

### Build main caches

The trade cache is needed for use with `vs_helper.py`. The IV cache is needed for use with `iv_finder.py`. In both cases, the caches must be updated to include recent data (i.e. today's).

Build trade data database. This will likely take multiple runs, until timeout errors stop appearing. Requires IB connection.
```powershell
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\market_data.h5 --bar-size 1d --info-type tr
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\market_data.h5 --bar-size 1w --info-type tr
```

Update trade data database
```powershell
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\market_data.h5 --bar-size 1d --info-type tr --update
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\market_data.h5 --bar-size 1w --info-type tr --update
```

Build implied volatility database. This will likely take multiple runs, until timeout errors stop appearing. Requires IB connection.
```powershell
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\iv_data.h5 --bar-size 1d --info-type iv
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\iv_data.h5 --bar-size 1w --info-type iv
```

Update implied volatility database
```powershell
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\iv_data.h5 --bar-size 1d --info-type iv --update
python -m scripts.cache_data --file .\data\optionable.txt --db .\data\iv_data.h5 --bar-size 1w --info-type iv --update
```

### Build earnings cache

```powershell
python -m scripts.cache_earnings --db data/earnings_data.h5 --file data/optionable.txt
```

### Create positions CSV

Initial creation. You then have to edit the resulting file and make legs of same position have a common position number, as well as designate the position type.
```powershell
python -m scripts.get_schwab_positions data/current_positions.csv
```

Update from recent trades (7/17/26 and after, in this case):
```powershell
python -m scripts.get_schwab_trades --positions-csv data/current_positions.csv 20260717
```

### Analyze current positions

```powershell
python -m scripts.position_analyzer --positions-file .\data\current_positions.csv --schwab --position-num 6
```