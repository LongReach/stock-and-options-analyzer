import asyncio
from typing import Optional, Dict, List, Tuple
from enum import Enum, auto
from logging import getLogger

from core.common import OrderInfo, SecurityDescriptor, OrderAction, OrderType, OrderStatus
from core.ib_driver import IBDriver

class OrderGroup:

    def __init__(self, entry_order: OrderInfo, stop_loss_order: OrderInfo, half_out_order: Optional[OrderInfo] = None):
        self.entry_order: OrderInfo = entry_order
        self.stop_loss_order: Optional[OrderInfo] = stop_loss_order
        self.half_out_order: Optional[OrderInfo] = half_out_order
        self.entry_price: float = 0.0
        self.exit_price: float = 0.0
        self.num_shares: int = 0

    def set_quantities(self, entry_price: float, exit_price: float, shares: int):
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.num_shares = shares


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


class Position:
    """
    Represents a position or prospective position in Guided Missile.

    A prospective position has orders that will trigger an entry into an active position, while an active position
    has orders that will lead to an exit. Typically, an active position has both a stop-loss order and a take-profit
    order.
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
        self.shares_entered: int = 0

        self._task_stack: List[asyncio.Task] = []

    async def activate(self, direction: PositionDirection, entry_prices: List[float], stop_prices: List[float], max_loss: float):
        """
        Sets up stop orders for a position entry. If going long, the position will be entered when the stop is triggered.
        Same idea with going short. If a "dual" entry, then both long and short orders will be set up. However, when one
        is triggered, the other will be removed.

        :param direction: long, short, or duel
        :param entry_prices: list of entry prices. List will be of length 2 if dual position.
        :param stop_prices: list of stop prices. List will be of length 2 if dual position.
        :param max_loss: max allowed loss for this position
        :return:
        """

        if self.position_state != PositionState.NONE:
            raise PositionException(f"Can't activate position, current state is {PositionState(self.position_state).name}")
        if len(self._task_stack) > 0:
            raise PositionException(f"Can't activate position, tasks in progress")

        async def _setup_long(_entry, _stop):
            num_shares = int(max_loss / (_entry - _stop))
            entry_order, error_str = await self.ib_driver.place_order(symbol_full=self.security_descriptor.to_string(), action=OrderAction.BUY, quantity=num_shares, price=_entry, order_type=OrderType.STOP, transmit=False)
            if error_str is not None:
                raise PositionException(f"Error activating order: {error_str}")
            stop_loss_order, error_str = await self.ib_driver.place_order(symbol_full=self.security_descriptor.to_string(), action=OrderAction.SELL, quantity=num_shares, price=_stop, order_type=OrderType.STOP, parent_order=entry_order, transmit=True)
            if error_str is not None:
                raise PositionException(f"Error activating order: {error_str}")
            self.long_order_group = OrderGroup(entry_order, stop_loss_order)
            self.long_order_group.set_quantities(_entry, _stop, num_shares)

        async def _setup_short(_entry, _stop):
            num_shares = int(max_loss / (_stop - _entry))
            entry_order, error_str = await self.ib_driver.place_order(symbol_full=self.security_descriptor.to_string(), action=OrderAction.SELL, quantity=num_shares, price=_entry, order_type=OrderType.STOP, transmit=False)
            if error_str is not None:
                raise PositionException(f"Error activating order: {error_str}")
            stop_loss_order, error_str = await self.ib_driver.place_order(symbol_full=self.security_descriptor.to_string(), action=OrderAction.BUY, quantity=num_shares, price=_stop, order_type=OrderType.STOP, parent_order=entry_order, transmit=True)
            if error_str is not None:
                raise PositionException(f"Error activating order: {error_str}")
            self.short_order_group = OrderGroup(entry_order, stop_loss_order)
            self.short_order_group.set_quantities(_entry, _stop, num_shares)

        if direction == PositionDirection.LONG:
            entry = entry_prices[0]
            stop = stop_prices[0]
            await _setup_long(entry, stop)
        elif direction == PositionDirection.SHORT:
            entry = entry_prices[0]
            stop = stop_prices[0]
            await _setup_long(entry, stop)
        else:
            entry = entry_prices[0]
            stop = stop_prices[0]
            await _setup_long(entry, stop)
            entry = entry_prices[1]
            stop = stop_prices[1]
            await _setup_short(entry, stop)

        self.position_state = PositionState.CREATED
        self.position_direction = direction

    async def update(self):
        """
        Update function to keep this object in sync with state of position in broker. It returns as quickly as
        possible.
        """

        # If there's a task (e.g. for cancelling orders) in progress, allow it to complete
        if len(self._task_stack) > 0:
            for task in self._task_stack:
                if not task.done() and not task.cancelled():
                    return
            self._task_stack = []

        if self.position_state == PositionState.NONE:
            pass
        elif self.position_state == PositionState.CREATED:
            await self._update_created_position()
        elif self.position_state == PositionState.ENTERED:
            await self._update_entered_position()
        else:
            pass

    async def _update_created_position(self):
        """
        The position has been created. From here, it can either be entered or canceled.
        """
        groups: List[Optional[OrderGroup]] = [self.long_order_group, self.short_order_group]
        cancel_group_idx = -1
        for idx, group in enumerate(groups):
            if group is not None:
                entry_order = group.entry_order
                if entry_order.order_status == OrderStatus.FILLED:
                    # Position has been entered, update bookkeeping
                    self.position_state = PositionState.ENTERED
                    if self.position_direction == PositionDirection.DUAL:
                        # We must cancel the other entry and update direction
                        cancel_group_idx = 1 - idx
                        self.position_direction = PositionDirection.LONG if idx == 0 else PositionDirection.SHORT

                    # Create half-out order
                    if idx == 0:
                        half_out_price = group.entry_price + (group.entry_price - group.exit_price) * 2.0
                        half_out_quantity = int(group.num_shares / 2)
                    else:
                        half_out_price = group.entry_price - (group.exit_price - group.entry_price) * 2.0
                        half_out_quantity = int(group.num_shares / 2)
                    task = asyncio.create_task(self._create_half_out_order(self.position_direction, half_out_price, half_out_quantity))
                    self._task_stack.append(task)

                if entry_order.order_status == OrderStatus.CANCELLED:
                    # Position has been canceled, mark it so
                    cancel_task = asyncio.create_task(self._cancel_orders(self.position_direction))
                    self._task_stack.append(cancel_task)

        if cancel_group_idx != -1:
            cancel_direction = PositionDirection.LONG if cancel_group_idx == 0 else PositionDirection.SHORT
            cancel_task = asyncio.create_task(self._cancel_orders(cancel_direction))
            self._task_stack.append(cancel_task)

    async def _update_entered_position(self):
        """
        The position has been entered. From here, it can either be half-exited or fully-exited.
        """
        groups: List[Optional[OrderGroup]] = [self.long_order_group, self.short_order_group]
        cancel_group_idx = -1
        for idx, group in enumerate(groups):
            if group is not None:
                entry_order = group.entry_order
                if entry_order.order_status == OrderStatus.FILLED:
                    if entry_order.shares_filled > self.shares_entered:
                        self.logger.info(f"XXX")
                        self.shares_entered = entry_order.shares_filled

    async def _cancel_orders(self, direction: PositionDirection, stops_only: bool = False):
        """
        Cancels unfilled orders that are still active, if they need to be cancelled. Should be wrapped in a
        task by caller.

        :param direction: long, short, or dual
        :param stops_only: if True, only remove stop loss orders.
        """
        if direction == PositionDirection.LONG:
            groups = [self.long_order_group]
        elif direction == PositionDirection.SHORT:
            groups = [self.short_order_group]
        else:
            groups = [self.long_order_group, self.short_order_group]

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

                if not stops_only:
                    await _do_cancel(group.entry_order)
                    await _do_cancel(group.half_out_order)

        if direction == PositionDirection.LONG:
            self.long_order_group = None
        elif direction == PositionDirection.SHORT:
            self.short_order_group = None
        else:
            self.long_order_group = None
            self.short_order_group = None

    async def _create_half_out_order(self, direction: PositionDirection, price: float, num_shares: int):
        group = self.long_order_group if direction == PositionDirection.LONG else self.short_order_group
        if group is None:
            return

        action = OrderAction.SELL if direction == PositionDirection.LONG else OrderAction.BUY
        half_out_order, error_str = await self.ib_driver.place_order(self.security_descriptor.to_string(), action=action, quantity=num_shares, price=price, order_type=OrderType.LIMIT)
        if error_str is not None:
            self.logger.warning(f"Error while creating half-out order: {error_str}")
            group.half_out_order = None
            return

        group.half_out_order = half_out_order
