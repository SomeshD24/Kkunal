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

Only values confirmed against the API documentation or the live scrip master
are listed here. Anything else can still be passed as a plain int/str.
"""

import datetime as _dt
from decimal import Decimal, ROUND_HALF_UP
from enum import IntEnum

#: Indian Standard Time. Exchange sessions, contract expiries and the API's own
#: timestamps are all IST, and a Choice session is valid for one IST trading day,
#: so the library reasons in IST rather than in the machine's local timezone.
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def ist_now() -> _dt.datetime:
    """The current moment in IST, regardless of the machine's timezone."""
    return _dt.datetime.now(tz=IST)


def ist_today() -> _dt.date:
    """
    Today's date in IST - the exchange's trading day.

    Using the local date instead would, on a machine more than a few hours from
    IST, place a still-valid session on the wrong calendar day and force a
    needless re-login (and so another OTP).
    """
    return ist_now().date()


class Segment(IntEnum):
    """Exchange segment identifiers, as published in the daily scrip master."""
    NSE_CASH = 1
    NSE_FO = 2
    BSE_CASH = 3
    BSE_FO = 4
    MCX = 5
    MCX_SPOT = 6
    NCDEX = 7
    NCDEX_SPOT = 8
    NSE_CURRENCY = 13
    NSE_CURRENCY_SPOT = 14


class Side(IntEnum):
    """Order side."""
    BUY = 1
    SELL = 2


class Validity(IntEnum):
    """Order validity."""
    DAY = 1


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


class OptionType:
    """Option right, as it appears in the scrip master."""
    CALL = _StrConst("CE")
    PUT = _StrConst("PE")


class Resolution:
    """
    Candle intervals accepted by ChartData.

    These are the only intraday codes the API accepts - notably there is no
    2- or 3-minute interval, and asking for one fails the whole request.
    """
    MIN_1 = _StrConst("1")
    MIN_5 = _StrConst("5")
    MIN_10 = _StrConst("10")
    MIN_15 = _StrConst("15")
    MIN_30 = _StrConst("30")
    HOUR_1 = _StrConst("60")
    DAY = _StrConst("D")
    WEEK = _StrConst("W")
    MONTH = _StrConst("M")


#: Every Interval code ChartData documents (intraday minutes, then end-of-day).
VALID_RESOLUTIONS = ("1", "5", "10", "15", "30", "60", "D", "W", "M", "Q", "H", "Y", "T", "F")

_RESOLUTION_ALIASES = {
    "1m": "1", "1min": "1", "minute": "1",
    "5m": "5", "5min": "5",
    "10m": "10", "10min": "10",
    "15m": "15", "15min": "15",
    "30m": "30", "30min": "30",
    "1h": "60", "60m": "60", "60min": "60", "hour": "60", "hourly": "60",
    "d": "D", "1d": "D", "day": "D", "daily": "D",
    "w": "W", "1w": "W", "week": "W", "weekly": "W",
    "mo": "M", "1mo": "M", "month": "M", "monthly": "M",
}


def normalize_resolution(resolution) -> str:
    """
    Maps a friendly name ('5m', '1h', 'daily') or a Resolution constant onto the
    code ChartData expects, and rejects codes the API does not support.

    The API's own single-letter codes are case-sensitive here on purpose: 'M' is
    monthly and 'H' is a long end-of-day interval, so a bare 'm' or 'h' is
    rejected as ambiguous rather than guessed at. Write '5m' / '1h' for intraday.
    """
    raw = str(resolution).strip()
    if raw in VALID_RESOLUTIONS:
        return raw
    alias = _RESOLUTION_ALIASES.get(raw.lower())
    if alias:
        return alias
    raise ValueError(
        f"Unsupported resolution {resolution!r}. ChartData accepts: "
        f"{', '.join(VALID_RESOLUTIONS)} (or aliases such as '5m', '1h', 'daily')."
    )


def to_paisa(rupees: float) -> int:
    """
    Converts rupees to the paisa integer the order API expects (1300.5 -> 130050).

    Exact half-paisa values round away from zero, the usual convention for money.
    Python's built-in round() is half-to-EVEN, which would make the result
    unpredictable at a boundary - 2.685 down to 268 but 2.675 up to 268 - and
    would turn 0.005 into 0. The value is read as a decimal string, so the
    binary representation of the float cannot shift the result either.
    """
    return int(Decimal(str(rupees)).scaleb(2).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_rupees(paisa: float) -> float:
    """Converts paisa from the API back to rupees (130050 -> 1300.5)."""
    return float(paisa) / 100.0


__all__ = [
    "IST",
    "ist_now",
    "ist_today",
    "Segment",
    "Side",
    "Validity",
    "OrderType",
    "ProductType",
    "OptionType",
    "Resolution",
    "VALID_RESOLUTIONS",
    "normalize_resolution",
    "to_paisa",
    "to_rupees",
]
