from typing import Dict


def test(**kwargs):
    print(f"kwargs are: {kwargs}")
    x, y = kwargs.values()
    print(f"Values are: {x}, {y}")


def tuple_test():
    def _test_it(tup):
        if tup:
            x, y = tup
            print(f"Tuple is: {x}, {y}")
        else:
            print("No tuple")

    tup = None
    _test_it(tup)
    tup = (1, 2)
    _test_it(tup)


test_dict = {"x": 1, "y": 2}
test(**test_dict)
tuple_test()
