import logging
from typing import Optional
from models import Position, Side

logger = logging.getLogger("trailing")


class TrailingStopManager:
    """
    Manages trailing stop-losses that follow price in the profitable direction.
    Activates after price moves a configurable percentage in our favor.
    """

    def __init__(self, activation_pct: float = 0.3, trail_pct: float = 0.2):
        self.activation_pct = activation_pct
        self.trail_pct = trail_pct
        self._trailing_stops: dict = {}  # position_id -> current trailing SL

    def update(self, position: Position, current_price: float) -> Optional[float]:
        """
        Returns updated stop-loss if trailing should adjust, else None.
        """
        pos_id = position.id
        original_sl = position.stop_loss
        current_trail = self._trailing_stops.get(pos_id)

        if position.side == Side.BUY:
            move_pct = (current_price - position.entry_price) / position.entry_price * 100
            if move_pct < self.activation_pct:
                return None

            new_trail = current_price * (1 - self.trail_pct / 100)

            if current_trail is None:
                if new_trail > original_sl:
                    self._trailing_stops[pos_id] = new_trail
                    logger.info(
                        f"[{pos_id}] Trailing activated: SL {original_sl:.2f} → {new_trail:.2f} "
                        f"(price at {current_price:.2f}, +{move_pct:.2f}%)"
                    )
                    return new_trail
            elif new_trail > current_trail:
                self._trailing_stops[pos_id] = new_trail
                logger.debug(f"[{pos_id}] Trail updated: {current_trail:.2f} → {new_trail:.2f}")
                return new_trail

        else:  # SELL
            move_pct = (position.entry_price - current_price) / position.entry_price * 100
            if move_pct < self.activation_pct:
                return None

            new_trail = current_price * (1 + self.trail_pct / 100)

            if current_trail is None:
                if new_trail < original_sl:
                    self._trailing_stops[pos_id] = new_trail
                    logger.info(
                        f"[{pos_id}] Trailing activated: SL {original_sl:.2f} → {new_trail:.2f} "
                        f"(price at {current_price:.2f}, +{move_pct:.2f}%)"
                    )
                    return new_trail
            elif new_trail < current_trail:
                self._trailing_stops[pos_id] = new_trail
                logger.debug(f"[{pos_id}] Trail updated: {current_trail:.2f} → {new_trail:.2f}")
                return new_trail

        return None

    def get_effective_sl(self, position: Position) -> float:
        trail = self._trailing_stops.get(position.id)
        if trail is not None:
            if position.side == Side.BUY:
                return max(trail, position.stop_loss)
            return min(trail, position.stop_loss)
        return position.stop_loss

    def remove(self, position_id: str):
        self._trailing_stops.pop(position_id, None)
