import re
import time
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import requests

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Tax Basis Step-Up Simulator",
    layout="wide",
    page_icon="📊"
)


# ==========================================
# DATA CACHING (BOI & FINNHUB/TIINGO)
# ==========================================

def _get_api_session() -> requests.Session:
    """
    Creates a resilient HTTP session with transport-level retries for server errors.
    """
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
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
def fetch_asset_data(ticker_symbol: str):
    """
    Fetches real-time asset data using Finnhub (primary) and Tiingo (fallback).
    Requires secrets configured in .streamlit/secrets.toml.
    """
    symbol = ticker_symbol.strip().upper()
    fh_key = st.secrets.get("FINNHUB_API_KEY", "")
    tg_key = st.secrets.get("TIINGO_API_KEY", "")

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


def get_historical_rate_for_date(target_date, df_history: pd.DataFrame, fallback_rate: float) -> float:
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

    except Exception as e:
        # We silently fall back, but safely
        return fallback_rate


# ==========================================
# TAX ENGINE: SECTION 91(B) + THE MOSES FILTER
# ==========================================
def calculate_portfolio_tax(lots: list, current_price: float, current_rate: float):
    """Calculates Section 91(b) tax and offsetting based on the Moses Ruling."""
    total_taxable_profit = 0.0
    total_recognized_loss = 0.0
    lot_results = []

    for index, lot in enumerate(lots):
        units = lot["Units"]
        buy_price = lot["Price"]
        buy_rate = lot["Rate"]

        # Dollar & Shekel calculations
        usd_profit = (current_price - buy_price) * units
        nominal_ils_profit = (current_price * units * current_rate) - (buy_price * units * buy_rate)

        taxable_profit = 0.0
        recognized_loss = 0.0

        # TAX LOGIC: Section 91(b) of the Israeli Income Tax Ordinance
        # Limits the final tax liability so it never exceeds the nominal ILS profit.
        # We calculate the theoretical tax on the real USD profit, and cap it at the nominal ILS profit.
        if usd_profit > 0 and nominal_ils_profit > 0:
            taxable_profit = min(usd_profit * current_rate, nominal_ils_profit)

        # TAX LOGIC: The "Moses" Court Ruling (הלכת מוזס)
        # Prevents investors from claiming artificial capital losses driven purely by currency devaluation.
        # A recognized capital loss for tax offset is strictly the minimum between the real (USD) loss and the nominal (ILS) loss.
        elif usd_profit < 0 and nominal_ils_profit < 0:
            real_ils_loss = abs(usd_profit) * current_rate
            nominal_ils_loss = abs(nominal_ils_profit)
            recognized_loss = min(real_ils_loss, nominal_ils_loss)

        total_taxable_profit += taxable_profit
        total_recognized_loss += recognized_loss

        lot_results.append({
            "Lot #": index + 1,
            "Orig. Date": lot.get("Date", "N/A"),
            "Rem. Units": round(units, 4),
            "USD Profit ($)": round(usd_profit, 2),
            "Nominal ILS (₪)": round(nominal_ils_profit, 2),
            "Taxable Profit (₪)": round(taxable_profit, 2),
            "Recognized Loss (₪)": round(recognized_loss, 2)
        })

    # Auto-Offsetting (Netting)
    net_taxable = max(0, total_taxable_profit - total_recognized_loss)
    final_tax_liability = net_taxable * 0.25

    return final_tax_liability, lot_results, total_taxable_profit, total_recognized_loss


# ==========================================
# SIDEBAR: GLOBAL SETTINGS
# ==========================================
default_start = (datetime.today() - timedelta(days=365 * 5)).strftime('%Y-%m-%d')
df_ils_init = fetch_historical_exchange_rates(default_start)
current_rate = float(df_ils_init['Close'].iloc[-1]) if not df_ils_init.empty else 3.60

st.sidebar.header("⚙️ Global Settings")
ticker_input = st.sidebar.text_input("Asset Ticker (e.g., SPY, QQQ)", value="SPY").upper()

if not re.match(r"^[A-Z0-9\-\.]+$", ticker_input):
    st.sidebar.error("❌ Invalid input: Please enter a valid English ticker symbol (e.g., SPY, QQQ).")
    st.stop()

