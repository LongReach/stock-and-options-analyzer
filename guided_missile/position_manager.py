import asyncio
from typing import Optional, Dict, List, Tuple
from enum import Enum, auto
from logging import getLogger

from core.ib_driver import IBDriver
from core.common import SecurityDescriptor, BarSize, HistoricalData
from guided_missile.position import Position, PositionState, PositionDirection


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
        # Maps symbol to historical data that's already streaming
        self._historical_data_cache: Dict[Tuple[str, BarSize], HistoricalData] = {}

        self._logger = getLogger(__file__)

    def add_position(
        self, security_descriptor: SecurityDescriptor
    ) -> Tuple[bool, Optional[str]]:
        """
        Adds position to tracking.

        :param security_descriptor: describes stock, ETF, or options contract
        :return: (True if success, error string or None)
        """

        existing_position = self._position_map.get(security_descriptor.to_string())
        if existing_position:
            if existing_position not in [
                PositionState.NONE,
                PositionState.CLOSED,
                PositionState.CANCELED,
            ]:
                return (
                    False,
                    f"Can't add position for {security_descriptor.to_string()}",
                )

        self._logger.info(f"PositionManager: adding position for {security_descriptor.to_string()}")
        self._position_map[security_descriptor.to_string()] = Position(
            security_descriptor
        )

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
        :param direction: TODO
        :param bars_back: TODO
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

        bar_highs = [bar.high for bar in historical_data.bar_data]
        highest_recent_price = max(bar_highs)
        bar_lows = [bar.low for bar in historical_data.bar_data]
        lowest_recent_price = min(bar_lows)

        if direction == PositionDirection.LONG:
            entries = [highest_recent_price]
            stops = [lowest_recent_price]
        elif direction == PositionDirection.SHORT:
            entries = [lowest_recent_price]
            stops = [highest_recent_price]
        else:
            entries = [highest_recent_price, lowest_recent_price]
            stops = [lowest_recent_price, highest_recent_price]

        self._logger.info(f"PositionManager: activate() uses entries of {entries}, stops of {stops}")
        try:
            existing_position.activate(
                direction, entries, stops, self.MAX_LOSS, self._cash_available
            )
        except Exception as e:
            return False, f"activate() failed with exception: {e}"

        return True, None

    async def enter(
        self,
        security_descriptor: SecurityDescriptor,
        direction: PositionDirection,
        bars_back: int,
    ) -> Tuple[bool, Optional[str]]:
        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return False, f"Can't enter position for {security_descriptor.to_string()}"

        self._logger.info(f"PositionManager: entering position for {security_descriptor.to_string()}")
        historical_data, error_str = await self._get_historical_data_stream(
            security_descriptor, bars_back=bars_back, bar_size=self.BAR_SIZE
        )
        if historical_data is None:
            return False, f"enter() failed with error: {error_str}"

        bar_highs = [bar.high for bar in historical_data.bar_data]
        highest_recent_price = max(bar_highs)
        bar_lows = [bar.low for bar in historical_data.bar_data]
        lowest_recent_price = min(bar_lows)

        if direction == PositionDirection.LONG:
            entry = highest_recent_price
            stop = lowest_recent_price
        elif direction == PositionDirection.LONG:
            entry = lowest_recent_price
            stop = highest_recent_price
        else:
            return False, "Dual mode not supported"

        try:
            existing_position.enter(
                direction, entry, stop, self.MAX_LOSS, self._cash_available
            )
        except Exception as e:
            return False, f"enter() failed with exception: {e}"

        return True, None

    async def cancel(
        self, security_descriptor: SecurityDescriptor
    ) -> Tuple[bool, Optional[str]]:
        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return False, f"Can't cancel position for {security_descriptor.to_string()}"

        self._logger.info(f"PositionManager: canceling position for {security_descriptor.to_string()}")
        try:
            existing_position.cancel()
        except Exception as e:
            return False, f"cancel() failed with exception: {e}"

        return True, None

    async def exit(
        self, security_descriptor: SecurityDescriptor
    ) -> Tuple[bool, Optional[str]]:
        existing_position = self._position_map.get(security_descriptor.to_string())
        if not existing_position:
            return False, f"Can't exit position for {security_descriptor.to_string()}"

        self._logger.info(f"PositionManager: exiting position for {security_descriptor.to_string()}")
        try:
            existing_position.exit()
        except Exception as e:
            return False, f"exit() failed with exception: {e}"

        return True, None

    async def update(self):
        for pos_name, position in self._position_map.items():
            position.update()

        await self._update_cash_amount()

    def get_info(self, security_descriptor: SecurityDescriptor) -> Optional[List[str]]:
        position = self._position_map.get(security_descriptor.to_string())
        if position is None:
            return None
        return position.get_info()

    def get_all_info(self) -> Dict[str, List[str]]:
        out_dict = {}
        for pos_name, position in self._position_map.items():
            out_dict[pos_name] = position.get_info()
        return out_dict

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
        # Check cache first
        historical_data = self._historical_data_cache.get(
            (security_descriptor.to_string(), bar_size)
        )
        if historical_data:
            if len(historical_data.bar_data) < bars_back:
                # We need to fetch it again
                historical_data = None

        if historical_data is None:
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

            if len(self._historical_data_cache) >= self.MAX_DATA_STREAMS:
                # Find a stream to remove
                lowest_id = -1
                removal_key = None
                removal_hd = None
                for key, hd in self._historical_data_cache.items():
                    if lowest_id == -1 or hd.get_id() < lowest_id:
                        lowest_id = hd.get_id()
                        removal_key = key
                        removal_hd = hd
                if removal_key is not None:
                    try:
                        await self.ib_driver.cancel_historical_data(removal_hd)
                    except:
                        pass
                    self._historical_data_cache.pop(removal_key, None)

            self._historical_data_cache[(security_descriptor.to_string(), bar_size)] = (
                historical_data
            )

        return historical_data, None

    async def _update_cash_amount(self):

        cash_deduction: float = 0.0

        # First, count the theoretical cost of positions not yet entered
        for security_desc, position in self._position_map.items():
            cash_deduction += position.theoretical_cost

        # Now, we ask IB directly about positions we're in
        positions_info, error_str = await self.ib_driver.get_positions()
        if error_str:
            # TODO: log something
            pass
        else:
            positions = positions_info.get_positions()
            for position in positions:
                cash_deduction += position.price * float(position.quantity)

        self._cash_available = self._account_value - cash_deduction
