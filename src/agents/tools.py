from langchain.tools import StructuredTool
from datetime import datetime, timedelta
from typing import List, Dict

from ..utils import logger
from .market import MarketConditionAgent

log = logger.get_logger(__name__)

class StockAnalysisTools:
    def __init__(self, data_ingestor, llm=None):
        self.data_ingestor = data_ingestor
        self.llm = llm

    def search_additional_news(self, query: str, stock_code: str) -> str:
        """
        Searches for additional company-specific news when more information is needed before making a final decision.
        """
        log.info(f"Tool 'search_additional_news' called with query: '{query}' for stock: {stock_code}")
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            news_list: List[Dict] = self.data_ingestor.get_company_news(stock_code, start_date, end_date)
            if not news_list:
                return f"No recent news found for {stock_code} in the last 7 days."
            filtered_news = [
                f"Headline: {news['headline']}\nSummary: {news['summary']}"
                for news in news_list if query.lower() in (news.get('headline', '') + news.get('summary', '')).lower()
            ]
            if not filtered_news:
                latest_headlines = "\n".join([f"- {n['headline']}" for n in news_list[:5]])
                return f"No news matching '{query}'. Latest headlines:\n{latest_headlines}"
            return "Found relevant news:\n\n" + "\n\n".join(filtered_news)
        except Exception as e:
            log.error(f"Error in search_additional_news tool: {e}")
            return "An error occurred while searching for news."

    def reassess_vix_threshold(self) -> str:
        """
        Call this tool ONLY when there is significant market-wide news or high uncertainty to re-evaluate the market risk.
        """
        log.info("Tool 'reassess_vix_threshold' called by LLM.")
        if not self.llm:
            return "Error: LLM is not available to perform this action."
        try:
            current_vix = self.data_ingestor.get_vix_index()
            market_index = self.data_ingestor.get_market_index()
            news = self.data_ingestor.get_general_market_news('general')
            threshold_agent = MarketConditionAgent(self.llm)
            assessment = threshold_agent.analyze(current_vix, market_index, news)
            return f"Successfully reassessed market risk. New VIX threshold is {assessment.get('dynamic_vix_threshold')}. Reasoning: {assessment.get('reasoning')}"
        except Exception as e:
            log.error(f"Error in reassess_vix_threshold tool: {e}")
            return "An error occurred during VIX threshold reassessment."

    def get_tools(self) -> list:
        search_tool = StructuredTool.from_function(
            func=self.search_additional_news,
            name="search_additional_news",
            description="Searches for additional company-specific news."
        )
        vix_reassess_tool = StructuredTool.from_function(
            func=self.reassess_vix_threshold,
            name="reassess_vix_threshold",
            description="Re-evaluates and sets a new dynamic VIX risk threshold based on current market conditions."
        )
        return [search_tool, vix_reassess_tool]