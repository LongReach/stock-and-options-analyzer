import asyncio
from argparse import ArgumentParser
from logging import basicConfig, INFO, getLogger
from typing import List, Tuple, Dict, Optional


def process_comma_separated_file(file_path: str):
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


def process_line_separated_file(file_path: str):
    try:
        with open(file_path, "r") as file:
            lines = file.readlines()
    except FileNotFoundError:
        print(f"Could not find file {file_path}")
        return

    lines = [line.strip() for line in lines]
    print(f"{','.join(lines)}")


def main(file_path: str, mode: int):
    if mode == 1:
        process_comma_separated_file(file_path)
    elif mode == 2:
        process_line_separated_file(file_path)
    else:
        print(f"Unknown mode {mode}")


parser = ArgumentParser(description="Tool for processing list of stocks and outputting in another form")
parser.add_argument("--file", help="file containing TradingView-style list of ticker symbols", required=True, type=str)
parser.add_argument(
    "--mode",
    help="1) turn comma-separated list in file into line-separated list, 2) turn line-separated list in file into comma-separated list",
    required=True,
    default=1,
    type=int,
)
args = parser.parse_args()

main(args.file, args.mode)