st.sidebar.markdown("---")
st.sidebar.subheader("💸 Friction Costs")
transaction_costs_usd = st.sidebar.number_input(
    "Buy & Sell Commissions (USD)",
    min_value=0.0,
    value=0.0,
    step=1.0,
    help="Total broker commissions for both the sell and immediate buyback operations (e.g., $5 to sell + $5 to buy = $10 total). This amount is deducted from the capital available for reinvestment."
)

st.sidebar.markdown("---")
st.sidebar.subheader("🔮 Future Projections")
expected_return = st.sidebar.number_input("Expected Annual Return (%)", min_value=-100.0, max_value=100.0, value=5.0,
                                          step=0.5)
investment_horizon = st.sidebar.slider("Investment Horizon (Years)", min_value=1, max_value=30, value=10)

with st.spinner("Initializing Market Data..."):
    current_price, asset_currency, pays_dividend = fetch_asset_data(ticker_input)

future_rate = st.sidebar.number_input("Est. Future USD/ILS Rate", min_value=1.0, max_value=10.0,
                                      value=float(current_rate), step=0.1)


# ==========================================
# MAIN DASHBOARD
# ==========================================
st.title("📊 Capital Gains Tax Simulator: Tax Basis Step-Up Strategy")
st.markdown(
    "Evaluate the financial viability of a **Tax Basis Step-Up** strategy under Section 91(b) of the Israeli Income Tax Ordinance.")

# --- PDF DOWNLOAD BUTTON ---
try:
    with open("Guide.pdf", "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    st.download_button(
        label="📄 Download the Complete Strategy Guide (PDF)",
        data=pdf_bytes,
        file_name="Tax_Base_Step-Up_Guide.pdf",
        mime="application/pdf",
        help="It is highly recommended to read this comprehensive guide before making any decisions or executing trades in your brokerage account."
    )
except FileNotFoundError:
    # If the file is missing, we silently pass or display a placeholder
    pass

# --- MODULE 1: MACRO VIEW (NOW INDEPENDENT) ---
st.header("1. Macro View: Historical Tax Shield Potential")
st.markdown(
    "This chart displays the historical strength of the USD vs. ILS compared to today's rate. A higher past rate translates to a larger potential tax shield today, regardless of the specific asset.")

start_date = st.date_input("Display USD/ILS history starting from:", value=pd.to_datetime(default_start))

with st.spinner("Fetching macro data..."):
    df_ils = fetch_historical_exchange_rates(start_date.strftime('%Y-%m-%d'))

if not df_ils.empty:
    df_ils['R_max_percent'] = ((df_ils['Close'] / current_rate) - 1) * 100

    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=df_ils.index, y=df_ils['R_max_percent'],
        name="Max Tax-Free USD Profit (%)",
        line=dict(color='#2ecc71', width=2.5),
        fill='tozeroy', fillcolor='rgba(46, 204, 113, 0.15)',
        hovertemplate="<b>Date:</b> %{x|%Y-%m-%d}<br><b>Rate:</b> %{customdata:.4f} ILS<br><b>Max Tax-Free USD Profit:</b> %{y:.2f}%<extra></extra>",
        customdata=df_ils['Close']
    ))
    fig1.add_hline(y=0, line_width=1.5, line_color="black", line_dash="dash")

    # Static title, completely detached from the asset and its price
    chart_title = f"Tax Base Step-Up Potential (Current Rate: {current_rate:.4f} ILS)"

    fig1.update_layout(
        title=chart_title,
        xaxis_title="Historical Date", yaxis_title="Maximum Tax-Exempt USD Return (%)",
        template="plotly_white", hovermode="x unified", margin=dict(l=0, r=0, t=40, b=0)
    )
    st.plotly_chart(fig1, use_container_width=True)
    st.caption(
        "* **Note on Exchange Rates:** The system fetches the official Representative Rate (שער יציג) from the Bank of Israel. This rate is published once a day (Mon-Fri) around 15:30 Israel time. Therefore, during morning hours or weekends, the rate reflects the last published business day. For Israeli tax purposes, capital gains are legally calculated using this official daily rate, not live continuous Forex rates.")
else:
    st.warning("Historical exchange rate data is currently unavailable.")

