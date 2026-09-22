# Kkunal

A Python library for the Choice FINX Trading API. Supports REST API, Interactive WebSockets (order/trade updates), and Live Price Feed WebSockets (FIX3.0 compressed data), with a built-in technical indicators module whose values match TA-Lib and TradingView.

## Installation

```bash
pip install kkunal
```

All dependencies (`requests`, `websockets`, `websocket-client`, `pandas`, `numpy`) are installed automatically. Python 3.9+.

---

## Quick Start

```python
from choice_api import ChoiceClient, BASE_URL_OMNE, BASE_URL_FINX

# Default endpoint (https://finxomne.choiceindia.com)
client = ChoiceClient(
    vendor_id="YOUR_VENDOR_ID",
    api_key="YOUR_JWT_BEARER_TOKEN"
)

# Alternate endpoint (https://finx.choiceindia.com)
client = ChoiceClient(
    vendor_id="YOUR_VENDOR_ID",
    api_key="YOUR_JWT_BEARER_TOKEN",
    base_url=BASE_URL_FINX
)

# Login (TOTP flow is handled automatically)
session_id = client.login(mobile_no="1234567890")
print(f"Session ID: {session_id}")
```

### Keep credentials out of your code

Never paste an API key into a script or notebook you might share or commit. Put it in the
environment instead:

```bash
export CHOICE_VENDOR_ID="..."      # PowerShell:  $env:CHOICE_VENDOR_ID = "..."
export CHOICE_API_KEY="..."
export CHOICE_MOBILE_NO="..."
```

```python
client = ChoiceClient.from_env()
client.login()                     # mobile number comes from CHOICE_MOBILE_NO
```

The library itself never prints a credential: exception messages and log lines are scrubbed of
the API key, session id, access token, OTPs and JWTs.

### Session persistence

Pass `session_file=` and the session is reused for the rest of the day instead of logging in
again. Sessions expire daily, so a file from another date is ignored:

```python
client.login(mobile_no="1234567890", session_file="session.json")
client.login(mobile_no="1234567890", session_file="session.json", force=True)   # ignore the saved one
```

If a saved session turns out to be dead (logged off elsewhere, expired early), the first request
that is rejected triggers one transparent re-login and is replayed - you do not have to handle it.
`client.save_session(path)` / `client.load_session(path)` are available for manual control. The
file holds a live session id, so it is written atomically and readable by your user only.

`ChoiceClient` is also a context manager:

```python
with ChoiceClient.from_env() as client:
    client.login()
    print(client.is_authenticated)   # True
    ...
# logged off automatically on exit
```

With `session_file=` the session is deliberately **left alive** on exit so the next run can reuse
it; call `client.logoff()` yourself to end it (that also deletes the saved file).

### Reliability

| Behaviour | Default | Change it with |
|---|---|---|
| HTTP timeout | 30 s | `ChoiceClient(..., timeout=60)` or `timeout=(5, 30)` for connect/read |
| Retries on timeout, HTTP 429 and 5xx, with backoff (`Retry-After` is honoured) | 2 | `max_retries=` |
| Failover between the two Choice gateways when one is unreachable | on | `failover=False` |
| Re-login and replay when the session is rejected mid-day | on | `auto_relogin=False` |
| Client-side rate limiting (requests per second) | off | `rate_limit=3`, `order_rate_limit=5` |
| Raise when a response body says `Status != "Success"` | off | `raise_on_error=True` |

Only **retry-safe** requests are retried: GETs and read-only POSTs such as historical data,
touchlines and margin. Anything that places, modifies or cancels an order, or moves funds, is
**never** retried automatically - a request that timed out may still have reached the exchange,
so check the order book before sending it again.

> Exchanges require strategies that send 10 or more orders per second to be registered as
> algos. `order_rate_limit=` is an easy guard rail to stay under that.

To see what the library is doing (retries, failover, re-logins, cache hits):

```python
import logging
logging.basicConfig(level=logging.INFO)
```

---

## Constants

Named constants replace the magic numbers and short codes the API expects:

