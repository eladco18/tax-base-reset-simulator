import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
import streamlit as st
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _get_api_session() -> requests.Session:
    """
    Creates a resilient HTTP session with transport-level retries for server errors.
    """
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
    )
    session.mount("https://", adapter)
    return session

_SESSION = _get_api_session()

# --- 1. BOI Exchange Rates ---
_BOI_SERIES_URL = (
    "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/"
    "dataflow/BOI.STATISTICS/EXR/1.0/RER_USD_ILS"
    "?startperiod={start}&endperiod={end}&format=sdmx-json&lang=en"
)


def _parse_boi_response(data: dict) -> pd.DataFrame:
    """Parses BOI SDMX-JSON into a timezone-naive DataFrame."""
    try:
        series_data = data["data"]["dataSets"][0]["series"]
        series_key = next(iter(series_data))
        observations = series_data[series_key]["observations"]
        dim_values = data["data"]["structure"]["dimensions"]["observation"][0]["values"]

        records = {}
        for idx_str, obs_list in observations.items():
            rate = obs_list[0]
            if rate is not None:
                date_str = dim_values[int(idx_str)]["id"]
                records[date_str] = float(rate)

        if not records:
            return pd.DataFrame()

        df = pd.DataFrame.from_dict(records, orient="index", columns=["Close"])
        df.index = pd.to_datetime(df.index)
        df.index.name = "Date"
        df.sort_index(inplace=True)
        return df
    except Exception as exc:
        raise ValueError(f"Unexpected BOI format: {exc}") from exc


@st.cache_data(ttl=3600)
def fetch_historical_exchange_rates(start_date: str) -> pd.DataFrame:
    """Fetches historical USD/ILS rates from the Bank of Israel."""
    end_date = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    url = _BOI_SERIES_URL.format(start=start_date, end=end_date)

    try:
        resp = _SESSION.get(url, timeout=15)
        if resp.status_code == 429:
            st.error("⚠️ BOI API rate limit reached.")
            return pd.DataFrame()
        if resp.status_code != 200:
            st.error(f"⚠️ BOI API error ({resp.status_code}).")
            return pd.DataFrame()

        return _parse_boi_response(resp.json())

    except Exception as exc:
        st.error(f"⚠️ BOI Error: {exc}")
        return pd.DataFrame()


# --- 2. Asset Data (Finnhub / Tiingo) ---
_FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_FINNHUB_PROFILE_URL = "https://finnhub.io/api/v1/stock/profile2"
_FINNHUB_DIVS_URL = "https://finnhub.io/api/v1/stock/dividend2"

_TIINGO_QUOTE_URL = "https://api.tiingo.com/iex/{symbol}"
_TIINGO_META_URL = "https://api.tiingo.com/tiingo/daily/{symbol}"


def _finnhub_fetch(symbol: str, key: str) -> tuple[float, str, bool]:
    """Fetches price, currency, and dividend status from Finnhub."""
    params = {"token": key, "symbol": symbol}

    # 1. Price
    q_resp = _SESSION.get(_FINNHUB_QUOTE_URL, params=params, timeout=10)
    q_resp.raise_for_status()
    price = float(q_resp.json().get("c", 0.0) or q_resp.json().get("pc", 0.0))
    if price == 0.0:
        raise ValueError("Zero price returned.")

    # 2. Currency
    p_resp = _SESSION.get(_FINNHUB_PROFILE_URL, params=params, timeout=10)
    p_resp.raise_for_status()
    currency = (p_resp.json().get("currency") or "USD").upper()

    # 3. Dividends (Best effort)
    pays_div = False
    try:
        d_resp = _SESSION.get(_FINNHUB_DIVS_URL, params=params, timeout=10)
        if d_resp.status_code == 200:
            pays_div = any(d.get("amount", 0) > 0 and d.get("year", 0) >= datetime.today().year - 1
                           for d in (d_resp.json() or []))
    except Exception:
        pass

    return price, currency, pays_div


def _tiingo_fetch(symbol: str, key: str) -> tuple[float, str, bool]:
    """Fetches fallback data from Tiingo."""
    headers = {"Authorization": f"Token {key}"}

    # 1. Price
    q_resp = _SESSION.get(_TIINGO_QUOTE_URL.format(symbol=symbol), headers=headers, timeout=10)
    q_resp.raise_for_status()
    data = q_resp.json()
    if not data: raise ValueError("Empty quote.")
    price = float(data[0].get("last") or data[0].get("tngoLast") or 0.0)

    # 2. Currency
    m_resp = _SESSION.get(_TIINGO_META_URL.format(symbol=symbol), headers=headers, timeout=10)
    m_resp.raise_for_status()
    currency = (m_resp.json().get("currency") or "USD").upper()

    return price, currency, False


@st.cache_data(ttl=3600)
def fetch_asset_data(ticker_symbol: str, fh_key: str, tg_key: str):
    """
    Fetches real-time asset data using Finnhub (primary) and Tiingo (fallback).
    Takes API keys as arguments to ensure cache safety.
    """
    symbol = ticker_symbol.strip().upper()

    if not fh_key and not tg_key:
        st.error("🚨 Missing API Keys in secrets.")
        return 0.0, "ERROR", False

    if fh_key:
        try:
            return _finnhub_fetch(symbol, fh_key)
        except Exception as e:
            st.warning(f"⚠️ Finnhub error: {e}. Trying fallback...")

    if tg_key:
        try:
            return _tiingo_fetch(symbol, tg_key)
        except Exception as e:
            st.error(f"🚨 Tiingo error: {e}")

    return 0.0, "ERROR", False


def get_historical_rate_for_date(target_date, df_history: pd.DataFrame, fallback_rate=None) -> float:
    """Finds the USD/ILS exchange rate for a specific past date, strictly handling timezones."""
    if df_history.empty:
        return fallback_rate

    try:
        # 1. Create a clean, timezone-naive copy of the historical index
        df_clean = df_history.copy()
        df_clean.index = pd.to_datetime(df_clean.index).tz_localize(None).normalize()

        # 2. Clean the target date from the user
        target_dt = pd.to_datetime(target_date).normalize()

        # 3. Use 'asof' to find the exact date, or the closest PREVIOUS trading day (for weekends/holidays)
        closest_date = df_clean.index.asof(target_dt)

        if pd.isna(closest_date):
            return fallback_rate  # Target date is older than available history

        rate = df_clean.loc[closest_date, 'Close']

        # 4. Handle edge cases where Yahoo might return duplicate rows for the same day
        if isinstance(rate, pd.Series):
            return float(rate.iloc[0])

        return float(rate)

    except Exception:
        # We silently fall back, but safely
        return fallback_rate