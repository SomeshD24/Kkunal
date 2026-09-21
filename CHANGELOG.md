# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [1.5.0] - 2026-09-21

A reliability and correctness release. Indicator values now match TA-Lib and TradingView
bar for bar, the HTTP layer survives the failures a live trading day actually produces, and
several ways of silently getting a wrong answer have been closed.

### Fixed

- **`ScripMaster.resolve()` could return an arbitrary derivative contract.**
  `resolve("RELIANCE", segment=Segment.NSE_FO)` matched 539 futures and options and returned
  whichever came first - a random put. It now raises `AmbiguousSymbolError` instead of guessing,
  and points to `find_future()` / `find_option()`. Cash symbols still resolve as before
  (NSE first, then BSE).
- **Smoothed indicators disagreed with charting platforms on short histories.** RSI, ATR, ADX,
  Supertrend, EMA, DEMA, TEMA and MACD seeded their recursion with the first value instead of the
  simple average of the first window, which is what Wilder specified and what TA-Lib and
  TradingView do. The values converged after ~100 bars, but on 30 daily candles RSI was off by a
  median of 4 points (worst case 17). They now match TA-Lib to 1e-12 from the first valid bar.
- **Parabolic SAR diverged from the reference algorithm** by up to several points after an
  outside-bar reversal, and always assumed an opening uptrend. Rewritten to Wilder's rules as
  TA-Lib implements them; identical on 300/300 random series.
- **`get_historical_data()` returned an empty DataFrame when the request failed**, so an expired
  session, a bad token, a throttle and a market holiday were indistinguishable. A failure now
  raises `HistoricalDataError` carrying the broker's message; only a genuinely empty window
  returns an empty frame (which now keeps its columns).
- `get_historical_data()` crashed on a `null` `Response`, and on volumes sent as `"1234.0"`,
  which took the whole batch down. Rows are parsed individually; malformed ones are skipped
  and counted in a warning.
- `to_date="2024-06-01"` stopped at that day's midnight, silently dropping its intraday
  candles. A `to_date` without a time now means the end of that day.
- `login()` left `access_token` as `None` when `ValidateTOTP` returned the session as a plain
  string, so the price feed logged on with an empty token.
- `PriceFeedSocketClient` reconnected after a network drop but never re-subscribed, leaving the
  feed connected and silent. Subscriptions are now remembered and replayed after every logon;
  they can also be requested before the socket is up, which removes the `sleep(2)` workaround.
- Leaving a `with ChoiceClient(...)` block logged the session off even when `session_file` was
  in use, so the next run loaded a dead session. A persisted session is now left alive, and
  `logoff()` deletes the saved file.
- `Resolution.MIN_3` was not a valid ChartData interval; the API rejects it. Removed, along with
  `Validity.IOC`, which could not be confirmed against the API. Unsupported resolutions now
  fail immediately with the list of valid codes.
- `pivot_points()` on intraday data used the previous *bar*. It now uses the previous trading
  *day*, as charting platforms draw daily pivots on intraday charts (`anchor=` to override).
- `IndicatorsAPI.add()` silently dropped misspelt keyword arguments; it now raises `TypeError`.
- `InteractiveSocketClient.on()` silently ignored event names outside a fixed list.
- The scrip-master cache could grow without bound and was not written atomically.
- Credentials could appear in exception messages and logs. Every message is now scrubbed of the
  API key, session id, access token, OTPs and JWTs; the raw body stays on `.response`.

### Added

- **Resilient transport.** Pooled connections, automatic retries with backoff for retry-safe
  requests (GETs and read-only POSTs), HTTP 429 handling that honours `Retry-After`, failover
  between the two Choice gateways, and optional client-side rate limiting
  (`rate_limit=`, `order_rate_limit=`). Requests that place, modify or cancel orders, or move
  funds, are **never** retried: a timed-out order may still have reached the exchange.
