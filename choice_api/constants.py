"""
Named constants for the magic numbers and short codes the Choice API expects.

Every one is an IntEnum or str subclass, so it can be passed straight to the
API methods wherever a plain int or str is accepted:

    >>> from choice_api import Segment, Side, OrderType, ProductType, Validity
    >>> client.orders.place_order(
    ...     segment_id=Segment.NSE_FO,
    ...     token=48552,
    ...     order_type=OrderType.LIMIT,
    ...     bs=Side.BUY,
    ...     qty=50,
    ...     price=130000,
    ...     trigger_price=0,
    ...     validity=Validity.DAY,
    ...     product_type=ProductType.INTRADAY,
    ... )

Prices and trigger prices are in **paisa**, not rupees: 1300.00 INR is 130000.
Use `to_paisa()` / `to_rupees()` to convert.
"""

from enum import IntEnum


class Segment(IntEnum):
    """Exchange segment identifiers."""
    NSE_CASH = 1
    NSE_FO = 2
    BSE_CASH = 3
    BSE_FO = 4
    NSE_CURRENCY = 13
    MCX = 5


class Side(IntEnum):
    """Order side."""
    BUY = 1
    SELL = 2


class Validity(IntEnum):
    """Order validity."""
    DAY = 1
    IOC = 2


class _StrConst(str):
    """A str subclass so these constants pass straight through to the API."""
    __slots__ = ()


class OrderType:
    """
    Order types accepted by the API.

    Market orders are not supported over the API; use LIMIT with a price that
    will fill, or STOP_LOSS_LIMIT with a trigger.
    """
    LIMIT = _StrConst("RL_LIMIT")
    STOP_LOSS_LIMIT = _StrConst("SL_LIMIT")


class ProductType:
    """Product type: intraday (margin) or delivery (carry-forward)."""
    INTRADAY = _StrConst("M")
    DELIVERY = _StrConst("D")


class Resolution:
    """Candle intervals accepted by HistoricalAPI.get_historical_data."""
    MIN_1 = _StrConst("1")
    MIN_3 = _StrConst("3")
    MIN_5 = _StrConst("5")
    MIN_10 = _StrConst("10")
    MIN_15 = _StrConst("15")
    MIN_30 = _StrConst("30")
    HOUR_1 = _StrConst("60")
    DAY = _StrConst("D")
    WEEK = _StrConst("W")
    MONTH = _StrConst("M")


def to_paisa(rupees: float) -> int:
    """Converts rupees to the paisa integer the order API expects (1300.5 -> 130050)."""
    return int(round(float(rupees) * 100))


def to_rupees(paisa: float) -> float:
    """Converts paisa from the API back to rupees (130050 -> 1300.5)."""
    return float(paisa) / 100.0


__all__ = [
    "Segment",
    "Side",
    "Validity",
    "OrderType",
    "ProductType",
    "Resolution",
    "to_paisa",
    "to_rupees",
]