```python
from choice_api import Segment, Side, OrderType, ProductType, Validity, Resolution, to_paisa

client.orders.place_order(
    segment_id=Segment.NSE_FO,          # 2
    token=48552,
    order_type=OrderType.LIMIT,         # 'RL_LIMIT'
    bs=Side.BUY,                        # 1
    qty=15,
    price=to_paisa(1300.50),            # 130050
    trigger_price=0,
    validity=Validity.DAY,              # 1
    product_type=ProductType.INTRADAY,  # 'M'
)
```

| Constant | Values |
|---|---|
| `Segment` | `NSE_CASH` (1), `NSE_FO` (2), `BSE_CASH` (3), `BSE_FO` (4), `MCX` (5), `MCX_SPOT` (6), `NCDEX` (7), `NCDEX_SPOT` (8), `NSE_CURRENCY` (13), `NSE_CURRENCY_SPOT` (14) |
| `Side` | `BUY`, `SELL` |
| `OrderType` | `LIMIT`, `STOP_LOSS_LIMIT` |
| `ProductType` | `INTRADAY`, `DELIVERY` |
| `OptionType` | `CALL` (`"CE"`), `PUT` (`"PE"`) |
| `Validity` | `DAY` |
| `Resolution` | `MIN_1`, `MIN_5`, `MIN_10`, `MIN_15`, `MIN_30`, `HOUR_1`, `DAY`, `WEEK`, `MONTH` |

Only values confirmed against the API or the live scrip master are listed; anything else can
still be passed as a plain int/str. Note there is **no 2- or 3-minute candle interval** - the
API rejects it.

Prices are in **paisa**, not rupees. `to_paisa(1300.50)` → `130050`, and
`to_rupees(130050)` → `1300.50`.

---

## Error Handling

Every failure raises a typed exception deriving from `ChoiceAPIError`, so you can
tell an expired session apart from a network blip or a rejected order:

```python
from choice_api import ChoiceAPIError, AuthenticationError, NetworkError, RateLimitError

try:
    client.orders.place_order(...)
except AuthenticationError:
    client.login(mobile_no="1234567890", force=True)
except RateLimitError as e:
    time.sleep(e.retry_after or 1)
except NetworkError:
    ...     # for an ORDER: check the order book first - it may have gone through
except ChoiceAPIError as e:
    print(e.status_code, e.endpoint, e.response)
```

| Exception | Raised when |
|---|---|
| `AuthenticationError` | Login failed, or the session is missing/expired (401/403) |
| `StaticIPError` | The request did not come from the static IP registered for your API key - the usual cause when running from a new network or a VPN. The message says where to fix it. Subclass of `AuthenticationError`. |
| `APIResponseError` | The request reached the API but was rejected |
| `RateLimitError` | HTTP 429. `retry_after` holds the wait in seconds, when the broker gives one |
| `HistoricalDataError` | ChartData failed - as opposed to simply having no candles |
| `OrderValidationError` | An order was rejected locally, before being sent (also a `ValueError`) |
| `NetworkError` | Timeout, DNS failure or connection reset |
| `InvalidResponseError` | A 2xx response whose body was not valid JSON |
| `AmbiguousSymbolError` | A symbol matched several instruments (also a `KeyError`) |
| `ScripMasterError` | The daily scrip master could not be downloaded or parsed |
| `WebSocketError` | A live feed or interactive socket failed |

---

## Scrip Master

The Scrip Master CSV is downloaded automatically when you log in. It maps instrument symbols to
their tokens, lot sizes, expiries and strikes. It is cached on disk per day, so repeated runs
reuse it instead of re-downloading several megabytes. Pass `fetch(force=True)` to refresh, or
`ScripMaster(cache_dir=...)` to control where it lives.

### `resolve(symbol, segment=None)`

Resolves a symbol straight to the `(segment_id, token)` pair the other APIs want.

