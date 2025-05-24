# Suite of Tools for Stock/Options Market Analysis

## Description

## Design Thoughts

At an earlier time, I'd created some software that pulled stock market data from Yahoo Finance, then charted it in different ways. Unfortunately, in 2025, the `yfinance` Python library became increasingly unreliable, due to Yahoo's servers throttling requests.

It seemed smarter to get data from a paid source, rather than a free one. Could I use an API provided by a brokerage I already had an account with? The answer was yes and the obvious choice was Interactive Brokers. For one thing, they offer a paper-trading account, so you can test out strategies "live", but in a simulated environment. 

For another, back in 2020, I had already written some daytrading software that communicated with Interactive Brokers' popular trading platform, Trader Workstation. The software both gathered market data and opened/closed actual positions. Here in 2025, I decided to do something similar again, except via the lightweight "Gateway" bridge. Interactive Brokers doesn't have the most user-friendly Python API, but it's very powerful and provides access to pretty much any market data I could possibly want. Since I have a funded account with IB, I don't have to worry about them blocking access or breaking a third party library, as was a constant concern with Yahoo.

### Repackaging as pandas dataframes

It makes good sense to keep historical market data, once obtained, in `pandas` dataframes. These can be easily cached on disk (past market data is unchanging), as well as fed to machine-learning models.

## Setup

I created a `conda` environment for this project. First step was to install the Interactive Brokers API, as detailed in their online guide. Once I ran `python setup.py install`, the Python packages were installed in my environment. I suppose you can use `venv`, if you prefer that.

The online docs don't mention it, but I had to run `conda install setuptools` prior to running `setup.py`.

The next step was to install the Gateway software and configure it. Note that the ports for live trading and paper trading are 4001 and 4002, respectively. 

In PyCharm, I set the interpreter type to "custom environment", then I chose my `conda` environment. 

## Troubleshooting