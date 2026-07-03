# Instructions for Claude

## Brief Description of Goals

I'd like to be able to get earnings dates, future and past, for stocks of my choosing. It probably makes the most sense to attempt this project using `yfinance` as a data source.

## More Detailed Instructions

### Phase One

In the `core/` folder, please add a source file called `earnings.py`. This will contain the "API" for accessing earning datas via `yfinance`.

In the `scripts/examples/` folder, please make a script that exercises `earnings.py` and prints a list of earnings dates for some stock specified in the arguments. Please use `argparse`, as I do for other scripts.

### Phase Two

I don't like that `yfinance` limits the amount of data that I can pull at any one time. I just realized that I have the right subscription to get earnings dates from Interactive Brokers, so let's try that approach. Please modify the classes `IBDriver` and `BaseDriver` to support this functionality. As in Phase One, I'd like to be able to get both past and future earnings dates.

In the `scripts/examples/` folder, please make a script called `ib_earnings_example.py`. This will exercise the new code. 