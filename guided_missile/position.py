import asyncio
from tokenize import group
from typing import Optional, Dict, List, Tuple
from enum import Enum, auto
from logging import getLogger

from core.common import (
    OrderInfo,
    SecurityDescriptor,
    OrderAction,
    OrderType,
    OrderStatus,
    HistoricalData,
)
from core.utils import wait_for_condition
from core.ib_driver import IBDriver


class OrderGroup:

    def __init__(
        self,
        entry_order: OrderInfo,
        stop_loss_order: OrderInfo,
        take_profit_order: Optional[OrderInfo] = None,
    ):
        self.entry_order: OrderInfo = entry_order
        self.stop_loss_order: OrderInfo = stop_loss_order
        self.take_profit_order: Optional[OrderInfo] = take_profit_order
        self.earlier_tp_orders: List[OrderInfo] = []
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
    """States a position can be in"""

    NONE = auto()
    CREATED = auto()
    ENTERED = auto()
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

    A position can be in one of several states. Each is handled by an asynchronous function that performs setup
    then waits for a command from the console, or for something to happen on the broker side (e.g. an
    order is entered). After that, control transitions to a new state function.

    If an unrecoverable error happens in a state, a PositionException will be raised.
    """

    ib_driver: IBDriver = None
    logger = getLogger(__file__)
    next_id = 1

    def __init__(self, security_descriptor: SecurityDescriptor):
        """
        Constructor for Position
        :param security_descriptor: describes stock, ETF, or options contract
        """
        self.security_descriptor: SecurityDescriptor = security_descriptor
        self.position_state: PositionState = PositionState.NONE
        self.position_direction: PositionDirection = PositionDirection.LONG
        # Holds the orders related to a long position, if one exists
        self.long_order_group: Optional[OrderGroup] = None
        # Holds the orders related to a short position, if one exists
        self.short_order_group: Optional[OrderGroup] = None
        # Unique position ID for logging purposes
        self.position_id = Position.next_id
        Position.next_id += 1
        # Will be non-zero for positions "armed" but not yet entered
        self.theoretical_cost: float = 0.0

        # If set, will contain historical bar data
        self._historical_data: Optional[HistoricalData] = None

        self._trigger_event: asyncio.Event = asyncio.Event()
        self._trigger_data: Dict = {}
        self._stop_event: asyncio.Event = asyncio.Event()

        self._state_task: Optional[asyncio.Task] = None

    def set_historical_data_stream(self, historical_data: Optional[HistoricalData]):
        """Sets a historical data stream to be associated with this position, or None"""
        self._historical_data = historical_data

    def get_historical_data_stream(self):
        """Returns historical data stream associated with this position or None"""
        return self._historical_data

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

        take_profit_orders: List[OrderInfo] = []
        if group.take_profit_order:
            take_profit_orders.append(group.take_profit_order)
        take_profit_orders.extend(group.earlier_tp_orders)

        shares_out = 0
        for tpo in take_profit_orders:
            shares_out += tpo.shares_filled
        if group.stop_loss_order:
            shares_out += group.stop_loss_order.shares_filled
        return shares_in - shares_out

    def get_profit(self) -> float:
        """Return profits realized"""
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
        total_cost = shares_in * price_in

        take_profit_orders: List[OrderInfo] = []
        if group.take_profit_order:
            take_profit_orders.append(group.take_profit_order)
        take_profit_orders.extend(group.earlier_tp_orders)

        total_revenue = 0
        for tpo in take_profit_orders:
            total_revenue += tpo.shares_filled * tpo.avg_fill_price
        if group.stop_loss_order:
            total_revenue += group.stop_loss_order.shares_filled * group.stop_loss_order.avg_fill_price

        if self.position_direction == PositionDirection.LONG:
            return total_revenue - total_cost
        else:
            return total_cost - total_revenue

    def trigger_event(self, **kwargs):
        """Triggers an action requested from console."""
        # TODO: better docs
        self._trigger_data = kwargs
        self._trigger_event.set()

    def stop_all_states(self):
        """TODO: docs"""
        self._stop_event.set()
        self._state_task = None

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def _get_target_hit(self, target_type: str) -> Optional[Tuple[PositionDirection, OrderInfo]]:
        """
        If an order has been filled, return info about order
        :param target_type: "entry", "stop", "profit"
        :return: (direction, order info) or None
        """
        # TODO: move this and func below
        groups = [self.long_order_group, self.short_order_group]
        for idx, group in enumerate(groups):
            if group:
                if target_type == "entry" and group.entry_order.totally_filled():
                    return PositionDirection.LONG if idx == 0 else PositionDirection.SHORT, group.entry_order
                if target_type == "stop" and group.stop_loss_order and group.stop_loss_order.totally_filled():
                    return PositionDirection.LONG if idx == 0 else PositionDirection.SHORT, group.stop_loss_order
                if target_type == "profit" and group.take_profit_order and group.take_profit_order.totally_filled():
                    return PositionDirection.LONG if idx == 0 else PositionDirection.SHORT, group.take_profit_order
        return None

    def _get_cancel_triggered(self) -> Optional[Tuple[PositionDirection, OrderInfo]]:
        """If entry order has been canceled on broker side, return (direction, order info) or None"""
        groups = [self.long_order_group, self.short_order_group]
        for idx, group in enumerate(groups):
            if group:
                if group.entry_order.order_status == OrderStatus.CANCELLED:
                    return PositionDirection.LONG if idx == 0 else PositionDirection.SHORT, group.entry_order
        return None

    def launch(self, after_reset: bool = False):
        # TODO: docs
        if after_reset:
            self._state_task = asyncio.create_task(self.entered_state(self.position_direction, fresh_entry=False))
        else:
            self._state_task = asyncio.create_task(self.start_state())

    def get_state_task_done(self) -> Tuple[bool, Optional[Exception]]:
        # TODO: docs
        if self._state_task is None:
            return True, None
        if self._state_task.done():
            return True, self._state_task.exception()
        return False, None

    async def start_state(self):
        """
        Initial state. Wait for position to be entered or activated.
        """
        self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} enters start_state")
        self.position_state = PositionState.NONE

        while not self._stop_event.is_set():
            if self._trigger_event.is_set():
                self._trigger_event.clear()
                event_name = self._trigger_data["event"]
                self._trigger_data.pop("event", None)
                # self.trigger_event(event="enter", direction=direction, entry_price=entry_price, stop_price=stop_price, max_loss=max_loss, cash_left=cash_left)
                # self.trigger_event(event="activate", direction=direction, entry_prices=entry_prices, stop_prices=stop_prices, max_loss=max_loss, cash_left=cash_left)
                if event_name == "activate":
                    self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} activated from start_state")
                    self.logger.info(f"**** butt {self._trigger_data}")
                    direction, entry_prices, stop_prices, max_loss, cash_left = self._trigger_data.values()
                    await self._to_state_created(direction, entry_prices, stop_prices, max_loss, cash_left)
                    break
                elif event_name == "enter":
                    self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} entered from start_state")
                    direction, entry_price, stop_price, max_loss, cash_left = self._trigger_data.values()
                    await self._to_state_entered(direction, entry_price, stop_price, max_loss, cash_left)
                    break
                else:
                    self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} receives command {event_name}, but can't do anything")

            await asyncio.sleep(0.1)

    async def created_state(self):
        """
        State entered after position has been activated. Wait for cancellation or entry.
        """
        self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} enters created_state")
        self.position_state = PositionState.CREATED

        cancel_direction: Optional[PositionDirection] = None
        fill_direction: Optional[PositionDirection] = None

        # Now we await cancellation or entry
        while not self._stop_event.is_set():
            # Look for cancellation from command console
            if self._trigger_event.is_set():
                self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} canceled while in created_state (console)")
                self._trigger_event.clear()
                event_name = self._trigger_data["event"]
                if event_name == "cancel":
                    # Cancel from control console
                    cancel_direction = self.position_direction
                    break

            # Look for cancellation from broker side
            cancel_results = self._get_cancel_triggered()
            if cancel_results:
                self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} canceled while in created_state (broker)")
                _dir, _info = cancel_results
                cancel_direction = _dir
                break

            # Look for entry order triggered
            fill_results = self._get_target_hit("entry")
            if fill_results:
                self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} entered while in created_state (broker)")
                _dir, _info = fill_results
                fill_direction = _dir
                break

            await asyncio.sleep(0.1)

        if cancel_direction is not None:
            await self._do_cancel(cancel_direction, go_to_cancelled_state=True)
        elif fill_direction is not None:
            if self.position_direction == PositionDirection.DUAL:
                # We need to cancel on one side or the other
                cancel_direction = PositionDirection.SHORT if fill_direction == PositionDirection.LONG else PositionDirection.LONG
                await self._do_cancel(cancel_direction)

            await self.entered_state(fill_direction, True)

    async def entered_state(self, direction: PositionDirection, fresh_entry: bool = True):
        """
        State entered after position has been entered or after partial-out take-profit hit. Wait for
        cancellation or entry.
        """
        self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} enters entered_state")
        group = (
            self.long_order_group
            if direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            error_msg = f"Couldn't find group for position {self.position_id}, direction {PositionDirection(direction).name}"
            self.logger.error(error_msg)
            raise PositionException(error_msg)

        self.logger.info(
            f"Making take-profit order for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

        if fresh_entry:
            # This is a newly entered position
            if group.stop_loss_order is None:
                raise PositionException(f"Stop loss order missing for position {self.position_id}")
            tp_num_shares = int(group.entry_order.shares_filled / 2)
            stop_price = group.stop_loss_order.avg_fill_price
            # We want a take profit order that's double the distance from entry point as stop loss is from
            # entry point.
            if direction == PositionDirection.LONG:
                tp_price = group.entry_order.avg_fill_price + (group.entry_order.avg_fill_price - stop_price) * 2.0
            else:
                tp_price = group.entry_order.avg_fill_price - (stop_price - group.entry_order.avg_fill_price) * 2.0
        else:
            # We've reentered this state after a reset or after taking some profit, need new take-profit order
            if group.take_profit_order is None:
                # After a reset, probably
                tp_num_shares = int(group.entry_order.shares_filled)
                # We want a new take-profit that's half a percent away from last take-profit
                if direction == PositionDirection.LONG:
                    tp_price = group.entry_order.avg_fill_price * 1.005
                else:
                    tp_price = group.entry_order.avg_fill_price * 0.995
            else:
                # Adjust stop-loss
                await self._adjust_stop_loss(self.position_direction)

                tp_num_shares = int(group.entry_order.shares_filled) - int(group.take_profit_order.shares_filled)
                # We want a new take-profit that's half a percent away from last take-profit
                if direction == PositionDirection.LONG:
                    tp_price = group.take_profit_order.avg_fill_price * 1.005
                else:
                    tp_price = group.take_profit_order.avg_fill_price * 0.995
                # Save this order for record-keeping
                group.earlier_tp_orders.append(group.take_profit_order)

        action = (
            OrderAction.SELL if direction == PositionDirection.LONG else OrderAction.BUY
        )
        take_profit_order, error_str = await self.ib_driver.place_order(
            self.security_descriptor.to_string(),
            action=action,
            quantity=tp_num_shares,
            price=tp_price,
            order_type=OrderType.LIMIT,
        )
        if error_str is not None:
            error_message = f"Error while creating take_profit order: {error_str}"
            self.logger.warning(error_message)
            # Seems unnecessary to raise error
            #raise PositionException(error_message)

        self.logger.info(
            f"Made take-profit order for {self.position_id} for {self.security_descriptor.to_string()}"
        )

        group.take_profit_order = take_profit_order
        self.position_state = PositionState.ENTERED
        # Cost is no longer theoretical, but real
        self.theoretical_cost = 0.0

        stop_loss_direction: Optional[PositionDirection] = None
        take_profit_direction: Optional[PositionDirection] = None
        do_exit = False

        self.position_state = PositionState.ENTERED
        # Now that we're entered, we wait for stop loss or take profit to be hit
        while not self._stop_event.is_set():
            if self._trigger_event.is_set():
                self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} canceled while in created_state (console)")
                self._trigger_event.clear()
                event_name = self._trigger_data["event"]
                if event_name == "exit":
                    # Exit command from control console
                    do_exit = True
                    break

            stop_loss_tup = self._get_target_hit("stop")
            if stop_loss_tup:
                _dir, _order = stop_loss_tup
                stop_loss_direction = _dir
                break

            profit_tup = self._get_target_hit("profit")
            if profit_tup:
                _dir, _order = profit_tup
                take_profit_direction = _dir
                break

            await asyncio.sleep(0.1)

        if stop_loss_direction is not None:
            # Cancel other orders, then go to closed state
            await self._do_cancel(self.position_direction)
            await self.closed_state()
        elif take_profit_direction is not None:
            await self._handle_take_profit(take_profit_direction)
        elif do_exit:
            await self._do_exit()

    async def closed_state(self):
        self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} enters closed_state")
        self.position_state = PositionState.CLOSED
        pass

    async def canceled_state(self):
        self.logger.info(f"Position: {self.position_id} for {self.security_descriptor.to_string()} enters canceled_state")
        self.position_state = PositionState.CANCELED
        pass

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

        self.trigger_event(event="activate", direction=direction, entry_prices=entry_prices, stop_prices=stop_prices, max_loss=max_loss, cash_left=cash_left)

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

        self.trigger_event(event="enter", direction=direction, entry_price=entry_price, stop_price=stop_price, max_loss=max_loss, cash_left=cash_left)

    def cancel(self, force_cancel: bool = False):
        """
        Cancel any orders that haven't been filled yet

        :param force_cancel: if True, cancel will happen for position we're already in. That is, stop-loss and
            take-profit orders will be canceled.
        """
        if not force_cancel and self.position_state != PositionState.CREATED:
            raise PositionException(
                f"Can't cancel position, current state is {PositionState(self.position_state).name}"
            )

        self.trigger_event(event="cancel")

    def exit(self):
        """Exit the position we're in"""
        if self.position_state not in [PositionState.ENTERED]:
            raise PositionException(
                f"Can't exit position, current state is {PositionState(self.position_state).name}"
            )
        if self.position_direction == PositionDirection.DUAL:
            raise PositionException("Can't directly exit dual position")

        self.trigger_event(event="exit")

    def get_info(self) -> List[str]:
        """
        Gets printable info about position
        :return: list of printable lines
        """
        cost = 0.0
        group = (
            self.long_order_group
            if self.position_direction == PositionDirection.LONG
            else self.short_order_group
        )
        if self.position_state in [PositionState.ENTERED]:
            num_shares = self.get_current_shares()
            shares_line = f"Shares: {num_shares}"
        elif self.position_state == PositionState.CREATED:
            if group:
                num_shares = group.entry_order.shares_remaining
                shares_line = f"Shares: {num_shares} (prospective)"
            else:
                num_shares = 0
                shares_line = "Shares: ???"
        else:
            num_shares = 0
            shares_line = "Shares: 0"

        extra_info_dict = {}
        if group:
            extra_info_dict["entry_price"] = group.entry_order.avg_fill_price
            extra_info_dict["entry_shares"] = group.entry_order.shares_filled
            extra_info_dict["entry_shares_remaining"] = (
                group.entry_order.shares_remaining
            )
            if group.stop_loss_order:
                extra_info_dict["exit_price"] = group.stop_loss_order.avg_fill_price
                extra_info_dict["exit_shares"] = group.stop_loss_order.shares_filled
                extra_info_dict["exit_shares_remaining"] = (
                    group.stop_loss_order.shares_remaining
                )

        lines = [
            f"Symbol: {self.security_descriptor.to_string()}",
            f"Position ID: {self.position_id}",
            f"State: {PositionState(self.position_state).name}",
            f"Direction: {PositionDirection(self.position_direction).name}",
            shares_line,
        ]
        if len(extra_info_dict):
            lines.append(f"Extra info: {extra_info_dict}")
        return lines

    async def _to_state_created(
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
        await self.created_state()

    async def _to_state_entered(
        self,
        direction: PositionDirection,
        entry_price: float,
        stop_price: float,
        max_loss: float,
        cash_left: float,
    ):
        """
        Enters a position right now.
        TODO: more docs
        """
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
            f"Placed market order for position {self.position_id} in direction {PositionDirection(direction).name} for {self.security_descriptor.to_string()}"
        )

        # Now we wait for market order to be filled
        fill_results = None
        while fill_results is None and not self._stop_event.is_set():
            fill_results = self._get_target_hit("entry")
            if fill_results:
                self.logger.info(
                    f"Position: {self.position_id} for {self.security_descriptor.to_string()} entered via immediate market order")
                break
            await asyncio.sleep(0.01)

        self.position_direction = direction
        await self.entered_state(direction, fresh_entry=True)

    async def _do_exit(self):
        """Exit the position we're in, then go to exited_state."""
        self.logger.info(
            f"Exiting position {self.position_id} for {self.security_descriptor.to_string()}"
        )
        num_shares = self.get_current_shares()
        await self._do_cancel(self.position_direction)

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

        group = self.long_order_group if self.position_direction == PositionDirection.LONG else self.short_order_group
        if group:
            # Make sure we save any TP order already executed
            if group.take_profit_order:
                group.earlier_tp_orders.append(group.take_profit_order)
            group.take_profit_order = exit_order

        await self.closed_state()

    async def _do_cancel(
        self, direction: PositionDirection, go_to_cancelled_state: bool = False
    ):
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

        async def _do_cancel_impl(order_info: Optional[OrderInfo]):
            if order_info is None:
                return
            if order_info.order_status in [
                OrderStatus.CANCELLED,
                OrderStatus.NONE,
                OrderStatus.FILLED,
            ]:
                return

            try:
                await self.ib_driver.cancel_order(order_info)
            except Exception as e:
                self.logger.warning(f"Exception while canceling order: {e}")
                pass

        for group in groups:
            if group:
                await _do_cancel_impl(group.stop_loss_order)
                await _do_cancel_impl(group.entry_order)
                await _do_cancel_impl(group.take_profit_order)

        self.logger.info(
            f"Cancelling orders for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )
        self.theoretical_cost = 0.0
        if go_to_cancelled_state:
            await self.canceled_state()

    async def _handle_take_profit(self, direction: PositionDirection):
        """
        Called when take-profit hit. Decides whether whole position can be closed or we need to go back to the
        entered stated.
        """
        group = (
            self.long_order_group
            if direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            error_msg = f"Can't handle take-profit for position {self.position_id}, no group"
            self.logger.error(error_msg)
            raise PositionException(error_msg)
        if group.take_profit_order is None:
            error_msg = f"Can't handle take-profit for position {self.position_id}, no take-profit order"
            self.logger.error(error_msg)
            raise PositionException(error_msg)

        shares_left = group.entry_order.shares_filled - group.take_profit_order.shares_filled
        if shares_left == 0:
            await self._do_cancel(direction, go_to_cancelled_state=False)
            await self.closed_state()
        else:
            await self.entered_state(direction, fresh_entry=False)

    async def _adjust_stop_loss(
        self, direction: PositionDirection, price: Optional[float] = None
    ):
        """
        Called when take-profit order hit. Adjusts stop-loss to match diminished position.
        """

        group = (
            self.long_order_group
            if direction == PositionDirection.LONG
            else self.short_order_group
        )
        if group is None:
            self.logger.warning(f"Can't adjust stop-loss for position {self.position_id}, no group")
            return
        if group.take_profit_order is None:
            self.logger.warning(f"Can't adjust stop-loss for position {self.position_id}, no completed take-profit order")
            return
        if group.stop_loss_order is None:
            self.logger.warning(f"Can't adjust stop-loss for position {self.position_id}, no stop-loss order")
            return

        self.logger.info(
            f"Adjusting stop-loss for {self.position_id} for {self.security_descriptor.to_string()}, direction is {PositionDirection(direction).name}"
        )

        # TODO: fix this in case multiple take-profits
        shares_left = group.take_profit_order.shares_filled - group.take_profit_order.shares_filled
        stop_loss_order = group.stop_loss_order
        if price is None:
            price = stop_loss_order.avg_fill_price

        action = (
            OrderAction.SELL if direction == PositionDirection.LONG else OrderAction.BUY
        )
        stop_loss_order, error_str = await self.ib_driver.change_order(
            stop_loss_order,
            action=action,
            quantity=shares_left,
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
