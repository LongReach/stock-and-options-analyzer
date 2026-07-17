# Suite of Tools for Stock/Options Market Analysis

![](./images/WIP.png)

## Table of Contents

* [Table of Contents](#table-of-contents)
* [For Potential Employers](#for-potential-employers)
* [Description](#description)
* [Design Inspirations](#design-inspirations)
  + [Why didn't I use existing third-party wrappers?](#why-didnt-i-use-existing-third-party-wrappers)
  + [Repackaging as pandas dataframes](#repackaging-as-pandas-dataframes)
* [Warning!](#warning)
* [Setup](#setup)
  + [Interactive Brokers](#interactive-brokers)
* [First Test](#first-test)
  + [Schwab](#schwab)
* [Libraries Here](#libraries-here)
* [Programs Here](#programs-here)
* [Troubleshooting](#troubleshooting)
  + [Interactive Brokers](#interactive-brokers-1)
    - [Error about another client accessing IB from a different IP address](#error-about-another-client-accessing-ib-from-a-different-ip-address)
    - [No market data during competing live session](#no-market-data-during-competing-live-session)
    - [Limitations on historical price data for an option](#limitations-on-historical-price-data-for-an-option)
    - [Interactive Brokers sometimes fails to return historical data, timing out, even though it HAS the data](#interactive-brokers-sometimes-fails-to-return-historical-data-timing-out-even-though-it-has-the-data)
    - [Interactive Brokers responds to historical data request with error about no data](#interactive-brokers-responds-to-historical-data-request-with-error-about-no-data)
    - [Errors about Interactive Brokers market data subscriptions](#errors-about-interactive-brokers-market-data-subscriptions)
    - [Can't get options data on weekend/after hours](#cant-get-options-data-on-weekendafter-hours)
    - [Can't get earnings dates from IB](#cant-get-earnings-dates-from-ib)
  + [Schwab](#schwab-1)
    - [How do I connect?](#how-do-i-connect)
    - [Why can't I get historical implied volatility data from Schwab?](#why-cant-i-get-historical-implied-volatility-data-from-schwab)

<small><i><a href='http://ecotrust-canada.github.io/markdown-toc/'>Table of contents generated with markdown-toc</a></i></small>

## For Potential Employers

See [this page](./docs/ForPotentialEmployers.md)

## Description

What this repository includes:
* `async` wrappers for the APIs of two major brokerages: Interactive Brokers and Charles Schwab. Both `IBDriver` and `ScnwabDriver` implement the broker-agnostic `BaseDriver` class. Thus, users can write programs to buy, sell, or analyze stock/options positions that will work with multiple brokerages, the specific one to be employed selected at run-time. For now, the feature set for `SchwabDriver` is a little less complete than for `IBDriver`, but I intend to keep both in sync going forward.
* `StockDataManager`: a class for collecting historical price and implied volatility data from the brokerage of choice and caching it on disk for fast access. No brokerage whose API I've worked with makes it possible to *QUICKLY* get large amounts of historical data. Caching is necessary for any application that depends on a swift analysis of historical data time series.
* `OptionDataManager`: a class that makes it easier to collect options data, as well as to place options orders. Again, broker-agnostic.
* `EarningsManager`: a tool that parses the Nasdaq's online earnings calendar to collect earnings dates for stocks of interest and to cache them for quick at-will access.
* `indicators.py`: a collection of commonly used stock trading indicators, such as MACD, RSI, and EMA.
* The `GuidedMissile` day-trading app. This is still a work in progress and I don't recommend using it.
* A collection of other tools, which make use of the core classes above in different ways. For example, `iv_finder` is a command-line scanning tool that finds stocks/ETFs with historically high or low implied volatility, and earnings before or after a specified date. This is handy for finding option chains that fit a particular options strategy.

> Note: Despite the overall broker-agnostic design, I've chosen, within my generic code, to use IB-style nomenclature for options contracts and dates. Security specification example: "QQQ-P-20260828-625.0". Date example: "20260513 09:30:00 US/Eastern". I find these readable and generic enough for general-purpose use.

## Design Inspirations

At an earlier time, I'd created some software that pulled stock market data from Yahoo Finance, then charted it in different ways. Unfortunately, in 2025, the `yfinance` Python library became increasingly unreliable, due to Yahoo's servers throttling requests.

It seemed smarter to get data from a paid source, rather than a free one. Could I use an API provided by a brokerage I already had an account with? The answer was yes and the obvious choice was [Interactive Brokers](https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/#api-introduction). For one thing, they offer a paper-trading account, so you can test out strategies "live", but in a simulated environment. For another, you can obtain historical data for specific options contracts, which you can't really do with Yahoo.

Third, back in 2020, I had already written some day trading software that communicated with Interactive Brokers' popular trading platform, Trader Workstation. The software both gathered market data and opened/closed actual positions. Here in 2025/2026, I decided to do something similar again, except via the lightweight "Gateway" bridge. Interactive Brokers doesn't have the most user-friendly Python API, but it's very powerful and provides access to pretty much any market data I could possibly want. 

### Why didn't I use existing third-party wrappers?

For Interactive Brokers, `ib_async` is a well-regarded third-party wrapper.

A few reasons I didn't use it:
* After my experience with `yfinance`, I distrusted third-party wrappers not officially supported by brokerages. I like having direct control.
* This was initially a do-it-myself project with limited scope. I simply didn't need a full-featured wrapper for IB's API. Plus, I had already written a basic wrapper of my own, several years before `ib_async` came into being, so I modernized and adapted that.
* As the project grew beyond its original scope, I decided to stick with my own library. `ib_async` exposes a bit too much of IB's implementation details for my liking, though this is perfectly natural for a thin wrapper. I wanted my interface to be more broker-agnostic. And I didn't want to have to write a wrapper around a wrapper -- better to just wrap the IB's API directly.
* I preferred my own approach to dealing with options contracts.

For Schwab, I gave in and chose to use the third-party wrapper, `schwab-py`. This was the recommendation of Claude Code, and it seemed to be the least painful approach.

### Repackaging as pandas dataframes

It makes good sense to keep historical market data, once obtained, in `pandas` dataframes. These can be easily cached on disk (past market data is unchanging), as well as fed to machine-learning models.

## Warning!

![](./images/SkullAndBones.jpg)

Use these tools at your own risk! I take no responsibility for any financial losses incurred through their use, whether due to bugs/faults in my software, mistakes or omissions in the documentation, user misunderstanding, problematic trading strategies, market behavior, unexpected presidential tweets, or anything else. I don't yet consider this to be a fully mature software framework. Several parts are under active development.

If you want to trade stocks or options, it's a good idea to practice with a paper account first.

## Setup

### Interactive Brokers

![](./images/IBLogo.png)

You have to have an Interactive Brokers account to use this software, even just to get market data. You also have to be subscribed to the "US Equity and Options Add-On Streaming Bundle (NP)", as well as the bundle that it requires as a prerequisite. And if you want to be able to get earnings dates from IB, that requires yet another subscription, to Wall Street Horizons. That one costs about $50 / month.

I created a `conda` environment for this project. First step was to install the Interactive Brokers API, as detailed in their [online guide](https://www.interactivebrokers.com/campus/ibkr-quant-news/interactive-brokers-python-api-native-a-step-by-step-guide/). Once I ran `python setup.py install`, the Python packages were installed in my environment. Or you can use `venv`, if you prefer that.

The online docs don't mention it, but I had to run `conda install setuptools` prior to running `setup.py`.

The next step was to install the Gateway software and configure it. Note that the ports for live trading and paper trading are 4001 and 4002, respectively. These tools will also work with Trader Workstation, which is Interactive Broker's desktop trading application. The ports for that use case are 7496 and 7497, for live and paper trading.

![](./images/IBGateway.png)    
`Above: Gateway`

For a command line interface, I use Anaconda's PowerShell Prompt. It works well with `git`, too. I use it like so:

```commandline
conda env list
conda activate options_2025_1
python -m scripts.place_order_example
```

## First Test

Test the setup using a program provided by Interactive Brokers themselves. Of course, the Gateway must be running and configured correctly.

```commandline
cd sample
python historical_market_data.py
```

(Make sure you've specified the right port in this script's source code.)

You should see some output like:
```
D:\CodingProjects\Python\TWS2025\sample>python historical_market_data.py
reqId: -1, errorCode: 2104, errorString: Market data farm connection is OK:usfarm, orderReject:
reqId: -1, errorCode: 2107, errorString: HMDS data farm connection is inactive but should be available upon demand.ushmds, orderReject:
reqId: -1, errorCode: 2158, errorString: Sec-def data farm connection is OK:secdefnj, orderReject:
reqId: -1, errorCode: 2106, errorString: HMDS data farm connection is OK:ushmds, orderReject:
4 Date: 20240523 09:30:00 US/Eastern, Open: 190.98, High: 191.01, Low: 189.05, Close: 189.42, Volume: 5298031, WAP: 189.938, BarCount: 24557
4 Date: 20240523 10:00:00 US/Eastern, Open: 189.42, High: 189.69, Low: 188.5, Close: 188.73, Volume: 5156525, WAP: 189.076, BarCount: 26118
4 Date: 20240523 11:00:00 US/Eastern, Open: 188.73, High: 189.71, Low: 188.68, Close: 189.69, Volume: 3032514, WAP: 189.367, BarCount: 15381
4 Date: 20240523 12:00:00 US/Eastern, Open: 189.69, High: 189.69, Low: 188.75, Close: 188.79, Volume: 2555639, WAP: 189.305, BarCount: 13657
4 Date: 20240523 13:00:00 US/Eastern, Open: 188.78, High: 188.97, Low: 187.56, Close: 187.59, Volume: 3872494, WAP: 188.318, BarCount: 19351
4 Date: 20240523 14:00:00 US/Eastern, Open: 187.59, High: 187.87, Low: 187.16, Close: 187.27, Volume: 3673161, WAP: 187.566, BarCount: 19337
4 Date: 20240523 15:00:00 US/Eastern, Open: 187.27, High: 187.83, Low: 186.62, Close: 186.91, Volume: 6469222, WAP: 187.157, BarCount: 35294
Historical Data Ended for 4. Started at 20240522 16:00:00 US/Eastern, ending at 20240523 16:00:00 US/Eastern
reqId: 4, errorCode: 366, errorString: No historical data query found for ticker id:4, orderReject:
```

To exit the program, close Gateway.

### Schwab

![](./images/SchwabLogo.png)

You need to register with Schwab's Developer Portal. It might take a few days for them to authorize you. Fortunately, you don't have to run any "gateway"-type software, as for Interactive Brokers. The first time you make use of `SchwabDriver`, the connection function will open up a login web page. Once you've logged into your Schwab account, your system will receive a security token that doesn't expire for a few days and is automatically refreshed. Thereafter, security measures won't interfere with being able to use `SchwabDriver` to get information or place orders. At least, not until the main token expires again.

Once you have a Developer Portal account, your next step will be creating a registered application.
* In the Dashboard, click "Create App".
* In the API products dropdown — Choose one or both of "Market Data Production" and "Accounts and Trading Production".
* Create an app name.
* Enter this app callback URL: https://127.0.0.1:8182
* The callback URL above is the localhost IP address for your local machine. This IP address is needed so that you can get the first batch of authentication tokens on your local machine.

For more basic setup instructions, see [schwab-py's documentation](https://schwab-py.readthedocs.io/en/latest/getting-started.html). Yes, `SchwabDriver` uses `schwab-py` under the hood.

Once you obtain your **API Key** and **App Secret**, you'll need to store these where your code can find them. They should never be in plain text in the code itself.

## Libraries Here

See the `core/` [README](./core/README.md).

## Programs Here

See the `scripts/` [README](./scripts/README.md)

## Troubleshooting

### Interactive Brokers

![](./images/IBLogo.png)

#### Error about another client accessing IB from a different IP address

You might have the IB app open on your phone (needed for authentication when you log on to a live trading account). Close it.

#### No market data during competing live session

Sometimes efforts to get options Greeks will fail because of an error, `No market data during competing live session`. Try closing the Gateway and reopening it.

#### Limitations on historical price data for an option

`No data of type EODChart is available for the exchange 'BEST' and the security type 'Option' and '1 d' and '1 day'`: When getting historical price data for an option, must use a smaller bar size than one-day.

#### Interactive Brokers sometimes fails to return historical data, timing out, even though it HAS the data

This seems to be a throttling issue. If you ask for data for too many securities in too short of a time frame (supposedly more than 60 within the space of ten minutes), IB won't respond. 

The solution, of course, is to store market data that's more than a few days old locally. It's never going to change, so no point to pulling it repeatedly from a remote server.

#### Interactive Brokers responds to historical data request with error about no data

Check that your request is formatted in a way that makes sense, e.g.:

```
INFO:ibapi.utils:REQUEST reqHistoricalData {'reqId': 2, 'contract': 2604726071856: 0,SPY,STK,,,0,,,SMART,,USD,,,False,,,,combo:, 'endDateTime': '', 'durationStr': '1 D', 'barSizeSetting': '1 day', 'whatToShow': 'OPTION_IMPLIED_VOLATILITY', 'useRTH': 1, 'formatDate': 1, 'keepUpToDate': False, 'chartOptions': []}
```

In this case, we're trying to get the most recent single 1-day bar of implied volatility data for SPY. It makes sense that the `endDateTime` field is empty and that `durationStr` is '1 D'. Had `durationStr` been in seconds or minutes, it might not have worked. Same with trying to use some odd `endDateTime`, such as one in the middle of the day.

#### Errors about Interactive Brokers market data subscriptions

You need to go to your account settings on the IB webpage. Subscribe to "US Equity and Options Add-On Streaming Bundle (NP)". You also have to subscribe to the bundle that this one tells you it needs.

IB might cancel your subscriptions if the cash in your account falls below a certain threshold. If that happens, you need to transfer in more cash, then sign up for the subscriptions again. 

#### Can't get options data on weekend/after hours

Inside Gateway, you might see an error like:
```
2026-06-14 10:52:32.299 [WU] ERROR [JTS-Model-Notifier-200] - Model is not valid: Active:true SPY/20260731/742.0/Put TOP/PACED isApplcbl=false ref=756733 befCalc=false isValid=false greeks=NaN/NaN/NaN/NaN mdlVol=FROZEN: (TICK_MPIV 0.15385965165194782 PerYear 1) impVol=FROZEN: NAN/NAN/NAN bidGreeks=NULL askGreeks=NULL lastGreeks=NULL
2026-06
```

Make sure that the call to:
```python
self.reqMktData(req_id, option_contract, "100,101", False, False, [])
```

...is preceded by:
```python
self.reqMarketDataType(1 if live else 2)
```

The `2` requests "frozen" data.

#### Can't get earnings dates from IB

You need a paid subscription to Wall Street Horizons, the API version. It costs about $50 per month. However, there are workarounds, found elsewhere in this codebase.

### Schwab

![](./images/SchwabLogo.png)

#### How do I connect?

If you've done all the setup, try the program, `scripts/examples/schwab_driver_example.py`. The first time you use it, it should launch a web page for logging into your Schwab account. It might break after that, but run it again and it should just work. Then you'll have the authentication token on your system.

#### Why can't I get historical implied volatility data from Schwab?

Schwab's API simply doesn't offer this. You can get IV as it is at the current moment, but not historical snapshots.