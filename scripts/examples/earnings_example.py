import argparse

from core.earnings import get_upcoming_earnings_date, get_past_earnings_dates


def main(ticker: str, limit: int):
    ticker = ticker.upper()

    upcoming = get_upcoming_earnings_date(ticker)
    if upcoming:
        print(f"Next earnings date for {ticker}: {upcoming}")
    else:
        print(f"No upcoming earnings date found for {ticker}.")

    print()

    df = get_past_earnings_dates(ticker, limit=limit)
    if df.empty:
        print(f"No past earnings data found for {ticker}.")
        return

    print(f"Past {len(df)} earnings dates for {ticker}:")
    print(f"{'Date':<30} {'EPS Est':>10} {'Reported EPS':>14} {'Surprise %':>12}")
    print("-" * 70)
    for dt, row in df.iterrows():
        date_str = dt.strftime("%Y-%m-%d %H:%M %Z")
        eps_est = f"{row['EPS Estimate']:.2f}" if pd.notna(row["EPS Estimate"]) else "N/A"
        reported = f"{row['Reported EPS']:.2f}" if pd.notna(row["Reported EPS"]) else "N/A"
        surprise = f"{row['Surprise(%)']:.2f}%" if pd.notna(row["Surprise(%)"]) else "N/A"
        print(f"{date_str:<30} {eps_est:>10} {reported:>14} {surprise:>12}")


import pandas as pd

parser = argparse.ArgumentParser(description="Print earnings dates for a stock ticker")
parser.add_argument("ticker", help="Stock ticker symbol, e.g. AAPL")
parser.add_argument("--limit", help="Number of past earnings dates to retrieve", default=8, type=int)
args = parser.parse_args()

main(args.ticker, args.limit)