```python
client.scrip_master.resolve("RELIANCE")                              # (1, 2885)  NSE first, then BSE
client.scrip_master.resolve("RELIANCE", segment=Segment.BSE_CASH)    # (3, 500325)
client.scrip_master.resolve("NIFTY")                                 # (1, 26000) the index itself
client.scrip_master.resolve("NIFTY26SEPFUT")                         # (2, 68407) exact SecDesc
```

It **never guesses between instruments**. `resolve("NIFTY", segment=Segment.NSE_FO)` matches
thousands of contracts, so it raises `AmbiguousSymbolError` (with a few `candidates`) rather than
hand you an arbitrary one - use the futures and options helpers below. An unknown symbol raises
`KeyError`.

### Futures and options

```python
sm = client.scrip_master

fut = sm.find_future("NIFTY")                        # nearest unexpired future
fut = sm.find_future("RELIANCE", expiry="2026-10-27")

opt = sm.find_option("NIFTY", 23750, "CE")           # nearest expiry (the weekly)
opt = sm.find_option("NIFTY", 23750, OptionType.PUT, expiry="29SEP26")

sm.expiries("NIFTY")                   # [date(2026, 9, 22), date(2026, 9, 29), ...]
sm.expiries("NIFTY", options=False)    # futures expiries
sm.strikes("NIFTY", option_type="CE")  # [17850.0, 17900.0, ...]  in rupees
sm.option_chain("NIFTY")               # every contract of the nearest expiry, sorted by strike
```

Each contract is a dict ready to feed into the other APIs:

```python
{'Token': 57082, 'Segment': 2, 'Exchange': 'NSEFO', 'Symbol': 'NIFTY', 'SecDesc': 'NIFTY2692223750CE',
 'Instrument': 'OPTIDX', 'Expiry': date(2026, 9, 22), 'StrikePrice': 23750.0, 'OptionType': 'CE',
 'MarketLot': 65, 'PriceDivisor': 100.0}

client.orders.place_order(segment_id=opt["Segment"], token=opt["Token"], qty=opt["MarketLot"], ...)
client.historical.get_historical_data(opt["Segment"], opt["Token"], "2026-09-01", "2026-09-21", "5m")
```

Strikes are in **rupees** (the raw file scales them by `PriceDivisor`). Expiries can be given as a
`date`, `"YYYY-MM-DD"` or the scrip master's own `"29SEP26"`. A strike that is not listed raises
`KeyError` naming the nearest ones that are.

### `get_token(symbol, segment=None)`

Looks up tokens for a given symbol or description.

- **Without `segment`**: Returns a **list of dicts** for ALL matching rows across every segment (NSE, BSE, F&O, etc.). Each dict contains `Token`, `Exchange`, `Segment`, `Symbol`, `SecDesc`, `Series`, `MarketLot`, `Instrument`, `Expiry`, `StrikePrice`, `OptionType`.
- **With `segment`** (e.g., `"1"`, `"3"`): Returns a **single token string** for the first match in that segment, or `None` if not found. For a derivative's underlying that first match is an arbitrary contract - use `find_future()` / `find_option()` instead.

```python
# Get all matches across all segments
matches = client.scrip_master.get_token("RELIANCE")
for m in matches:
    print(f"Segment: {m['Segment']} — Token: {m['Token']}, Symbol: {m['Symbol']}")
# Segment: 1 — Token: 2885, Symbol: RELIANCE      (NSE)
# Segment: 3 — Token: 500325, Symbol: RELIANCE    (BSE)
# Segment: 2 — ...                                (every RELIANCE future and option)

# Get specific segment token
nse_token = client.scrip_master.get_token("RELIANCE", segment="1")
bse_token = client.scrip_master.get_token("RELIANCE", segment="3")
```

### `search(name, segment=None, instrument=None, limit=None)`

Case-insensitive fuzzy search: returns all rows where Symbol or SecDesc **contains** the given name.
A broad term such as `"NIFTY"` matches thousands of option contracts, so narrow it down:

```python
results = client.scrip_master.search("NIFTY", segment=Segment.NSE_FO, instrument="FUTIDX", limit=10)
for r in results:
    print(f"{r['Exchange']} | {r['SecDesc']} | Token: {r['Token']} | Expiry: {r['Expiry']}")
```

