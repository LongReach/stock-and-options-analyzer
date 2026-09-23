# app

_Important: Things in this folder need refactoring. Don't use this code for now._

## Purpose

Code here is meant to serve as a foundation for applications that actually initiate trades. This framework is built around the `AppPosition` class, which manages the entry, lifecycle, and exit of a position. It can be a single-leg position, such as being long or short on a stock, or a multi-leg position, such a credit spread. `AppPosition` is responsible for:
* Defining the position, i.e. the symbols and number of contracts.
* Keeping track of streaming price data.
* Keeping track of realized and unrealized profit/losses.
* Creating entry orders, as well as stop-out and profit-taking orders, then sending those orders to the broker.
* Monitoring the state of those orders, as they're fulfilled, canceled, etc.
* Periodically backing itself up to disk, in case the application crashes, as well as so the user can review the position's performance after it's fully closed.

At any given time, an `AppPosition` can be open or closed. If it's open, there are active orders.