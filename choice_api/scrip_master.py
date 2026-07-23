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
        self.symbol_to_rows = defaultdict(list)
        self.token_to_details = {}
        self.all_rows = []
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
                        self.all_rows.append(row)
                        
                        # Map symbol/sec_desc to the row itself
                        if symbol:
                            self.symbol_to_rows[symbol].append(row)
                        if sec_desc and sec_desc != symbol:
                            self.symbol_to_rows[sec_desc].append(row)
                            
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
