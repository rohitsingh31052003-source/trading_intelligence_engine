from datetime import datetime

from engine.intelligence.liquidity import LiquidityEngine
from engine.models.swing import (
    SwingPoint,
    SwingStatus,
    SwingStrength,
    SwingType,
)
from engine.models.swing_evidence import SwingEvidence


def make_swing(price, swing_type):

    return SwingPoint(
        timestamp=datetime.now(),
        index=0,
        price=price,
        swing_type=swing_type,
        confirmation_index=5,
        confirmed=True,
        status=SwingStatus.CONFIRMED,
        strength=SwingStrength.STRONG,
        evidence=SwingEvidence(),
    )


swings = [

    make_swing(1325.50, SwingType.HIGH),
    make_swing(1326.00, SwingType.HIGH),
    make_swing(1326.05, SwingType.HIGH),

    make_swing(1216.50, SwingType.LOW),
    make_swing(1217.00, SwingType.LOW),

]

engine = LiquidityEngine()

pools = engine.detect(swings)

print("\n=== Liquidity Pools ===\n")

for pool in pools:

    print(pool.liquidity_type.value.replace("_", " "))
    print()

    print(f"Price        : {pool.price:.2f}")
    print(f"Swings       : {pool.swing_count}")
    print(f"Strength     : {pool.strength:.0f}")
    print(f"Category     : {pool.category.name}")
    print(f"Status       : {pool.status.name}")

    print("\n--------------------------------\n")