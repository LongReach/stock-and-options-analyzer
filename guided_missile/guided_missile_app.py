import asyncio
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum, auto
from asyncio import Event

from core.common import SecurityDescriptor
from core.ib_driver import IBDriver
from guided_missile.position_manager import PositionManager, PositionDirection

class Command(Enum):

    ACTIVATE_LONG = auto()
    ACTIVATE_SHORT = auto()
    ACTIVATE_DUAL = auto()
    ENTER_LONG = auto()
    ENTER_SHORT = auto()
    CANCEL = auto()
    EXIT = auto()
    INFO = auto()
    HELP = auto()
    QUIT = auto()

class GuidedMissile:

    STARTING_CASH = 100000.0

    def __init__(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        self._position_manager = PositionManager(ib_driver, self.STARTING_CASH)

        self.command_map: Dict[str, Command] = {
            "al": Command.ACTIVATE_LONG,
            "as": Command.ACTIVATE_SHORT,
            "ad": Command.ACTIVATE_DUAL,
            "el": Command.ENTER_LONG,
            "es": Command.ENTER_SHORT,
            "can": Command.CANCEL,
            "exit": Command.EXIT,
            "info": Command.INFO,
            "help": Command.HELP,
            "quit": Command.QUIT
        }

        self._stop_event = Event()

    async def run_loop(self):
        while not self._stop_event.is_set():
            await self._position_manager.update()
            await asyncio.sleep(0.01)

    async def input_loop(self):
        print("Welcome to Guided Missile")
        print("--------------------------------------------------------")
        print("enter 'help' for help\n")
        while True:
            try:
                input_str = input("> ")
                parse_success, command_dict = self.parse_input(input_str)
                if not parse_success:
                    print(f"Error parsing command: {command_dict['error']}")
                    continue

                command = command_dict["command"]
                if command in [Command.ACTIVATE_LONG, Command.ACTIVATE_SHORT, Command.ACTIVATE_DUAL, Command.ENTER_LONG, Command.ENTER_SHORT, Command.CANCEL, Command.EXIT]:
                    await self._run_position_command(command_dict)
                elif command == Command.INFO:
                    self.print_info(command_dict.get("symbol"))
                elif command == Command.HELP:
                    self.print_help(command_dict.get("command_name"))
                elif command == Command.QUIT:
                    print("Quitting...")
                    self._stop_event.set()
                    break
            except Exception as e:
                print(f"GuidedMissile input loop got exception: {e}")
                self._stop_event.set()
                break

            await asyncio.sleep(0.5)

    def parse_input(self, input_str: str) -> Tuple[bool, Dict[str, Any]]:
        parts = input_str.split(" ")
        if len(parts) < 1:
            return False, {"error": "No command given."}

        ret_dict = {}

        command = parts[0]
        command = command.lower()
        if command not in ["al", "as", "ad", "el", "es", "can", "exit", "info", "help", "quit"]:
            return False, {"error": f"Command {command} not supported."}
        ret_dict["command"] = self.command_map[command]

        symbol = None
        if command in ["al", "as", "ad", "el", "es", "can", "exit"]:
            if len(parts) < 2:
                return False, {"error": "No symbol given."}
            else:
                symbol = parts[1]
        elif command == "info":
            if len(parts) > 1:
                symbol = parts[1]
        if symbol:
            ret_dict["symbol"] = symbol

        command_name = None
        if command == "help":
            if len(parts) > 1:
                command_name = parts[1]
        if command_name:
            ret_dict["command_name"] = command_name

        bar_count = None
        if command in ["al", "as", "ad", "el", "es"]:
            if len(parts) < 3:
                return False, {"error": "No bar count given."}
            else:
                bar_count = int(parts[2])
        if bar_count:
            ret_dict["bar_count"] = bar_count

        return True, ret_dict

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
            elif command in ["can", "exit"]:
                print(f"{command} <symbol>")
            elif command in ["info"]:
                print(f"{command} [symbol]")
            elif command in ["help"]:
                print(f"{command} <command>")

    def print_info(self, symbol: Optional[str]):
        def _print_it(lines: List[str]):
            print("\n".join(lines))

        if symbol is None:
            info_dict = self._position_manager.get_all_info()
            for symbol_name, lines in info_dict.items():
                print("--------------------------------------------------")
                _print_it(lines)
            print("--------------------------------------------------")
        else:
            info_lines = self._position_manager.get_info(SecurityDescriptor(symbol))
            if info_lines is None:
                print(f"No info available for: {symbol}")
            else:
                _print_it(info_lines)

    async def _run_position_command(self, command_dict: Dict[str, Any]):
        direction = PositionDirection.LONG
        if command_dict["command"] in [Command.ENTER_SHORT, Command.ACTIVATE_SHORT]:
            direction = PositionDirection.SHORT
        elif command_dict["command"] == Command.ACTIVATE_DUAL:
            direction = PositionDirection.DUAL

        security_descriptor = SecurityDescriptor(command_dict["symbol"])
        success, error_str = self._position_manager.add_position(security_descriptor)
        if not success:
            print(f"Command failed with error: {error_str}")
            return
        if command_dict["command"] in [Command.ACTIVATE_LONG, Command.ACTIVATE_SHORT, Command.ACTIVATE_DUAL]:
            success, error_str = await self._position_manager.activate(security_descriptor, direction, command_dict["bar_count"])
        elif command_dict["command"] in [Command.ENTER_SHORT, Command.ENTER_LONG]:
            success, error_str = await self._position_manager.enter(security_descriptor, direction, command_dict["bar_count"])
        elif command_dict["command"] == Command.CANCEL:
            success, error_str = await self._position_manager.cancel(security_descriptor)
        elif command_dict["command"] == Command.EXIT:
            success, error_str = await self._position_manager.exit(security_descriptor)

        if not success:
            print(f"Command failed with error: {error_str}")
            return
        print(f"Successfully ran command {command_dict["command"]} for {security_descriptor.to_string()}")
        return
