import asyncio
from typing import Optional, Dict, List, Tuple
from enum import Enum, auto
from logging import getLogger

from core.common import (
    OrderInfo,
    SecurityDescriptor,
    OrderAction,
    OrderType,
    OrderStatus,
)
from core.utils import wait_for_condition
from core.ib_driver import IBDriver


class OrderGroup:

    def __init__(
        self,
        entry_order: OrderInfo,
        stop_loss_order: OrderInfo,
        half_out_order: Optional[OrderInfo] = None,
    ):
        self.entry_order: OrderInfo = entry_order
        self.stop_loss_order: OrderInfo = stop_loss_order
        # TODO: two take-profit orders
        self.half_out_order: Optional[OrderInfo] = half_out_order
        self.initial_entry_price: float = 0.0
        self.initial_exit_price: float = 0.0
        self.initial_num_shares: int = 0

    def set_initial_quantities(
        self, entry_price: float, exit_price: float, shares: int
    ):
        self.initial_entry_price = entry_price
        self.initial_exit_price = exit_price
        self.initial_num_shares = shares


class PositionState(Enum):
    NONE = auto()
    CREATED = auto()
    ENTERED = auto()
    HALF_OUT = auto()
    CLOSED = auto()
    CANCELED = auto()


class PositionDirection(Enum):
    LONG = auto()
    SHORT = auto()
    DUAL = auto()


class PositionException(Exception):
    pass


class InsufficientCashException(Exception):

    def __init__(self, cash_needed, cash_left, message="Insufficient Cash"):
        self.cash_needed = cash_needed
        self.cash_left = cash_left
        self.message = message
        super().__init__(self.message)


