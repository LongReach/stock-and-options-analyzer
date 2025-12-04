# I'm lazy

TRADES_COLUMNS = [
    "position number",
    "date opened",
    "right",
    "expiration",
    "strike",
    "num contracts",
    "opening price",
    "date closed",
    "last price",
    "IV",
    "delta",
    "theta",
    "gamma",
    "vega",
]
POSITIONS_COLUMNS = [
    "position number",
    "strategy",
    "ticker",
    "date opened",
    "rolled from",
    "date closed",
]

the_lists = [TRADES_COLUMNS, POSITIONS_COLUMNS]

for a_list in the_lists:
    for i, item in enumerate(a_list):
        enum_str = str(item).upper().replace(" ", "_")
        print(f"{enum_str} = {i}")
    print()
