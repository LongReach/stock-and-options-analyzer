# Instructions for Claude

## Brief Description of Goals

I want a software tool that analyzes options positions I currently hold. I'm interested in per-leg details like delta, theta, gamma, IV, etc. I'm also interested in metrics for a position as a whole, such as its current unrealized profit/loss, its total delta/theta/gamma/vega, and its potential max profit/max loss.

## More Detailed Instructions

### Phase One

You will find a CSV file, `data/options_trades_2026.csv`, which contains some open positions.

Please make a script called `position_analyzer.py` in the `scripts/` folder. It will use the `argparse` library in a way similar to `cache_data.py`. The `--help` argument should work the same way.

This script will take the following command line arguments:
* `--positions-file`: path to a CSV containing open positions
* `--symbol` (optional): narrows analysis down to options which share a common underlying, e.g. "SPY" or "QQQ". If not given, the analysis process won't care what the underlying is.
* `--expiration` (optional): narrows analysis down to options with specified expiration date, e.g. "20260821". If not given, the analysis process won't care what the expiration date is for a particular option.

The script will make use of the class `OptionDataManager` for collecting current data about options contracts. The class `OptionData` provides access to many desired fields.

For the open options positions filtered down to, `position_analyzer.py` will output, for each leg, a row containing the following information:
* Contract name, e.g. `SPY-C-20260821-800.0`
* Position number: comes from the CSV
* Position type: comes from the CSV
* Quantity, number of contracts for that leg: comes from the CSV. A negative number indicates options sold, rather than bought
* Trade price: comes from the CSV
* Current price, per contract
* Current implied volatility, per contract
* Current delta, per contract
* Current theta, per contract
* Current gamma, per contract
* Current vega, per contract

Please use a pandas dataframe for each row of output. `OptionData` provides access to pandas data.

The script will also output a final row, with the same columns the per-leg rows, except that all the values will apply to all the legs in aggregate. The output should be pretty-formatted. The rules for determining aggregate delta, theta, gamma, etc. for options positions are well-documented online.

## Phase Two

Please add two more filtering arguments, `--position-num` and `--position-type`.

`--position-num` will filter on "Position #", as seen in the reference CSV.

`--position-type` will filter on "Position Type". However, arguments given to the script from the command line will take the following form:
* IC: maps to "Iron Condor"
* CS: maps to "Credit Spread"
* DS: maps to "Debit Spread"
* L: maps to "Naked Long"
* S: maps to "Naked Short"
* CAL: maps to "Calendar"
* DCAL: maps to "Double Calendar"
* DIAG: maps to "Diagonal"
* DDIAG: maps to "Double Diagonal"

## Phase Three

Will be written later.