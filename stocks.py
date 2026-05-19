"""NSE universes: Nifty 50, Nifty 100, and extended sector coverage."""

from __future__ import annotations

NIFTY_50_SYMBOLS: list[str] = [
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BPCL",
    "BHARTIARTL",
    "BRITANNIA",
    "CIPLA",
    "COALINDIA",
    "DIVISLAB",
    "DRREDDY",
    "EICHERMOT",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "ITC",
    "INDUSINDBK",
    "INFY",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NTPC",
    "NESTLEIND",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SUNPHARMA",
    "TCS",
    "TATACONSUM",
    "TMPV",
    "TATASTEEL",
    "TECHM",
    "TITAN",
    "ULTRACEMCO",
    "WIPRO",
    "HDFCAMC",
]

NIFTY_100_EXTRA: list[str] = [
    "ABB",
    "ADANIGREEN",
    "AMBUJACEM",
    "AUROPHARMA",
    "BANKBARODA",
    "BEL",
    "BERGEPAINT",
    "BOSCHLTD",
    "CANBK",
    "CHOLAFIN",
    "COLPAL",
    "DABUR",
    "DLF",
    "GAIL",
    "GODREJCP",
    "HAVELLS",
    "ICICIPRULI",
    "INDIGO",
    "IOC",
    "JINDALSTEL",
    "LICI",
    "LTIM",
    "MOTHERSON",
    "NAUKRI",
    "PIDILITIND",
    "PNB",
    "SHREECEM",
    "SIEMENS",
    "TATAPOWER",
    "TORNTPHARM",
    "TRENT",
    "VEDL",
    "DMART",
    "HAL",
    "IRCTC",
    "JIOFIN",
    "MAXHEALTH",
    "NHPC",
    "POLYCAB",
    "SRF",
    "TVSMOTOR",
    "VBL",
    "YESBANK",
    "INDHOTEL",
    "PIIND",
    "MANKIND",
    "CGPOWER",
    "DIXON",
    "ETERNAL",
    "HUDCO",
    "IRFC",
    "RECLTD",
    "PFC",
    "UNIONBANK",
    "IDFCFIRSTB",
    "FEDERALBNK",
    "AUBANK",
    "MARICO",
    "PAGEIND",
    "PERSISTENT",
    "COFORGE",
    "MPHASIS",
    "OFSS",
    "TATAELXSI",
    "LUPIN",
    "BIOCON",
    "ALKEM",
    "ZYDUSLIFE",
    "ICICIGI",
    "MUTHOOTFIN",
    "SHRIRAMFIN",
    "GODREJPROP",
    "OBEROIRLTY",
    "PHOENIXLTD",
    "PRESTIGE",
    "BHEL",
    "CONCOR",
    "SAIL",
    "NMDC",
    "NATIONALUM",
    "HINDZINC",
    "OIL",
    "PETRONET",
    "IGL",
    "MGL",
]

NIFTY_100_SYMBOLS: list[str] = sorted(set(NIFTY_50_SYMBOLS + NIFTY_100_EXTRA))

# All sectors represented (used for reports)
ALL_SECTORS: list[str] = [
    "Banking",
    "IT",
    "Energy",
    "Metal",
    "Pharma",
    "Finance",
    "Auto",
    "FMCG",
    "Infra",
    "Telecom",
    "Realty",
    "Aviation",
    "Chemicals",
    "Consumer",
    "Healthcare",
    "PSU",
    "Services",
]


_NIFTY_500_CACHE: list[str] | None = None
_FNO_CACHE: set[str] | None = None

_NSE_FNO_API = "https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O"

_NIFTY500_CSV_URL = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
)


def _try_download_nifty500_csv(path) -> None:
    """Fetch NSE index CSV when missing (e.g. fresh clone / GitHub Actions)."""
    import logging
    import urllib.request

    logger = logging.getLogger(__name__)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            _NIFTY500_CSV_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NSE-Scanner/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
        logger.info("Downloaded Nifty 500 list to %s", path)
    except Exception:
        logger.warning("Could not download Nifty 500 CSV; falling back to Nifty 100.")


def load_nifty500_symbols() -> list[str]:
    """Load Nifty 500 from data/ind_nifty500list.csv (NSE archive)."""
    global _NIFTY_500_CACHE
    if _NIFTY_500_CACHE is not None:
        return _NIFTY_500_CACHE
    import csv
    from pathlib import Path

    path = Path(__file__).resolve().parent / "data" / "ind_nifty500list.csv"
    if not path.is_file():
        _try_download_nifty500_csv(path)
    if not path.is_file():
        _NIFTY_500_CACHE = list(NIFTY_100_SYMBOLS)
        return _NIFTY_500_CACHE
    symbols: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip()
            if sym and (row.get("Series") or "EQ").strip() == "EQ":
                symbols.append(sym)
    _NIFTY_500_CACHE = sorted(set(symbols)) if symbols else list(NIFTY_100_SYMBOLS)
    return _NIFTY_500_CACHE


def _load_fno_from_nse_api() -> set[str]:
    import json
    import logging
    import urllib.request

    logger = logging.getLogger(__name__)
    try:
        req = urllib.request.Request(
            _NSE_FNO_API,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; NSE-Scanner/1.0)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        rows = body.get("data") or []
        syms = {
            str(row.get("symbol", "")).strip().upper()
            for row in rows
            if row.get("symbol")
        }
        syms.discard("")
        if syms:
            return syms
    except Exception:
        logger.warning("Could not fetch NSE F&O list from API.")
    return set()


def _save_fno_cache(symbols: set[str]) -> None:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent / "data" / "fno_symbols.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(symbols), indent=2), encoding="utf-8")


def load_fno_symbols(*, refresh: bool = False) -> set[str]:
    """NSE F&O underlying symbols (cached in data/fno_symbols.json)."""
    global _FNO_CACHE
    if _FNO_CACHE is not None and not refresh:
        return _FNO_CACHE

    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent / "data" / "fno_symbols.json"
    if path.is_file() and not refresh:
        try:
            loaded = set(json.loads(path.read_text(encoding="utf-8")))
            if loaded:
                _FNO_CACHE = loaded
                return _FNO_CACHE
        except Exception:
            pass

    syms = _load_fno_from_nse_api()
    if not syms:
        syms = set(NIFTY_100_SYMBOLS)
    _save_fno_cache(syms)
    _FNO_CACHE = syms
    return _FNO_CACHE


def is_fno_eligible(symbol: str) -> bool:
    return symbol.upper() in load_fno_symbols()


def get_scan_universe() -> list[str]:
    """Intraday scan universe — Nifty 500 when CSV present, else Nifty 100."""
    from config import SCAN_UNIVERSE_MODE

    if SCAN_UNIVERSE_MODE == "nifty500":
        return load_nifty500_symbols()
    return list(NIFTY_100_SYMBOLS)


def get_tradeable_universe() -> list[str]:
    """Nifty universe intersected with F&O underlyings (before move/liquidity filters)."""
    base = set(get_scan_universe())
    fno = load_fno_symbols()
    return sorted(base & fno)


def to_yfinance_symbol(symbol: str) -> str:
    from config import YFINANCE_SUFFIX

    return f"{symbol}{YFINANCE_SUFFIX}"
