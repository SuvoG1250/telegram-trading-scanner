"""NSE universes: Nifty indices, full EQ list, and price-band scan lists."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

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
_NSE_EQUITY_CSV_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_PRICE_BAND_CACHE = Path(__file__).resolve().parent / "data" / "nse_universe_100_1000.json"


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


def _try_download_csv(url: str, path: Path, label: str) -> None:
    import urllib.request

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NSE-Scanner/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            path.write_bytes(resp.read())
        logger.info("Downloaded %s to %s", label, path)
    except Exception:
        logger.warning("Could not download %s.", label)


def load_nse_equity_symbols() -> list[str]:
    """All NSE equity (EQ series) symbols from EQUITY_L.csv."""
    import csv

    path = Path(__file__).resolve().parent / "data" / "EQUITY_L.csv"
    if not path.is_file():
        _try_download_csv(_NSE_EQUITY_CSV_URL, path, "NSE EQUITY_L")
    if not path.is_file():
        logger.warning("EQUITY_L.csv missing; using Nifty 500 as fallback.")
        return load_nifty500_symbols()

    symbols: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = (row.get("SYMBOL") or row.get("Symbol") or "").strip().upper()
            series = (row.get("SERIES") or row.get("Series") or "EQ").strip().upper()
            if sym and series == "EQ" and len(sym) >= 2:
                symbols.append(sym)
    out = sorted(set(symbols))
    return out if out else load_nifty500_symbols()


def _extract_last_closes(data, chunk: list[str]) -> dict[str, float]:
    """Parse yfinance multi-ticker download into symbol -> last close."""
    import pandas as pd

    from config import YFINANCE_SUFFIX

    prices: dict[str, float] = {}
    if data is None or (hasattr(data, "empty") and data.empty):
        return prices

    if len(chunk) == 1:
        sym = chunk[0]
        try:
            close = data["Close"].dropna()
            if len(close):
                prices[sym] = float(close.iloc[-1])
        except Exception:
            pass
        return prices

    if not isinstance(data.columns, pd.MultiIndex):
        return prices

    names = data.columns.names or ()
    tickers_in = set(data.columns.get_level_values(0))
    for sym in chunk:
        yf_sym = f"{sym}{YFINANCE_SUFFIX}"
        if yf_sym not in tickers_in:
            continue
        try:
            if names == ("Ticker", "Price"):
                close = data[(yf_sym, "Close")].dropna()
            else:
                close = data[("Close", yf_sym)].dropna()
            if len(close):
                prices[sym] = float(close.iloc[-1])
        except Exception:
            continue
    return prices


def _fetch_chunk_closes(chunk: list[str]) -> dict[str, float]:
    """Download last closes for a symbol chunk (yfinance batch + per-symbol fallback)."""
    import time

    import yfinance as yf

    from config import YFINANCE_SUFFIX
    from data_fetcher import fetch_daily

    tickers = [f"{s}{YFINANCE_SUFFIX}" for s in chunk]
    prices: dict[str, float] = {}
    try:
        data = yf.download(
            tickers,
            period="10d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=False,
            progress=False,
        )
        prices.update(_extract_last_closes(data, chunk))
    except Exception:
        logger.warning("Batch price fetch failed for chunk starting %s", chunk[0] if chunk else "?")

    missing = [s for s in chunk if s not in prices]
    for sym in missing:
        daily = fetch_daily(sym, period="1mo")
        if daily.empty:
            continue
        prices[sym] = float(daily["Close"].iloc[-1])
        time.sleep(0.35)
    return prices


def filter_symbols_by_price(
    symbols: list[str],
    min_price: float,
    max_price: float,
    *,
    chunk_size: int = 25,
) -> list[str]:
    """Keep symbols whose latest daily close is within [min_price, max_price]."""
    import time

    matched: list[str] = []
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        prices = _fetch_chunk_closes(chunk)
        for sym, px in prices.items():
            if min_price <= px <= max_price:
                matched.append(sym)
        if i + chunk_size < len(symbols):
            time.sleep(1.2)
        if (i // chunk_size) % 20 == 0 and i > 0:
            logger.info("Price filter progress: %s / %s (matched %s)", i, len(symbols), len(matched))
    return sorted(set(matched))


def refresh_nse_price_band_universe(*, force: bool = False) -> list[str]:
    """
    Build/cache NSE EQ symbols with last close in configured price band.
    Cache file: data/nse_universe_100_1000.json (IST date).
    """
    from config import MAX_STOCK_PRICE, MIN_STOCK_PRICE
    from market_time import today_key

    day = today_key()
    if _PRICE_BAND_CACHE.is_file() and not force:
        try:
            cached = json.loads(_PRICE_BAND_CACHE.read_text(encoding="utf-8"))
            if (
                cached.get("date") == day
                and float(cached.get("min_price", 0)) == MIN_STOCK_PRICE
                and float(cached.get("max_price", 0)) == MAX_STOCK_PRICE
            ):
                syms = list(cached.get("symbols") or [])
                if syms:
                    logger.info("Price-band universe from cache: %s symbols", len(syms))
                    return syms
        except Exception:
            pass

    all_eq = load_nse_equity_symbols()
    logger.info(
        "Filtering %s NSE EQ symbols for Rs %.0f-%.0f …",
        len(all_eq),
        MIN_STOCK_PRICE,
        MAX_STOCK_PRICE,
    )
    band = filter_symbols_by_price(all_eq, MIN_STOCK_PRICE, MAX_STOCK_PRICE)
    if not band:
        logger.warning("Price-band filter returned 0 symbols; using Nifty 500 in band as fallback.")
        band = filter_symbols_by_price(load_nifty500_symbols(), MIN_STOCK_PRICE, MAX_STOCK_PRICE)
    payload = {
        "date": day,
        "min_price": MIN_STOCK_PRICE,
        "max_price": MAX_STOCK_PRICE,
        "source_count": len(all_eq),
        "symbols": band,
    }
    _PRICE_BAND_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _PRICE_BAND_CACHE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Price-band universe saved: %s / %s symbols", len(band), len(all_eq))
    return band


def load_nse_price_band_symbols(*, refresh: bool = False) -> list[str]:
    """Cached intraday universe: all NSE EQ in MIN_STOCK_PRICE–MAX_STOCK_PRICE."""
    if refresh:
        return refresh_nse_price_band_universe(force=True)
    band = refresh_nse_price_band_universe(force=False)
    if band:
        return band
    return load_nifty500_symbols()


def get_scan_universe() -> list[str]:
    """Intraday scan universe."""
    from config import SCAN_UNIVERSE_MODE

    mode = (SCAN_UNIVERSE_MODE or "nifty500").lower()
    if mode in ("nse_price_band", "nse_all", "all_nse", "price_band"):
        return load_nse_price_band_symbols()
    if mode == "nifty500":
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
