from typing import Dict, List, Optional, Any, Tuple

from core.ib_driver import IBDriver
from guided_missile.position_manager import PositionManager

class GuidedMissile:

    def __init__(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        self._position_manager = PositionManager(ib_driver)

    async def input_loop(self):
        while True:
            input_str = input("> ")

    def parse_input(self, input_str: str) -> Tuple[bool, Optional[str]]:
        parts = input_str.split(" ")
        if len(parts) < 1:
            return False, "No command given."

        command = parts[0]
        command = command.lower()
        if command not in ["al", "as", "ad", "el", "es", "can", "exit", "info", "help"]:
            return False, f"Command {command} not supported."

        if command in ["al", "as", "ad", "el", "es", "can", "exit", "info"]:
            if len(parts) < 2:
                return False, "No symbol given."

        return True, None

    def print_help(self, command: Optional[str] = None):
        if command is None:
            print("Commands:")
            print("----------------------")
            print("al: activate long")
            print("as: activate short")
            print("ad: activate dual")
            print("el: enter long")
            print("es: enter short")
            print("can: cancel position")
            print("exit: exit position")
            print("info: info about position or all positions")
            print("help: general or about named command")
        else:
            print(f"{command} command:")
            print("----------------------")

            if command in ["al", "as", "ad", "el", "es"]:
                print(f"{command} <symbol> <bars>")
