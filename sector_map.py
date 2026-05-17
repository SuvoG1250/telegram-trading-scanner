"""Sector mapping for large-cap Indian stocks."""

from __future__ import annotations

from stocks import NIFTY_100_SYMBOLS

STOCK_SECTOR: dict[str, str] = {
    "HDFCBANK": "Banking",
    "ICICIBANK": "Banking",
    "KOTAKBANK": "Banking",
    "AXISBANK": "Banking",
    "SBIN": "Banking",
    "INDUSINDBK": "Banking",
    "BANKBARODA": "Banking",
    "CANBK": "Banking",
    "PNB": "Banking",
    "YESBANK": "Banking",
    "INFY": "IT",
    "TCS": "IT",
    "HCLTECH": "IT",
    "TECHM": "IT",
    "WIPRO": "IT",
    "LTIM": "IT",
    "NAUKRI": "IT",
    "RELIANCE": "Energy",
    "ONGC": "Energy",
    "BPCL": "Energy",
    "COALINDIA": "Energy",
    "NTPC": "Energy",
    "POWERGRID": "Energy",
    "TATAPOWER": "Energy",
    "GAIL": "Energy",
    "IOC": "Energy",
    "ADANIENT": "Energy",
    "ADANIGREEN": "Energy",
    "TATASTEEL": "Metal",
    "JSWSTEEL": "Metal",
    "HINDALCO": "Metal",
    "VEDL": "Metal",
    "JINDALSTEL": "Metal",
    "COALINDIA": "Metal",
    "SUNPHARMA": "Pharma",
    "DRREDDY": "Pharma",
    "CIPLA": "Pharma",
    "DIVISLAB": "Pharma",
    "AUROPHARMA": "Pharma",
    "TORNTPHARM": "Pharma",
    "LICI": "Finance",
    "BAJFINANCE": "Finance",
    "BAJAJFINSV": "Finance",
    "HDFCLIFE": "Finance",
    "SBILIFE": "Finance",
    "ICICIPRULI": "Finance",
    "CHOLAFIN": "Finance",
    "JIOFIN": "Finance",
    "MARUTI": "Auto",
    "TATAMOTORS": "Auto",
    "M&M": "Auto",
    "BAJAJ-AUTO": "Auto",
    "HEROMOTOCO": "Auto",
    "EICHERMOT": "Auto",
    "MOTHERSON": "Auto",
    "TVSMOTOR": "Auto",
    "HINDUNILVR": "FMCG",
    "ITC": "FMCG",
    "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG",
    "TATACONSUM": "FMCG",
    "DABUR": "FMCG",
    "GODREJCP": "FMCG",
    "COLPAL": "FMCG",
    "ASIANPAINT": "FMCG",
    "TITAN": "FMCG",
    "ULTRACEMCO": "Infra",
    "LT": "Infra",
    "GRASIM": "Infra",
    "ADANIPORTS": "Infra",
    "DLF": "Infra",
    "ABB": "Infra",
    "SIEMENS": "Infra",
    "BHARTIARTL": "Telecom",
    "INDIGO": "Aviation",
    "IRCTC": "Services",
}


def sector_for(symbol: str) -> str:
    return STOCK_SECTOR.get(symbol, "Other")


def symbols_by_sector() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for sym in NIFTY_100_SYMBOLS:
        sec = sector_for(sym)
        groups.setdefault(sec, []).append(sym)
    return groups
