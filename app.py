import re
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

    q_resp = _SESSION.get(_FINNHUB_QUOTE_URL, params=params, timeout=10)
    q_resp.raise_for_status()
    price = float(q_resp.json().get("c", 0.0) or q_resp.json().get("pc", 0.0))
    if price == 0.0:
        raise ValueError("Zero price returned.")

    p_resp = _SESSION.get(_FINNHUB_PROFILE_URL, params=params, timeout=10)
    p_resp.raise_for_status()
    currency = (p_resp.json().get("currency") or "USD").upper()

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

    q_resp = _SESSION.get(_TIINGO_QUOTE_URL.format(symbol=symbol), headers=headers, timeout=10)
    q_resp.raise_for_status()
    data = q_resp.json()
    if not data: raise ValueError("Empty quote.")
    price = float(data[0].get("last") or data[0].get("tngoLast") or 0.0)

    m_resp = _SESSION.get(_TIINGO_META_URL.format(symbol=symbol), headers=headers, timeout=10)
    m_resp.raise_for_status()
    currency = (m_resp.json().get("currency") or "USD").upper()

    return price, currency, False


@st.cache_data(ttl=3600)
def fetch_asset_data(ticker_symbol: str):
    """
    Fetches real-time asset data using Finnhub (primary) and Tiingo (fallback).
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
        df_clean = df_history.copy()
        df_clean.index = pd.to_datetime(df_clean.index).tz_localize(None).normalize()
        target_dt = pd.to_datetime(target_date).normalize()
        closest_date = df_clean.index.asof(target_dt)

        if pd.isna(closest_date):
            return fallback_rate

        rate = df_clean.loc[closest_date, 'Close']

        if isinstance(rate, pd.Series):
            return float(rate.iloc[0])

        return float(rate)

    except Exception:
        return fallback_rate


# ==========================================
# TAX ENGINE: SECTION 91(B) + THE MOSES FILTER
# ==========================================
def calculate_portfolio_tax(lots, current_price, current_rate, total_sell_commission=0.0):
    """Calculates Section 91(b) tax, offsets using the Moses Ruling, and deducts sell commissions."""
    total_units_in_portfolio = sum(lot["Units"] for lot in lots)
    total_taxable_profit = 0.0
    total_recognized_loss = 0.0
    lot_results = []

    for index, lot in enumerate(lots):
        # Calculate Pro-Rata sell commission per lot
        lot_sell_comm = total_sell_commission * (
                    lot["Units"] / total_units_in_portfolio) if total_units_in_portfolio > 0 else 0.0

        # USD calculations (Net Proceeds)
        usd_cost = lot["Effective_Price"] * lot["Units"]
        usd_proceeds_net = (current_price * lot["Units"]) - lot_sell_comm
        usd_profit = usd_proceeds_net - usd_cost

        # ILS conversions (using the net USD proceeds before rate conversion)
        ils_cost = usd_cost * lot["Rate"]
        ils_proceeds_net = usd_proceeds_net * current_rate
        nominal_ils_profit = ils_proceeds_net - ils_cost

        taxable_profit = 0.0
        recognized_loss = 0.0

        # TAX LOGIC: Section 91(b) of the Israeli Income Tax Ordinance
        # Limits the final tax liability so it never exceeds the nominal ILS profit.
        if usd_profit > 0 and nominal_ils_profit > 0:
            taxable_profit = min(usd_profit * current_rate, nominal_ils_profit)

        # TAX LOGIC: The "Moses" Court Ruling (הלכת מוזס)
        # Prevents investors from claiming artificial capital losses driven purely by currency devaluation.
        elif usd_profit < 0 and nominal_ils_profit < 0:
            real_ils_loss = abs(usd_profit) * current_rate
            nominal_ils_loss = abs(nominal_ils_profit)
            recognized_loss = min(real_ils_loss, nominal_ils_loss)

        total_taxable_profit += taxable_profit
        total_recognized_loss += recognized_loss

        lot_results.append({
            "Lot #": index + 1,
            "Orig. Date": lot.get("Date", "N/A"),
            "Rem. Units": round(lot["Units"], 4),
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
st.sidebar.subheader("💸 Friction Costs (Reset Event)")
est_sell_commission = st.sidebar.number_input("Estimated Sell Commission ($)", min_value=0.0, value=0.0, step=1.0)
est_buy_commission = st.sidebar.number_input("Estimated Buy Commission ($)", min_value=0.0, value=0.0, step=1.0)

st.sidebar.markdown("---")
st.sidebar.subheader("🔮 Future Projections")
expected_return = st.sidebar.number_input("Expected Annual Return (%)", min_value=-100.0, max_value=100.0, value=10.0,
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

try:
    with open("Guide.pdf", "rb") as pdf_file:
        st.download_button(
            label="📄 Download the Complete Strategy Guide (PDF)",
            data=pdf_file.read(),
            file_name="Tax_Base_Step-Up_Guide.pdf",
            mime="application/pdf"
        )
except FileNotFoundError:
    pass

# --- MODULE 1: MACRO VIEW ---
st.header("1. Macro View: Historical Tax Shield Potential")
st.markdown("This chart displays the historical strength of the USD vs. ILS compared to today's rate.")
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
    fig1.update_layout(
        title=f"Tax Base Step-Up Potential (Current Rate: {current_rate:.4f} ILS)",
        xaxis_title="Historical Date", yaxis_title="Maximum Tax-Exempt USD Return (%)",
        template="plotly_white", hovermode="x unified", margin=dict(l=0, r=0, t=40, b=0)
    )
    st.plotly_chart(fig1, use_container_width=True)
else:
    st.warning("Historical exchange rate data is currently unavailable.")

# --- MODULE 2: THE FULL LEDGER ---
st.markdown("---")
st.header("2. The Ledger: Historical Transactions")
st.write(
    f"Enter your transaction history for **{ticker_input}**. The system calculates cost basis integrating your commissions.")

st.info(
    f"📌 **Asset:** **{ticker_input}** &nbsp;|&nbsp; "
    f"**Price:** **${current_price:,.2f}** &nbsp;|&nbsp; "
    f"**USD/ILS Rate:** **₪{current_rate:,.4f}**"
)

# UI/UX FIX: Added the Buy Commission column to the schema
default_ledger = pd.DataFrame(columns=["Date", "Action", "Units", "Price (USD)", "Buy Commission ($)", "USD/ILS Rate"])

edited_df = st.data_editor(
    default_ledger,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Date": st.column_config.DateColumn("Date", required=True, max_value=datetime.today().date()),
        "Action": st.column_config.SelectboxColumn("Action", options=["Buy", "Sell"], required=True),
        "Units": st.column_config.NumberColumn("Units", min_value=0.001, required=True),
        "Price (USD)": st.column_config.NumberColumn("Price (USD)", min_value=0.01, required=True),
        "Buy Commission ($)": st.column_config.NumberColumn("Buy Commission ($)", min_value=0.0, default=0.0),
        "USD/ILS Rate": st.column_config.NumberColumn("USD/ILS Rate (Leave blank for Auto-Fill)", min_value=1.0,
                                                      required=False),
    }
)

edited_df = edited_df.dropna(subset=["Date", "Action", "Units", "Price (USD)"]).reset_index(drop=True)
st.caption("💡 **הערה חשובה:** בטבלה זו יש להזין תחת 'Buy Commission' רק עמלות של עסקאות **קנייה**. עמלות מכירה היסטוריות אינן משפיעות על בסיס המס של הפוזיציות שנותרו לכם כיום, ולכן המערכת מתעלמת מהן.")
edited_df['Action_Rank'] = edited_df['Action'].map({'Buy': 1, 'Sell': 2})
edited_df = edited_df.sort_values(by=["Date", "Action_Rank"]).reset_index(drop=True)

open_lots = []
validation_error = False

for _, row in edited_df.iterrows():
    action = row["Action"]
    units = row["Units"]
    price = row["Price (USD)"]
    date = row["Date"]

    # Defensive parsing for commissions
    raw_comm = row.get("Buy Commission ($)", 0.0)
    buy_commission = float(raw_comm) if pd.notna(raw_comm) and raw_comm != "" else 0.0

    rate = row["USD/ILS Rate"]
    if pd.isna(rate) or rate <= 0:
        rate = get_historical_rate_for_date(date, df_ils_init, fallback_rate=current_rate)

    if action == "Buy":
        # Integrating Buy Commission into the Tax Lot
        effective_price = ((price * units) + buy_commission) / units
        open_lots.append({
            "Date": date.strftime('%Y-%m-%d') if isinstance(date, datetime) else date,
            "Units": units,
            "Effective_Price": effective_price,
            "Rate": rate
        })
    elif action == "Sell":
        units_to_sell = units
        current_available = sum(lot["Units"] for lot in open_lots)

        if units_to_sell > current_available:
            st.error(
                f"🛑 **Chronological Error:** On {date}, you logged a 'Sell' of {units_to_sell} units, but balance was {current_available}.")
            validation_error = True
            break

        # ALGORITHM: Stable FIFO Matching
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
st.header("4. The Tax Engine: Adjusted Cost Basis & 91(b)")

# PASSING SELL COMMISSION TO THE ENGINE
total_tax_today, lot_results, tot_taxable, tot_loss = calculate_portfolio_tax(
    open_lots, current_price, current_rate, total_sell_commission=est_sell_commission
)

res_df = pd.DataFrame(lot_results)
with st.expander("🔍 Click to view Advanced Tax Lot Breakdown", expanded=True):
    st.dataframe(res_df, use_container_width=True)

col_t1, col_t2 = st.columns(2)
col_t1.metric("Total Taxable Profit", f"₪{tot_taxable:,.2f}")
col_t2.metric("Total Recognized Loss (Moses)", f"₪{tot_loss:,.2f}")
st.success(f"### 🎯 Final Estimated Tax Liability (If Reset Today): ₪{total_tax_today:,.2f}")

# --- MODULE 5: CFO STRATEGY (BREAKEVEN) ---
st.markdown("---")
st.header("5. Strategic Analysis: Tax Savings vs. Compound Interest")

years = np.arange(1, investment_horizon + 1)
scenario_a_net = []  # HOLD
scenario_b_net = []  # Reset Tax Base Today

# ==========================================
# SCENARIO B: THE RESET LOGIC (WITH FRICTION COSTS)
# ==========================================
# TAX LOGIC: Net proceeds after tax and friction costs
# 1. Gross USD value minus the sell commission
net_usd_proceeds = total_usd_value - est_sell_commission

# 2. Convert to ILS and subtract the tax paid
ils_proceeds_before_tax = net_usd_proceeds * current_rate
ils_after_tax = ils_proceeds_before_tax - total_tax_today

# 3. Convert back to USD and deduct the new buy commission
cash_for_reinvestment_usd = (ils_after_tax / current_rate) - est_buy_commission

# 4. Calculate exactly how many shares we can buy now
new_units_after_reset = cash_for_reinvestment_usd / current_price

# 5. Capitalize the buy commission into the new tax lot's effective price
effective_reset_price = (cash_for_reinvestment_usd + est_buy_commission) / new_units_after_reset

reset_lot = [{
    "Date": datetime.today().strftime('%Y-%m-%d'),
    "Units": new_units_after_reset,
    "Effective_Price": effective_reset_price,
    "Rate": current_rate
}]

for y in years:
    future_price_y = current_price * ((1 + (expected_return / 100)) ** y)

    # Scenario A projection (HOLD)
    tax_a, _, _, _ = calculate_portfolio_tax(open_lots, future_price_y, future_rate,
                                             total_sell_commission=est_sell_commission)
    gross_ils_a = total_units_remaining * future_price_y * future_rate
    scenario_a_net.append(gross_ils_a - tax_a)

    # Scenario B projection (Reset)
    tax_b, _, _, _ = calculate_portfolio_tax(reset_lot, future_price_y, future_rate,
                                             total_sell_commission=est_sell_commission)
    gross_ils_b = new_units_after_reset * future_price_y * future_rate
    scenario_b_net.append(gross_ils_b - tax_b)

breakeven_year = None
for i in range(len(years)):
    if scenario_a_net[i] > scenario_b_net[i]:
        breakeven_year = years[i]
        break

fig2 = go.Figure()
fig2.add_trace(go.Scatter(x=years, y=scenario_a_net, mode='lines', name='HOLD', line=dict(color='#27ae60', width=3)))
fig2.add_trace(
    go.Scatter(x=years, y=scenario_b_net, mode='lines', name='Tax Base Step-Up', line=dict(color='#c0392b', width=3)))

if breakeven_year:
    fig2.add_vline(x=breakeven_year, line_width=2, line_dash="dash", line_color="black",
                   annotation_text=f"Breakeven: Year {breakeven_year}")

fig2.update_layout(
    title="Net Portfolio Value (ILS) After Final Tax", xaxis_title="Years into the Future", yaxis_title="Net Value (₪)",
    template="plotly_white", hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)
st.plotly_chart(fig2, use_container_width=True)

# CFO Verdict
st.subheader("CFO Verdict ⚖️")
if total_tax_today == 0:
    if len(scenario_b_net) > 0 and len(scenario_a_net) > 0:
        if scenario_b_net[-1] > scenario_a_net[-1]:
            st.success(
                "**Optimal Condition (The Holy Grail):** Tax liability is ₪0. Mathematically optimal to execute a Tax Base Reset today.")
        else:
            st.error(
                "**Warning (Value Destroyer):** Executing a reset today lowers your original tax base or friction costs destroy the profit. 'HOLD' wins.")
elif breakeven_year:
    if breakeven_year == 1:
        st.error(
            f"**Warning:** Resetting is **not profitable**. Tax/friction paid today permanently cripples your compound interest. 'HOLD' wins immediately.")
    else:
        st.warning(
            f"**Time-Sensitive:** Resetting today is profitable ONLY IF you sell within the next **{breakeven_year - 1} years**.")
else:
    st.success(
        f"**Strategy Validated:** Within your {investment_horizon}-year horizon, the Tax Base Step-Up remains highly profitable.")

# ACTIONABLE DISCLAIMER
disclaimer_items = [
    "⚠️ <b>הבהרה משפטית:</b> תוצאות הסימולציה מבוססות על מודל מתמטי והערכות עתידיות. המערכת נועדה למטרות מחקר, לימוד והדגמה בלבד, ואינה מהווה ייעוץ מס פרטני.",
    "💡 <b>נקודות קריטיות לתשומת לב לקראת ביצוע:</b>",
    "<b>1. עמלות מינימום:</b> בתיקים קטנים, עמלות המינימום עלולות למחוק את רוב או כל חיסכון המס.",
    "<b>2. סכנת המרה כפולה:</b> ודא שתמורת המכירה נכנסת לחשבון המט\"ח (USD) ו<b>שלא</b> מתבצעת המרה אוטומטית לשקלים.",
    "<b>3. פערי ציטוט בשוק (Bid-Ask Spread):</b> בעת פעולת ה\"איפוס\", קיימת עלות חיכוך מובנית בפערי ציטוט.",
    "<b>4. סכנת \"עסקה מלאכותית\" (סעיף 86 לפקודה):</b> מכירה וקנייה מיידית של <i>אותו נייר ערך בדיוק</i> עלולה להיות מסווגת כעסקה מלאכותית.",
    "<b>5. סכנת ה\"זיהוי הספציפי\":</b> הסימולטור מניח מימוש בשיטת FIFO. מימוש בשיטות אחרות רלוונטי רק לברוקר זר ונושא סיכון מול מס הכנסה.",
    "<b>6. אשליית קיזוז הפסדים (הלכת מוזס):</b> הפסד הנובע <i>אך ורק</i> משחיקת שער המטבע אינו מוכר לקיזוז מס.",
    "<b>7. מס יסף (Surtax):</b> משקיעים החוצים את תקרת ההכנסות השנתית (למעלה מ-700 אלף ש\"ח) עשויים לחוב במס יסף של 3% נוספים."
]

if pays_dividend:
    disclaimer_items.append(
        "<b>8. מס דיבידנדים (Tax Drag):</b> הנכס מחלק דיבידנדים. חישוב הריבית דריבית העתידי בסימולציה לא מנכה את המס שנגבה במקור בעת החלוקה.")

inner_html = "<br><br>".join(disclaimer_items)
rtl_disclaimer_html = f'<div dir="rtl" style="background-color: #e8f4f8; padding: 15px; border-radius: 5px; border: 1px solid #b8daff; color: #004085; text-align: right; font-family: sans-serif; line-height: 1.6;">{inner_html}</div>'
st.markdown(rtl_disclaimer_html, unsafe_allow_html=True)