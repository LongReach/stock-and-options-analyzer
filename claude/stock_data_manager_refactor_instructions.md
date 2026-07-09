# Instructions for Claude

## Brief Description of Goals

Please make modifications to `stock_data.py` and `stock_data_manager.py` that cause time series stock market data to be saved in a database, rather than in individual ZIP files. The goal is to avoid having to talk to Interactive Brokers to retrieve market data that's already cached locally. Retrieving the cached data is a lot faster. However, if recent data is not in the cache, then the `StockDataManager` class will obtain it from Interactive Brokers (via `IBDriver`) and add it to the cache.

## More Detailed Instructions

Each sequence of bar data is for:
* a particular stock, option contract, or ETF
* a particular timeframe, e.g. one-minute, five-minute, one day, one week, etc. See `BarSize`.
* a particular type of date, e.g. trade price, implied volatilty, etc. See `RequestedInfoType`.

For the database solution, my goals are:
* use a solution that's standard for this sort of problem
* use a solution that's efficient in consumption of disk space
* use a solution that supports pandas data

Please avoid making changes to files other than `stock_data.py` and `stock_data_manager.py`, unless this is unavoidable.