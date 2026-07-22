import csv
import urllib.request
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ScripMaster:
    """
    Manages downloading and parsing the daily Scrip Master from Choice API.
    Provides fast lookups for Tokens, Lot Sizes, and Symbols.
    """
    def __init__(self):
        self.symbol_to_token = {}
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
                        
                        # Map symbol/sec_desc to token for reverse lookup
                        if sec_desc:
                            self.symbol_to_token[sec_desc] = token
                        if symbol and symbol not in self.symbol_to_token:
                            self.symbol_to_token[symbol] = token
                            
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

    def get_token(self, symbol_or_desc: str, exchange: str = None) -> str:
        """
        Returns the Token for a given Symbol or SecDesc.
        If exchange is provided (e.g., 'NSE' or 'BSE'), it will ensure the exact match for that exchange.
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
            
        return self.symbol_to_token.get(symbol_or_desc)

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
