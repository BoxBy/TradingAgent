# ==============================================================================
# Market Prompts (Simplified for public repository)
# ==============================================================================

NEWS_SCREENER_PROMPT = """From the news headlines, identify stock tickers.
{news_headlines}
Respond with a JSON object with a 'tickers' key. Example: {{"tickers": ["AAPL", "MSFT"]}}"""

MARKET_CONDITION_PROMPT = """Analyze the market data and suggest action thresholds.
- VIX: {vix_value:.2f}
- S&P 500: {market_index_value:,.2f}
- News:
{news_headlines}

Respond with a JSON object containing 'vix_threshold', 'buy_threshold', and 'sell_threshold'."""

PORTFOLIO_REVIEW_PROMPT = """Review the stock holding based on the provided data.

**Stock:** {stock_code}
**Initial Reason:** {initial_reasoning}
**Current Analysis:** {current_analysis}

Provide a `conviction_score` from -10 to 10 and a brief 'summary'.
Respond in JSON format."""

EMERGENCY_NEWS_PROMPT = """Is the following news an emergency for {stock_code} that requires immediate action?

News:
{news_headlines}

Respond with a JSON object: {{"is_emergency": <true_or_false>, "reason": "..."}}"""


# ==============================================================================
# Stock Prompts (Simplified for public repository)
# ==============================================================================

SENTIMENT_ANALYSIS_PROMPT = """Analyze the sentiment of these news headlines: {news_headlines}. Respond in JSON with 'sentiment' and 'summary' keys."""

FUNDAMENTAL_ANALYSIS_PROMPT = """Analyze the company's financial data: {data}. Respond in JSON with 'health' and 'valuation' keys."""

QUALITATIVE_ANALYSIS_PROMPT = """Analyze the company's profile: {profile} and news: {news}. Respond in JSON with 'moat' and 'management' keys."""

CHART_PATTERN_PROMPT = """Analyze the chart data and provide a summary.

{chart_data}

Respond with a simple text summary."""


# ==============================================================================
# Main Orchestrator Prompts (Simplified for public repository)
# ==============================================================================

FINAL_BATCH_DECISION_SYSTEM_PROMPT = """You are a trading AI. Analyze the provided stock data and decide whether to BUY, SELL, or HOLD each stock.
For each stock, provide a decision and a brief reasoning.
Your final output must be a single JSON object with a 'decisions' key holding a list of plans."""

FINAL_BATCH_DECISION_HUMAN_PROMPT = """Today's Date: {current_date}
Account Status: {account_status}

Analyze the following stocks:
{comprehensive_analyses}"""


# ==============================================================================
# Emergency Manager Prompts (Simplified for public repository)
# ==============================================================================

IDENTIFY_AFFECTED_STOCKS_PROMPT = """An event has occurred: "{event}"
Which of these stocks are affected? {portfolio}
Return a JSON list of stock codes."""

ANALYZE_IMPACT_PROMPT = """Analyze the impact of the event: "{event}" on stock {stock_code}.
Respond in JSON with 'impact' ('positive' or 'negative') and 'reasoning'."""
