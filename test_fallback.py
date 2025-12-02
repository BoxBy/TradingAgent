import sys
import os
import logging

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.data_providers.ingestion import DataIngestion
from src.data_providers.kis_wrapper import TradingInterface
from src import config

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

def test_fallback():
    log.info("--- Testing Fallback Logic ---")
    
    # Initialize KIS Wrapper (Mock or Real)
    kis = TradingInterface(mock_trading=True)
    
    # Initialize Ingestion
    ingestor = DataIngestion(kis)
    
    # Force Finnhub Client to None to simulate error/missing key
    ingestor.finnhub_client = None
    log.info("Simulating Finnhub failure (client set to None)")
    
    # Test 1: Fundamental Data Fallback (KR)
    stock_code_kr = "005930" # Samsung Electronics
    log.info(f"Testing Fundamental Fallback for {stock_code_kr}...")
    data_kr = ingestor.get_fundamental_data(stock_code_kr)
    log.info(f"Result KR: {data_kr}")
    
    # Test 2: Fundamental Data Fallback (US)
    stock_code_us = "AAPL"
    log.info(f"Testing Fundamental Fallback for {stock_code_us}...")
    data_us = ingestor.get_fundamental_data(stock_code_us)
    log.info(f"Result US: {data_us}")
    
    # Test 3: News Fallback (KR)
    log.info(f"Testing News Fallback for {stock_code_kr}...")
    news_kr = ingestor.get_company_news(stock_code_kr, "2024-01-01", "2024-01-31")
    log.info(f"Result KR News count: {len(news_kr)}")
    if news_kr:
        log.info(f"Sample KR News: {news_kr[0]['headline']}")

    # Test 4: News Fallback (US)
    log.info(f"Testing News Fallback for {stock_code_us}...")
    news_us = ingestor.get_company_news(stock_code_us, "2024-01-01", "2024-01-31")
    log.info(f"Result US News count: {len(news_us)}")
    if news_us:
        log.info(f"Sample US News: {news_us[0]['headline']}")

if __name__ == "__main__":
    test_fallback()
