import re
import time
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Import custom modules (Architecture Split)
from market_data import fetch_historical_exchange_rates, fetch_asset_data, get_historical_rate_for_date
from tax_engine import calculate_portfolio_tax

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Tax Basis Step-Up Simulator",
    layout="wide",
    page_icon="📊"
)

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

# Security Fix: Fetching keys safely outside the cached function
fh_key = st.secrets.get("FINNHUB_API_KEY", "")
tg_key = st.secrets.get("TIINGO_API_KEY", "")

with st.spinner("Initializing Market Data..."):
    current_price, asset_currency, pays_dividend = fetch_asset_data(ticker_input, fh_key, tg_key)

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
        "USD/ILS Rate": st.column_config.NumberColumn("USD/ILS Rate (Leave blank for Auto-Fill)", min_value=1.0, required=False),
    }
)

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

total_tax_today, lot_results, tot_taxable, tot_loss = calculate_portfolio_tax(open_lots, current_price, current_rate)

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
                "**Optimal Condition (The Holy Grail):** Your current tax liability is ₪0 due to favorable exchange rate conditions, despite having unrealized USD profits! It is mathematically optimal to execute a Tax Base Reset today, raising your future tax shield at zero tax cost.")
        else:
            st.error(
                "**Warning (Value Destroyer):** Although your current tax liability is ₪0, executing a reset today would **lower** your original tax base. In the future, you will pay more tax on the growth than if you simply held. The 'HOLD' strategy is mathematically superior.")

elif breakeven_year:
    if breakeven_year == 1:
        st.error(
            f"**Warning:** Resetting the tax base is **not profitable**. Under your {expected_return}% return projection, the tax paid today permanently cripples your compound interest. 'HOLD' wins immediately.")
    else:
        st.warning(
            f"**Time-Sensitive Strategy:** Resetting today is profitable ONLY IF you sell within the next **{breakeven_year - 1} years**. \n\nFrom **Year {breakeven_year}** onwards, the 'HOLD' strategy wins because the compound interest lost on the tax paid today exceeds your future tax savings.")

else:
    st.success(
        f"**Strategy Validated:** Within your {investment_horizon}-year horizon, the Tax Base Step-Up remains highly profitable. The tax savings outweigh the lost compound interest for the entire projected period.")

# Actionable Disclaimer Box
disclaimer_items = [
    "⚠️ <b>הבהרה משפטית:</b> תוצאות הסימולציה מבוססות על מודל מתמטי והערכות עתידיות. המערכת נועדה למטרות מחקר, לימוד והדגמה בלבד, ואינה מהווה ייעוץ מס פרטני, ייעוץ פיננסי, או המלצה לביצוע פעולות בשוק ההון. חובה להתייעץ עם רואה חשבון או יועץ מס מוסמך טרם קבלת החלטות פיננסיות.",
    "💡 <b>נקודות קריטיות לתשומת לב לקראת ביצוע:</b> הסימולציה מציגה את השפעת מס רווח ההון על הקרן בלבד. ביצוע \"העלאת מס בסיס\" בפועל דורש שתי פעולות רצופות, ולכן חובה לוודא מול הברוקר:",
    "<b>1. עמלות מינימום:</b> קח בחשבון עמלות קנייה ומכירה. בתיקים קטנים, עמלות המינימום עלולות למחוק את רוב או כל חיסכון המס. אנא ודא כי הזנת את העלות המשוערת של עמלות אלו בשדה המיועד לכך בתפריט הסימולטור (Friction Costs) כדי לקבל תוצאה מדויקת.",
    "<b>2. סכנת המרה כפולה:</b> ודא שתמורת המכירה נכנסת לחשבון המט\"ח (USD) ו<b>שלא</b> מתבצעת המרה אוטומטית לשקלים, כדי למנוע עמלות חליפין ופערי שער (Spread) מיותרים.",
    "<b>3. פערי ציטוט בשוק (Bid-Ask Spread):</b> מעבר לעמלות הקנייה והמכירה של הברוקר, פעולה מהירה בשוק ההון כרוכה בעלות חיכוך מובנית. בעת פעולת ה\"איפוס\", אתה תיאלץ למכור את הנכס במחיר הקונה (Bid) הנמוך מעט, ומיד לקנות אותו במחיר המוכר (Ask) הגבוה מעט. בניירות ערך חסרי נזילות (סחירות נמוכה), פער זה מתרחב ועלול למחוק חלק מחיסכון המס.",
    "<b>4. סכנת \"עסקה מלאכותית\" (סעיף 86 לפקודה):</b> מכירה וקנייה מיידית של <i>אותו נייר ערך בדיוק</i> עלולה להיות מסווגת על ידי מס הכנסה כעסקה מלאכותית (Wash Sale), מה שעשוי לאיין את ההכרה באירוע המס. כדי להתמודד עם סוגיה זו ולשמור על החשיפה לשוק, משקיעים רבים בוחרים לבצע את הרכישה החוזרת בקרן מחקה עוקבת של יצרן אחר (למשל, מכירת קרן SPY ורכישת קרן VOO או IVV באותו רגע), או לחלופין, להמתין מספר ימי מסחר לפני הרכישה החוזרת.",
    "<b>5. מגבלות בחירת שכבות מס (סכנת ה\"זיהוי הספציפי\"):</b> הסימולטור מניח מימוש בשיטת FIFO (נכנס ראשון, יוצא ראשון), שהיא ברירת המחדל החוקית בישראל. אם אתה סוחר דרך בנק או בית השקעות ישראלי, שיטה זו נכפית עליך אוטומטית במערכת. אם אתה סוחר דרך ברוקר זר ומתכנן למכור שכבה ספציפית (Specific Identification) כדי לייעל את המס, שים לב כי הסימולטור אינו תומך בתרחיש זה ומנוע המס שלו מבוסס בלעדית על אלגוריתם ה-FIFO.",
    "<b>6. אשליית קיזוז הפסדים (הלכת מוזס):</b> אם הנכס נמצא בהפסד שקלי ואתה שוקל למכור אותו רק כדי לקזז רווחים מניירות ערך אחרים, שים לב: \"הלכת מוזס\" קובעת כי הפסד הון הנובע <i>אך ורק</i> משחיקת שער המטבע אינו מוכר כהפסד הון בר-קיזוז לצורכי מס. הפעולה במקרה זה עלולה \"להשמיד ערך\" ולהוריד את בסיס המס ההיסטורי שלך מבלי להעניק לך מגן מס אמיתי בהווה.",
    "<b>7. מס יסף (Surtax):</b> הסימולטור מחשב את אירוע המס לפי שיעור בסיס של 25%. משקיעים החוצים את תקרת ההכנסות השנתית (למעלה מ-700 אלף ש\"ח, כולל הרווח הרעיוני שייווצר מהאיפוס עצמו) עשויים להיות מחויבים במס יסף של 3% נוספים (סה\"כ 28%). תוספת זו אינה משוקללת במודל ועלולה לשנות את כדאיות האסטרטגיה."
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