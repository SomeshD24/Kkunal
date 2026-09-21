import csv
import io
import os
import tempfile
import urllib.request
import urllib.error
import logging
from datetime import date, datetime, timedelta
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Union

from .exceptions import AmbiguousSymbolError

logger = logging.getLogger(__name__)

_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}

# Spot segments tried in this order when a symbol is listed on several exchanges.
_SPOT_SEGMENT_PREFERENCE = ("1", "3")

ExpiryLike = Union[str, date, datetime]


def _parse_expiry(raw: Any) -> Optional[date]:
    """Parses the scrip master's '29SEP26' form (and ISO dates) without relying on the locale."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    text = str(raw or "").strip().upper()
    if not text:
        return None
    try:
        if len(text) == 7 and text[2:5] in _MONTHS:                    # 29SEP26
            return date(2000 + int(text[5:]), _MONTHS[text[2:5]], int(text[:2]))
        if len(text) == 9 and text[2:5] in _MONTHS:                    # 29SEP2026
            return date(int(text[5:]), _MONTHS[text[2:5]], int(text[:2]))
        return datetime.strptime(text[:10], "%Y-%m-%d").date()         # 2026-09-29
    except (ValueError, KeyError):
        return None


class ScripMaster:
    """
    Manages downloading and parsing the daily Scrip Master from Choice API.
    Provides fast lookups for Tokens, Lot Sizes, and Symbols, including
    futures and options contracts.
    """
    def __init__(self, cache_dir: Optional[str] = None, cache_keep: int = 3):
        self.symbol_to_rows: Dict[str, List[dict]] = defaultdict(list)
        self.token_to_details: Dict[str, dict] = {}
        self.all_rows: List[dict] = []
        self.is_loaded = False
        self.loaded_date: Optional[str] = None
        self.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), 'choice_scripmaster')
        self.cache_keep = cache_keep
        self._expiry_cache: Dict[str, Optional[date]] = {}

    # ---------------------------------------------------------------- cache

    def _cache_path(self, date_str: str) -> str:
        return os.path.join(self.cache_dir, f"SCRIP_MASTER_{date_str}.csv")

    def _read_cache(self, date_str: str) -> Optional[str]:
        """Returns cached CSV text for the date, or None."""
        path = self._cache_path(date_str)
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except OSError as e:
            logger.debug("Could not read scrip master cache %s: %s", path, e)
        return None

    def _write_cache(self, date_str: str, text: str) -> None:
        """Best-effort cache write; never fatal. Atomic, so a crash cannot leave half a file."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=self.cache_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(text)
                os.replace(tmp_path, self._cache_path(date_str))
            except BaseException:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise
            self._prune_cache(keep=self._cache_path(date_str))
        except OSError as e:
            logger.debug("Could not cache scrip master: %s", e)

    def _prune_cache(self, keep: Optional[str] = None) -> None:
        """
        Keeps only the newest few daily files, so the cache cannot grow without bound.
        `keep` is never removed, whatever its timestamp says.
        """
        try:
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)
                     if f.startswith("SCRIP_MASTER_") and f.endswith(".csv")]
            if keep:
                files = [f for f in files if os.path.abspath(f) != os.path.abspath(keep)]
            files.sort(key=os.path.getmtime, reverse=True)
            budget = max(1, self.cache_keep) - (1 if keep else 0)
            for stale in files[max(0, budget):]:
                os.remove(stale)
        except OSError as e:
            logger.debug("Could not prune scrip master cache: %s", e)

    def _ingest(self, text: str, date_str: str) -> int:
        """Parses scrip master CSV text into the lookup tables."""
        symbol_to_rows: Dict[str, List[dict]] = defaultdict(list)
        token_to_details: Dict[str, dict] = {}
        all_rows: List[dict] = []

        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            token = (row.get('Token') or '').strip()
            if not token:
                continue

            symbol = (row.get('Symbol') or '').strip()
            sec_desc = (row.get('SecDesc') or '').strip()

            token_to_details[token] = row
            all_rows.append(row)

            if symbol:
                symbol_to_rows[symbol].append(row)
            if sec_desc and sec_desc != symbol:
                symbol_to_rows[sec_desc].append(row)

        # Swap in one step so a concurrent reader never sees half-built tables.
        if all_rows:
            self.symbol_to_rows = symbol_to_rows
            self.token_to_details = token_to_details
            self.all_rows = all_rows
            self.is_loaded = True
            self.loaded_date = date_str
        return len(all_rows)

    def fetch(self, force: bool = False, lookback_days: int = 7) -> bool:
        """
        Loads the Scrip Master for the current date.

        The file is cached on disk per day, so repeated runs reuse it instead of
        re-downloading several megabytes. If today's file is unavailable (weekends,
        holidays, early morning), it walks back up to `lookback_days`.

        Args:
            force: Skip the on-disk cache and download again.
        """
        for days_back in range(max(1, lookback_days)):
            target_date = datetime.now() - timedelta(days=days_back)
            # Month abbreviations must be English regardless of the OS locale.
            date_str = f"{target_date.day:02d}{list(_MONTHS)[target_date.month - 1].title()}{target_date.year}"

            if not force:
                cached = self._read_cache(date_str)
                if cached:
                    count = self._ingest(cached, date_str)
                    if count:
                        logger.info("Loaded %d symbols from cached %s master.", count, date_str)
                        return True

            url = f"https://scripmaster.choiceindia.com/scripmaster/SCRIP_MASTER_{date_str}.csv"
            logger.info("Downloading scrip master for %s...", date_str)

            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    text = response.read().decode('utf-8', errors='ignore')

                count = self._ingest(text, date_str)
                if not count:
                    logger.warning("Scrip master for %s contained no rows.", date_str)
                    continue

                self._write_cache(date_str, text)
                logger.info("Successfully loaded %d symbols from %s master.", count, date_str)
                return True

            except urllib.error.HTTPError as e:
                if e.code == 404:
                    logger.debug("Scrip master not found for %s, trying previous day.", date_str)
                    continue
                logger.error("HTTP Error fetching scrip master: %s", e)
                break
            except Exception as e:
                logger.error("Failed to fetch scrip master: %s", e)
                break

        logger.warning("Could not download Scrip Master.")
        return False

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _summary(row: dict) -> dict:
        """The string-valued summary dict returned by get_token() and search()."""
        keys = ('Token', 'Exchange', 'Segment', 'Symbol', 'SecDesc', 'Series', 'MarketLot',
                'Instrument', 'Expiry', 'StrikePrice', 'OptionType')
        return {k: (row.get(k) or '').strip() for k in keys}

    def _expiry_of(self, row: dict) -> Optional[date]:
        raw = (row.get('Expiry') or '').strip()
        if raw not in self._expiry_cache:
            self._expiry_cache[raw] = _parse_expiry(raw)
        return self._expiry_cache[raw]

    @staticmethod
    def _is_derivative(row: dict) -> bool:
        return (row.get('Instrument') or '').strip().upper().startswith(('FUT', 'OPT'))

    def _contract(self, row: dict) -> dict:
        """A typed view of one row, ready to feed into the order and historical APIs."""
        try:
            divisor = float((row.get('PriceDivisor') or '').strip() or 1) or 1.0
        except ValueError:
            divisor = 1.0
        strike: Optional[float] = None
        try:
            raw_strike = float((row.get('StrikePrice') or '').strip() or -1)
            if raw_strike > 0:
                # The master stores 2325000 with a divisor of 100, meaning strike 23250.
                strike = raw_strike / divisor
        except ValueError:
            pass
        try:
            lot = int(float((row.get('MarketLot') or '').strip() or 1))
        except ValueError:
            lot = 1
        return {
            'Token': int(row['Token'].strip()),
            'Segment': int((row.get('Segment') or '0').strip() or 0),
            'Exchange': (row.get('Exchange') or '').strip(),
            'Symbol': (row.get('Symbol') or '').strip(),
            'SecDesc': (row.get('SecDesc') or '').strip(),
            'Instrument': (row.get('Instrument') or '').strip(),
            'Expiry': self._expiry_of(row),
            'StrikePrice': strike,
            'OptionType': (row.get('OptionType') or '').strip() or None,
            'MarketLot': lot,
            'PriceDivisor': divisor,
        }

    def _rows_for(self, symbol: str) -> List[dict]:
        rows = self.symbol_to_rows.get(symbol) or self.symbol_to_rows.get(str(symbol).upper().strip())
        if rows:
            return rows
        if not self.is_loaded:
            raise KeyError(
                f"Scrip master is not loaded, so {symbol!r} cannot be resolved. "
                "Call fetch() or log in first."
            )
        raise KeyError(f"{symbol!r} not found in the scrip master.")

    # --------------------------------------------------------------- lookups

    def get_token(self, symbol_or_desc: str, segment: Optional[Union[str, int]] = None):
        """
        Looks up tokens for a given Symbol or SecDesc.

        If `segment` is provided (e.g., '1', '13'), returns the single
        token string for that specific segment match, or None if not found.
        For a derivative's underlying (e.g. 'NIFTY' in segment 2) that is simply
        the first of many contracts - use find_future()/find_option() instead.

        If `segment` is NOT provided, returns a list of dicts for ALL matching
        rows across every segment. Each dict contains:
            Token, Exchange, Segment, Symbol, SecDesc, Series, MarketLot,
            Instrument, Expiry, StrikePrice, OptionType

        Returns:
            list[dict] when segment is None — all matching rows.
            str or None when segment is specified — single token or None.
        """
        if not self.is_loaded:
            logger.warning("Scrip master is not loaded; call fetch() first. Returning no match.")

        rows = self.symbol_to_rows.get(symbol_or_desc, [])

        if segment:
            segment_str = str(int(segment)) if not isinstance(segment, str) else segment.strip()
            for row in rows:
                if (row.get('Segment') or '').strip() == segment_str:
                    return (row.get('Token') or '').strip()
            return None

        # No segment specified — return all matching rows
        return [self._summary(row) for row in rows]

    def search(self, name: str, segment: Optional[int] = None,
               instrument: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
        """
        Case-insensitive search: returns all rows where Symbol or SecDesc
        contains the given name. Useful for fuzzy discovery of instruments.

        Args:
            segment: Only this segment (e.g. Segment.NSE_CASH).
            instrument: Only this instrument type, e.g. 'FUTIDX', 'OPTSTK'.
            limit: Stop after this many matches - a broad term such as 'NIFTY'
                otherwise matches thousands of option contracts.

        Returns:
            list[dict] — matching rows with Token, Exchange, Segment, Symbol, SecDesc,
            Series, MarketLot, Instrument, Expiry, StrikePrice, OptionType.
        """
        name_upper = name.upper().strip()
        segment_str = str(int(segment)) if segment is not None else None
        instrument_upper = instrument.upper().strip() if instrument else None
        results: List[dict] = []
        for row in self.all_rows:
            if segment_str is not None and (row.get('Segment') or '').strip() != segment_str:
                continue
            if instrument_upper and (row.get('Instrument') or '').strip().upper() != instrument_upper:
                continue
            d_symbol = (row.get('Symbol') or '').strip().upper()
            d_sec_desc = (row.get('SecDesc') or '').strip().upper()
            if name_upper in d_symbol or name_upper in d_sec_desc:
                results.append(self._summary(row))
                if limit and len(results) >= limit:
                    break
        return results

    def resolve(self, symbol: str, segment: Optional[int] = None) -> Tuple[int, int]:
        """
        Resolves a symbol to a single (segment_id, token) pair, ready to pass
        straight into the order and historical APIs.

        It never guesses between instruments. A cash symbol listed on several
        exchanges resolves to NSE, then BSE (pass `segment` to choose). Anything
        still ambiguous - notably an underlying such as 'NIFTY' in an F&O segment,
        which matches thousands of contracts - raises AmbiguousSymbolError; use
        find_future() / find_option(), or pass the exact SecDesc ('NIFTY26SEPFUT').

        Args:
            symbol: Symbol or exact SecDesc, e.g. 'RELIANCE' or 'NIFTY26SEPFUT'.
            segment: Restrict to a segment (e.g. Segment.NSE_CASH).

        Returns:
            (segment_id, token) as ints.

        Raises:
            KeyError: the symbol is not in the scrip master (or not in `segment`).
            AmbiguousSymbolError: several instruments match (a KeyError subclass).
        """
        rows = self._rows_for(symbol)

        if segment is not None:
            segment_str = str(int(segment))
            rows = [r for r in rows if (r.get('Segment') or '').strip() == segment_str]
            if not rows:
                raise KeyError(f"{symbol!r} not found in segment {segment_str}.")

        if len(rows) > 1:
            spot = [r for r in rows if not self._is_derivative(r)]
            if spot:
                rows = spot
                if segment is None:
                    segments = sorted({(r.get('Segment') or '').strip() for r in rows})
                    if len(segments) > 1:
                        chosen = next((s for s in _SPOT_SEGMENT_PREFERENCE if s in segments), segments[0])
                        logger.info("%s exists in segments %s; using %s. Pass segment= to choose.",
                                    symbol, segments, chosen)
                        rows = [r for r in rows if (r.get('Segment') or '').strip() == chosen]

        if len(rows) > 1:
            candidates = sorted({(r.get('SecDesc') or '').strip() for r in rows
                                 if (r.get('Instrument') or '').upper().startswith('FUT')})
            candidates = (candidates or sorted({(r.get('SecDesc') or '').strip() for r in rows}))[:5]
            if any(self._is_derivative(r) for r in rows):
                advice = ("Use find_future() / find_option(), or pass the exact SecDesc "
                          f"(e.g. {candidates[0]!r}).")
            else:
                advice = "Pass the exact token instead; these instruments share one symbol."
            raise AmbiguousSymbolError(
                f"{symbol!r} matches {len(rows)} instruments"
                + (f" in segment {int(segment)}" if segment is not None else "")
                + f". {advice}",
                candidates=candidates
            )

        row = rows[0]
        return int(row['Segment'].strip()), int(row['Token'].strip())

    # ----------------------------------------------------------- derivatives

    def _derivatives(self, symbol: str, kind: str, segment: Optional[int] = None) -> List[dict]:
        """All FUT* or OPT* rows for an underlying, restricted to one segment."""
        rows = [r for r in self._rows_for(symbol)
                if (r.get('Instrument') or '').strip().upper().startswith(kind)
                and (r.get('Symbol') or '').strip().upper() == str(symbol).upper().strip()]
        if segment is not None:
            segment_str = str(int(segment))
            rows = [r for r in rows if (r.get('Segment') or '').strip() == segment_str]
        if not rows:
            where = f" in segment {int(segment)}" if segment is not None else ""
            raise KeyError(f"No {'futures' if kind == 'FUT' else 'options'} found for {symbol!r}{where}.")

        segments = sorted({(r.get('Segment') or '').strip() for r in rows})
        if len(segments) > 1:
            raise AmbiguousSymbolError(
                f"{symbol!r} has {'futures' if kind == 'FUT' else 'options'} in segments {segments}. "
                "Pass segment= to choose.",
                candidates=segments
            )
        return rows

    def _pick_expiry(self, rows: List[dict], expiry: Optional[ExpiryLike], symbol: str) -> date:
        available = sorted({e for e in (self._expiry_of(r) for r in rows) if e is not None})
        if expiry is None:
            today = date.today()
            upcoming = [e for e in available if e >= today]
            if not upcoming:
                raise KeyError(f"No unexpired contracts for {symbol!r}.")
            return upcoming[0]
        wanted = _parse_expiry(expiry)
        if wanted is None:
            raise ValueError(f"Could not parse expiry {expiry!r}. Use a date, 'YYYY-MM-DD' or '29SEP26'.")
        if wanted not in available:
            nearby = [e.isoformat() for e in available[:6]]
            raise KeyError(f"{symbol!r} has no contract expiring {wanted.isoformat()}. Available: {nearby}")
        return wanted

    def expiries(self, symbol: str, segment: Optional[int] = None, options: bool = True) -> List[date]:
        """
        Sorted expiry dates listed for an underlying.

        Args:
            options: True for option expiries (which include the weeklies),
                False for futures expiries.
        """
        rows = self._derivatives(symbol, 'OPT' if options else 'FUT', segment)
        return sorted({e for e in (self._expiry_of(r) for r in rows) if e is not None})

    def find_future(self, symbol: str, expiry: Optional[ExpiryLike] = None,
                    segment: Optional[int] = None) -> dict:
        """
        Finds a futures contract. With no expiry, the nearest unexpired one.

        Example:
            >>> fut = client.scrip_master.find_future("NIFTY")
            >>> fut["Segment"], fut["Token"], fut["MarketLot"]
            (2, 68407, 65)

        Returns:
            dict with Token, Segment (ints), Exchange, Symbol, SecDesc, Instrument,
            Expiry (date), StrikePrice (None), OptionType (None), MarketLot (int),
            PriceDivisor.
        """
        rows = self._derivatives(symbol, 'FUT', segment)
        wanted = self._pick_expiry(rows, expiry, symbol)
        matches = [r for r in rows if self._expiry_of(r) == wanted]
        if len(matches) > 1:
            raise AmbiguousSymbolError(
                f"{symbol!r} has {len(matches)} futures expiring {wanted.isoformat()}.",
                candidates=[(r.get('SecDesc') or '').strip() for r in matches][:5]
            )
        return self._contract(matches[0])

    def strikes(self, symbol: str, expiry: Optional[ExpiryLike] = None,
                option_type: Optional[str] = None, segment: Optional[int] = None) -> List[float]:
        """Sorted strike prices (in rupees) listed for an underlying and expiry."""
        rows = self._derivatives(symbol, 'OPT', segment)
        wanted = self._pick_expiry(rows, expiry, symbol)
        right = option_type.upper().strip() if option_type else None
        found = set()
        for r in rows:
            if self._expiry_of(r) != wanted:
                continue
            if right and (r.get('OptionType') or '').strip().upper() != right:
                continue
            strike = self._contract(r)['StrikePrice']
            if strike is not None:
                found.add(strike)
        return sorted(found)

    def find_option(self, symbol: str, strike: float, option_type: str,
                    expiry: Optional[ExpiryLike] = None, segment: Optional[int] = None) -> dict:
        """
        Finds an option contract. With no expiry, the nearest unexpired one.

        Example:
            >>> opt = client.scrip_master.find_option("NIFTY", 23250, "CE")
            >>> client.orders.place_order(segment_id=opt["Segment"], token=opt["Token"],
            ...                           qty=opt["MarketLot"], ...)

        Args:
            strike: Strike price in rupees (23250, not the scaled 2325000).
            option_type: 'CE' or 'PE' (see OptionType).

        Raises:
            KeyError: no such contract; the message lists the nearest strikes.
        """
        right = str(option_type).upper().strip()
        if right not in ("CE", "PE"):
            raise ValueError(f"option_type must be 'CE' or 'PE', got {option_type!r}")

        rows = self._derivatives(symbol, 'OPT', segment)
        wanted = self._pick_expiry(rows, expiry, symbol)
        target = float(strike)

        matches = []
        listed = set()
        for r in rows:
            if self._expiry_of(r) != wanted or (r.get('OptionType') or '').strip().upper() != right:
                continue
            contract = self._contract(r)
            if contract['StrikePrice'] is None:
                continue
            listed.add(contract['StrikePrice'])
            if abs(contract['StrikePrice'] - target) < 1e-6:
                matches.append(contract)

        if not matches:
            nearest = sorted(listed, key=lambda s: abs(s - target))[:5]
            raise KeyError(
                f"No {symbol} {right} at strike {target:g} expiring {wanted.isoformat()}. "
                f"Nearest listed strikes: {sorted(nearest)}"
            )
        if len(matches) > 1:
            raise AmbiguousSymbolError(
                f"{len(matches)} contracts match {symbol} {target:g} {right} {wanted.isoformat()}.",
                candidates=[m['SecDesc'] for m in matches][:5]
            )
        return matches[0]

    def option_chain(self, symbol: str, expiry: Optional[ExpiryLike] = None,
                     segment: Optional[int] = None) -> List[dict]:
        """Every option contract for an underlying and expiry, sorted by strike then CE/PE."""
        rows = self._derivatives(symbol, 'OPT', segment)
        wanted = self._pick_expiry(rows, expiry, symbol)
        chain = [self._contract(r) for r in rows if self._expiry_of(r) == wanted]
        chain = [c for c in chain if c['StrikePrice'] is not None]
        chain.sort(key=lambda c: (c['StrikePrice'], c['OptionType'] or ''))
        return chain

    # ---------------------------------------------------------------- details

    def get_details(self, token: str) -> dict:
        """Returns all CSV row details for a given Token."""
        return self.token_to_details.get(str(token), {})

    def get_lot_size(self, token: str) -> int:
        """Helper to quickly get the lot size for a token."""
        details = self.get_details(token)
        lot_size_str = details.get("MarketLot", "1")
        try:
            return int(float(lot_size_str))
        except (TypeError, ValueError):
            return 1
