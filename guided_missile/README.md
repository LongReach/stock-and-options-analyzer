# guided_missile

## About

GuidedMissile is a command line daytrading application intended to be used in conjunction with Trader Workstation, the desktop trading application provided by Interactive Brokers. Daytrading, of course, is a distinct kind of trading from swing trading, position trading, and long-term investing. Daytraders open and close positions within the same day, trying to profit from volatile market conditions or volatile stocks. (In 2025 and 2026, the former were helpfully provided by certain drama-addicted global leadership figures, as well as the increasing uncertainty injected by rapid advances in machine-learning.)

![](../images/DuneLego.png)      
`Lego representation of the Battle of Arrakeen from "Dune". What daytrading can feel like if you don't know what you're doing. To me, the descending Sardaukar soldiers about to suprise the Atreides troops represent an incoming Trump tweet that's about to send the market into chaos.`

This software supports a strategy in which stocks/ETFs/options can be expected to (hopefully) make a strong move in the up or down direction after breaking out of a short-term range. GuidedMissile automatically sets up orders that will be triggered at the point of breakout. Along with those orders, it also sets up stop-loss and take-profit orders. Position sizes are determined automatically in order to keep risk defined. As positions are partially exited, the stop-loss is adjusted automatically. GuidedMissile can also be used for hasty cancellations, position exits, and adjustments. 

Daytrading, by its nature, moves very fast and this tool is meant to inject some "fire and forget" dependency into the process. Since the user is likely to be dealing with multiple positions or potential positions at once, it's good to be able to turn some of them over to an autopilot.

***DO NOT*** use this tool unless you have a clear idea of what you're doing, which few people viewing this README are likely to. Please activate TWS in paper-trading mode and use that for a while, as opposed to trading real money.

_July 2026: some work is needed on this software. Will come back to._

## Running

Run scripts here from root folder like:
```powershell
# Or whatever your environment is
conda activate options_2025_1
python -m scripts.missile_launcher
```