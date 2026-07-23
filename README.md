# Kkunal

A Python library for the Choice FINX Trading API. Supports REST API, Interactive WebSockets (order/trade updates), and Live Price Feed WebSockets (FIX3.0 compressed data).

## Installation

```bash
pip install kkunal
```

All dependencies (`requests`, `pycryptodome`, `websockets`, `pandas`) are installed automatically.

---

## Quick Start

```python
from choice_api import ChoiceClient

client = ChoiceClient(
    vendor_id="YOUR_VENDOR_ID",
    vendor_key="YOUR_VENDOR_KEY",
    api_key="YOUR_JWT_BEARER_TOKEN",
    aes_key="YOUR_AES_KEY",
    aes_iv="YOUR_AES_IV"
)

# Login (TOTP flow is handled automatically)
session_id = client.login(mobile_no="1234567890")
print(f"Session ID: {session_id}")
```

### Session Persistence

You can save and reload sessions to avoid logging in repeatedly during the same trading day:

```python
session_file = "my_session.json"

if client.load_session(session_file):
    print("Restored today's session.")
else:
    client.login(mobile_no="1234567890")
    client.save_session(session_file)
```

> **Note:** Sessions expire daily. `load_session` will return `False` if the saved session is from a previous day.

---

## Scrip Master

The Scrip Master CSV is automatically downloaded when you log in. It maps instrument symbols to their tokens, lot sizes, and other metadata.

### `get_token(symbol, exchange=None)`

Looks up tokens for a given symbol or description.

- **Without `exchange`**: Returns a **list of dicts** for ALL matching rows across every segment (NSE, BSE, CDS, etc.). Each dict contains `Token`, `Exchange`, `Symbol`, `SecDesc`, `Series`, `MarketLot`.
- **With `exchange`** (e.g., `"NSE"`, `"BSE"`): Returns a **single token string** for that specific exchange, or `None` if not found.

```python
# Get all matches across all segments
matches = client.scrip_master.get_token("RELIANCE")
for m in matches:
    print(f"{m['Exchange']} — Token: {m['Token']}, Symbol: {m['Symbol']}")
# NSE — Token: 2885, Symbol: RELIANCE
# BSE — Token: 500325, Symbol: RELIANCE
# ...

# Get specific exchange token
nse_token = client.scrip_master.get_token("RELIANCE", exchange="NSE")
bse_token = client.scrip_master.get_token("RELIANCE", exchange="BSE")
```

### `search(name)`

Case-insensitive fuzzy search: returns all rows where Symbol or SecDesc **contains** the given name.

```python
results = client.scrip_master.search("NIFTY")
for r in results:
    print(f"{r['Exchange']} | {r['Symbol']} | Token: {r['Token']}")
```

### `get_details(token)`

Returns all CSV row details for a given token as a dictionary.

```python
details = client.scrip_master.get_details("2885")
print(details)
# {'Exchange': 'NSE', 'Segment': '1', 'Token': '2885', 'Symbol': 'RELIANCE', ...}
```

### `get_lot_size(token)`

Returns the market lot size for a token.

```python
lot = client.scrip_master.get_lot_size("2885")
print(lot)  # 1 for equity, 250 for NIFTY futures, etc.
```

---

## Orders

> **Important:** Prices must be in **paisa** (multiply INR by 100). For F&O orders, `qty` must be in **total shares** (multiples of the lot size), not the number of lots.

### `client.orders.place_order(...)`

| Parameter | Type | Description |
|---|---|---|
| `segment_id` | `int` | `1` = NSE Cash, `2` = NSE F&O, `3` = BSE Cash |
| `token` | `int` | Instrument token from Scrip Master |
| `order_type` | `str` | `"RL_MKT"` = Market, `"RL_LIMIT"` = Limit, `"SL_MKT"` = Stop Loss Market, `"SL_LIMIT"` = Stop Loss Limit |
| `bs` | `int` | `1` = Buy, `2` = Sell |
| `qty` | `int` | Total quantity in shares |
| `price` | `float` | Price in paisa (e.g., 1300 INR → `130000`) |
| `trigger_price` | `float` | Trigger price in paisa (0 for non-SL orders) |
| `validity` | `int` | `1` = Day |
| `product_type` | `str` | `"M"` = Intraday (Margin), `"D"` = Delivery/CarryForward |
| `disclosed_qty` | `int` | Optional. Disclosed quantity (default `0`) |

```python
response = client.orders.place_order(
    segment_id=1,
    token=2885,
    order_type="RL_MKT",
    bs=1,
    qty=1,
    price=0,
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

Returns touchline data for multiple instruments.

```python
# Format: "SegmentId1,Token1|SegmentId2,Token2"
touchline = client.market.get_multiple_touchline("1,2885|1,11536")
```

---

## Historical Data

### `client.historical.get_historical_data(segment_id, token, from_date, to_date, resolution)`

Returns historical OHLCV data as a **Pandas DataFrame**.

| Parameter | Type | Description |
|---|---|---|
| `segment_id` | `int` | Exchange segment |
| `token` | `int` | Instrument token |
| `from_date` | `str` or `int` | Start date (`"YYYY-MM-DD"` or seconds from 1980) |
| `to_date` | `str` or `int` | End date (`"YYYY-MM-DD"` or seconds from 1980) |
| `resolution` | `str` | `"1"` = 1 min, `"5"` = 5 min, `"D"` = Daily |

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

The returned DataFrame has columns: `Time`, `Open`, `High`, `Low`, `Close`, `Volume`, `OI`. Prices are automatically adjusted using the `PriceDivisor` from the API response.

---

## Interactive WebSockets

Receives live order updates, trade confirmations, and market status events.

```python
import asyncio
from choice_api import InteractiveSocketClient

async def main():
    ws = InteractiveSocketClient(token=client.session_id)

    ws.on("ORD_NRML", lambda data: print(f"Order Update: {data}"))
    ws.on("TRD_MSG", lambda data: print(f"Trade: {data}"))
    ws.on("MKT_STAT", lambda data: print(f"Market Status: {data}"))

    await ws.connect()

asyncio.run(main())
```

**Event types:** `ORD_NRML` (order updates), `TRD_MSG` (trade confirmations), `MKT_STAT` (market open/close).

---

## Price Feed WebSockets (FIX3.0)

Receives live Level 1 (Touchline) and Level 2 (Best Five / Depth) market data via TCP socket with Zlib compression.

```python
import asyncio
from choice_api import PriceFeedSocketClient

async def main():
    feed = PriceFeedSocketClient(
        host=client.bcast_ip,
        port=client.bcast_port,
        user_id="YOUR_USER_ID"
    )

    feed.on_message(lambda raw: print(f"Feed: {raw}"))

    # Start connection (sends login automatically)
    asyncio.create_task(feed.connect())

    # Wait for connection, then subscribe
    await asyncio.sleep(2)
    feed.subscribe_touchline(client.session_id, segment_id=1, token=2885)
    feed.subscribe_best_five(client.session_id, segment_id=1, token=2885)

    # Keep running
    await asyncio.sleep(3600)

asyncio.run(main())
```

---

## Logoff

```python
client.logoff()
```
