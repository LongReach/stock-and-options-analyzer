# Instructions for Claude

## Brief Description of Goals

I'd like to be able to get earnings dates, future and past, for stocks of my choosing. It probably makes the most sense to attempt this project using `yfinance` as a data source.

## More Detailed Instructions

### Phase One

In the `core/` folder, please add a source file called `earnings.py`. This will contain the "API" for accessing earning datas via `yfinance`.

In the `scripts/examples/` folder, please make a script that exercises `earnings.py` and prints a list of earnings dates for some stock specified in the arguments. Please use `argparse`, as I do for other scripts.

### Phase Two

To be written later.