"""Sector mapping for all stocks in the scan universe."""

from __future__ import annotations

from stocks import ALL_SECTORS, get_scan_universe

STOCK_SECTOR: dict[str, str] = {
    # Banking
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
    "UNIONBANK": "Banking",
    "IDFCFIRSTB": "Banking",
    "FEDERALBNK": "Banking",
    "AUBANK": "Banking",
    # IT
    "INFY": "IT",
    "TCS": "IT",
    "HCLTECH": "IT",
    "TECHM": "IT",
    "WIPRO": "IT",
    "LTIM": "IT",
    "NAUKRI": "IT",
    "PERSISTENT": "IT",
    "COFORGE": "IT",
    "MPHASIS": "IT",
    "OFSS": "IT",
    "TATAELXSI": "IT",
    # Energy
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
    "OIL": "Energy",
    "PETRONET": "Energy",
    "IGL": "Energy",
    "MGL": "Energy",
    # Metal
    "TATASTEEL": "Metal",
    "JSWSTEEL": "Metal",
    "HINDALCO": "Metal",
    "VEDL": "Metal",
    "JINDALSTEL": "Metal",
    "SAIL": "Metal",
    "NMDC": "Metal",
    "NATIONALUM": "Metal",
    "HINDZINC": "Metal",
    # Pharma
    "SUNPHARMA": "Pharma",
    "DRREDDY": "Pharma",
    "CIPLA": "Pharma",
    "DIVISLAB": "Pharma",
    "AUROPHARMA": "Pharma",
    "TORNTPHARM": "Pharma",
    "LUPIN": "Pharma",
    "BIOCON": "Pharma",
    "ALKEM": "Pharma",
    "ZYDUSLIFE": "Pharma",
    # Finance / NBFC / Insurance
    "LICI": "Finance",
    "BAJFINANCE": "Finance",
    "BAJAJFINSV": "Finance",
    "HDFCLIFE": "Finance",
    "SBILIFE": "Finance",
    "ICICIPRULI": "Finance",
    "CHOLAFIN": "Finance",
    "JIOFIN": "Finance",
    "HDFCAMC": "Finance",
    "MUTHOOTFIN": "Finance",
    "SHRIRAMFIN": "Finance",
    "ICICIGI": "Finance",
    # Auto
    "MARUTI": "Auto",
    "TMPV": "Auto",
    "M&M": "Auto",
    "BAJAJ-AUTO": "Auto",
    "HEROMOTOCO": "Auto",
    "MOTHERSON": "Auto",
    "TVSMOTOR": "Auto",
    "EICHERMOT": "Auto",
    "BOSCHLTD": "Auto",
    # FMCG
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
    "MARICO": "FMCG",
    "VBL": "FMCG",
    "TRENT": "Consumer",
    "DMART": "Consumer",
    "PAGEIND": "Consumer",
    # Infra / Capital goods
    "ULTRACEMCO": "Infra",
    "LT": "Infra",
    "GRASIM": "Infra",
    "ADANIPORTS": "Infra",
    "DLF": "Infra",
    "ABB": "Infra",
    "SIEMENS": "Infra",
    "AMBUJACEM": "Infra",
    "SHREECEM": "Infra",
    "BHEL": "Infra",
    "CONCOR": "Infra",
    "CGPOWER": "Infra",
    "DIXON": "Infra",
    "HAVELLS": "Infra",
    "BEL": "Infra",
    "POLYCAB": "Infra",
    # Telecom
    "BHARTIARTL": "Telecom",
    # Realty
    "GODREJPROP": "Realty",
    "OBEROIRLTY": "Realty",
    "PHOENIXLTD": "Realty",
    "PRESTIGE": "Realty",
    # Aviation / Services
    "INDIGO": "Aviation",
    "IRCTC": "Services",
    "INDHOTEL": "Services",
    # Chemicals
    "SRF": "Chemicals",
    "PIDILITIND": "Chemicals",
    "BERGEPAINT": "Chemicals",
    # Healthcare
    "APOLLOHOSP": "Healthcare",
    "MAXHEALTH": "Healthcare",
    "MANKIND": "Healthcare",
    # PSU
    "NHPC": "PSU",
    "HUDCO": "PSU",
    "IRFC": "PSU",
    "RECLTD": "PSU",
    "PFC": "PSU",
    "HAL": "PSU",
    "COALINDIA": "PSU",
}


def sector_for(symbol: str) -> str:
    return STOCK_SECTOR.get(symbol, "Other")


def symbols_by_sector() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {s: [] for s in ALL_SECTORS}
    groups["Other"] = []
    for sym in get_scan_universe():
        sec = sector_for(sym)
        groups.setdefault(sec, []).append(sym)
    return {k: v for k, v in groups.items() if v}


def group_rows_by_sector(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        sec = sector_for(row["symbol"])
        out.setdefault(sec, []).append(row)
    return out
