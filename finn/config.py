from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')
PORTFOLIO = [
    ('RELIANCE', 'Energy & retail'), ('TCS', 'IT services'),
    ('HDFCBANK', 'Banking'), ('ICICIBANK', 'Banking'),
    ('LT', 'Engineering'), ('BHARTIARTL', 'Telecom'), ('ITC', 'Consumer'),
    ('SUNPHARMA', 'Pharma'), ('MARUTI', 'Auto'), ('TITAN', 'Retail'),
    ('ASIANPAINT', 'Paints'), ('ULTRACEMCO', 'Cement'), ('TATASTEEL', 'Metals'),
    ('TRENT', 'Retail'), ('DIXON', 'Electronics'), ('CDSL', 'Market infrastructure'),
    ('ASTRAL', 'Building materials'), ('KPITTECH', 'Auto software'),
]
RESEARCH_PICKS = ['INFY', 'PERSISTENT', 'TATACONSUM']
CATEGORIES = ['results', 'M&A', 'fund_raise', 'management_change', 'litigation',
              'insider_trading', 'earnings_call', 'investor_meetings', 'credit_rating',
              'dividend', 'bonus_split', 'capex', 'press_release', 'postal_ballot',
              'agm_egm', 'pledging', 'unclassified']
DEFAULT_RULES = {'price_pct': 4.0, 'volume_multiple': 2.0, 'lookback': 20,
                 'categories': ['results', 'M&A', 'management_change', 'litigation',
                                'credit_rating', 'capex', 'pledging'],
                 'min_priority': 70}
