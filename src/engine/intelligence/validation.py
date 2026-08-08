from __future__ import annotations

from typing import Any, Iterable

from engine.models.validation import (
    ExitReason,
    ValidationResult,
    ValidationStatus,
)


class SignalValidationEngine:
    """
    Validate a generated trading signal against subsequent
    market candles.

    The engine evaluates:

    - entry activation
    - stop-loss hits
    - take-profit hits
    - ambiguous candles
    - expiry
    - MFE
    - MAE
    - realized R-multiple

    The engine does not execute trades.
    """

    DEFAULT_MAX_CANDLES = 50

    # =========================================================
    # PUBLIC API
    # =========================================================

    def validate(
        self,
        signal: Any,
        candles: Iterable[Any],
        *,
        max_candles: int | None = None,
    ) -> ValidationResult:
        """
        Validate a signal against subsequent OHLC candles.

        Entry is considered triggered when:

            low <= entry_price <= high

        After entry:

        LONG:
            TP -> high >= take_profit
            SL -> low <= stop_loss

        SHORT:
            TP -> low <= take_profit
            SL -> high >= stop_loss

        If both TP and SL are touched within the same candle,
        the result is AMBIGUOUS because OHLC data cannot tell
        which level was reached first.
        """

        candles = list(candles)

        if max_candles is None:
            max_candles = self.DEFAULT_MAX_CANDLES

        try:
            max_candles = int(max_candles)
        except (TypeError, ValueError):
            max_candles = self.DEFAULT_MAX_CANDLES

        max_candles = max(0, max_candles)

        direction = self._get_direction(signal)

        entry = self._get_price(signal, "entry_price")
        stop_loss = self._get_price(signal, "stop_loss")
        take_profit = self._get_price(signal, "take_profit")

        # -----------------------------------------------------
        # INVALID PRICE CONFIGURATION
        # -----------------------------------------------------

        if (
            entry is None
            or stop_loss is None
            or take_profit is None
        ):
            return self._invalid_price_result(
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        if direction not in {"LONG", "SHORT"}:
            return self._invalid_price_result(
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason="Signal has an invalid direction.",
                detail="Direction must be LONG or SHORT.",
            )

        if not self._valid_price_geometry(
            direction,
            entry,
            stop_loss,
            take_profit,
        ):
            return self._invalid_price_result(
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason="Signal contains invalid price geometry.",
                detail=(
                    "Stop loss and take profit must be positioned "
                    "correctly relative to the entry."
                ),
            )

        # -----------------------------------------------------
        # EMPTY / ZERO-LENGTH WINDOW
        # -----------------------------------------------------

        if not candles or max_candles == 0:
            return ValidationResult(
                status=ValidationStatus.OPEN,
                exit_reason=ExitReason.NONE,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_triggered=False,
                exit_price=None,
                candles_evaluated=0,
                duration_candles=0,
                realized_r=None,
                mfe_r=0.0,
                mae_r=0.0,
                validation_timestamp=None,
                reason="No candles available for validation.",
                details=(
                    "Signal remains unresolved.",
                ),
            )

        validation_candles = candles[:max_candles]

        entry_triggered = False
        entry_index: int | None = None

        mfe_r = 0.0
        mae_r = 0.0
        candles_evaluated = 0

        last_valid_timestamp = None
        valid_candle_seen = False

        # -----------------------------------------------------
        # CANDLE LOOP
        # -----------------------------------------------------

        for index, candle in enumerate(validation_candles):

            candles_evaluated += 1

            high = self._candle_high(candle)
            low = self._candle_low(candle)

            timestamp = self._candle_timestamp(candle)

            if timestamp is not None:
                last_valid_timestamp = timestamp

            if high is None or low is None:
                continue

            if high < low:
                continue

            valid_candle_seen = True

            # -------------------------------------------------
            # ENTRY
            # -------------------------------------------------

            if not entry_triggered:

                if not self._entry_touched(
                    direction,
                    entry,
                    high,
                    low,
                ):
                    continue

                entry_triggered = True
                entry_index = index

                mfe_r = max(
                    mfe_r,
                    self._calculate_mfe(
                        direction,
                        entry,
                        high,
                        low,
                        stop_loss,
                    ),
                )

                mae_r = min(
                    mae_r,
                    self._calculate_mae(
                        direction,
                        entry,
                        high,
                        low,
                        stop_loss,
                    ),
                )

                touched_tp = self._take_profit_touched(
                    direction,
                    take_profit,
                    high,
                    low,
                )

                touched_sl = self._stop_loss_touched(
                    direction,
                    stop_loss,
                    high,
                    low,
                )

                if touched_tp and touched_sl:
                    return self._ambiguous_result(
                        entry=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        candles_evaluated=candles_evaluated,
                        duration_candles=1,
                        mfe_r=mfe_r,
                        mae_r=mae_r,
                        candle=candle,
                    )

                if touched_tp:
                    return self._win_result(
                        entry=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        candles_evaluated=candles_evaluated,
                        duration_candles=1,
                        mfe_r=mfe_r,
                        mae_r=mae_r,
                        timestamp=timestamp,
                    )

                if touched_sl:
                    return self._loss_result(
                        entry=entry,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        candles_evaluated=candles_evaluated,
                        duration_candles=1,
                        mfe_r=mfe_r,
                        mae_r=mae_r,
                        timestamp=timestamp,
                    )

                continue

            # -------------------------------------------------
            # AFTER ENTRY
            # -------------------------------------------------

            mfe_r = max(
                mfe_r,
                self._calculate_mfe(
                    direction,
                    entry,
                    high,
                    low,
                    stop_loss,
                ),
            )

            mae_r = min(
                mae_r,
                self._calculate_mae(
                    direction,
                    entry,
                    high,
                    low,
                    stop_loss,
                ),
            )

            touched_tp = self._take_profit_touched(
                direction,
                take_profit,
                high,
                low,
            )

            touched_sl = self._stop_loss_touched(
                direction,
                stop_loss,
                high,
                low,
            )

            duration = (
                index - entry_index + 1
                if entry_index is not None
                else 1
            )

            if touched_tp and touched_sl:
                return self._ambiguous_result(
                    entry=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    candles_evaluated=candles_evaluated,
                    duration_candles=duration,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    candle=candle,
                )

            if touched_tp:
                return self._win_result(
                    entry=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    candles_evaluated=candles_evaluated,
                    duration_candles=duration,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    timestamp=timestamp,
                )

            if touched_sl:
                return self._loss_result(
                    entry=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    candles_evaluated=candles_evaluated,
                    duration_candles=duration,
                    mfe_r=mfe_r,
                    mae_r=mae_r,
                    timestamp=timestamp,
                )

        # -----------------------------------------------------
        # NO VALID OHLC CANDLES
        # -----------------------------------------------------

        if not valid_candle_seen:
            return ValidationResult(
                status=ValidationStatus.OPEN,
                exit_reason=ExitReason.NONE,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_triggered=False,
                exit_price=None,
                candles_evaluated=candles_evaluated,
                duration_candles=0,
                realized_r=None,
                mfe_r=0.0,
                mae_r=0.0,
                validation_timestamp=last_valid_timestamp,
                reason="No valid OHLC candles were available.",
                details=(
                    "Validation could not evaluate price movement.",
                ),
            )

        # -----------------------------------------------------
        # EXPIRY
        # -----------------------------------------------------

        if entry_triggered:

            duration = (
                candles_evaluated - entry_index
                if entry_index is not None
                else 0
            )

            return ValidationResult(
                status=ValidationStatus.EXPIRED,
                exit_reason=ExitReason.EXPIRY,
                entry_price=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_triggered=True,
                exit_price=None,
                candles_evaluated=candles_evaluated,
                duration_candles=duration,
                realized_r=None,
                mfe_r=mfe_r,
                mae_r=mae_r,
                validation_timestamp=last_valid_timestamp,
                reason="Signal expired before reaching TP or SL.",
                details=(
                    "Entry was triggered.",
                    "Neither take profit nor stop loss was reached.",
                ),
            )

        # -----------------------------------------------------
        # ENTRY NOT TRIGGERED
        # -----------------------------------------------------

        return ValidationResult(
            status=ValidationStatus.NOT_TRIGGERED,
            exit_reason=ExitReason.NOT_TRIGGERED,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_triggered=False,
            exit_price=None,
            candles_evaluated=candles_evaluated,
            duration_candles=0,
            realized_r=None,
            mfe_r=0.0,
            mae_r=0.0,
            validation_timestamp=last_valid_timestamp,
            reason="Entry was not triggered within the validation window.",
            details=(
                "No candle reached the entry price.",
            ),
        )

    # =========================================================
    # RESULT BUILDERS
    # =========================================================

    def _invalid_price_result(
        self,
        *,
        entry: float | None,
        stop_loss: float | None,
        take_profit: float | None,
        reason: str = "Signal does not contain valid price levels.",
        detail: str = (
            "Entry, stop loss and take profit are required."
        ),
    ) -> ValidationResult:

        return ValidationResult(
            status=ValidationStatus.NOT_TRIGGERED,
            exit_reason=ExitReason.NOT_TRIGGERED,
            entry_price=entry or 0.0,
            stop_loss=stop_loss or 0.0,
            take_profit=take_profit or 0.0,
            entry_triggered=False,
            exit_price=None,
            candles_evaluated=0,
            duration_candles=0,
            realized_r=None,
            mfe_r=0.0,
            mae_r=0.0,
            validation_timestamp=None,
            reason=reason,
            details=(detail,),
        )

    def _win_result(
        self,
        *,
        entry: float,
        stop_loss: float,
        take_profit: float,
        candles_evaluated: int,
        duration_candles: int,
        mfe_r: float,
        mae_r: float,
        timestamp: object | None,
    ) -> ValidationResult:

        risk = self._risk_per_unit(
            entry,
            stop_loss,
        )

        realized_r = (
            self._profit_distance(
                entry,
                take_profit,
            )
            / risk
            if risk > 0
            else None
        )

        return ValidationResult(
            status=ValidationStatus.WIN,
            exit_reason=ExitReason.TAKE_PROFIT,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_triggered=True,
            exit_price=take_profit,
            candles_evaluated=candles_evaluated,
            duration_candles=duration_candles,
            realized_r=realized_r,
            mfe_r=max(
                mfe_r,
                realized_r or 0.0,
            ),
            mae_r=mae_r,
            validation_timestamp=timestamp,
            reason="Take profit was reached.",
            details=(
                "Signal reached the target.",
                "Validation result is profitable.",
            ),
        )

    def _loss_result(
        self,
        *,
        entry: float,
        stop_loss: float,
        take_profit: float,
        candles_evaluated: int,
        duration_candles: int,
        mfe_r: float,
        mae_r: float,
        timestamp: object | None,
    ) -> ValidationResult:

        return ValidationResult(
            status=ValidationStatus.LOSS,
            exit_reason=ExitReason.STOP_LOSS,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_triggered=True,
            exit_price=stop_loss,
            candles_evaluated=candles_evaluated,
            duration_candles=duration_candles,
            realized_r=-1.0,
            mfe_r=mfe_r,
            mae_r=min(
                mae_r,
                -1.0,
            ),
            validation_timestamp=timestamp,
            reason="Stop loss was reached.",
            details=(
                "Signal reached the invalidation level.",
                "Validation result is a loss.",
            ),
        )

    def _ambiguous_result(
        self,
        *,
        entry: float,
        stop_loss: float,
        take_profit: float,
        candles_evaluated: int,
        duration_candles: int,
        mfe_r: float,
        mae_r: float,
        candle: Any,
    ) -> ValidationResult:

        return ValidationResult(
            status=ValidationStatus.AMBIGUOUS,
            exit_reason=ExitReason.BOTH_TOUCHED,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_triggered=True,
            exit_price=None,
            candles_evaluated=candles_evaluated,
            duration_candles=duration_candles,
            realized_r=None,
            mfe_r=mfe_r,
            mae_r=mae_r,
            validation_timestamp=self._candle_timestamp(candle),
            reason=(
                "Both take profit and stop loss were "
                "touched within the same candle."
            ),
            details=(
                "OHLC data cannot determine which level was touched first.",
                "Result is therefore classified as ambiguous.",
            ),
        )

    # =========================================================
    # DIRECTION
    # =========================================================

    def _get_direction(
        self,
        signal: Any,
    ) -> str:

        value = getattr(
            signal,
            "direction",
            None,
        )

        if value is None:
            return ""

        return str(
            getattr(
                value,
                "value",
                value,
            )
        ).upper()

    # =========================================================
    # PRICE HELPERS
    # =========================================================

    def _get_price(
        self,
        signal: Any,
        name: str,
    ) -> float | None:

        value = getattr(
            signal,
            name,
            None,
        )

        if value is None:
            return None

        try:
            value = float(value)
        except (TypeError, ValueError):
            return None

        if value <= 0:
            return None

        return value

    def _valid_price_geometry(
        self,
        direction: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> bool:

        if direction == "LONG":
            return (
                stop_loss < entry
                and take_profit > entry
            )

        if direction == "SHORT":
            return (
                stop_loss > entry
                and take_profit < entry
            )

        return False

    def _candle_high(
        self,
        candle: Any,
    ) -> float | None:

        return self._numeric_attribute(
            candle,
            "high",
        )

    def _candle_low(
        self,
        candle: Any,
    ) -> float | None:

        return self._numeric_attribute(
            candle,
            "low",
        )

    def _numeric_attribute(
        self,
        object_: Any,
        name: str,
    ) -> float | None:

        value = getattr(
            object_,
            name,
            None,
        )

        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # =========================================================
    # ENTRY
    # =========================================================

    def _entry_touched(
        self,
        direction: str,
        entry: float,
        high: float,
        low: float,
    ) -> bool:
        """
        Entry uses OHLC-touch semantics.

        The direction argument is retained deliberately so
        the method remains explicit about signal direction.
        """

        return low <= entry <= high

    # =========================================================
    # TARGET / STOP
    # =========================================================

    def _take_profit_touched(
        self,
        direction: str,
        take_profit: float,
        high: float,
        low: float,
    ) -> bool:

        if direction == "LONG":
            return high >= take_profit

        if direction == "SHORT":
            return low <= take_profit

        return False

    def _stop_loss_touched(
        self,
        direction: str,
        stop_loss: float,
        high: float,
        low: float,
    ) -> bool:

        if direction == "LONG":
            return low <= stop_loss

        if direction == "SHORT":
            return high >= stop_loss

        return False

    # =========================================================
    # RISK
    # =========================================================

    def _risk_per_unit(
        self,
        entry: float,
        stop_loss: float,
    ) -> float:

        return abs(
            entry - stop_loss
        )

    def _profit_distance(
        self,
        entry: float,
        target: float,
    ) -> float:

        return abs(
            target - entry
        )

    # =========================================================
    # MFE / MAE
    # =========================================================

    def _calculate_mfe(
        self,
        direction: str,
        entry: float,
        high: float,
        low: float,
        stop_loss: float,
    ) -> float:

        risk = self._risk_per_unit(
            entry,
            stop_loss,
        )

        if risk <= 0:
            return 0.0

        if direction == "LONG":
            favorable_distance = high - entry

        elif direction == "SHORT":
            favorable_distance = entry - low

        else:
            return 0.0

        return max(
            0.0,
            favorable_distance / risk,
        )

    def _calculate_mae(
        self,
        direction: str,
        entry: float,
        high: float,
        low: float,
        stop_loss: float,
    ) -> float:

        risk = self._risk_per_unit(
            entry,
            stop_loss,
        )

        if risk <= 0:
            return 0.0

        if direction == "LONG":
            adverse_distance = low - entry

        elif direction == "SHORT":
            adverse_distance = entry - high

        else:
            return 0.0

        return min(
            0.0,
            adverse_distance / risk,
        )

    # =========================================================
    # TIMESTAMP
    # =========================================================

    def _candle_timestamp(
        self,
        candle: Any,
    ) -> object | None:

        for name in (
            "timestamp",
            "time",
            "datetime",
            "date",
        ):
            value = getattr(
                candle,
                name,
                None,
            )

            if value is not None:
                return value

        return None