# --- MODULE 2: THE FULL LEDGER ---
st.markdown("---")
st.header("2. The Ledger: Historical Transactions")
st.write(f"Enter your transaction history for **{ticker_input}**. The system will calculate your current open lots using the **FIFO** method.")

# --- Contextual Market Snapshot ---
st.info(
    f"📌 **Asset:** **{ticker_input}** &nbsp;|&nbsp; "
    f"**Price:** **${current_price:,.2f}** &nbsp;|&nbsp; "
    f"**USD/ILS Rate:** **₪{current_rate:,.4f}**"
)

default_ledger = pd.DataFrame(columns=["Date", "Action", "Units", "Price (USD)", "USD/ILS Rate"])
edited_df = st.data_editor(
    default_ledger,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Date": st.column_config.DateColumn("Date", required=True, max_value=datetime.today().date()),
        "Action": st.column_config.SelectboxColumn("Action", options=["Buy", "Sell"], required=True),
        "Units": st.column_config.NumberColumn("Units", min_value=0.001, required=True),
        "Price (USD)": st.column_config.NumberColumn("Price (USD)", min_value=0.01, required=True),
        "USD/ILS Rate": st.column_config.NumberColumn("USD/ILS Rate (Leave blank for Auto-Fill)", min_value=1.0,
                                                      required=False),
    }
)

# Drop rows only if Date, Action, Units, or Price are NaN (Rate is allowed to be NaN now)
edited_df = edited_df.dropna(subset=["Date", "Action", "Units", "Price (USD)"]).reset_index(drop=True)

# Chronological FIFO Algorithm with Stable Sort
edited_df['Action_Rank'] = edited_df['Action'].map({'Buy': 1, 'Sell': 2})
edited_df = edited_df.sort_values(by=["Date", "Action_Rank"]).reset_index(drop=True)

open_lots = []
validation_error = False

for _, row in edited_df.iterrows():
    action = row["Action"]
    units = row["Units"]
    price = row["Price (USD)"]
    date = row["Date"]

    # --- THE AUTO-FILL LOGIC ---
    rate = row["USD/ILS Rate"]
    if pd.isna(rate) or rate <= 0:
        rate = get_historical_rate_for_date(date, df_ils_init, fallback_rate=current_rate)
    # ---------------------------

    if action == "Buy":
        open_lots.append({"Date": date, "Units": units, "Price": price, "Rate": rate})
    elif action == "Sell":
        units_to_sell = units
        current_available = sum(lot["Units"] for lot in open_lots)

        if units_to_sell > current_available:
            st.error(
                f"🛑 **Chronological Error Detected:** On {date.strftime('%Y-%m-%d')}, you logged a 'Sell' of {units_to_sell} units, but the available balance at that moment was only {current_available} units. Negative balances are not permitted. Please correct the ledger.")
            validation_error = True
            break

        # ALGORITHM: Stable FIFO Matching
        # If the sell quantity exceeds the oldest available lot, we fully deplete the oldest lot,
        # subtract its units from the total needed, and advance to the next chronological lot.
        while units_to_sell > 0 and open_lots:
            oldest_lot = open_lots[0]
            if oldest_lot["Units"] <= units_to_sell:
                units_to_sell -= oldest_lot["Units"]
                open_lots.pop(0)
            else:
                oldest_lot["Units"] -= units_to_sell
                units_to_sell = 0

if validation_error:
    st.stop()

# --- MODULE 3: SANITY CHECK ---
st.markdown("---")
st.header("3. Current Portfolio & Sanity Check")

total_units_remaining = sum(lot["Units"] for lot in open_lots)
total_usd_value = total_units_remaining * current_price
total_ils_value = total_usd_value * current_rate

col1, col2, col3 = st.columns(3)
col1.metric("Current Open Shares", f"{total_units_remaining:,.4f}")
col2.metric("Total Current Value (USD)", f"${total_usd_value:,.2f}")
col3.metric("Total Current Value (ILS)", f"₪{total_ils_value:,.2f}")

st.info(
    "💡 **Best Practice:** Verify that the 'Current Open Shares' matches the exact balance in your brokerage account.")
sanity_verified = st.checkbox("I confirm that this accurately reflects my current portfolio.")

if not sanity_verified:
    st.warning(
        "🔒 Please verify your data and check the confirmation box above to unlock the Tax Engine and CFO Strategy.")
    st.stop()
