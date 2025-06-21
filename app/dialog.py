from typing import List, Dict, Any, Union, Optional, Tuple

from app.common import TradeColumn, PositionColumn, column_enum_to_str
from core.utils import get_datetime

class Dialog:
    """Base class for a 'dialog box' (really just a series of questions) in text-based interface"""

    def __init__(self, dialog_name: Optional[str] = None):
        if dialog_name is not None:
            self._dialog_name = dialog_name
        else:
            self._dialog_name = None
        self._fields_and_defaults: Optional[Dict] = None
        self._fields_and_outputs: Dict[Union[TradeColumn, PositionColumn], Any] = {}
        self._other_fields: Dict[str, Any] = {}

    def set_fields_and_defaults(self, fields_and_defs: Dict[Union[TradeColumn, PositionColumn], Any]):
        """Sets which fields user must input, as well as their default values"""
        self._fields_and_defaults = fields_and_defs

    def collect_input(self):
        """Collects all necessary input from user, can be overloaded"""
        out_dict = {}
        print("Enter fields")
        for field, default in self._fields_and_defaults.items():
            empty_default = (default == "" or default == -1 or default == -1.0)
            default_hint = "" if empty_default else f" (default is {default})"
            got_input = False
            field_type = type(default)
            val = ""
            while not got_input:
                val = input(f"{column_enum_to_str(field)}{default_hint}: ")
                success, msg = self._validate_field(val, field, field_type)
                if success:
                    got_input = True
                else:
                    print(f"Invalid input: {msg}")
            if val == "" and not empty_default:
                val = default
            val = field_type(val)
            out_dict[field] = val
        self._fields_and_outputs = out_dict

    def get_main_fields(self) -> Dict[Union[TradeColumn, PositionColumn], Any]:
        """Returns dictionary of fields meant to go into the "spreadsheet" and their values"""
        return self._fields_and_outputs

    def get_other_fields(self) -> Dict[str, Any]:
        """
        Returns fields and values that WON'T go into "spreadsheet", but have been collected
        from user input into dialog.
        """
        return self._other_fields

    @staticmethod
    def _validate_field(val: str, field: Union[PositionColumn, TradeColumn], field_type: type) -> Tuple[bool, str]:
        """
        Confirms that user input for a particular field is valid
        :param val: value the user has inputted
        :param field: enumeration for field
        :param field_type: type of field (whether value will be string, int, etc.)
        :return:
        """
        if val == "":
            return True, ""

        try:
            val = field_type(val)
        except:
            return False, f"{val} not of type {field_type}"

        position_fields = isinstance(field, PositionColumn)

        if position_fields and field == PositionColumn.POSITION_NUMBER or not position_fields and field == TradeColumn.POSITION_NUMBER:
            if val < 0:
                return False, f"Bad position number {val}"
        elif position_fields and (field == PositionColumn.DATE_OPENED or field == PositionColumn.DATE_CLOSED) or not position_fields and (field == TradeColumn.DATE_OPENED or field == TradeColumn.DATE_CLOSED):
            try:
                as_dt = get_datetime(val)
            except:
                return False, f"Date not valid {val}"
        elif position_fields and field == PositionColumn.STRATEGY:
            if val not in ["IC", "CS", "DS"]:
                return False, f"{val} is not valid strategy"
        elif not position_fields and field == TradeColumn.EXPIRATION:
            try:
                as_dt = get_datetime(val)
            except:
                return False, f"Date not valid {val}"
        elif not position_fields and field == TradeColumn.RIGHT:
            if val not in ["C", "P"]:
                return False, f"Invalid right value {val}"
        elif not position_fields and field == TradeColumn.STRIKE:
            if val < 0.0 or val > 1000.0:
                return False, f"Bad strike {val}"
        return True, ""

class MainDialog(Dialog):

    def __init__(self, dialog_name: Optional[str] = None):
        super().__init__(dialog_name)

    def collect_input(self):
        choice_made = False
        while not choice_made:
            print("\nMake choice:")
            print("1) New position, 2) Modify position, 3) Show positions, 4) Show single position, 5) Exit")
            choice = input(": ")
            if choice == "1":
                self._other_fields["choice"] = "new position"
                choice_made = True
            elif choice == "2":
                print("Enter position number:")
                self._other_fields["choice"] = "modify position"
                self._other_fields["position number"] = int(input(": "))
                choice_made = True
            elif choice == "3":
                self._other_fields["choice"] = "show positions"
                choice_made = True
            elif choice == "4":
                print("Enter position number:")
                self._other_fields["choice"] = "show position"
                self._other_fields["position number"] = int(input(": "))
                choice_made = True
            elif choice == "5":
                self._other_fields["choice"] = "exit"
                choice_made = True
            else:
                print("Invalid choice.")

class PositionDialog(Dialog):

    def __init__(self, dialog_name: Optional[str] = None):
        super().__init__(dialog_name)

    def collect_input(self):
        print()
        print(self._dialog_name)
        super().collect_input()

class TradeDialog(Dialog):

    def __init__(self, dialog_name: Optional[str] = None):
        super().__init__(dialog_name)

    def collect_input(self):
        print()
        print(self._dialog_name)
        super().collect_input()