### `get_details(token)`

Returns all CSV row details for a given token as a dictionary.

```python
details = client.scrip_master.get_details("2885")
print(details)
```

### `get_lot_size(token)`

Returns the market lot size for a token.

```python
lot = client.scrip_master.get_lot_size("2885")
print(lot)  # 1 for equity, 65 for NIFTY futures, etc.
```

---

## Orders

> **Important:** Prices must be in **paisa** (multiply INR by 100). For F&O orders, `qty` must be in **total shares** (multiples of the lot size), not the number of lots.
>
> Orders are checked locally before they are sent - side, quantity and prices - and an obviously malformed
> one raises `OrderValidationError` without touching the network. A fractional price logs a warning, since
> paisa are whole numbers and `1300.5` usually means rupees were passed by mistake (use `to_paisa()`).
> Order requests are **never retried automatically**: if one times out, check the order book before resending.

### `client.orders.place_order(...)`

| Parameter | Type | Description |
|---|---|---|
| `segment_id` | `int` | `1` = NSE Cash, `2` = NSE F&O, `3` = BSE Cash |
| `token` | `int` | Instrument token from Scrip Master |
| `order_type` | `str` | `"RL_LIMIT"` = Limit, `"SL_LIMIT"` = Stop Loss Limit *(Note: Market orders are not supported via API)* |
| `bs` | `int` | `1` = Buy, `2` = Sell |
| `qty` | `int` | Total quantity in shares |
| `price` | `float` | Price in paisa (e.g., 1300 INR → `130000`) |
| `trigger_price` | `float` | Trigger price in paisa (0 for non-SL orders) |
| `validity` | `int` | `1` = Day |
| `product_type` | `str` | `"M"` = Intraday (Margin), `"D"` = Delivery/CarryForward |
| `disclosed_qty` | `int` | Optional. Disclosed quantity (default `0`) |
| `client_order_no` | `int` | Optional. Your own reference number, used later by `modify_order`/`cancel_order`. A unique one is generated if omitted. |

```python
response = client.orders.place_order(
    segment_id=1,
    token=2885,
    order_type="RL_LIMIT",
    bs=1,
    qty=1,
    price=130000,
    trigger_price=0,
    validity=1,
    product_type="D"
)
```

### `client.orders.modify_order(...)`

Modifies an existing order. Requires `client_order_no`, `exchange_order_no`, and `gateway_order_no` from the order book.

```python
response = client.orders.modify_order(
    client_order_no=123456,
    exchange_order_no="1234567890",
    gateway_order_no="1234567890",
    segment_id=1,
    token=2885,
    order_type="RL_LIMIT",
    bs=1,
    qty=1,
    price=130000,
    trigger_price=0,
    validity=1,
    product_type="D"
)
```

### `client.orders.cancel_order(...)`

Cancels an existing order. Same parameters as `modify_order` plus optional `exchange_order_time`.

### `client.orders.get_order_book()`

Returns all orders placed during the current session.

```python
order_book = client.orders.get_order_book()
```

### `client.orders.get_order_book_v2()`

Returns the order book (version 2 format).

### `client.orders.get_order_by_no(order_no)`

Returns details for a specific order number.

```python
order = client.orders.get_order_by_no(123456)
```

### `client.orders.get_trade_book()`

Returns all executed trades.

```python
trades = client.orders.get_trade_book()
```

### `client.orders.get_order_messages(req_id)`

Returns order-related messages for a given request ID.

### `client.orders.get_margin(...)` / `calculate_margin(...)`

