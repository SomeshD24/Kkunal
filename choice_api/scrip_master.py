import csv
import io
import os
import tempfile
import urllib.request
import logging
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

class ScripMaster:
    """
    Manages downloading and parsing the daily Scrip Master from Choice API.
    Provides fast lookups for Tokens, Lot Sizes, and Symbols.
    """
    def __init__(self, cache_dir=None):
        self.symbol_to_rows = defaultdict(list)
        self.token_to_details = {}
        self.all_rows = []
        self.is_loaded = False
        self.loaded_date = None
        self.cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), 'choice_scripmaster')

    def _cache_path(self, date_str):
        return os.path.join(self.cache_dir, f"SCRIP_MASTER_{date_str}.csv")

    def _read_cache(self, date_str):
        """Returns cached CSV text for the date, or None."""
        path = self._cache_path(date_str)
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
        except OSError as e:
            logger.debug("Could not read scrip master cache %s: %s", path, e)
        return None

    def _write_cache(self, date_str, text):
        """Best-effort cache write; never fatal."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(self._cache_path(date_str), 'w', encoding='utf-8') as f:
                f.write(text)
        except OSError as e:
            logger.debug("Could not cache scrip master: %s", e)

    def _ingest(self, text, date_str):
        """Parses scrip master CSV text into the lookup tables."""
        self.symbol_to_rows = defaultdict(list)
        self.token_to_details = {}
        self.all_rows = []

        reader = csv.DictReader(io.StringIO(text))
        count = 0
        for row in reader:
            token = row.get('Token', '').strip()
            if not token:
                continue

            symbol = row.get('Symbol', '').strip()
            sec_desc = row.get('SecDesc', '').strip()

            self.token_to_details[token] = row
            self.all_rows.append(row)

            if symbol:
                self.symbol_to_rows[symbol].append(row)
            if sec_desc and sec_desc != symbol:
                self.symbol_to_rows[sec_desc].append(row)

            count += 1

        self.is_loaded = count > 0
        self.loaded_date = date_str if self.is_loaded else None
        return count

    def fetch(self, force=False):
        """
        Loads the Scrip Master for the current date.

        The file is cached on disk per day, so repeated runs reuse it instead of
        re-downloading several megabytes. If today's file is unavailable (weekends,
        holidays, early morning), it falls back to the previous two days.

        Args:
            force: Skip the on-disk cache and download again.
        """
        # Try today, then yesterday, then the day before
        for days_back in range(3):
            target_date = datetime.now() - timedelta(days=days_back)
            date_str = target_date.strftime("%d%b%Y")

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

    def get_token(self, symbol_or_desc: str, segment: str = None):
        """
        Looks up tokens for a given Symbol or SecDesc.
        
        If `segment` is provided (e.g., '1', '13'), returns the single 
        token string for that specific segment match, or None if not found.
        
        If `segment` is NOT provided, returns a list of dicts for ALL matching 
        rows across every segment. Each dict contains:
            Token, Exchange, Segment, Symbol, SecDesc, Series, MarketLot
        
        Returns:
            list[dict] when segment is None — all matching rows.
            str or None when segment is specified — single token or None.
        """
        if not self.is_loaded:
            logger.warning("Scrip master is not loaded; call fetch() first. Returning no match.")

        rows = self.symbol_to_rows.get(symbol_or_desc, [])
        
        if segment:
            segment_str = str(segment).strip()
            for row in rows:
                if row.get('Segment', '').strip() == segment_str:
                    return row.get('Token', '').strip()
            return None
        
        # No segment specified — return all matching rows
        results = []
        for row in rows:
            results.append({
                'Token': row.get('Token', '').strip(),
                'Exchange': row.get('Exchange', '').strip(),
                'Segment': row.get('Segment', '').strip(),
                'Symbol': row.get('Symbol', '').strip(),
                'SecDesc': row.get('SecDesc', '').strip(),
                'Series': row.get('Series', '').strip(),
                'MarketLot': row.get('MarketLot', '').strip(),
            })
        return results

    def search(self, name: str):
        """
        Case-insensitive search: returns all rows where Symbol or SecDesc 
        contains the given name. Useful for fuzzy discovery of instruments.
        
        Returns:
            list[dict] — matching rows with Token, Exchange, Segment, Symbol, SecDesc, Series, MarketLot.
        """
        name_upper = name.upper().strip()
        results = []
        for row in self.all_rows:
            d_symbol = row.get('Symbol', '').strip().upper()
            d_sec_desc = row.get('SecDesc', '').strip().upper()
            if name_upper in d_symbol or name_upper in d_sec_desc:
                results.append({
                    'Token': row.get('Token', '').strip(),
                    'Exchange': row.get('Exchange', '').strip(),
                    'Segment': row.get('Segment', '').strip(),
                    'Symbol': row.get('Symbol', '').strip(),
                    'SecDesc': row.get('SecDesc', '').strip(),
                    'Series': row.get('Series', '').strip(),
                    'MarketLot': row.get('MarketLot', '').strip(),
                })
        return results

    def resolve(self, symbol: str, segment=None):
        """
        Resolves a symbol to a single (segment_id, token) pair, ready to pass
        straight into the order and historical APIs.

        Args:
            symbol: Symbol or SecDesc, e.g. 'RELIANCE'.
            segment: Restrict to a segment (e.g. Segment.NSE_CASH). When omitted
                and the symbol exists in several segments, the first match is
                returned and the alternatives are logged.

        Returns:
            (segment_id, token) as ints.

        Raises:
            KeyError: if the symbol is not in the scrip master.
        """
        rows = self.symbol_to_rows.get(symbol) or self.symbol_to_rows.get(symbol.upper().strip())
        if not rows:
            if not self.is_loaded:
                raise KeyError(
                    f"Scrip master is not loaded, so {symbol!r} cannot be resolved. "
                    "Call fetch() or log in first."
                )
            raise KeyError(f"{symbol!r} not found in the scrip master.")

        if segment is not None:
            segment_str = str(int(segment))
            for row in rows:
                if row.get('Segment', '').strip() == segment_str:
                    return int(segment_str), int(row['Token'].strip())
            raise KeyError(f"{symbol!r} not found in segment {segment_str}.")

        if len(rows) > 1:
            others = sorted({r.get('Segment', '').strip() for r in rows})
            logger.info(
                "%s exists in segments %s; using %s. Pass segment= to choose.",
                symbol, others, rows[0].get('Segment', '').strip()
            )
        return int(rows[0]['Segment'].strip()), int(rows[0]['Token'].strip())

    def get_details(self, token: str) -> dict:
        """Returns all CSV row details for a given Token."""
        return self.token_to_details.get(str(token), {})

    def get_lot_size(self, token: str) -> int:
        """Helper to quickly get the lot size for a token."""
        details = self.get_details(token)
        lot_size_str = details.get("MarketLot", "1")
        try:
            return int(lot_size_str)
        except ValueError:
            return 1
