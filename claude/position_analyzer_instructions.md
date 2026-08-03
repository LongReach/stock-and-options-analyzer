# Instructions for Claude

## Brief Description of Goals

I want a software tool that analyzes options positions I currently hold. I'm interested in per-leg details like delta, theta, gamma, IV, etc. I'm also interested in metrics for a position as a whole, such as its current unrealized profit/loss, its total delta/theta/gamma/vega, and its potential max profit/max loss.

## More Detailed Instructions

### Phase One (Complete)

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

## Phase Two  (Complete)

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

## Phase Three (Complete)

Add support for an optional `--show` argument. If given, the tool will show all positions currently held, as specified by the given CSV file. No other functionality will be exercised.

What will be shown will be a pretty-printed table with the following columns:
* Position Number: see `D:\CodingProjects\Python\TWS2025\data\current_positions.csv` as a reference
* Symbol: since there might be multiple contracts per position, strip then down to the underlying ticker that they have in common (e.g. SPY, QQQ, INTC) and display that
* Entry date: display the earliest entry date for any of the legs in the position
* Cost basis: for the whole position, with negative values representing overall credit collected
* Realized P/L
* Unrealized P/L

## Phase Four (Complete)

As soon as the CSV is loaded, make the tool verify that these columns are all present:
Position #,Date In,Position Type,Symbol,Quantity,Trade Price,Date Out,Quantity Out,Exit Price

The tool should complain if any of these headers aren't present at the stop of the CSV. Also, it'll complain if any row is malformed. What malformed means is that some field is missing or contains the wrong type of data. Expected types by column:
* Position #: should be an `int`
* Date In: should be an IB-style datatime, e.g. "20260717 09:58:57 US/Eastern"
* Position Type: Should conform to a description found in `POSITION_TYPE_MAP`, e.g. "Double Diagonal"
* Symbol: should be a symbol in my preferred format, e.g. "SPY" or "SPY-C-20250627-600.0". Use `SecurityDescriptor.from_string()` for verification.
* Quantity: should be an `int`
* Trade Price: should be a `float`
* Date Out: should be an IB-style datatime, e.g. "20260717 09:58:57 US/Eastern", or else blank
* Quantity Out: should be an `int`
* Exit Price: should be a `float`

If any complaints happen, the tool should exit gracefully without doing anything else. Please print the contents of any problem rows for the user's reference.

Please don't do anything fancy with git. Just work in the current branch, which is `misc`.

## Phase Five (Complete)

### Step 1 (Complete)

The tool prints an expected next-day move, like so:
```
Expected next-day move (1 std dev)
------------------------------------------------------------
  SPY    price     735.51  IV  0.5690  move +/-  21.91  (713.60 to 757.42)
```

After the expected move is printed, have the tool print an expected move loss. Use the following formula:
```
expected_move_loss = abs(delta) * expected_move + 0.5 * abs(gamma) * expected_move * expected_move 
```

Note that "max loss" / "maximum loss" language is reserved for the maximum possible loss on the position as
a whole, so it must not be used for this quantity.

The values of `delta` and `gamma` are from the position as a whole.

## Phase Six

### Step 1 (Complete)

In per-leg analysis, the table includes the "Cur Price" field. It should only show the current price for legs where contracts are still held (long or short). If it's a flat leg (0 contracts held), show the average exit price, as obtained from the CSV.

Please rename the field in the table from "Cur Price" to "Cur/Exit Price".

### Step 2 (Complete)

Have the `--show` argument display two tables. One will be the "Positions held" table, which we already have.

The second will be the "Closed positions" table. It will be the same as "Positions held", except for positions that are completely closed (zero contracts long/short on all the legs).

If no held positions or closed positions exist, simply don't display the applicable table.

### Step 3 (Complete)

The position analyzer will take a new argument, `--xlsx`. If given and `--positions-file` is also given, the tool will generate an Excel spreadsheet. I plan to import these spreadsheets into my Google Drive.

The spreadsheet will have two pages, "Positions" and "Legs".

If new Python packages need to be installed for spreadsheet functionality, I'd that to be done in my conda environment: `options_2025_1`

#### The "Legs" Page

Each row on the "Legs" page will contain these fields:
Contract, Pos #, Pos Type, Qty, Held, Trade Price, Cur/Exit Price, Realized

Each row corresponds to a leg in the CSV file.

Please make the text for "Qty" green if a positive number, red if a negative number. Same idea for "Held", except keep the text black if the number is zero. For "Realized", make the background cell color green if the number is positive, red if negative. Leave colorless if it's zero.

#### The "Positions" Page

Each row on the "Positions" page will contain these fields:
Pos #, Symbol, Entry Date, Cost Basis, Realized, Unrealized, Closed

These are the same as what appears in the "Positions held" / "Closed positions" tables generated by use of the `--show` flag. It should be easy to repurpose existing code.

As for the "Closed" field, that will contain an "X" character if the position is closed. Otherwise, the field will be empty.

Please make the text for "Cost Basis" green if a positive number, red if a negative number. For "Realized" and "Unrealized", make the background cell color green if the number is positive, red if negative. Leave colorless if it's zero.

The positions will be listed in order of Pos #. It doesn't matter if the position is closed or still open. Both will appear.