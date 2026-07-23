import csv
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
    def __init__(self):
        self.symbol_to_tokens = defaultdict(list)  # symbol/desc -> [token1, token2, ...]
        self.token_to_details = {}
        self.is_loaded = False

    def fetch(self):
        """
        Fetches the Scrip Master CSV for the current date.
        If today's file is unavailable (e.g., weekends/holidays early morning), 
        it falls back to the previous day.
        """
        logger.info("Fetching daily scrip master...")
        
        # Try today, then yesterday, then the day before
        for days_back in range(3):
            target_date = datetime.now() - timedelta(days=days_back)
            date_str = target_date.strftime("%d%b%Y")
            url = f"https://scripmaster.choiceindia.com/scripmaster/SCRIP_MASTER_{date_str}.csv"
            
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    # Decode the response and parse as CSV
                    lines = (line.decode('utf-8', errors='ignore') for line in response)
                    reader = csv.DictReader(lines)
                    
                    count = 0
                    for row in reader:
                        token = row.get('Token', '').strip()
                        if not token:
                            continue
                            
                        symbol = row.get('Symbol', '').strip()
                        sec_desc = row.get('SecDesc', '').strip()
                        
                        # Store complete details
                        self.token_to_details[token] = row
                        
                        # Map symbol/sec_desc to list of tokens (all segments)
                        if symbol:
                            if token not in self.symbol_to_tokens[symbol]:
                                self.symbol_to_tokens[symbol].append(token)
                        if sec_desc:
                            if token not in self.symbol_to_tokens[sec_desc]:
                                self.symbol_to_tokens[sec_desc].append(token)
                            
                        count += 1
                        
                self.is_loaded = True
                logger.info(f"Successfully loaded {count} symbols from {date_str} master.")
                return True
                
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    logger.debug(f"Scrip master not found for {date_str}, trying previous day.")
                    continue
                else:
                    logger.error(f"HTTP Error fetching scrip master: {e}")
                    break
            except Exception as e:
                logger.error(f"Failed to fetch scrip master: {e}")
                break
                
        logger.warning("Could not download Scrip Master.")
        return False

    def get_token(self, symbol_or_desc: str, exchange: str = None):
        """
        Looks up tokens for a given Symbol or SecDesc.
        
        If `exchange` is provided (e.g., 'NSE', 'BSE', 'CDS'), returns the single 
        token string for that specific exchange match, or None if not found.
        
        If `exchange` is NOT provided, returns a list of dicts for ALL matching 
        rows across every segment/exchange. Each dict contains:
            Token, Exchange, Symbol, SecDesc, Series, MarketLot
        This ensures you see NSE, BSE, CDS, and any other segment matches.
        
        Returns:
            list[dict] when exchange is None — all matching rows.
            str or None when exchange is specified — single token or None.
        """
        if exchange:
            exchange_upper = exchange.upper().strip()
            for token, details in self.token_to_details.items():
                d_symbol = details.get('Symbol', '').strip()
                d_sec_desc = details.get('SecDesc', '').strip()
                d_exch = details.get('Exchange', '').strip().upper()
                if (d_symbol == symbol_or_desc or d_sec_desc == symbol_or_desc) and d_exch == exchange_upper:
                    return token
            return None
        
        # No exchange specified — return all matching rows
        tokens = self.symbol_to_tokens.get(symbol_or_desc, [])
        results = []
        for t in tokens:
            details = self.token_to_details.get(t, {})
            results.append({
                'Token': t,
                'Exchange': details.get('Exchange', '').strip(),
                'Symbol': details.get('Symbol', '').strip(),
                'SecDesc': details.get('SecDesc', '').strip(),
                'Series': details.get('Series', '').strip(),
                'MarketLot': details.get('MarketLot', '').strip(),
            })
        return results if results else []

    def search(self, name: str):
        """
        Case-insensitive search: returns all rows where Symbol or SecDesc 
        contains the given name. Useful for fuzzy discovery of instruments.
        
        Returns:
            list[dict] — matching rows with Token, Exchange, Symbol, SecDesc, Series, MarketLot.
        """
        name_upper = name.upper().strip()
        results = []
        seen_tokens = set()
        for token, details in self.token_to_details.items():
            if token in seen_tokens:
                continue
            d_symbol = details.get('Symbol', '').strip().upper()
            d_sec_desc = details.get('SecDesc', '').strip().upper()
            if name_upper in d_symbol or name_upper in d_sec_desc:
                seen_tokens.add(token)
                results.append({
                    'Token': token,
                    'Exchange': details.get('Exchange', '').strip(),
                    'Symbol': details.get('Symbol', '').strip(),
                    'SecDesc': details.get('SecDesc', '').strip(),
                    'Series': details.get('Series', '').strip(),
                    'MarketLot': details.get('MarketLot', '').strip(),
                })
        return results

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
