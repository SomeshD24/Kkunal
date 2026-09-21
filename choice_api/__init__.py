from .client import ChoiceClient, BASE_URL_OMNE, BASE_URL_FINX
from .orders import OrdersAPI
from .portfolio import PortfolioAPI
from .funds import FundsAPI
from .market import MarketAPI
from .historical import HistoricalAPI
from .scrip_master import ScripMaster
from .websockets_interactive import InteractiveSocketClient
from .websockets_feed import PriceFeedSocketClient
from .exceptions import (
    ChoiceAPIError,
    AuthenticationError,
    APIResponseError,
    NetworkError,
    InvalidResponseError,
    ScripMasterError,
    WebSocketError,
)
from .constants import (
    Segment,
    Side,
    Validity,
    OrderType,
    ProductType,
    Resolution,
    to_paisa,
    to_rupees,
)
from .indicators import (
    IndicatorsAPI,
    INDICATOR_ALIASES,
    resolve_indicator,
    # Signal Utilities
    crossover,
    crossunder,
    # Moving Averages & Trend
    sma,
    ema,
    dema,
    tema,
    wma,
    macd,
    adx,
    supertrend,
    parabolic_sar,
    ichimoku,
    # Oscillators & Momentum
    rsi,
    stochastic,
    cci,
    williams_r,
    # Volatility
    bollinger_bands,
    atr,
    donchian_channel,
    # Volume
    vwap,
    obv,
    # Candle Transformations & Levels
    heikin_ashi,
    pivot_points,
)

__version__ = "1.4.0"
__all__ = [
    "ChoiceClient",
    "BASE_URL_OMNE",
    "BASE_URL_FINX",
    "OrdersAPI",
    "PortfolioAPI",
    "FundsAPI",
    "MarketAPI",
    "HistoricalAPI",
    "ScripMaster",
    "InteractiveSocketClient",
    "PriceFeedSocketClient",
    "IndicatorsAPI",
    # Exceptions
    "ChoiceAPIError",
    "AuthenticationError",
    "APIResponseError",
    "NetworkError",
    "InvalidResponseError",
    "ScripMasterError",
    "WebSocketError",
    # Constants
    "Segment",
    "Side",
    "Validity",
    "OrderType",
    "ProductType",
    "Resolution",
    "to_paisa",
    "to_rupees",
    # Indicator name resolution
    "INDICATOR_ALIASES",
    "resolve_indicator",
    # Signal Utilities
    "crossover",
    "crossunder",
    # Moving Averages & Trend
    "sma",
    "ema",
    "dema",
    "tema",
    "wma",
    "macd",
    "adx",
    "supertrend",
    "parabolic_sar",
    "ichimoku",
    # Oscillators & Momentum
    "rsi",
    "stochastic",
    "cci",
    "williams_r",
    # Volatility
    "bollinger_bands",
    "atr",
    "donchian_channel",
    # Volume
    "vwap",
    "obv",
    # Candle Transformations & Levels
    "heikin_ashi",
    "pivot_points",
]
