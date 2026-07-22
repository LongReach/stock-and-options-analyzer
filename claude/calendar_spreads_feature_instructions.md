# Instructions for Claude

## Brief Description of Goals

I want a software tool that analyzes potential calendar spread positions and reports back with useful information.

## More Detailed Instructions

### Phase One (Complete)

Create a script in `scripts/` called `calendar_helper.py`. It will take the following command line arguments:
* --schwab: if connection is being made to Schwab brokerage with `SchwabDriver`
* --ib: if connection is being made to IB brokerage with `IBDriver`
* --symbol: stock or ETF ticker
* --right: either "P" for "put" or "C" for "call"
* --strike (optional): if given, strike to use for calendar spread. If not given, tool should choose strike closest to being at-the-money
* --dte-front: days to expiration for front (sold) option. If no options match this expiration, then pick the closest one.
* --dte-back (optional): days to expiration for back (bought) option. If no options match this expiration, then pick the closest one. If not given, then pick the first available expiration date after the front option's expiration date.

The tool should print an error if:
* if no connection to broker can be made
* if the right isn't "P" or "C"
* the user-specified strike doesn't exist
* the expiration date for the back-dated option does not come after the expiration date for the front-dated option.
* the tool is unable to find matching strikes for front and back dated options

Information I'd like pretty-printed:
* The dates and DTEs of the front and back options
* The ratio between implied volatility of the front option and the back option
* If broker is IB, where the ratio sits in the range of the last 20 days. In other words, compute the ratio between the IVs of the two contracts for each of the last 20 days, then give today's ratio as a percentile of that range.
* Aggregate delta for the calendar spread
* Aggregate theta for the calendar spread
* Aggregate gamma for the calendar spread
* Aggregate vega for the calendar spread
* Total cost, assuming one front contract and one back contract
* Maximum possible profit

### Phase Two (Complete)

Change the `--dte-front` and `--dte-back` arguments, so that they can either take in a DTE value or a IB-style date, e.g. 20260821. If a date is given, it's converted to a DTE value, then used that way through the code flow. As before `--dte-back` is optional.

Add a double-calendar feature. It will be activated by the `--double` argument. If the double-calendar feature is being used, the `--strike` argument will be ignored. Select the two strikes based on sensible best practice. They should be at somewhere around the expected move.

### Phase Three

You, Claude Code, said:
> But those are rules of thumb, not something the tool derives. If you'd like, I could add a mode that suggests front/back DTEs — e.g. pick the front near a target DTE and the back to maximize the theta ratio or hit a target IV-term-structure spread — rather than requiring you to specify them. Want me to sketch that out?

Let's add an optional argument called `--auto`. If `--auto` is given, the front's expiration will be somewhere near `--dte-front`. The back's expiration will be chosen by the tool. I'm not sure what to optimize for. For now, let's focus on maximizing the theta ration, but feel free to suggest other modes. 