elif total_units_remaining <= 0:
    st.warning("Your current balance is 0. There are no open lots to simulate.")
    st.stop()

# --- MODULE 4: THE TAX ENGINE ---
st.markdown("---")
st.header("4. The Tax Engine: Section 91(b) & Moses Ruling")
st.write(
    "This engine calculates your tax liability per lot, applying the Moses Ruling to separate recognized capital losses from nominal currency losses, and automatically offsets losses against profits.")

# Run the updated Tax Engine
total_tax_today, lot_results, tot_taxable, tot_loss = calculate_portfolio_tax(open_lots, current_price, current_rate)

res_df = pd.DataFrame(lot_results)
with st.expander("🔍 Click to view Advanced Tax Lot Breakdown", expanded=True):
    st.dataframe(res_df, use_container_width=True)

col_t1, col_t2 = st.columns(2)
col_t1.metric(
    "Total Taxable Profit",
    f"₪{tot_taxable:,.2f}",
    help="הרווח החייב במס לאחר הפעלת 'רצפת ההגנה' הנומינלית של סעיף 91(ב). המערכת מחשבת את המס על הנמוך מבין הרווח השקלי לרווח הדולרי."
)
col_t2.metric(
    "Total Recognized Loss (Moses)",
    f"₪{tot_loss:,.2f}",
    help="הפסד הון המוכר לקיזוז מס. על פי 'הלכת מוזס', המערכת מזהה ומאפסת הפסדים שנוצרו אך ורק מהפרשי שער מטבע (כאשר אין הפסד כלכלי אמיתי בדולר)."
)

st.success(f"### 🎯 Final Estimated Tax Liability (If Reset Today): ₪{total_tax_today:,.2f}")

# --- MODULE 5: CFO STRATEGY (BREAKEVEN) ---
st.markdown("---")
st.header("5. Strategic Analysis: Tax Savings vs. Compound Interest")
st.write(
    "Does it make mathematical sense to pay tax today to raise your tax base? Let's project the Net ILS value (after final tax) for both scenarios.")

years = np.arange(1, investment_horizon + 1)
scenario_a_net = []  # HOLD
scenario_b_net = []  # Reset Tax Base Today

# ==========================================
# SCENARIO B INITIALIZATION (WITH FRICTION COSTS)
# ==========================================
# 1. Calculate net portfolio value in ILS after paying today's estimated tax
net_ils_after_tax_today = total_ils_value - total_tax_today

# 2. Convert net proceeds back to USD and deduct the friction costs (broker commissions)
new_usd_base = (net_ils_after_tax_today / current_rate) - transaction_costs_usd

# 3. Safety check: Ensure friction costs didn't push the portfolio into negative territory
new_usd_base = max(0.0, new_usd_base)

# 4. Calculate the new quantity of shares that can be repurchased with the remaining capital
new_units = new_usd_base / current_price

# 5. Create the newly "reset" tax lot for future projections
reset_lot = [{"Units": new_units, "Price": current_price, "Rate": current_rate}]

for y in years:
    future_price_y = current_price * ((1 + (expected_return / 100)) ** y)

    # Scenario A projection
    tax_a, _, _, _ = calculate_portfolio_tax(open_lots, future_price_y, future_rate)
    gross_ils_a = total_units_remaining * future_price_y * future_rate
    scenario_a_net.append(gross_ils_a - tax_a)

    # Scenario B projection
    tax_b, _, _, _ = calculate_portfolio_tax(reset_lot, future_price_y, future_rate)
    gross_ils_b = new_units * future_price_y * future_rate
    scenario_b_net.append(gross_ils_b - tax_b)

# Find Breakeven
breakeven_year = None
for i in range(len(years)):
    if scenario_a_net[i] > scenario_b_net[i]:
        breakeven_year = years[i]
        break

fig2 = go.Figure()

# Plot HOLD scenario (Green line)
fig2.add_trace(go.Scatter(
    x=years,
    y=scenario_a_net,
    mode='lines',
    name='HOLD',
    line=dict(color='#27ae60', width=3),
    hovertemplate="Net Portfolio Value: %{y:,.2f} ILS<extra></extra>"
))

