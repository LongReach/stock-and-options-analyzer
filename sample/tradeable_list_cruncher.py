import asyncio
from argparse import ArgumentParser
from logging import basicConfig, INFO, getLogger
from typing import List, Tuple, Dict, Optional


def main(file_path: str):
    try:
        with open(file_path, "r") as file:
            content = file.read()

    except FileNotFoundError:
        print(f"Could not find file {file_path}")
        return

    out_symbols: List[str] = []

    symbols = content.split(",")
    for symbol in symbols:
        parts = symbol.split(":")
        try:
            out_symbols.append(parts[1])
        except IndexError:
            out_symbols.append(symbol)

    out_symbols.sort()
    for symbol in out_symbols:
        print(symbol)


parser = ArgumentParser(description="Tool for processing list of stocks from TradingView")
parser.add_argument("--file", help="file containing TradingView-style list of ticker symbols", required=True, type=str)
args = parser.parse_args()

main(args.file)