Convenience method to calculate margin requirements before placing orders. Same syntax as [`client.funds.get_margin`](#clientfundsget_margin--calculatemargin).

```python
margin = client.orders.get_margin(segment_id=2, token=48552, qty=425)
```

---

## Portfolio

### `client.portfolio.get_holdings()`

Returns current holdings.

```python
holdings = client.portfolio.get_holdings()
```

### `client.portfolio.get_net_position()`

Returns net positions.

```python
positions = client.portfolio.get_net_position()
```

### `client.portfolio.position_conversion(...)`

Converts an open position from one product type to another (e.g., Intraday to Delivery).

| Parameter | Type | Description |
|---|---|---|
| `segment_id` | `int` | Exchange segment |
| `token` | `int` | Instrument token |
| `client_order_no` | `int` | Client order number |
| `buy_sell` | `int` | `1` = Buy, `2` = Sell |
| `quantity` | `int` | Quantity to convert |
| `product_type` | `str` | Target product type |
| `source_product_type` | `str` | Current product type |

### `client.portfolio.verify_dis(...)`

Verifies eDIS (Electronic Delivery Instruction Slip) for delivery sell orders.

### `client.portfolio.get_dis_status()`

Returns the current DIS verification status.

---

## Funds

### `client.funds.get_funds_view()`

Returns funds summary.

```python
funds = client.funds.get_funds_view()
```

### `client.funds.get_funds_view_new()`

Returns funds summary in the new format.

### `client.funds.get_margin(...)` / `calculate_margin(...)`

Calculates required margin for single or multiple contracts.

| Parameter | Type | Description |
|---|---|---|
| `segment_id` | `int` | `1` = NSE Cash, `2` = NSE F&O, `3` = BSE Cash |
| `token_qty` | `str` or `list` | Pipe-separated string (`"48552|425"`), tilde-separated string (`"48552|425~48553|100"`), or list of tuples `[(48552, 425), (48553, 100)]` / dicts `[{"token": 48552, "qty": 425}]` |
| `mode` | `int` | Optional. Mode integer (default `1`) |
| `device_id` | `str` | Optional. Device ID string (default `"MAC"`) |
| `token` | `int` / `str` | Optional. Single token shorthand (used with `qty`) |
| `qty` | `int` | Optional. Single quantity shorthand (used with `token`) |

```python
# Single contract using string
margin = client.funds.get_margin(segment_id=2, token_qty="48552|425")

# Single contract using token and qty shorthand
margin = client.funds.get_margin(segment_id=2, token=48552, qty=425)

# Multiple contracts using string
margin = client.funds.get_margin(segment_id=2, token_qty="48552|425~48553|100")

# Multiple contracts using list of tuples
margin = client.funds.get_margin(
    segment_id=2,
    token_qty=[(48552, 425), (48553, 100)]
)

print(margin)
# {"Status": "Success", "Response": { ... }, "Reason": ""}
```

*(Also accessible via `client.orders.get_margin(...)` or `calculate_margin`)*

### `client.funds.process_payout(amount, bank_acc_no, product_type=0)`

Initiates a fund withdrawal.

### `client.funds.payment_via_netbanking(amount, bank_acc_no, bank_ifsc_code, return_url, segment_id, product_type=0)`

Initiates a net banking payment.

### `client.funds.payment_via_hdfc_upi(amount, bank_acc_no, user_vpa, segment_id, product_type=0)`

Initiates a HDFC UPI payment.

### `client.funds.check_vpa(user_vpa)`

Validates a UPI VPA address.

### `client.funds.payment_via_razorpay(amount, bank_acc_no, bank_ifsc_code, upi_id, segment_id, payment_type=0, product_type=0)`

Initiates a RazorPay payment.

### `client.funds.payment_ack_response(transaction_id)`

Acknowledges a payment transaction.

---

## Market

### `client.market.get_market_status()`

Returns current market status across all segments.

```python
status = client.market.get_market_status()
```

### `client.market.get_user_profile()`

Returns the authenticated user's profile.

```python
profile = client.market.get_user_profile()
```

### `client.market.get_multiple_touchline(multiple_seg_token)`

Returns touchline data (last price, best bid/ask) for multiple instruments.

```python
# Raw format: "SegmentId@Token" pairs joined by commas
touchline = client.market.get_multiple_touchline("1@2885,1@11536")

# ...or let the library build it from (segment, token) pairs or contract dicts
touchline = client.market.get_multiple_touchline([(1, 2885), (1, 11536)])
touchline = client.market.get_multiple_touchline([client.scrip_master.find_future("NIFTY")])
```

---

## Historical Data

### `client.historical.get_by_symbol(symbol, from_date, to_date, resolution='D', segment=None, indicators=None)`

Fetches candles by symbol, resolving the token through the Scrip Master — no token
lookup needed.

```python
df = client.historical.get_by_symbol("RELIANCE", "2024-01-01", "2024-06-01")

# with indicators in the same call
df = client.historical.get_by_symbol(
    "RELIANCE", "2024-01-01", "2024-06-01",
    resolution=Resolution.DAY,
    indicators=["rsi", "macd", "supertrend"],
)

# a derivative: pass its exact SecDesc, or use find_future() / find_option()
df = client.historical.get_by_symbol("NIFTY26SEPFUT", "2026-09-01", "2026-09-21", "5m")
```

### `client.historical.get_historical_data(segment_id, token, from_date, to_date, resolution)`

Returns historical OHLCV data as a **Pandas DataFrame**.

| Parameter | Type | Description |
|---|---|---|
| `segment_id` | `int` | Exchange segment |
| `token` | `int` | Instrument token |
| `from_date` | `str`, `date`, `datetime` or `int` | Start: `"YYYY-MM-DD"`, `"YYYY-MM-DD HH:MM:SS"`, a date/datetime/Timestamp, or seconds from 1980 |
| `to_date` | same | End. A value **without a time means the end of that day**, so its intraday candles are included |
| `resolution` | `str` | A `Resolution` constant, an API code (`"1"`, `"5"`, `"10"`, `"15"`, `"30"`, `"60"`, `"D"`, `"W"`, `"M"`) or an alias (`"5m"`, `"1h"`, `"daily"`) |
| `allow_partial` | `bool` | See below. Default `True` |

```python
df = client.historical.get_historical_data(
    segment_id=1,
    token=2885,
    from_date="2024-01-01",
    to_date="2024-12-31",
    resolution="D"
)
print(df.head())
#                   Time     Open     High      Low    Close   Volume  OI
# 0  2024-01-01 00:00:00  2501.00  2520.50  2490.00  2515.30  1234567   0
```

The returned DataFrame has columns: `Time`, `Open`, `High`, `Low`, `Close`, `Volume`, `OI`, sorted by
time with duplicates removed. `Time` is IST wall-clock. Prices are automatically adjusted using the
`PriceDivisor` from the API response.

**Long ranges just work.** The API caps how much history a single call may cover (and answers an
over-long request with an empty result, not an error), so a long range is fetched in windows -
7 days at a time for 1-minute candles, up to a year for daily - and stitched back together:

```python
df = client.historical.get_historical_data(1, 2885, "2023-01-01", "2024-12-31", "5m")   # ~25 requests
```

**A failure is never disguised as "no data".** If the API rejects the request - bad token, expired
session, unsupported interval - `HistoricalDataError` is raised with the broker's own message. An
empty DataFrame (which still has its columns) means the window genuinely has no candles, such as
a market holiday. When a long range is fetched in several windows and only some fail, the rest are
kept, a warning is logged and the gaps are listed in `df.attrs["failed_windows"]`; pass
`allow_partial=False` to raise instead.

---

## Technical Indicators

`kkunal` includes a built-in vectorized Technical Analysis indicator engine based on `pandas` and `numpy`. No external C-dependencies required.

**Values match TA-Lib and TradingView bar for bar, warmup included.** The test suite checks every
smoothed indicator against independent reference implementations, and against TA-Lib itself when
it is installed: SMA, EMA, DEMA, TEMA, WMA, RSI, CCI, Williams %R, MFI, ROC, Bollinger Bands,
Stochastic and Parabolic SAR agree to 1e-12. Where TA-Lib and TradingView differ from each other
in how they seed the first window (ADX, MACD, ATR), TradingView's convention is used; the
difference vanishes after the first ~100 bars.

### Supported Indicators

| Category | Indicators |
|---|---|
| **Trend** | SMA, EMA, DEMA, TEMA, WMA, MACD, ADX, Supertrend, Parabolic SAR, Ichimoku Cloud |
| **Momentum** | RSI, Stochastic Oscillator (%K, %D), CCI, Williams %R, MFI, ROC |
| **Volatility** | Bollinger Bands (with %B), ATR, Donchian Channel, Keltner Channel |
| **Volume** | VWAP, OBV |
| **Levels** | Pivot Points (Standard/Fibonacci/Camarilla), Central Pivot Range (CPR) |
| **Utilities** | Crossover, Crossunder, Heikin Ashi |

### Usage Methods

> **Warmup:** a windowed indicator returns `NaN` until its lookback window is full, so a
> value only ever comes from a complete window (`EMA_20` starts at bar 19, `RSI_14` at bar 14,
> `ADX_14` at bar 27). Use `df.dropna()` before feeding results into a strategy. Input must be
> in chronological order, oldest first - which is how `get_historical_data` returns it.

#### 1. Fetch Historical Data with Indicators in One Step
```python
# Every indicator
df = client.historical.get_historical_data_with_indicators(
    segment_id=1, token=2885,
    from_date="2024-01-01", to_date="2024-12-31", resolution="D",
    indicators="all"
)

# Or select specific indicators
df = client.historical.get_historical_data_with_indicators(
    segment_id=1, token=2885,
    from_date="2024-01-01", to_date="2024-12-31", resolution="D",
    indicators=["rsi", "macd", "supertrend", "bb", "ichimoku", "pivot"]
)
```

`indicators` accepts `'all'` (every indicator), `'core'` (the nine most common ones),
or a list of names and shorthands such as `['rsi', 'macd', 'st', 'bb', 'kc', 'cpr']`. An
unrecognised name raises `ValueError` - before any network call is made.

#### 2. Apply via `client.indicators`
```python
df = client.historical.get_historical_data(1, 2885, "2024-01-01", "2024-12-31", "D")

# Add specific indicators
df = client.indicators.add_rsi(df, period=14)
df = client.indicators.add_macd(df)
df = client.indicators.add_supertrend(df, period=10, multiplier=3.0)
df = client.indicators.add_bollinger_bands(df, period=20, std_dev=2.0)
df = client.indicators.add_keltner_channel(df, period=20, multiplier=2.0, atr_period=10)
df = client.indicators.add_mfi(df, period=14)
df = client.indicators.add_ichimoku(df)
df = client.indicators.add_parabolic_sar(df)
df = client.indicators.add_pivot_points(df, method="fibonacci")
df = client.indicators.add_cpr(df)
df = client.indicators.add_heikin_ashi(df)

# Pick indicators by name, chosen at runtime
df = client.indicators.add(df, "rsi", "macd", "supertrend")
df = client.indicators.add(df, "rsi", period=21)     # a misspelt keyword raises TypeError

# Or add every indicator at once (with customizable periods)
df_all = client.indicators.add_all(df, sma_period=50, ema_period=50, rsi_period=21)
df_core = client.indicators.add_all(df, core_only=True)
```

#### 3. Standalone Indicator Functions
```python
from choice_api import rsi, macd, supertrend, bollinger_bands, ichimoku, pivot_points, cpr, mfi, roc, keltner_channel

rsi_series = rsi(df, period=14)
macd_df = macd(df, fast_period=12, slow_period=26, signal_period=9)
st_df = supertrend(df, period=10, multiplier=3.0)
bb_df = bollinger_bands(df, period=20, std_dev=2.0)  # Includes BB_PercentB
kc_df = keltner_channel(df, period=20, multiplier=2.0, atr_period=10)
ichi_df = ichimoku(df)
pp_df = pivot_points(df, method="camarilla")
cpr_df = cpr(df)                                     # CPR_Pivot, CPR_BC, CPR_TC, CPR_Width
```

> **Notes**
> - `bollinger_bands` uses the **population** standard deviation (`ddof=0`), so bands line up with TradingView.
> - `pivot_points` and `cpr` are built from the **previous period's** High/Low/Close, so they are known before the bar opens. On **intraday** data that period is the previous trading **day** - every bar of a session carries the same daily levels, as charting platforms draw them; on daily or slower data it is the previous bar. Override with `anchor="bar"`, `"D"`, `"W"` or `"M"`. The first period is `NaN`.
> - `cpr` always reports `CPR_TC` as the upper edge and `CPR_BC` as the lower one. A small `CPR_Width` (percent of the pivot) often precedes a trending session.

#### 4. Signal Crossover Detection
```python
from choice_api import crossover, crossunder, ema

ema_9 = ema(df, period=9)
ema_21 = ema(df, period=21)

buy_signals = crossover(ema_9, ema_21)    # EMA 9 crosses above EMA 21
sell_signals = crossunder(ema_9, ema_21)   # EMA 9 crosses below EMA 21

print(f"Buy signals on dates: {df['Time'][buy_signals].tolist()}")
```

---

## Interactive WebSockets

Receives live order updates, trade confirmations, and market status events.

```python
import asyncio
from choice_api import InteractiveSocketClient

async def main():
    # token is the session_id obtained after login
    ws = InteractiveSocketClient(token=client.session_id)

    ws.on("ORD_NRML", lambda data: print(f"Order Update: {data}"))
    ws.on("TRD_MSG", lambda data: print(f"Trade: {data}"))
    ws.on("MKT_STAT", lambda data: print(f"Market Status: {data}"))
    ws.on("*", lambda data: print(f"Anything else: {data}"))      # every message, whatever its type
    ws.on("error", lambda exc: print(f"Socket error: {exc}"))

    # reconnect=True keeps the socket alive across drops (3s, 6s, ... up to 60s between attempts)
    await ws.connect(reconnect=True)

# IMPORTANT: If running in a Jupyter Notebook, use `await main()` instead of `asyncio.run(main())`
if __name__ == "__main__":
    asyncio.run(main())
```

**Event types:** `ORD_NRML` (order updates), `TRD_MSG` (trade confirmations), `MKT_STAT` (market open/close),
any other `MessageType` the server sends, `"*"` for everything, and `"error"`. Without
`reconnect=True`, `connect()` returns when the connection closes. Call `await ws.disconnect()` to stop.

---

## Price Feed WebSockets (FIX3.0)

Receives live Level 1 (Touchline) and Level 2 (Best Five / Depth) market data via TCP socket with Zlib compression.

This client is **synchronous** — it runs on a background thread, so no `asyncio` is needed.

```python
import time
from choice_api import PriceFeedSocketClient

feed = PriceFeedSocketClient(
    host=client.bcast_ip,
    port=client.bcast_port,
    vendor_id=client.vendor_id,
    access_token=client.access_token
)

# Register callback for live market data
feed.on_message(lambda data: print(f"Market Data: {data}"))

# Subscribe whenever you like - before or after starting. Requests made before the
# socket is up are queued and sent as soon as the logon completes.
feed.subscribe_touchline(client.session_id, segment_id=1, token=2885)
feed.subscribe_best_five(client.session_id, segment_id=1, token=2885)

# Start the background thread (logs in automatically)
feed.start_websocket()

try:
    time.sleep(3600)
finally:
    feed.stop_websocket()
```

If the connection drops, the client reconnects with backoff, logs in again and **replays every
subscription**, so the feed resumes on its own instead of staying connected but silent.

> **Note:** prices in the feed are delivered in **paisa**, not rupees — divide by 100 yourself if you need rupees.

---

## Logoff

```python
client.logoff()
```

---

## Development

```bash
pip install -e ".[dev]"
python -m unittest discover -s tests     # ~190 tests, no network or credentials needed
ruff check choice_api tests
mypy
```

Installing `TA-Lib` as well enables the extra cross-checks against the reference implementation.
See [CHANGELOG.md](CHANGELOG.md) for what changed in each release.