# Plot Tax Base Reset scenario (Red line)
fig2.add_trace(go.Scatter(
    x=years,
    y=scenario_b_net,
    mode='lines',
    name='Tax Base Step-Up',
    line=dict(color='#c0392b', width=3),
    hovertemplate="Net Portfolio Value: %{y:,.2f} ILS<extra></extra>"
))

# Add breakeven line if it exists
if breakeven_year:
    fig2.add_vline(x=breakeven_year, line_width=2, line_dash="dash", line_color="black",
                   annotation_text=f"Breakeven: Year {breakeven_year}", annotation_position="top left")

# Configure layout and horizontal legend
fig2.update_layout(
    title="Net Portfolio Value (ILS) After Final Tax",
    xaxis_title="Years into the Future",
    yaxis_title="Net Value (₪)",
    template="plotly_white",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

# Customize X-axis to display "X Years" in hover and ticks
fig2.update_xaxes(
    hoverformat=".0f",
    ticksuffix=" Years"
)

st.plotly_chart(fig2, use_container_width=True)

# CFO Verdict
st.subheader("CFO Verdict ⚖️")

if total_tax_today == 0:
    if len(scenario_b_net) > 0 and len(scenario_a_net) > 0:
        if scenario_b_net[-1] > scenario_a_net[-1]:
            st.success(
                "**Model Suggests: Potentially Beneficial.** Your current tax liability is ₪0 due to favorable exchange rate conditions. The model indicates that executing a Tax Base Reset today may raise your future tax shield effectively at zero current tax cost.")
        else:
            st.error(
                "**Model Suggests: Potential Value Destruction.** Although your current tax liability is ₪0, executing a reset today would **lower** your original tax base. The 'HOLD' strategy appears mathematically superior.")

elif breakeven_year:
    if breakeven_year == 1:
        st.error(
            f"**Model Suggests: Not Profitable.** Under your {expected_return}% return projection, the tax and friction costs paid today significantly hurt your compound interest. 'HOLD' appears to be the better strategy immediately.")
    else:
        st.warning(
            f"**Model Suggests: Time-Sensitive Strategy.** Resetting today is projected to be profitable ONLY IF you plan to sell within the next **{breakeven_year - 1} years**. \n\nFrom **Year {breakeven_year}** onwards, the 'HOLD' strategy wins.")

else:
    st.success(
        f"**Model Suggests: Long-Term Benefit.** Within your {investment_horizon}-year horizon, the Tax Base Step-Up is projected to remain profitable. The future tax savings outweigh the lost compound interest.")

# Actionable Disclaimer Box
disclaimer_items = [
    "⚠️ <b>הבהרה משפטית:</b> תוצאות הסימולציה מבוססות על מודל מתמטי והערכות עתידיות. המערכת נועדה למטרות מחקר, לימוד והדגמה בלבד, ואינה מהווה ייעוץ מס פרטני, ייעוץ פיננסי, או המלצה לביצוע פעולות בשוק ההון. חובה להתייעץ עם רואה חשבון או יועץ מס מוסמך טרם קבלת החלטות פיננסיות.",
    "💡 <b>נקודות קריטיות לתשומת לב לקראת ביצוע:</b> הסימולציה מציגה את השפעת מס רווח ההון על הקרן בלבד. ביצוע \"העלאת מס בסיס\" בפועל דורש שתי פעולות רצופות, ולכן חובה לוודא מול הברוקר:",
    "<b>1. חישוב עמלות שמרני (Friction Costs):</b> בתיקים קטנים, עמלות קנייה ומכירה עלולות למחוק את רוב או כל חיסכון המס. הסימולטור מפחית את העמלות שהזנת מסך ההון הזמין להשקעה מחדש, אך למען פשטות המודל, הן <b>אינן</b> משוקללות לתוך בסיס המס ההיסטורי (Adjusted Cost Basis). חישוב פרטני אצל רואה חשבון עשוי להקטין את חבות המס שלך אף יותר מהמוצג.",
    "<b>2. סכנת המרה כפולה:</b> ודא שתמורת המכירה נכנסת לחשבון המט\"ח (USD) ו<b>שלא</b> מתבצעת המרה אוטומטית לשקלים, כדי למנוע עמלות חליפין ופערי שער (Spread) מיותרים.",
    "<b>3. פערי ציטוט בשוק (Bid-Ask Spread):</b> מעבר לעמלות הקנייה והמכירה של הברוקר, פעולה מהירה בשוק ההון כרוכה בעלות חיכוך מובנית. בעת פעולת ה\"איפוס\", אתה תיאלץ למכור את הנכס במחיר הקונה (Bid) הנמוך מעט, ומיד לקנות אותו במחיר המוכר (Ask) הגבוה מעט. בניירות ערך חסרי נזילות (סחירות נמוכה), פער זה מתרחב ועלול למחוק חלק מחיסכון המס.",
    "<b>4. סכנת \"עסקה מלאכותית\" (סעיף 86 לפקודה):</b> מכירה וקנייה מיידית של <i>אותו נייר ערך בדיוק</i> עלולה להיות מסווגת על ידי מס הכנסה כעסקה מלאכותית (Wash Sale), מה שעשוי לאיין את ההכרה באירוע המס. כדי להתמודד עם סוגיה זו ולשמור על החשיפה לשוק, משקיעים רבים בוחרים לבצע את הרכישה החוזרת בקרן מחקה עוקבת של יצרן אחר (למשל, מכירת קרן SPY ורכישת קרן VOO או IVV באותו רגע), או לחלופין, להמתין מספר ימי מסחר לפני הרכישה החוזרת.",
    "<b>5. מגבלות בחירת שכבות מס (סכנת ה\"זיהוי הספציפי\"):</b> הסימולטור מניח מימוש בשיטת FIFO (נכנס ראשון, יוצא ראשון), שהיא ברירת המחדל החוקית בישראל. אם אתה סוחר דרך בנק או בית השקעות ישראלי, שיטה זו נכפית עליך אוטומטית במערכת. אם אתה סוחר דרך ברוקר זר ומתכנן למכור שכבה ספציפית (Specific Identification) כדי לייעל את המס, שים לב כי הסימולטור אינו תומך בתרחיש זה ומנוע המס שלו מבוסס בלעדית על אלגוריתם ה-FIFO.",
    "<b>6. פרשנות מחמירה לקיזוז הפסדים (הלכת מוזס):</b> אם הנכס נמצא בהפסד שקלי ואתה שוקל למכור אותו רק כדי לקזז רווחים אחרים, שים לב: הסימולטור נוקט בפרשנות שמרנית לפסיקה (ולחוזר מס הכנסה 10/2025). הפסד הון הנובע <i>אך ורק</i> משחיקת שער המטבע יאופס לחלוטין ולא יוכר לקיזוז במערכת. הפעולה במקרה זה עלולה \"להשמיד ערך\" ולהוריד את בסיס המס ההיסטורי מבלי להעניק מגן מס.",
    "<b>7. מס יסף (Surtax):</b> הסימולטור מחשב את אירוע המס לפי שיעור בסיס של 25%. משקיעים החוצים את מדרגות ההכנסה הגבוהות (למעלה מכ-700 אלף ש\"ח בשנה, כולל הרווח הרעיוני שייווצר מהאיפוס עצמו) כפופים למס יסף של 3% ומעלה בהתאם למדרגות החוק. תוספת זו אינה משוקללת במודל ועלולה להאריך משמעותית את זמן החזר ההשקעה (Breakeven)."
]

# TAX LOGIC: Append the dividend warning ONLY if the asset pays dividends
if pays_dividend:
    disclaimer_items.append(
        "<b>8. מס דיבידנדים (Tax Drag):</b> המערכת זיהתה שנכס הבסיס שבחרת מחלק דיבידנדים. חשוב לדעת שחישוב הריבית דריבית העתידי (התשואה שהזנת) לא מנכה את המס שנגבה במקור בעת חלוקת הדיבידנד (לרוב 25%). במציאות, גביית המס השוטפת תקטין במעט את קצב הצמיחה האמיתי של התיק."
    )

# UI/UX: Construct the HTML strictly without any code-level indentation leaks
inner_html = "<br><br>".join(disclaimer_items)
rtl_disclaimer_html = f'<div dir="rtl" style="background-color: #e8f4f8; padding: 15px; border-radius: 5px; border: 1px solid #b8daff; color: #004085; text-align: right; font-family: sans-serif; line-height: 1.6;">{inner_html}</div>'

# Render the clean HTML component
st.markdown(rtl_disclaimer_html, unsafe_allow_html=True)