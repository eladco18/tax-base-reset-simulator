import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import numpy as np
from datetime import datetime, timedelta
import re

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Tax Base Reset Simulator",
    layout="wide",
    page_icon="📊"
)


# ==========================================
# DATA CACHING (YFINANCE)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_historical_exchange_rates(start_date: str) -> pd.DataFrame:
    try:
        ticker = yf.Ticker("ILS=X")
        df = ticker.history(start=start_date, end=datetime.today().strftime('%Y-%m-%d'), auto_adjust=False)
        if df.empty:
            df = yf.download("ILS=X", start=start_date, end=datetime.today().strftime('%Y-%m-%d'), progress=False)
        return df
    except Exception as e:
        st.error(f"API Connection Error: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_asset_data(ticker_symbol: str):
    """Fetches the current price, currency, and dividend status of the specified asset."""
    try:
        stock = yf.Ticker(ticker_symbol.upper())
        hist = stock.history(period="1d")
        if hist.empty:
            return 0.0, "UNKNOWN", False

        price = float(hist['Close'].iloc[-1])
        currency = stock.info.get('currency', 'UNKNOWN').upper()

        # Check if the asset pays dividends (Yield > 0 or recent dividend history)
        div_yield = stock.info.get('dividendYield')
        pays_dividend = True if (div_yield and div_yield > 0) else False
        if not pays_dividend:
            recent_divs = stock.dividends
            if not recent_divs.empty and recent_divs.index[-1].year >= datetime.today().year - 1:
                pays_dividend = True

        return price, currency, pays_dividend
    except Exception:
        return 0.0, "ERROR", False


def get_historical_rate_for_date(target_date, df_history: pd.DataFrame, fallback_rate: float) -> float:
    """Finds the USD/ILS exchange rate for a specific past date."""
    if df_history.empty:
        return fallback_rate

    target_dt = pd.to_datetime(target_date)
    # If exact date exists
    if target_dt in df_history.index:
        return float(df_history.loc[target_dt, 'Close'])

    # If weekend/holiday, get the nearest prior date (pad method)
    try:
        idx = df_history.index.get_indexer([target_dt], method='pad')
        if idx[0] != -1:
            return float(df_history.iloc[idx[0]]['Close'])
    except Exception:
        pass

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

        # Taxable Profit Logic (Sec 91b)
        if usd_profit > 0 and nominal_ils_profit > 0:
            taxable_profit = min(usd_profit * current_rate, nominal_ils_profit)

        # Recognized Loss Logic (Moses Ruling)
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
st.sidebar.header("⚙️ Global Settings")
ticker_input = st.sidebar.text_input("Asset Ticker (e.g., SPY, QQQ)", value="SPY").upper()

if not re.match(r"^[A-Z0-9\-\.]+$", ticker_input):
    st.sidebar.error("❌ Invalid input: Please enter a valid English ticker symbol (e.g., SPY, QQQ).")
    st.stop()

st.sidebar.markdown("---")
st.sidebar.subheader("🔮 Future Projections")
expected_return = st.sidebar.number_input("Expected Annual Return (%)", min_value=-100.0, max_value=100.0, value=0.0,
                                          step=0.5)
investment_horizon = st.sidebar.slider("Investment Horizon (Years)", min_value=1, max_value=30, value=0)

with st.spinner("Initializing Market Data..."):
    current_price, asset_currency, pays_dividend = fetch_asset_data(ticker_input)
    default_start = (datetime.today() - timedelta(days=365 * 5)).strftime('%Y-%m-%d')
    df_ils_init = fetch_historical_exchange_rates(default_start)
    current_rate = float(df_ils_init['Close'].iloc[-1]) if not df_ils_init.empty else 3.60

future_rate = st.sidebar.number_input("Est. Future USD/ILS Rate", min_value=1.0, max_value=10.0,
                                      value=float(current_rate), step=0.1)

# ==========================================
# MAIN DASHBOARD
# ==========================================
st.title("📊 Capital Gains Tax Simulator: Tax Base Reset")
st.markdown(
    "Evaluate the financial viability of a **Tax Base Reset** strategy under Section 91(b) of the Israeli Income Tax Ordinance.")

# --- CURRENCY VALIDATION WALL ---
if asset_currency not in ["USD", "UNKNOWN", "ERROR"]:
    st.error(
        f"### 🛑 Currency Mismatch Detected\nThe security **{ticker_input}** you entered is traded in **{asset_currency}**. \n\nFor the Section 91(b) 'Tax Base Reset' strategy to mathematically apply, the asset must be denominated in **USD**. Please enter a valid US-traded ticker in the sidebar to continue.")

else:
    # --- MODULE 1: MACRO VIEW ---
    st.header("1. Macro View: Historical Tax Shield Potential")
    start_date = st.date_input("Display USD/ILS history starting from:", value=pd.to_datetime(default_start))

    with st.spinner("Fetching macro data..."):
        df_ils = fetch_historical_exchange_rates(start_date.strftime('%Y-%m-%d'))

    if not df_ils.empty and current_price > 0:
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
            title=f"Tax Base Reset Potential (Current Rate: {current_rate:.4f} ILS | {ticker_input}: ${current_price:.2f})",
            xaxis_title="Historical Date", yaxis_title="Maximum Tax-Exempt USD Return (%)",
            template="plotly_white", hovermode="x unified", margin=dict(l=0, r=0, t=40, b=0)
        )
        st.plotly_chart(fig1, use_container_width=True)

        # --- MODULE 2: THE FULL LEDGER ---
        st.markdown("---")
        st.header("2. The Ledger: Historical Transactions")
        st.write(
            f"Enter your transaction history for **{ticker_input}**. The system will calculate your current open lots using the **FIFO** method.")

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

                while units_to_sell > 0 and open_lots:
                    oldest_lot = open_lots[0]
                    if oldest_lot["Units"] <= units_to_sell:
                        units_to_sell -= oldest_lot["Units"]
                        open_lots.pop(0)
                    else:
                        oldest_lot["Units"] -= units_to_sell
                        units_to_sell = 0

        if not validation_error:
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
            sanity_verified = st.checkbox("✅ I confirm that this accurately reflects my current portfolio.")

            # --- MODULE 4: THE TAX ENGINE ---
            if not sanity_verified:
                st.warning(
                    "🔒 Please verify your data and check the confirmation box above to unlock the Tax Engine and CFO Strategy.")
            elif total_units_remaining <= 0:
                st.warning("Your current balance is 0. There are no open lots to simulate.")
            else:
                st.markdown("---")
                st.header("4. The Tax Engine: Section 91(b) & Moses Ruling")
                st.write(
                    "This engine calculates your tax liability per lot, applying the Moses Ruling to separate recognized capital losses from nominal currency losses, and automatically offsets losses against profits.")

                # Run the updated Tax Engine
                total_tax_today, lot_results, tot_taxable, tot_loss = calculate_portfolio_tax(open_lots, current_price,
                                                                                              current_rate)

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

                # Scenario B Init
                net_ils_after_tax_today = total_ils_value - total_tax_today
                new_usd_base = net_ils_after_tax_today / current_rate
                new_units = new_usd_base / current_price
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
                    name='Tax Base Reset',
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
                    # Check if resetting actually improves the future net value (The Holy Grail) or hurts it (Lowering Tax Base)
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
                        f"**Strategy Validated:** Within your {investment_horizon}-year horizon, the Tax Base Reset remains highly profitable. The tax savings outweigh the lost compound interest for the entire projected period.")

                # Actionable Disclaimer Box
                disclaimer_items = [
                    "💡 <b>נקודות קריטיות לתשומת לב לקראת ביצוע:</b> הסימולציה מציגה את השפעת מס רווח ההון על הקרן בלבד. ביצוע \"ניקוי שולחן\" בפועל דורש שתי פעולות רצופות, ולכן חובה לוודא מול הברוקר:",
                    "<b>1. עמלות מינימום:</b> קח בחשבון עמלות קנייה ומכירה. בתיקים קטנים, עמלות המינימום (לרוב כ-15$-20$ לפעולה הכפולה) עלולות למחוק את רוב או כל חיסכון המס.",
                    "<b>2. סכנת המרה כפולה:</b> ודא שתמורת המכירה נכנסת לחשבון המט\"ח (USD) ו<b>שלא</b> מתבצעת המרה אוטומטית לשקלים, כדי למנוע עמלות חליפין ופערי שער (Spread) מיותרים.",
                    "<b>3. סכנת \"עסקה מלאכותית\" (סעיף 86 לפקודה):</b> מכירה וקנייה מיידית של <i>אותו נייר ערך בדיוק</i> עלולה להיות מסווגת על ידי מס הכנסה כעסקה מלאכותית (Wash Sale), מה שעשוי לאיין את ההכרה באירוע המס (מאפשר לפקיד השומה להתעלם מפעולה פיננסית אם הוא קובע שהפעולה נעשתה אך ורק כדי להימנע מתשלום מס, ואין מאחוריה שום היגיון כלכלי או מסחרי אמיתי). כדי להתמודד עם סוגיה זו ולשמור על החשיפה לשוק, משקיעים רבים בוחרים לבצע את הרכישה החוזרת בקרן מחקה עוקבת של יצרן אחר (למשל, מכירת קרן SPY ורכישת קרן VOO או IVV באותו רגע)."
                ]

                # Append the dividend warning ONLY if the asset pays dividends
                if pays_dividend:
                    disclaimer_items.append(
                        "<b>4. מס דיבידנדים (Tax Drag):</b> המערכת זיהתה שנכס הבסיס שבחרת מחלק דיבידנדים. חשוב לדעת שחישוב הריבית דריבית העתידי (התשואה שהזנת) לא מנכה את המס שנגבה במקור בעת חלוקת הדיבידנד (לרוב 25%). במציאות, גביית המס השוטפת תקטין במעט את קצב הצמיחה האמיתי של התיק."
                    )

                # Construct the HTML strictly without any code-level indentation leaks
                inner_html = "<br><br>".join(disclaimer_items)
                rtl_disclaimer_html = f'<div dir="rtl" style="background-color: #e8f4f8; padding: 15px; border-radius: 5px; border: 1px solid #b8daff; color: #004085; text-align: right; font-family: sans-serif; line-height: 1.6;">{inner_html}</div>'

                # Render the clean HTML component
                st.markdown(rtl_disclaimer_html, unsafe_allow_html=True)
    else:
        st.error("Failed to fetch necessary market data. Please verify the ticker symbol or try again later.")