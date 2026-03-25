import asyncio
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum, auto
from asyncio import Event

from core.common import SecurityDescriptor, OrderPurpose
from core.ib_driver import IBDriver
from core.utils import get_exception_traceback
from guided_missile.position_manager import PositionManager, PositionDirection


class Command(Enum):
    """Enum representing available GuidedMissile commands"""

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
    RESET = auto()
    POSITIONS = auto()
    CLEAR = auto()
    ADJUST = auto()

async def get_input(prompt="> "):
    """
    Needed because input() used the normal way blocks EVERYTHING in the process, including coroutines.
    This way, it runs in its own thread.
    """
    return await asyncio.to_thread(input, prompt)

class GuidedMissile:
    """Top-level implementation of GuidedMissile app"""

    STARTING_CASH = 500000.0

    def __init__(self, ib_driver: IBDriver):
        self._ib_driver = ib_driver
        self._position_manager = PositionManager(ib_driver, self.STARTING_CASH)

        # Maps commands as entered by user to enum
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
            "quit": Command.QUIT,
            "reset": Command.RESET,
            "positions": Command.POSITIONS,
            "clear": Command.CLEAR,
            "adjust": Command.ADJUST
        }

        self._reverse_command_map: Dict[Command, str] = {v: k for k, v in self.command_map.items()}

        self._stop_event = Event()

    async def run_loop(self):
        """The main loop, which continuously updates bookkeeping for held positions"""
        while not self._stop_event.is_set():
            await self._position_manager.update()
            await asyncio.sleep(0.01)

    async def input_loop(self):
        """The input loop, which implements a command console"""
        print("Welcome to Guided Missile")
        print("--------------------------------------------------------")
        print("enter 'help' for help\n")
        while True:
            try:
                input_str = await get_input("> ")
                if input_str == "":
                    continue
                parse_success, command_dict = self.parse_input(input_str)
                if not parse_success:
                    print(f"Error parsing command: {command_dict['error']}")
                    continue

                command = command_dict["command"]
                # Is this a position change command?
                if command in [
                    Command.ACTIVATE_LONG,
                    Command.ACTIVATE_SHORT,
                    Command.ACTIVATE_DUAL,
                    Command.ENTER_LONG,
                    Command.ENTER_SHORT,
                    Command.CANCEL,
                    Command.EXIT,
                    Command.RESET,
                    Command.ADJUST
                ]:
                    await self._run_position_command(command_dict)
                elif command == Command.INFO:
                    self.print_info(command_dict.get("symbol"))
                # The following are for commands not specific to a position
                elif command == Command.HELP:
                    self.print_help(command_dict.get("command_name"))
                elif command == Command.POSITIONS:
                    await self.print_positions()
                elif command == Command.CLEAR:
                    await self.clear_positions()
                elif command == Command.QUIT:
                    print("Quitting...")
                    self._stop_event.set()
                    break
            except Exception as e:
                print(f"GuidedMissile input loop got exception: {e}")
                print(f"Traceback is: {get_exception_traceback(e)}")
                self._stop_event.set()
                break

            await asyncio.sleep(0.5)

    def parse_input(self, input_str: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Parses user input. If successful, returns True and a dictionary containing command data.
        If unsuccessful, returns False and a dictionary containing error data.
        """
        parts = input_str.split(" ")
        if len(parts) < 1:
            return False, {"error": "No command given."}

        ret_dict = {}

        # Make sure the command is supported
        command = parts[0]
        command = command.lower()
        if command not in [
            "al",
            "as",
            "ad",
            "el",
            "es",
            "can",
            "exit",
            "info",
            "help",
            "quit",
            "reset",
            "positions",
            "clear",
            "adjust"
        ]:
            return False, {"error": f"Command {command} not supported."}
        ret_dict["command"] = self.command_map[command]

        symbol = None
        # Process commands specific to a position
        if command in ["al", "as", "ad", "el", "es", "can", "exit", "reset", "adjust"]:
            if len(parts) < 2:
                return False, {"error": "No symbol given."}
            else:
                symbol = parts[1]
                symbol = symbol.upper()
        elif command == "info":
            if len(parts) > 1:
                symbol = parts[1]
                symbol = symbol.upper()
        if symbol:
            ret_dict["symbol"] = symbol

        # Process help command
        command_name = None
        if command == "help":
            if len(parts) > 1:
                command_name = parts[1]
        if command_name:
            ret_dict["command_name"] = command_name

        # Process commands that require bar count
        bar_count = None
        if command in ["al", "as", "ad", "el", "es"]:
            if len(parts) < 3:
                return False, {"error": "No bar count given."}
            else:
                bar_count = int(parts[2])
        if bar_count:
            ret_dict["bar_count"] = bar_count

        if command == "adjust":
            adjust_dict = self._parse_adjust_command(parts)
            if adjust_dict.get("error") is not None:
                return False, adjust_dict
            ret_dict = ret_dict | adjust_dict

        return True, ret_dict

    def print_help(self, command: Optional[str] = None):
        """Prints help about a particular command or all commands"""
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
            print("adjust: adjust stops/profit targets")
            print("info: info about position or all positions")
            print("help: general or about named command")
            print("positions: show positions, as reported directly by broker")
            print("clear: clear closed positions, so that info about them no longer appears")
            print("quit: quit GuidedMissile")
        else:
            print(f"{command} command:")
            print("----------------------")

            if command in ["al", "as", "ad", "el", "es"]:
                print(f"{command} <symbol> <bars>")
            elif command in ["can", "exit", "reset"]:
                print(f"{command} <symbol>")
            elif command in ["info"]:
                print(f"{command} [symbol]")
            elif command in ["help"]:
                print(f"{command} <command>")
            elif command == "adjust":
                print(f"{command} <spt/tgt> [+/-]<price>")
            elif command == "quit":
                print(f"{command}")
            elif command == "positions":
                print(f"{command}")
            elif command == "clear":
                print(f"{command}")

    def print_info(self, symbol: Optional[str]):
        """Prints info about a particular position or all positions"""

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
            print("--------------------------------------------------")
            if info_lines is None:
                print(f"No info available for: {symbol}")
            else:
                _print_it(info_lines)
            print("--------------------------------------------------")
        account_value, cash_amount = self._position_manager.get_cash_status()
        print(f"Account value: {account_value}")
        print(f"Cash available: {cash_amount}")

    async def print_positions(self):
        lines = await self._position_manager.get_position_info()
        print("Positions (as reported by broker):")
        print("--------------------------------------------------")
        print("\n".join(lines))

    async def clear_positions(self):
        await self._position_manager.clear_positions()

    async def _run_position_command(self, command_dict: Dict[str, Any]):
        """Runs a command that modifies a position in some way"""
        direction = PositionDirection.LONG
        command = command_dict["command"]
        if command in [Command.ENTER_SHORT, Command.ACTIVATE_SHORT]:
            direction = PositionDirection.SHORT
        elif command == Command.ACTIVATE_DUAL:
            direction = PositionDirection.DUAL

        security_descriptor = SecurityDescriptor(command_dict["symbol"])
        if command not in [Command.CANCEL, Command.EXIT, Command.RESET, Command.ADJUST]:
            success, error_str = self._position_manager.add_position(security_descriptor)
            if not success:
                print(f"Command failed with error: {error_str}")
                return

        success, error_str = True, None
        if command in [
            Command.ACTIVATE_LONG,
            Command.ACTIVATE_SHORT,
            Command.ACTIVATE_DUAL,
        ]:
            success, error_str = await self._position_manager.activate(
                security_descriptor, direction, command_dict["bar_count"]
            )
        elif command in [Command.ENTER_SHORT, Command.ENTER_LONG]:
            success, error_str = await self._position_manager.enter(
                security_descriptor, direction, command_dict["bar_count"]
            )
        elif command == Command.CANCEL:
            success, error_str = await self._position_manager.cancel(security_descriptor)
        elif command == Command.EXIT:
            success, error_str = await self._position_manager.exit(security_descriptor)
        elif command == Command.RESET:
            success, error_str = await self._position_manager.reset(security_descriptor)
        elif command == command.ADJUST:
            price = command_dict["price"]
            relative = command_dict["relative"]
            order_purpose = command_dict["purpose"]
            success, error_str = await self._position_manager.adjust(security_descriptor, price, relative, order_purpose)

        if not success:
            print(f"Command failed with error: {error_str}")
            return
        print(f"Successfully ran command {command} for {security_descriptor.to_string()}")
        return

    def _parse_adjust_command(self, parts: List[str]) -> Dict[str, Any]:
        if len(parts) < 4:
            return {"error": f"Three parameters required, only got {len(parts) - 1}"}
        if parts[2] not in ["stp", "tgt", "ent"]:
            return {"error": f"Second parameter must be stp/tgt/ent, was {parts[2]}"}

        order_purpose_lookup: Dict[str, OrderPurpose] = {
            "stp": OrderPurpose.STOP_LOSS,
            "ent": OrderPurpose.ENTRY,
            "tgt": OrderPurpose.TAKE_PROFIT
        }
        order_purpose = order_purpose_lookup.get(parts[2])

        relative_adjust = False
        direction_multiplier = 1.0
        try:
            price_param = parts[3]
            if price_param[0] in ["+", "-"]:
                relative_adjust = True
                direction_multiplier = -1.0 if price_param[0] == "-" else 1.0
                price_param = price_param[1:]
            price = float(price_param)
        except Exception as e:
            return {"error": f"Error processing price parameter: {e}"}

        return {"price": price * direction_multiplier, "relative": relative_adjust, "purpose": order_purpose}

