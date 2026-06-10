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
# UI/UX: GLOBAL RTL CSS INJECTION
# ==========================================
st.markdown("""
    <style>
    /* Force RTL on Download Buttons and align to right */
    div[data-testid="stDownloadButton"] {
        display: flex;
        justify-content: flex-end;
    }
    div[data-testid="stDownloadButton"] button {
        direction: rtl;
    }

    /* Force RTL on tooltips (help text) */
    div[data-baseweb="tooltip"] {
        direction: rtl;
        text-align: right;
    }

    /* Force RTL and right-alignment on native Streamlit widget labels */
    .stTextInput label, .stNumberInput label, .stSelectbox label, .stSlider label {
        direction: rtl;
        text-align: right;
        display: block;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# SIDEBAR: GLOBAL SETTINGS
# ==========================================
default_start = (datetime.today() - timedelta(days=365 * 5)).strftime('%Y-%m-%d')
df_ils_init = fetch_historical_exchange_rates(default_start)
current_rate = float(df_ils_init['Close'].iloc[-1]) if not df_ils_init.empty else 3.60

st.sidebar.markdown("<h2 dir='rtl' style='text-align: right;'>⚙️ הגדרות כלליות</h2>", unsafe_allow_html=True)
ticker_input = st.sidebar.text_input("סימול נכס (לדוגמה: SPY, QQQ)", value="SPY").upper()

if not re.match(r"^[A-Z0-9\-\.]+$", ticker_input):
    st.sidebar.markdown(
        '<div dir="rtl" style="color: #721c24; background-color: #f8d7da; padding: 10px; border-radius: 5px; text-align: right;">❌ קלט לא חוקי: אנא הזינו סימול חוקי באנגלית.</div>',
        unsafe_allow_html=True)
    st.stop()

st.sidebar.markdown("---")
st.sidebar.markdown("<h3 dir='rtl' style='text-align: right;'>💸 עלויות חיכוך</h3>", unsafe_allow_html=True)
transaction_costs_usd = st.sidebar.number_input(
    "עמלות קנייה ומכירה ($)",
    min_value=0.0,
    value=0.0,
    step=1.0,
    help="סך כל עמלות הברוקר עבור פעולות המכירה והקנייה המיידית (לדוגמה: $5 למכירה + $5 לקנייה = $10 סך הכל). סכום זה יופחת מקרן ההשקעה הזמינה.\u200F"
)

st.sidebar.markdown("---")
st.sidebar.markdown("<h3 dir='rtl' style='text-align: right;'>🔮 תחזיות לעתיד</h3>", unsafe_allow_html=True)
expected_return = st.sidebar.number_input("תשואה שנתית צפויה (%)", min_value=-100.0, max_value=100.0, value=5.0,
                                          step=0.5)
investment_horizon = st.sidebar.slider("אופק השקעה (בשנים)", min_value=1, max_value=30, value=10)

# Security Fix: Fetching keys safely outside the cached function
fh_key = st.secrets.get("FINNHUB_API_KEY", "")
tg_key = st.secrets.get("TIINGO_API_KEY", "")

with st.spinner("מאתחל נתוני שוק..."):
    current_price, asset_currency, pays_dividend = fetch_asset_data(ticker_input, fh_key, tg_key)

future_rate = st.sidebar.number_input("שער דולר/שקל עתידי צפוי", min_value=1.0, max_value=10.0,
                                      value=3.5, step=0.1)

# ==========================================
# MAIN DASHBOARD
# ==========================================
st.markdown("<h1 dir='rtl' style='text-align: right;'>📊 סימולטור מס רווחי הון: אסטרטגיית העלאת בסיס מס</h1>",
            unsafe_allow_html=True)
st.markdown(
    '<div dir="rtl" style="text-align: right; font-size: 1.1rem; margin-bottom: 20px;">הערכת הכדאיות הכלכלית של אסטרטגיית <b>"העלאת בסיס מס" (Tax Base Step-Up)</b> בהתאם לסעיף 91(ב) לפקודת מס הכנסה.</div>',
    unsafe_allow_html=True)

# --- PDF DOWNLOAD BUTTON ---
try:
    with open("Guide.pdf", "rb") as pdf_file:
        pdf_bytes = pdf_file.read()

    st.download_button(
        label="📄 להורדת המדריך האסטרטגי המלא (PDF)",
        data=pdf_bytes,
        file_name="Tax_Base_Step-Up_Guide.pdf",
        mime="application/pdf",
        help="מומלץ מאוד לקרוא מדריך מקיף זה בטרם קבלת החלטות מס או ביצוע פעולות בחשבון ההשקעות שלכם.\u200F"
    )
except FileNotFoundError:
    pass

# --- MODULE 1: MACRO VIEW ---
st.markdown("<h2 dir='rtl' style='text-align: right;'>1. מבט מאקרו: פוטנציאל מגן המס ההיסטורי</h2>",
            unsafe_allow_html=True)
st.markdown(
    '<div dir="rtl" style="text-align: right; margin-bottom: 15px;">גרף זה מציג את החוזק ההיסטורי של הדולר מול השקל בהשוואה לשער של היום. שער היסטורי גבוה יותר מתורגם לפוטנציאל גבוה יותר של מגן מס היום, ללא תלות בנכס ספציפי.</div>',
    unsafe_allow_html=True)

# Custom RTL Label for the Date Input
st.markdown(
    "<div dir='rtl' style='text-align: right; font-weight: bold; margin-bottom: 5px;'>הציגו היסטוריית שער דולר/שקל החל מתאריך:</div>",
    unsafe_allow_html=True)
start_date = st.date_input("", value=pd.to_datetime(default_start), label_visibility="collapsed")

with st.spinner("מושך נתוני מאקרו..."):
    df_ils = fetch_historical_exchange_rates(start_date.strftime('%Y-%m-%d'))

if not df_ils.empty:
    df_ils['R_max_percent'] = ((df_ils['Close'] / current_rate) - 1) * 100

    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=df_ils.index, y=df_ils['R_max_percent'],
        name="פוטנציאל רווח דולרי פטור ממס (%)",
        line=dict(color='#2ecc71', width=2.5),
        fill='tozeroy', fillcolor='rgba(46, 204, 113, 0.15)',
        hovertemplate="<b>תאריך:</b> %{x|%Y-%m-%d}<br><b>שער:</b> ₪%{customdata:.4f}<br><b>מקסימום רווח דולרי פטור:</b> %{y:.2f}%<extra></extra>",
        customdata=df_ils['Close']
    ))
    fig1.add_hline(y=0, line_width=1.5, line_color="black", line_dash="dash")

    chart_title = f"פוטנציאל העלאת בסיס מס (שער נוכחי: ₪{current_rate:.4f})"

    fig1.update_layout(
        title=dict(text=chart_title, x=1, xanchor='right'),
        xaxis_title="תאריך היסטורי", yaxis_title="תשואה דולרית מקסימלית פטורה (%)",
        template="plotly_white", hovermode="x unified", margin=dict(l=0, r=0, t=40, b=0)
    )
    st.plotly_chart(fig1, use_container_width=True)
    st.markdown(
        '<div dir="rtl" style="text-align: right; font-size: 0.85rem; color: gray; margin-top: 10px;">* <b>הערה לגבי שערי חליפין:</b> המערכת שואבת את השער היציג הרשמי מבנק ישראל. שער זה מפורסם פעם ביום (ב\'-ו\') סביב השעה 15:30 שעון ישראל. לכן, בשעות הבוקר או בסופי שבוע, השער משקף את יום העסקים האחרון שפורסם. לצורכי מס בישראל, רווחי הון מחושבים חוקית על בסיס שער יציג זה, ולא לפי שערי מסחר רציף (Forex).</div>',
        unsafe_allow_html=True)
else:
    st.markdown(
        '<div dir="rtl" style="background-color: #fff3cd; padding: 15px; border-radius: 5px; color: #856404; text-align: right; border: 1px solid #ffeeba;">⚠️ נתוני שער חליפין היסטוריים אינם זמינים כרגע.</div>',
        unsafe_allow_html=True)

# --- MODULE 2: THE FULL LEDGER ---
st.markdown("---")
st.markdown("<h2 dir='rtl' style='text-align: right;'>2. יומן העסקאות: היסטוריית פעולות (Ledger)</h2>",
            unsafe_allow_html=True)
st.markdown(
    f'<div dir="rtl" style="text-align: right; margin-bottom: 15px;">הזינו את היסטוריית הרכישות והמכירות שלכם עבור <b>{ticker_input}</b>. המערכת תחשב את שכבות המס הפתוחות (Tax Lots) בתיק שלכם על בסיס שיטת ה-<b>FIFO</b> (נכנס ראשון, יוצא ראשון).</div>',
    unsafe_allow_html=True)

# --- Contextual Market Snapshot ---
st.markdown(
    f'<div dir="rtl" style="background-color: #e8f4f8; padding: 10px; border-radius: 5px; color: #004085; text-align: right; border: 1px solid #b8daff;">📌 <b>נכס:</b> <b>{ticker_input}</b> &nbsp;|&nbsp; <b>מחיר:</b> <b>${current_price:,.2f}</b> &nbsp;|&nbsp; <b>שער דולר/שקל:</b> <b>₪{current_rate:,.4f}</b></div>',
    unsafe_allow_html=True)
st.write("")  # Spacer

default_ledger = pd.DataFrame(columns=["תאריך", "פעולה", "יחידות", "מחיר ($)", "שער (₪/$)"])
edited_df = st.data_editor(
    default_ledger,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "תאריך": st.column_config.DateColumn("תאריך", required=True, max_value=datetime.today().date()),
        "פעולה": st.column_config.SelectboxColumn("פעולה", options=["קנייה", "מכירה"], required=True),
        "יחידות": st.column_config.NumberColumn("יחידות", min_value=0.001, required=True),
        "מחיר ($)": st.column_config.NumberColumn("מחיר ($)", min_value=0.01, required=True),
        "שער (₪/$)": st.column_config.NumberColumn("שער (₪/$) - השאירו ריק להשלמה אוטומטית", min_value=1.0,
                                                   required=False),
    }
)

edited_df = edited_df.dropna(subset=["תאריך", "פעולה", "יחידות", "מחיר ($)"]).reset_index(drop=True)

# Chronological FIFO Algorithm with Stable Sort
edited_df['Action_Rank'] = edited_df['פעולה'].map({'קנייה': 1, 'מכירה': 2})
edited_df = edited_df.sort_values(by=["תאריך", "Action_Rank"]).reset_index(drop=True)

open_lots = []
validation_error = False

for _, row in edited_df.iterrows():
    action = row["פעולה"]
    units = row["יחידות"]
    price = row["מחיר ($)"]
    date = row["תאריך"]

    # --- THE AUTO-FILL LOGIC ---
    rate = row["שער (₪/$)"]
    if pd.isna(rate) or rate <= 0:
        rate = get_historical_rate_for_date(date, df_ils_init, fallback_rate=current_rate)
    # ---------------------------

    # Map Hebrew UI back to English keys for the tax engine
    if action == "קנייה":
        open_lots.append({"Date": date, "Units": units, "Price": price, "Rate": rate})
    elif action == "מכירה":
        units_to_sell = units
        current_available = sum(lot["Units"] for lot in open_lots)

        if units_to_sell > current_available:
            st.markdown(
                f'<div dir="rtl" style="background-color: #f8d7da; padding: 15px; border-radius: 5px; color: #721c24; text-align: right; border: 1px solid #f5c6cb;">🛑 <b>שגיאה כרונולוגית:</b> בתאריך {date.strftime("%Y-%m-%d")} הזנתם מכירה של {units_to_sell} יחידות, אך היתרה הזמינה באותו רגע עמדה על {current_available} יחידות בלבד. יתרות שליליות אינן חוקיות. אנא תקנו את היומן.</div>',
                unsafe_allow_html=True)
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
st.markdown("<h2 dir='rtl' style='text-align: right;'>3. תמונת מצב התיק ואימות נתונים (Sanity Check)</h2>",
            unsafe_allow_html=True)

total_units_remaining = sum(lot["Units"] for lot in open_lots)
total_usd_value = total_units_remaining * current_price
total_ils_value = total_usd_value * current_rate

col1, col2, col3 = st.columns(3)
col1.metric("סך יחידות פתוחות", f"{total_units_remaining:,.4f}")
col2.metric("שווי נוכחי כולל ($)", f"${total_usd_value:,.2f}")
col3.metric("שווי נוכחי כולל (₪)", f"₪{total_ils_value:,.2f}")

st.markdown(
    '<div dir="rtl" style="background-color: #e8f4f8; padding: 15px; border-radius: 5px; color: #004085; text-align: right; font-family: sans-serif; margin-bottom: 15px; border: 1px solid #b8daff;">💡 <b>המלצת מערכת (Best Practice):</b> ודאו ש"סך יחידות פתוחות" תואם במדויק ליתרה המופיעה בחשבון הברוקר או הבנק שלכם.</div>',
    unsafe_allow_html=True)
sanity_verified = st.checkbox("אני מאשר/ת שהנתונים משקפים במדויק את התיק הנוכחי שלי.")

if not sanity_verified:
    st.markdown(
        '<div dir="rtl" style="background-color: #fff3cd; padding: 15px; border-radius: 5px; color: #856404; text-align: right; font-family: sans-serif; border: 1px solid #ffeeba;">🔒 אנא אשרו את הנתונים בתיבת הסימון מעלה כדי לפתוח את מנוע המס ואת האסטרטגיה הפיננסית.</div>',
        unsafe_allow_html=True)
    st.stop()
elif total_units_remaining <= 0:
    st.markdown(
        '<div dir="rtl" style="background-color: #fff3cd; padding: 15px; border-radius: 5px; color: #856404; text-align: right; font-family: sans-serif; border: 1px solid #ffeeba;">היתרה הנוכחית שלכם היא 0. אין שכבות פתוחות לביצוע סימולציה.</div>',
        unsafe_allow_html=True)
    st.stop()

# --- MODULE 4: THE TAX ENGINE ---
st.markdown("---")
st.markdown("<h2 dir='rtl' style='text-align: right;'>4. מנוע המס: סעיף 91(ב) והלכת מוזס</h2>", unsafe_allow_html=True)
st.markdown(
    '<div dir="rtl" style="text-align: right; margin-bottom: 15px;">מנוע זה מפרק את התיק ומחשב חבות מס עבור כל שכבת רכישה (Tax Lot) בנפרד. הוא מיישם את "הלכת מוזס" כדי להפריד בין הפסדי הון מוכרים להפסדי מטבע נומינליים שאינם מוכרים, ומבצע קיזוז הפסדים אוטומטי מול הרווחים.</div>',
    unsafe_allow_html=True)

total_tax_today, lot_results, tot_taxable, tot_loss, total_burned_shield = calculate_portfolio_tax(open_lots,
                                                                                                   current_price,
                                                                                                   current_rate)

res_df = pd.DataFrame(lot_results)

# Translate Advanced Table Headers
res_df.rename(columns={
    "Lot #": "שכבה",
    "Orig. Date": "תאריך מקורי",
    "Rem. Units": "יחידות נותרות",
    "USD Profit ($)": "רווח דולרי ($)",
    "Nominal ILS (₪)": "רווח/הפסד שקלי (₪)",
    "Taxable Profit (₪)": "רווח חייב במס (₪)",
    "Recognized Loss (₪)": "הפסד מוכר לקיזוז (₪)",
    "Lost Cash Shield (₪)": "מגן מס שנשרף (₪)"
}, inplace=True)

with st.expander("🔍 צפו בפירוט שכבות המס (Advanced Tax Lot Breakdown)", expanded=True):
    def highlight_burned(val):
        color = '#ffcccc' if isinstance(val, (int, float)) and val > 0 else ''
        return f'background-color: {color}'


    if "מגן מס שנשרף (₪)" in res_df.columns:
        styled_df = res_df.style.map(highlight_burned, subset=["מגן מס שנשרף (₪)"])
        st.dataframe(styled_df, use_container_width=True)
    else:
        st.dataframe(res_df, use_container_width=True)

col_t1, col_t2, col_t3 = st.columns(3)
col_t1.metric("סה״כ רווח חייב במס", f"₪{tot_taxable:,.2f}")
col_t2.metric("סה״כ הפסד הון מוכר (מוזס)", f"₪{tot_loss:,.2f}")

# Display the burned shield metric visually
if total_burned_shield > 0:
    col_t3.metric("🔥 מגן מס (מזומן) שנשרף", f"₪{total_burned_shield:,.2f}", delta="קנס הורדת בסיס (Step-Down)",
                  delta_color="inverse")
else:
    col_t3.metric("🏆 מגן מס (מזומן) שנשרף", "₪0.00", delta="נקודת הקיזוז המושלם", delta_color="normal")

st.markdown(
    f'<div dir="rtl" style="background-color: #d4edda; padding: 20px; border-radius: 5px; color: #155724; text-align: right; border: 1px solid #c3e6cb;"><h3 style="margin: 0;">🎯 חבות המס הסופית (אם נבצע איפוס היום): ₪{total_tax_today:,.2f}</h3></div>',
    unsafe_allow_html=True)
st.markdown("<br>", unsafe_allow_html=True)

# Warning callout right under the tax liability if shield is burned
if total_tax_today == 0 and total_burned_shield > 0:
    st.markdown(
        f'<div dir="rtl" style="background-color: #fff3cd; padding: 15px; border-radius: 5px; color: #856404; text-align: right; border: 1px solid #ffeeba;"><b>⚠️ שים לב:</b> תשלום המס היום הוא אכן אפס, אך במחיר של שריפת מגן מס בשווי מזומן של <b>₪{total_burned_shield:,.2f}</b>. המשיכו לגרף מטה כדי לבדוק אם אובדן זה משתלם אל מול התחזיות העתידיות שלכם.</div>',
        unsafe_allow_html=True)

# --- MODULE 5: CFO STRATEGY (BREAKEVEN) ---
st.markdown("---")
st.markdown("<h2 dir='rtl' style='text-align: right;'>5. ניתוח אסטרטגי: חיסכון במס מול ריבית דריבית</h2>",
            unsafe_allow_html=True)
st.markdown(
    '<div dir="rtl" style="text-align: right; margin-bottom: 15px;">האם יש היגיון מתמטי לשלם מס היום כדי להעלות את בסיס המס העתידי שלכם? לפניכם תחזית של שווי התיק נטו בשקלים (לאחר תשלום המס הסופי) עבור שני התרחישים.</div>',
    unsafe_allow_html=True)

years = np.arange(1, investment_horizon + 1)
scenario_a_net = []  # HOLD
scenario_b_net = []  # Reset Tax Base Today

# ==========================================
# SCENARIO B INITIALIZATION (WITH FRICTION COSTS)
# ==========================================
net_ils_after_tax_today = total_ils_value - total_tax_today
new_usd_base = (net_ils_after_tax_today / current_rate) - transaction_costs_usd
new_usd_base = max(0.0, new_usd_base)
new_units = new_usd_base / current_price

reset_lot = [{"Units": new_units, "Price": current_price, "Rate": current_rate}]

for y in years:
    future_price_y = current_price * ((1 + (expected_return / 100)) ** y)

    # Scenario A projection
    tax_a, _, _, _, _ = calculate_portfolio_tax(open_lots, future_price_y, future_rate)
    gross_ils_a = total_units_remaining * future_price_y * future_rate
    scenario_a_net.append(gross_ils_a - tax_a)

    # Scenario B projection
    tax_b, _, _, _, _ = calculate_portfolio_tax(reset_lot, future_price_y, future_rate)
    gross_ils_b = new_units * future_price_y * future_rate
    scenario_b_net.append(gross_ils_b - tax_b)

# Find Breakeven
breakeven_year = None
for i in range(len(years)):
    if scenario_a_net[i] > scenario_b_net[i]:
        breakeven_year = years[i]
        break

fig2 = go.Figure()

# Plot HOLD scenario
fig2.add_trace(go.Scatter(
    x=years,
    y=scenario_a_net,
    mode='lines',
    name='HOLD (החזקה)',
    line=dict(color='#27ae60', width=3),
    hovertemplate="שווי תיק נטו: ₪%{y:,.2f}<extra></extra>"
))

# Plot Tax Base Reset scenario
fig2.add_trace(go.Scatter(
    x=years,
    y=scenario_b_net,
    mode='lines',
    name='Tax Base Step-Up (איפוס)',
    line=dict(color='#c0392b', width=3),
    hovertemplate="שווי תיק נטו: ₪%{y:,.2f}<extra></extra>"
))

if breakeven_year:
    fig2.add_vline(x=breakeven_year, line_width=2, line_dash="dash", line_color="black",
                   annotation_text=f"נקודת איזון: שנה {breakeven_year}", annotation_position="top left")

fig2.update_layout(
    title=dict(text="שווי תיק נטו (₪) לאחר תשלום מס סופי", x=1, xanchor='right'),
    xaxis_title="שנים קדימה",
    yaxis_title="שווי נטו (₪)",
    template="plotly_white",
    hovermode="x unified",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

fig2.update_xaxes(
    hoverformat=".0f",
    ticksuffix=" שנים"
)

st.plotly_chart(fig2, use_container_width=True)

# CFO Verdict
st.markdown("<br><h3 dir='rtl' style='text-align: right;'>⚖️ פסק הדין האסטרטגי (CFO Verdict)</h3>",
            unsafe_allow_html=True)

total_orig_usd_cost = sum(lot["Price"] * lot["Units"] for lot in open_lots)
total_usd_profit_today = total_usd_value - total_orig_usd_cost

# --- STAGE 1: IDENTIFY THE CURRENT SCENARIO (THE MATRIX) ---
if total_tax_today == 0 and total_usd_profit_today > 0:
    if total_burned_shield <= 1.0:
        st.markdown(
            '<div dir="rtl" style="background-color: #d4edda; padding: 15px; border-radius: 5px; color: #155724; text-align: right; border: 1px solid #c3e6cb;"><h4 style="margin-top: 0;">🏆 תרחיש 1: The Golden Point (נקודת הקיזוז המושלם)</h4>מצב אידיאלי: אתם מקבעים את הרווח הדולרי ומקפיצים את בסיס המס (Step-Up) באפס מס, <b>מבלי להקריב מטריית הגנה שקלית</b>. פעולה כירורגית ואופטימלית.</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div dir="rtl" style="background-color: #fff3cd; padding: 15px; border-radius: 5px; color: #856404; text-align: right; border: 1px solid #ffeeba;"><h4 style="margin-top: 0;">⚠️ תרחיש 2: מלכודת ה-Step-Down השקלית</h4>פעולה בסיכון: תשלום המס היום הוא 0 ₪, אך אתם <b>זורקים לפח מגן מס עתידי בשווי של ₪{total_burned_shield:,.2f}</b>.</div>',
            unsafe_allow_html=True)

elif total_tax_today > 0 and total_usd_profit_today > 0:
    st.markdown(
        f'<div dir="rtl" style="background-color: #f8d7da; padding: 15px; border-radius: 5px; color: #721c24; text-align: right; border: 1px solid #f5c6cb;"><h4 style="margin-top: 0;">🛑 תרחיש 3: הקדמת מס מיותרת</h4>הפעולה תגרור תשלום מס מיידי במזומן של <b>₪{total_tax_today:,.2f}</b>. הוצאת נזילות מהתיק פוגעת אנושות באפקט הריבית דריבית.</div>',
        unsafe_allow_html=True)

elif total_usd_profit_today <= 0:
    st.markdown(
        '<div dir="rtl" style="background-color: #e2e3e5; padding: 15px; border-radius: 5px; color: #383d41; text-align: right; border: 1px solid #d6d8db;"><h4 style="margin-top: 0;">📉 תרחיש 4/5: השמדת ערך או אשליית מטבע</h4>הנכס נמצא בהפסד דולרי. ביצוע איפוס עכשיו מהווה הורדת בסיס (Step-Down). אסטרטגיה זו כדאית אך ורק במקרה של "מכירה רעיונית" שבה מנצלים את ההפסד המוכר לקיזוז מיידי מול נכסים מורווחים אחרים בתיק.</div>',
        unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# --- STAGE 2: FUTURE PROJECTIONS ANALYSIS (THE BREAKEVEN LOGIC) ---
st.markdown("---")
st.markdown("<h4 dir='rtl' style='text-align: right;'>🔮 ניתוח כדאיות מבוסס עתיד (Future Projection Analysis)</h4>",
            unsafe_allow_html=True)

if total_tax_today == 0 and total_burned_shield > 1.0 and total_usd_profit_today > 0:
    if len(scenario_b_net) > 0 and len(scenario_a_net) > 0 and scenario_b_net[-1] > scenario_a_net[-1]:
        st.markdown(
            '<div dir="rtl" style="background-color: #d4edda; padding: 15px; border-radius: 5px; color: #155724; text-align: right; border: 1px solid #c3e6cb;"><b>שבירת המלכודת:</b> מנוע התחזיות קובע שעל אף שאתם שורפים מגן מס שקלי היום, תחזית העלייה של הדולר שהזנתם מנפחת את המגן הריאלי החדש בצורה שמפצה על כך. האסטרטגיה <b>מנצחת את חלופת ה-HOLD</b> לאורך תקופת ההשקעה.</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div dir="rtl" style="background-color: #f8d7da; padding: 15px; border-radius: 5px; color: #721c24; text-align: right; border: 1px solid #f5c6cb;"><b>השמדת ערך ודאית:</b> מנוע התחזיות מוכיח כי הוויתור על מגן המס השקלי היום לא משתלם. הגרף מראה שה-HOLD מנצח בענק.</div>',
            unsafe_allow_html=True)

elif breakeven_year:
    if breakeven_year == 1:
        st.markdown(
            f'<div dir="rtl" style="background-color: #f8d7da; padding: 15px; border-radius: 5px; color: #721c24; text-align: right; border: 1px solid #f5c6cb;"><b>אזהרה חמורה:</b> תחת תחזית תשואה של {expected_return}%, חלופת ה-HOLD מנצחת באופן מיידי. הפעולה אינה כדאית.</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div dir="rtl" style="background-color: #fff3cd; padding: 15px; border-radius: 5px; color: #856404; text-align: right; border: 1px solid #ffeeba;"><b>חלון זמנים מוגבל (Time-Sensitive):</b> האסטרטגיה רווחית אך ורק אם תמשכו את הכסף ב-<b>{breakeven_year - 1} השנים הקרובות</b>. החל משנה {breakeven_year}, אובדן התשואה (הריבית דריבית) על מס/עמלות ששולמו היום יעלה על חיסכון המס העתידי.</div>',
            unsafe_allow_html=True)
else:
    if total_tax_today > 0 or total_usd_profit_today <= 0:
        st.markdown(
            f'<div dir="rtl" style="background-color: #e2e3e5; padding: 15px; border-radius: 5px; color: #383d41; text-align: right; border: 1px solid #d6d8db;">למרות החיסכון במס, על פני טווח של {investment_horizon} שנים, מודל הצמיחה מראה יתרון להחזקה (HOLD) או כדאיות רק תחת שיקולי קיזוז חיצוניים.</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            f'<div dir="rtl" style="background-color: #d4edda; padding: 15px; border-radius: 5px; color: #155724; text-align: right; border: 1px solid #c3e6cb;"><b>אסטרטגיה מנצחת:</b> על פני אופק של {investment_horizon} שנים, אסטרטגיית ה-Step-Up מנצחת. החיסכון במס ממקסם את הוצאת הכספים במועד המשיכה הסופי.</div>',
            unsafe_allow_html=True)

# Actionable Disclaimer Box
disclaimer_items = [
    "⚠️ <b>הבהרה משפטית:</b> תוצאות הסימולציה מבוססות על מודל מתמטי והערכות עתידיות. המערכת נועדה למטרות מחקר, לימוד והדגמה בלבד, ואינה מהווה ייעוץ מס פרטני, ייעוץ פיננסי, או המלצה לביצוע פעולות בשוק ההון. חובה להתייעץ עם רואה חשבון או יועץ מס מוסמך טרם קבלת החלטות פיננסיות.",
    "💡 <b>נקודות קריטיות לתשומת לב לקראת ביצוע:</b> הסימולציה מציגה את השפעת מס רווח ההון על הקרן בלבד. ביצוע \"העלאת מס בסיס\" בפועל דורש שתי פעולות רצופות, ולכן חובה לוודא מול הברוקר:",
    "<b>1. חישוב עמלות שמרני (Friction Costs):</b> בתיקים קטנים, עמלות קנייה ומכירה עלולות למחוק את רוב או כל חיסכון המס. הסימולטור מפחית את העמלות שהזנת מסך ההון הזמין להשקעה מחדש, אך למען פשטות המודל, הן <b>אינן</b> משוקללות באופן רטרואקטיבי לתוך בסיס המס ההיסטורי (Adjusted Cost Basis). חישוב פרטני אצל רואה חשבון עשוי להקטין את חבות המס שלך אף יותר מהמוצג.",
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