class Position:
    """
    Represents a position or prospective position in Guided Missile.

    A prospective position has orders that will trigger an entry into an active position, while an active position
    has orders that will lead to an exit. Typically, an active position has both a stop-loss order and a take-profit
    order.

    Design:

    Each "command" sent to a Position object triggers an asynchronous task that communicates with the broker.
    Results are then checked in the update() function, which is synchronous. We don't want anything slowing
    down the main update loop.

    Note: The position state member variable might reflect a state that isn't fully entered yet, e.g. the
    position will be marked as "cancelled" even though the async work of cancellation is underway.
    """

    ib_driver: IBDriver = None
    logger = getLogger(__file__)
    next_id = 1

    def __init__(self, security_descriptor: SecurityDescriptor):
        self.security_descriptor: SecurityDescriptor = security_descriptor
        self.position_state: PositionState = PositionState.NONE
        self.position_direction: PositionDirection = PositionDirection.LONG
        self.long_order_group: Optional[OrderGroup] = None
        self.short_order_group: Optional[OrderGroup] = None
        self.position_id = Position.next_id
        Position.next_id += 1
        # Will be non-zero for positions "armed" but not yet entered
        self.theoretical_cost: float = 0.0

        self._task_stack: List[asyncio.Task] = []

    def get_current_shares(self) -> int:
        """Return number of shares that we're currently long or short on"""
        if self.position_direction == PositionDirection.DUAL:
            return 0
        group = (
            self.long_order_group
            if self.position_direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            return 0
        shares_in = group.entry_order.shares_filled
        shares_out = 0
        if group.half_out_order:
            shares_out += group.half_out_order.shares_filled
        if group.stop_loss_order:
            shares_out += group.stop_loss_order.shares_filled
        return shares_in - shares_out

    def get_profit(self) -> float:
        """Return profits realized"""
        # TODO: finish
        if self.position_direction == PositionDirection.DUAL:
            return 0
        group = (
            self.long_order_group
            if self.position_direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            return 0
        shares_in = group.entry_order.shares_filled
        price_in = group.entry_order.avg_fill_price
        shares_out_1 = 0
        price_out_1 = 0.0
        shares_out_2 = 0
        price_out_2 = 0.0
        if group.half_out_order:
            shares_out_1 = group.half_out_order.shares_filled
            price_out_1 = group.half_out_order.avg_fill_price
        if group.stop_loss_order:
            shares_out_2 = group.stop_loss_order.shares_filled
            price_out_2 = group.stop_loss_order.avg_fill_price
        if self.position_direction == PositionDirection.LONG:
            return (
                shares_out_1 * price_out_1 + shares_out_2 * price_out_2
            ) - shares_in * price_in
        else:
            return shares_in * price_in - (
                shares_out_1 * price_out_1 + shares_out_2 * price_out_2
            )

    def tasks_complete(self) -> bool:
        """Returns True if all asynchronous tasks for Position are done."""
        if len(self._task_stack) > 0:
            for task in self._task_stack:
                if not task.done() and not task.cancelled():
                    return False
        return True

    async def wait_for_tasks_complete(self) -> bool:
        return await wait_for_condition(lambda: self.tasks_complete(), timeout=30.0)

    def activate(
        self,
        direction: PositionDirection,
        entry_prices: List[float],
        stop_prices: List[float],
        max_loss: float,
        cash_left: float,
    ):
        """
        Sets up stop orders for a position entry. If going long, the position will be entered when the stop is triggered.
        Same idea with going short. If a "dual" entry, then both long and short orders will be set up. However, when one
        is triggered, the other will be removed.

        :param direction: long, short, or dual
        :param entry_prices: list of entry prices. List will be of length 2 if dual position.
        :param stop_prices: list of stop prices. List will be of length 2 if dual position.
        :param max_loss: max allowed loss for this position
        :param cash_left: cash remaining in account
        :raises PositionException:
        :raises InsufficientCashException:
        """
        if self.position_state != PositionState.NONE:
            raise PositionException(
                f"Can't activate position, current state is {PositionState(self.position_state).name}"
            )

        self.logger.info(
            f"Activating position {self.position_id} in direction {PositionDirection(direction).name} for {self.security_descriptor.to_string()}"
        )

        activate_task = asyncio.create_task(
            self._do_activate(direction, entry_prices, stop_prices, max_loss, cash_left)
        )
        self._task_stack.append(activate_task)

    def enter(
        self,
        direction: PositionDirection,
        entry_price: float,
        stop_price: float,
        max_loss: float,
        cash_left: float,
    ):
        """
        Enters a position right now

        :param direction: long, short, or dual
        :param entry_price: --
        :param stop_price: --
        :param max_loss: max allowed loss for this position
        :param cash_left: cash left in account
        :raises PositionException:
        :raises InsufficientCashException:
        """

        if self.position_state != PositionState.NONE:
            raise PositionException(
                f"Can't enter position, current state is {PositionState(self.position_state).name}"
            )
        if direction == PositionDirection.DUAL:
            raise PositionException("Can't directly enter dual position")

        enter_task = asyncio.create_task(
            self._do_enter(direction, entry_price, stop_price, max_loss, cash_left)
        )
        self._task_stack.append(enter_task)

    def cancel(self):
        """Cancel any orders that haven't been filled yet"""
        if self.position_state != PositionState.CREATED:
            raise PositionException(
                f"Can't cancel position, current state is {PositionState(self.position_state).name}"
            )

        cancel_task = asyncio.create_task(self._cancel_orders(self.position_direction))
        self._task_stack.append(cancel_task)

    def exit(self):
        """Exit the position we're in"""
        if self.position_state not in [PositionState.ENTERED, PositionState.HALF_OUT]:
            raise PositionException(
                f"Can't exit position, current state is {PositionState(self.position_state).name}"
            )
        if self.position_direction == PositionDirection.DUAL:
            raise PositionException("Can't directly exit dual position")

        exit_task = asyncio.create_task(self._do_exit())
        self._task_stack.append(exit_task)

    def update(self):
        """
        Update function to keep this object in sync with state of position in broker.
        """

        # If there's a task (e.g. for cancelling orders) in progress, allow it to complete
        if len(self._task_stack) > 0:
            for task in self._task_stack:
                if not task.done() and not task.cancelled():
                    return
                if task.done():
                    ex = task.exception()
                    self.logger.error(f"Exception updating position: {ex}")
            self._task_stack = []

        if self.position_state == PositionState.NONE:
            pass
        elif self.position_state == PositionState.CREATED:
            self._update_created_position()
        elif self.position_state == PositionState.ENTERED:
            self._update_entered_position()
        elif self.position_state == PositionState.HALF_OUT:
            self._update_half_out_position()
        else:
            pass

    def get_info(self) -> List[str]:
        if self.position_state in [PositionState.ENTERED, PositionState.HALF_OUT]:
            num_shares = self.get_current_shares()
            shares_line = f"Shares: {num_shares}"
        elif self.position_state == PositionState.CREATED:
            group = (
                self.long_order_group
                if self.position_direction == PositionDirection.LONG
                else self.short_order_group
            )
            if group:
                num_shares = group.entry_order.shares_remaining
                shares_line = f"Shares: {num_shares} (prospective)"
            else:
                num_shares = 0
                shares_line = "Shares: ???"
        else:
            num_shares = 0
            shares_line = "Shares: 0"

        lines = [
            f"Symbol: {self.security_descriptor.to_string()}"
            f"Position ID: {self.position_id}",
            f"State: {PositionState(self.position_state).name}",
            f"Direction: {PositionDirection(self.position_direction).name}",
            shares_line,
        ]
        return lines

    def _update_created_position(self):
        """
        The position has been created. From here, it can either be entered or canceled.
        """
        groups: List[Optional[OrderGroup]] = [
            self.long_order_group,
            self.short_order_group,
        ]
        cancel_group_idx = -1
        for idx, group in enumerate(groups):
            if group is not None:
                entry_order = group.entry_order
                if entry_order.order_status == OrderStatus.FILLED:
                    # ======================================
                    # Position has been entered, update bookkeeping and create half-out order
                    # ======================================

                    self.position_state = PositionState.ENTERED
                    if self.position_direction == PositionDirection.DUAL:
                        # We must cancel the other entry and update direction
                        cancel_group_idx = 1 - idx
                        self.position_direction = (
                            PositionDirection.LONG
                            if idx == 0
                            else PositionDirection.SHORT
                        )

                    self.logger.info(
                        f"Have entered position {self.position_id} for {self.security_descriptor.to_string()}"
                    )

                    # Create half-out order
                    if idx == 0:
                        half_out_price = (
                            group.initial_entry_price
                            + (group.initial_entry_price - group.initial_exit_price)
                            * 2.0
                        )
                        half_out_quantity = int(group.initial_num_shares / 2)
                    else:
                        half_out_price = (
                            group.initial_entry_price
                            - (group.initial_exit_price - group.initial_entry_price)
                            * 2.0
                        )
                        half_out_quantity = int(group.initial_num_shares / 2)
                    task = asyncio.create_task(
                        self._create_half_out_order(
                            self.position_direction, half_out_price, half_out_quantity
                        )
                    )
                    self._task_stack.append(task)
                    # Cost is no longer theoretical, but real
                    self.theoretical_cost = 0.0

                if entry_order.order_status == OrderStatus.CANCELLED:
                    # ======================================
                    # Position has been canceled, mark it so
                    # ======================================
                    self.logger.info(
                        f"Position {self.position_id} for {self.security_descriptor.to_string()} has been cancelled remotely"
                    )
                    cancel_task = asyncio.create_task(
                        self._cancel_orders(self.position_direction)
                    )
                    self._task_stack.append(cancel_task)
                    self.theoretical_cost = 0.0

        if cancel_group_idx != -1:
            cancel_direction = (
                PositionDirection.LONG
                if cancel_group_idx == 0
                else PositionDirection.SHORT
            )
            self.logger.info(
                f"Cancelling dual order for position {self.position_id} for {self.security_descriptor.to_string()}."
            )
            cancel_task = asyncio.create_task(self._cancel_orders(cancel_direction))
            self._task_stack.append(cancel_task)

    def _update_entered_position(self):
        """
        The position has been entered. From here, it can either be half-exited or fully-exited.
        """
        groups: List[Optional[OrderGroup]] = [
            self.long_order_group,
            self.short_order_group,
        ]
        for idx, group in enumerate(groups):
            if group is not None:
                half_out_order = group.half_out_order
                if half_out_order and half_out_order.order_status == OrderStatus.FILLED:
                    # Time to switch to half-out state
                    self.logger.info(
                        f"Half-out for {self.position_id} for {self.security_descriptor.to_string()}"
                    )
                    self.position_state = PositionState.HALF_OUT

                    # Time to adjust stop loss
                    adjust_task = asyncio.create_task(
                        self._adjust_stop_loss(
                            PositionDirection.LONG
                            if idx == 0
                            else PositionDirection.SHORT
                        )
                    )
                    self._task_stack.append(adjust_task)
                    continue

                stop_loss_order = group.stop_loss_order
                if (
                    stop_loss_order
                    and stop_loss_order.order_status == OrderStatus.FILLED
                ):
                    # We're out of the position
                    self.logger.info(
                        f"Stopped out for {self.position_id} for {self.security_descriptor.to_string()}"
                    )
                    self.position_state = PositionState.CLOSED

                    # Let's cancel all orders
                    cancel_task = asyncio.create_task(
                        self._cancel_orders(self.position_direction)
                    )
                    self._task_stack.append(cancel_task)
                    continue

    def _update_half_out_position(self):
        """
        The position has been half-exited. From here, it can only be fully-exited.
        """
        groups: List[Optional[OrderGroup]] = [
            self.long_order_group,
            self.short_order_group,
        ]
        cancel_group_idx = -1
        for idx, group in enumerate(groups):
            if group is not None:
                stop_loss_order = group.stop_loss_order
                if (
                    stop_loss_order
                    and stop_loss_order.order_status == OrderStatus.FILLED
                ):
                    # We're out of the position
                    self.logger.info(
                        f"Stopped out for {self.position_id} for {self.security_descriptor.to_string()}"
                    )
                    self.position_state = PositionState.CLOSED

                    # Let's cancel all orders
                    cancel_task = asyncio.create_task(
                        self._cancel_orders(self.position_direction)
                    )
                    self._task_stack.append(cancel_task)
                    continue

    async def _do_activate(
        self,
        direction: PositionDirection,
        entry_prices: List[float],
        stop_prices: List[float],
        max_loss: float,
        cash_left: float,
    ):
        """
        See activate(). Meant to be wrapped in a task.
        """
        self.position_state = PositionState.CREATED

        if direction == PositionDirection.LONG:
            entry = entry_prices[0]
            stop = stop_prices[0]
            shares_entered, cost = await self._setup_long(
                entry, stop, max_loss, cash_left
            )
            self.theoretical_cost = cost
        elif direction == PositionDirection.SHORT:
            entry = entry_prices[0]
            stop = stop_prices[0]
            shares_entered, cost = await self._setup_short(
                entry, stop, max_loss, cash_left
            )
            self.theoretical_cost = cost
        else:
            entry = entry_prices[0]
            stop = stop_prices[0]
            shares_entered_l, cost_l = await self._setup_long(
                entry, stop, max_loss, cash_left
            )
            entry = entry_prices[1]
            stop = stop_prices[1]
            shares_entered_s, cost_s = await self._setup_short(
                entry, stop, max_loss, cash_left
            )
            self.theoretical_cost = cost_l if cost_l > cost_s else cost_s

        self.logger.info(
            f"Activated position {self.position_id} in direction {PositionDirection(direction).name} for {self.security_descriptor.to_string()}"
        )

        self.position_direction = direction

    async def _do_enter(
        self,
        direction: PositionDirection,
        entry_price: float,
        stop_price: float,
        max_loss: float,
        cash_left: float,
    ):
        """
        Enters a position right now. See enter(). Meant to be wrapped in a task.
        """
        self.position_state = PositionState.CREATED

        self.logger.info(
            f"Entering position {self.position_id} in direction {PositionDirection(direction).name} for {self.security_descriptor.to_string()}"
        )
        if direction == PositionDirection.LONG:
            shares_entered, cost = await self._setup_long(
                entry_price, stop_price, max_loss, cash_left, market_order=True
            )
        elif direction == PositionDirection.SHORT:
            shares_entered, cost = await self._setup_short(
                entry_price, stop_price, max_loss, cash_left, market_order=True
            )

        self.logger.info(
            f"Entered position {self.position_id} in direction {PositionDirection(direction).name} for {self.security_descriptor.to_string()}"
        )

        self.position_direction = direction

    async def _do_exit(self):
        """Exit the position we're in. Meant to be wrapped in a task."""
        self.logger.info(
            f"Exiting position {self.position_id} for {self.security_descriptor.to_string()}"
        )
        self.position_state = PositionState.CANCELED
        await self._cancel_orders(self.position_direction)

        self.position_state = PositionState.CLOSED

        num_shares = self.get_current_shares()
        action = (
            OrderAction.SELL
            if self.position_direction == PositionDirection.LONG
            else OrderAction.BUY
        )
        exit_order, error_str = await self.ib_driver.place_order(
            symbol_full=self.security_descriptor.to_string(),
            action=action,
            quantity=num_shares,
            order_type=OrderType.MARKET,
            transmit=True,
        )
        if error_str is not None:
            raise PositionException(f"Error exiting order: {error_str}")

        self.logger.info(
            f"Exited position {self.position_id} for {self.security_descriptor.to_string()}"
        )

    async def _cancel_orders(self, direction: PositionDirection):
        """
        Cancels unfilled orders that are still active, if they need to be cancelled. Should be wrapped in a
        task by caller.

        :param direction: long, short, or dual
        """
        self.position_state = PositionState.CANCELED

        if direction == PositionDirection.LONG:
            groups = [self.long_order_group]
        elif direction == PositionDirection.SHORT:
            groups = [self.short_order_group]
        else:
            groups = [self.long_order_group, self.short_order_group]

        self.logger.info(
            f"Cancelling orders for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

        async def _do_cancel(order_info: Optional[OrderInfo]):
            if order_info is None:
                return
            if order_info.order_status in [OrderStatus.CANCELLED, OrderStatus.NONE]:
                return

            try:
                await self.ib_driver.cancel_order(order_info)
            except Exception as e:
                self.logger.warning(f"Exception while canceling order: {e}")
                pass

        for group in groups:
            if group:
                await _do_cancel(group.stop_loss_order)
                await _do_cancel(group.entry_order)
                await _do_cancel(group.half_out_order)

        if direction == PositionDirection.LONG:
            self.long_order_group = None
        elif direction == PositionDirection.SHORT:
            self.short_order_group = None
        else:
            self.long_order_group = None
            self.short_order_group = None

        self.logger.info(
            f"Cancelling orders for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

    async def _create_half_out_order(
        self, direction: PositionDirection, price: float, num_shares: int
    ):
        """Creates a profit-taking order. Meant to be wrapped in a task."""

        group = (
            self.long_order_group
            if direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            return

        self.logger.info(
            f"Making half-out order for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

        action = (
            OrderAction.SELL if direction == PositionDirection.LONG else OrderAction.BUY
        )
        half_out_order, error_str = await self.ib_driver.place_order(
            self.security_descriptor.to_string(),
            action=action,
            quantity=num_shares,
            price=price,
            order_type=OrderType.LIMIT,
        )
        if error_str is not None:
            self.logger.warning(f"Error while creating half-out order: {error_str}")
            return

        self.logger.info(
            f"Made half-out order for {self.position_id} for {self.security_descriptor.to_string()}"
        )

        group.half_out_order = half_out_order

    async def _adjust_stop_loss(
        self, direction: PositionDirection, price: Optional[float] = None
    ):
        """Adjusts stop-loss to match diminished position. Meant to be wrapped in a task."""

        group = (
            self.long_order_group
            if direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            return

        self.logger.info(
            f"Adjusting stop-loss for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

        stop_loss_order = group.stop_loss_order
        num_shares_held = self.get_current_shares()
        if price is None:
            price = stop_loss_order.avg_fill_price
            if price is None:
                self.logger.warning(
                    f"No price for stop-loss order {stop_loss_order.get_info_str()}"
                )
                return
        action = (
            OrderAction.SELL if direction == PositionDirection.LONG else OrderAction.BUY
        )
        stop_loss_order, error_str = await self.ib_driver.change_order(
            stop_loss_order,
            action=action,
            quantity=num_shares_held,
            price=price,
            order_type=OrderType.STOP,
        )
        if error_str is not None:
            self.logger.warning(f"Error while adjusting stop loss: {error_str}")
            return

        self.logger.info(
            f"Have adjusted stop-loss for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

    async def _setup_long(
        self, _entry, _stop, max_loss, cash_left, market_order: bool = False
    ) -> Tuple[int, float]:
        """Helper function for setting up long entry"""
        num_shares = int(max_loss / (_entry - _stop))
        cost = float(num_shares) * _entry
        if cost > cash_left:
            raise InsufficientCashException(cost, cash_left)
        entry_order_type = OrderType.MARKET if market_order else OrderType.STOP
        entry_order, error_str = await self.ib_driver.place_order(
            symbol_full=self.security_descriptor.to_string(),
            action=OrderAction.BUY,
            quantity=num_shares,
            price=_entry,
            order_type=entry_order_type,
            transmit=False,
        )
        if error_str is not None:
            raise PositionException(f"Error activating order: {error_str}")
        stop_loss_order, error_str = await self.ib_driver.place_order(
            symbol_full=self.security_descriptor.to_string(),
            action=OrderAction.SELL,
            quantity=num_shares,
            price=_stop,
            order_type=OrderType.STOP,
            parent_order=entry_order,
            transmit=True,
        )
        if error_str is not None:
            raise PositionException(f"Error activating order: {error_str}")
        self.long_order_group = OrderGroup(entry_order, stop_loss_order)
        self.long_order_group.set_initial_quantities(_entry, _stop, num_shares)
        return num_shares, cost

    async def _setup_short(
        self, _entry, _stop, max_loss, cash_left, market_order: bool = False
    ) -> Tuple[int, float]:
        """Helper function for setting up short entry"""
        num_shares = int(max_loss / (_stop - _entry))
        cost = float(num_shares) * _entry
        if cost > cash_left:
            raise InsufficientCashException(cost, cash_left)
        entry_order_type = OrderType.MARKET if market_order else OrderType.STOP
        entry_order, error_str = await self.ib_driver.place_order(
            symbol_full=self.security_descriptor.to_string(),
            action=OrderAction.SELL,
            quantity=num_shares,
            price=_entry,
            order_type=entry_order_type,
            transmit=False,
        )
        if error_str is not None:
            raise PositionException(f"Error activating order: {error_str}")
        stop_loss_order, error_str = await self.ib_driver.place_order(
            symbol_full=self.security_descriptor.to_string(),
            action=OrderAction.BUY,
            quantity=num_shares,
            price=_stop,
            order_type=OrderType.STOP,
            parent_order=entry_order,
            transmit=True,
        )
        if error_str is not None:
            raise PositionException(f"Error activating order: {error_str}")
        self.short_order_group = OrderGroup(entry_order, stop_loss_order)
        self.short_order_group.set_initial_quantities(_entry, _stop, num_shares)
        return num_shares, cost
