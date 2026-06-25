# core

This folder contains all the files that make up my API. (Perhaps it should be renamed to `api/`.)

As of right now, the code here only works with Interactive Brokers, but other brokerages will likely be supported in the future.

## File Inventory

| Name                     | Purpose                                                                                                                         |
|--------------------------|---------------------------------------------------------------------------------------------------------------------------------|
| `cache.py`               | A generic time-to-live cache class                                                                                              |
| `common.py`              | Classes core to this API. Designed to be broker-agnostic, i.e. not just for use with Interactive Brokers.                       |
| `ib/`                    | Classes specifically for communicating with Interactive Brokers. Can be used on their own or in conjuction with other code here. |
| `indicators.py`          | Code for various trading indicators such as stochastics, MACD, and RSI |
| `option_data_manager.py` | A helper class for obtaining option contract data                                                                               |
| `stock_data_manager.py`  | A helper class for obtaining and locally caching stock data. The caching feature is essential for writing high-performance applications that require a lot of historical data. |
| `utils.py`               | Utility functions |

Usage examples can be found in `scripts/examples/`.