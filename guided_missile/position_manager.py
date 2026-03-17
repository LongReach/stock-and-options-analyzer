import asyncio
from typing import Optional, Dict, List, Tuple
from enum import Enum, auto
from logging import getLogger

from core.ib_driver import IBDriver
from core.common import (
    SecurityDescriptor,
    BarSize,
    HistoricalData,
    OrderInfo,
    OrderStatus,
    OrderType,
    OrderAction,
)
from core.utils import wait_for_condition
from guided_missile.position import (
    Position,
    PositionState,
    PositionDirection,
    OrderGroup,
)


class PositionManager:
    """For managing positions in GuidedMissile application"""

    BAR_SIZE = BarSize.TWO_MINUTES
    MAX_LOSS = 100.0
    MAX_DATA_STREAMS = 30

    def __init__(self, ib_driver: IBDriver, cash_available: float):
        self.ib_driver = ib_driver
        Position.ib_driver = ib_driver

        self._account_value: float = cash_available
        self._cash_available: float = cash_available

        self._position_map: Dict[str, Position] = {}
        self._logger = getLogger(__file__)

        self._need_update_account_values: bool = False

    def add_position(self, security_descriptor: SecurityDescriptor) -> Tuple[bool, Optional[str]]:
        """
        Adds position to tracking.

        :param security_descriptor: describes stock, ETF, or options contract
        :return: (True if success, error string or None)
        """

        existing_position = self._position_map.get(security_descriptor.to_string())
        if existing_position:
            if existing_position.position_state not in [
                PositionState.NONE,
                PositionState.CLOSED,
                PositionState.CANCELED,
            ]:
                return (
                    False,
                    f"Can't add position for {security_descriptor.to_string()}",
                )
            existing_position.stop_all_states()

        self._logger.info(f"PositionManager: adding position for {security_descriptor.to_string()}")
        new_position = Position(security_descriptor)
        new_position.set_pm_callback(self.position_changed_cb)
        self._position_map[security_descriptor.to_string()] = new_position

        new_position.launch()

        return True, None

    async def activate(
        self,
        security_descriptor: SecurityDescriptor,
        direction: PositionDirection,
        bars_back: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Activates position for entry when price triggers order. Entry point will be chosen based on recent bar data.
        Same with stop loss.

        :param security_descriptor: describes stock, ETF, or options contract
        :param direction: whether long, short, or dual
        :param bars_back: how many bars back to look to determine entry point
        :return: (True if success, error string or None)
        """
        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return (
                False,
                f"Can't activate position for {security_descriptor.to_string()}",
            )

        self._logger.info(f"PositionManager: activating position for {security_descriptor.to_string()}")
        historical_data, error_str = await self._get_historical_data_stream(
            security_descriptor, bars_back=bars_back, bar_size=self.BAR_SIZE
        )
        if historical_data is None:
            return False, f"activate() failed with error: {error_str}"
        existing_position.set_historical_data_stream(historical_data)

        bar_highs = [bar.high for bar in historical_data.bar_data]
        highest_recent_price = max(bar_highs)
        top_price_buffer = self._get_entry_exit_buffer(highest_recent_price)
        bar_lows = [bar.low for bar in historical_data.bar_data]
        lowest_recent_price = min(bar_lows)
        bottom_price_buffer = self._get_entry_exit_buffer(lowest_recent_price)

        if direction == PositionDirection.LONG:
            entries = [highest_recent_price + top_price_buffer]
            stops = [lowest_recent_price - bottom_price_buffer]
        elif direction == PositionDirection.SHORT:
            entries = [lowest_recent_price - bottom_price_buffer]
            stops = [highest_recent_price + top_price_buffer]
        else:
            entries = [highest_recent_price + top_price_buffer, lowest_recent_price - bottom_price_buffer]
            stops = [lowest_recent_price - bottom_price_buffer, highest_recent_price + top_price_buffer]

        self._logger.info(f"PositionManager: activate() uses entries of {entries}, stops of {stops}")
        try:
            existing_position.activate(direction, entries, stops, self.MAX_LOSS, self._cash_available)
        except Exception as e:
            return False, f"activate() failed with exception: {e}"

        return True, None

    async def enter(
        self,
        security_descriptor: SecurityDescriptor,
        direction: PositionDirection,
        bars_back: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Enters position immediately. Entry point will be chosen based on recent bar data.
        Same with stop loss.

        :param security_descriptor: describes stock, ETF, or options contract
        :param direction: whether long, short, or dual
        :param bars_back: how many bars back to look to determine entry point
        :return: (True if success, error string or None)
        """

        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return False, f"Can't enter position for {security_descriptor.to_string()}"

        self._logger.info(f"PositionManager: entering position for {security_descriptor.to_string()}")
        historical_data, error_str = await self._get_historical_data_stream(
            security_descriptor, bars_back=bars_back, bar_size=self.BAR_SIZE
        )
        if historical_data is None:
            return False, f"enter() failed with error: {error_str}"
        existing_position.set_historical_data_stream(historical_data)

        bar_highs = [bar.high for bar in historical_data.bar_data]
        highest_recent_price = max(bar_highs)
        top_price_buffer = self._get_entry_exit_buffer(highest_recent_price)
        bar_lows = [bar.low for bar in historical_data.bar_data]
        lowest_recent_price = min(bar_lows)
        bottom_price_buffer = self._get_entry_exit_buffer(lowest_recent_price)

        if direction == PositionDirection.LONG:
            entry = highest_recent_price + top_price_buffer
            stop = lowest_recent_price - bottom_price_buffer
        elif direction == PositionDirection.SHORT:
            entry = lowest_recent_price - bottom_price_buffer
            stop = highest_recent_price + top_price_buffer
        else:
            return False, "Dual mode not supported"

        try:
            existing_position.enter(direction, entry, stop, self.MAX_LOSS, self._cash_available)
        except Exception as e:
            return False, f"enter() failed with exception: {e}"

        return True, None

    async def cancel(self, security_descriptor: SecurityDescriptor) -> Tuple[bool, Optional[str]]:
        """
        Cancels position that hasn't yet been entered
        :param security_descriptor: describes stock, ETF, or options contract
        """

        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return False, f"Can't cancel position for {security_descriptor.to_string()}"

        self._logger.info(f"PositionManager: canceling position for {security_descriptor.to_string()}")
        try:
            existing_position.cancel()
        except Exception as e:
            return False, f"cancel() failed with exception: {e}"

        return True, None

    async def exit(self, security_descriptor: SecurityDescriptor) -> Tuple[bool, Optional[str]]:
        """
        Exits position that has been entered
        :param security_descriptor: describes stock, ETF, or options contract
        """

        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return False, f"Can't exit position for {security_descriptor.to_string()}"

        self._logger.info(f"PositionManager: exiting position for {security_descriptor.to_string()}")
        try:
            existing_position.exit()
        except Exception as e:
            return False, f"exit() failed with exception: {e}"

        return True, None

    async def reset(self, security_descriptor: SecurityDescriptor):
        """Rebuilds a Position object for a position that we're actually in, on the brokerage side."""
        existing_position = self._position_map.get(security_descriptor.to_string())
        if existing_position and existing_position.position_state not in [
            PositionState.ENTERED,
            PositionState.CLOSED,
            PositionState.CANCELED,
            PositionState.NONE,
        ]:
            return (
                False,
                "Can't rebuild position, have not entered it. Try cancelling or exiting it first.",
            )

        # Get all positions from brokerage side
        position_info, error_str = await self.ib_driver.get_positions()
        if error_str is not None:
            return False, f"Failed to get positions, error is: {error_str}"

        price = 0.0
        quantity = 0
        is_short = False
        positions = position_info.get_positions()
        for position in positions:
            if position.security_descriptor.to_string() == security_descriptor.to_string():
                price = position.price
                quantity = position.quantity
                is_short = position.short_position
        if quantity == 0:
            return (
                False,
                f"Could not reset position for {security_descriptor.to_string()}, no shares held",
            )

        # Try to kill existing position
        if existing_position:
            existing_position.cancel(force_cancel=True)
            success = await wait_for_condition(
                lambda: existing_position.position_state == PositionState.CANCELED,
                timeout=30.0,
            )
            if not success:
                return (
                    False,
                    f"Could not cancel position for position {existing_position.position_id}",
                )

        self._logger.info(
            f"Attempting to reset position for {security_descriptor.to_string()}, actual quantity {quantity}"
        )
        new_position = Position(security_descriptor)
        new_position.set_pm_callback(self.position_changed_cb)
        direction = PositionDirection.SHORT if is_short else PositionDirection.LONG
        new_position.position_direction = direction
        new_position.position_state = PositionState.ENTERED
        entry_order = OrderInfo()
        entry_order.order_status = OrderStatus.FILLED
        entry_order.order_type = OrderType.MARKET
        entry_order.security_descriptor = security_descriptor
        entry_order.avg_fill_price = price
        entry_order.shares_filled = quantity
        entry_order.shares_remaining = 0

        # TODO: what if transmit is False? Can we transmit from trading tool?
        if direction == PositionDirection.LONG:
            stop_price = price - price * 0.005
            stop_order, error_str = await self.ib_driver.place_order(
                security_descriptor.to_string(),
                action=OrderAction.SELL,
                order_type=OrderType.STOP,
                quantity=quantity,
                price=stop_price,
            )
        else:
            stop_price = price + price * 0.005
            stop_order, error_str = await self.ib_driver.place_order(
                security_descriptor.to_string(),
                action=OrderAction.BUY,
                order_type=OrderType.STOP,
                quantity=quantity,
                price=stop_price,
            )
        if error_str is not None:
            return False, f"Failed to create stop order, error is: {error_str}"

        group = OrderGroup(entry_order, stop_order)
        group.set_initial_quantities(price, stop_price, quantity)
        if direction == PositionDirection.LONG:
            new_position.long_order_group = group
        else:
            new_position.short_order_group = group

        self._position_map[security_descriptor.to_string()] = new_position
        new_position.launch(after_reset=True)

        return True, None

    async def update(self):
        # TODO: docs
        if self._need_update_account_values:
            await self._update_cash_amount()
            self._need_update_account_values = False

        for pos_name, position in self._position_map.items():
            task_done, exception = position.is_state_machine_done()
            if task_done and exception is not None:
                self._logger.error(f"Exception for position {position.position_id}: {exception}")
                position.stop_all_states()

    def get_info(self, security_descriptor: SecurityDescriptor) -> Optional[List[str]]:
        """
        Gets printable information for a particular position, as list of strings.
        :param security_descriptor: --
        :return: list of strings or None
        """
        position = self._position_map.get(security_descriptor.to_string())
        if position is None:
            return None
        return position.get_info()

    def get_all_info(self) -> Dict[str, List[str]]:
        """
        Gets printable information for all positions held.
        :return: dict mapping symbol name to list of strings
        """
        out_dict = {}
        for pos_name, position in self._position_map.items():
            out_dict[pos_name] = position.get_info()
        return out_dict

    async def get_position_info(self) -> List[str]:
        positions_info, error_str = await self.ib_driver.get_positions()
        if error_str is not None:
            return []
        out_lines = []
        for position in positions_info.get_positions():
            line = f"Symbol={position.security_descriptor.to_string()}, shares={position.quantity}, price={position.price}, short={position.short_position}"
            out_lines.append(line)
        return out_lines

    def get_cash_status(self) -> Tuple[float, float]:
        return self._account_value, self._cash_available

    def position_changed_cb(self, position_id: int, shares: int, price: float):
        """Called whenever a position changes"""
        self._need_update_account_values = True

    async def _get_historical_data_stream(
        self, security_descriptor: SecurityDescriptor, bars_back: int, bar_size: BarSize
    ) -> Tuple[Optional[HistoricalData], Optional[str]]:
        """
        Gets historical data stream. It might be cached already, or we might need to fetch it fresh.

        :param security_descriptor: --
        :param bars_back: how many bars back to go
        :param bar_size: --
        :return: (HistoricalData object or None, error str or None)
        """
        try:
            historical_data, error_str = await self.ib_driver.get_historical_data(
                security_descriptor.to_string(),
                num_bars=bars_back,
                bar_size=self.BAR_SIZE,
                live_data=True,
            )
            if error_str is not None:
                return None, f"Error getting historical data: {error_str}"
        except Exception as e:
            return None, f"Exception getting historical data: {e}"

        return historical_data, None

    async def _update_cash_amount(self):
        """
        Updates bookkeeping about cash in account
        """

        cash_deduction: float = 0.0

        # First, count the theoretical cost of positions not yet entered
        for security_desc, position in self._position_map.items():
            cash_deduction += position.theoretical_cost

        # Now, we ask IB directly about positions we're in
        positions_info, error_str = await self.ib_driver.get_positions()
        if error_str:
            self._logger.error(f"Could not update cash amount. Error is: {error_str}")
        else:
            positions = positions_info.get_positions()
            for position in positions:
                cash_deduction += position.price * float(position.quantity)

        self._cash_available = self._account_value - cash_deduction

    @staticmethod
    def _get_entry_exit_buffer(price: float) -> float:
        buffer_value_map: Dict[float, float] = {
            20.0: 0.01,
            50.0: 0.02,
            100.0: 0.03,
            500.0: 0.05,
            1000.0: 0.10,
            1000000.0: 0.50,
        }
        for price_entry, buffer_val in buffer_value_map.items():
            if price < price_entry:
                return buffer_val
        return 0.0