- **Automatic re-login.** A session rejected mid-day is renewed once and the request replayed.
- **Futures and options lookup**: `find_future()`, `find_option()`, `expiries()`, `strikes()`,
  `option_chain()`. Strikes are in rupees; results carry the token, segment and lot size ready
  for `place_order`. `search()` gained `segment=`, `instrument=` and `limit=`.
- **Long histories just work.** Ranges beyond what ChartData serves in one call are fetched in
  windows and stitched together, sorted and de-duplicated.
- New indicators: **MFI**, **ROC**, **Keltner Channel** and **CPR** (Central Pivot Range).
- New exceptions: `StaticIPError` (with the fix spelled out - the most common failure when
  running from a new network), `RateLimitError`, `HistoricalDataError`, `OrderValidationError`,
  `AmbiguousSymbolError`.
- Orders are validated locally (side, quantity, prices) before being sent, and a fractional
  price triggers a warning that prices are in paisa.
- `ChoiceClient.from_env()` and `CHOICE_MOBILE_NO`, to keep credentials out of source code.
- `login(force=True)`; `raise_on_error=True` to raise on any non-Success response body.
- `get_multiple_touchline()` accepts `(segment, token)` pairs or contract dicts.
- `InteractiveSocketClient.connect(reconnect=True)` and a `"*"` wildcard event.
- Friendly resolution aliases (`"5m"`, `"1h"`, `"daily"`), more `Segment` members (verified
  against the live scrip master) and `OptionType`.
- Type information is now published (`py.typed`).

### Changed

- WMA and CCI are vectorised: about 100x and 25x faster respectively on 100,000 rows.
- MACD rejects `fast_period >= slow_period`.
- EMA-family indicators now return `NaN` until their first window is full, like every other
  windowed indicator; Parabolic SAR has no value on the first bar.
- Packaging moved to `pyproject.toml` (PEP 621) with a single-source version; `setup.py` removed.
- Minimum Python is 3.8, the floor of the declared dependencies.
- CI runs the test suite on Python 3.8-3.13 (Linux and Windows) plus ruff and mypy, and a
  release cannot publish unless it passes.

### Upgrade notes

- Indicator values change for the first ~100 bars of a series (they now agree with TA-Lib and
  TradingView). Re-check any thresholds tuned on short histories.
- Code that treated an empty DataFrame from `get_historical_data()` as "request failed" should
  catch `HistoricalDataError` instead.
- `resolve()` raises for ambiguous symbols where it previously returned the first match.

## [1.4.0] - 2026-09-21

### Added
- Typed exception hierarchy (`ChoiceAPIError` and subclasses).
- Named constants: `Segment`, `Side`, `OrderType`, `ProductType`, `Validity`, `Resolution`,
  `to_paisa()` / `to_rupees()`.
- `login(session_file=...)`, context-manager support, `is_authenticated`, a safe `__repr__`.
- Scrip-master disk cache and `resolve()`; `historical.get_by_symbol()`; `indicators.add()`.

### Fixed
- `add_all()` applied 9 of 21 indicators although `indicators="all"` promised all of them.
- SMA, Stochastic, CCI, Williams %R, Bollinger, Donchian and Ichimoku returned values computed
  from partial windows; they now return `NaN` until the window is full.

## [1.3.1] - 2026-09-21

### Fixed
- **`pip install kkunal` produced an unimportable package**: `websocket-client` was never
  declared as a dependency.
- RSI leaked a fabricated `100.0` on bar `period-1`; `pivot_points` used the current bar's own
  HLC (lookahead bias); Bollinger Bands now use the population standard deviation.
- `parabolic_sar` / `heikin_ashi` crashed on an empty DataFrame; numpy integer periods were rejected.
- HTTP requests had no timeout; unparseable dates silently became 1980-01-01; every order was
  sent with `ClientOrderNo: 123456`.

## [1.3.0] - 2026-09-02

### Added
- Margin calculator (`GetMargin`) via `client.funds.get_margin()` and `client.orders.get_margin()`.
- Technical indicators module.
