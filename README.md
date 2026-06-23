# 📊 Capital Gains Tax Simulator: Tax Basis Step-Up Strategy

An advanced, interactive Python-based web application designed for Israeli investors holding USD-denominated assets. This simulator evaluates the financial viability of a **Tax Basis Step-Up** strategy under Section 91(b) of the Israeli Income Tax Ordinance and the Moses Ruling.

## 🎯 Executive Summary
Israeli tax law provides a "nominal protection" mechanism: capital gains tax is capped at the nominal ILS profit. In environments where the USD depreciates against the ILS, investors may have significant real USD profits but zero nominal ILS profits. This creates a legal loophole to perform a "wash sale" (selling and immediately rebuying the asset) to reset the tax basis higher, at zero tax cost. 

However, executing this strategy involves friction costs (commissions, bid-ask spreads) and impacts compound interest. This simulator acts as a robust Tax Engine and CFO dashboard to calculate the exact breakeven point and determine if the strategy is mathematically sound for your portfolio.

## ✨ Key Features
* **Advanced Tax Engine:** Evaluates Section 91(b) tax liabilities per individual Tax Lot using a highly stable, chronological First-In-First-Out (FIFO) queueing algorithm with automated splitting logic.
* **The Moses Ruling Filter:** Implements an automated double-loss filter to isolate and nullify nominal currency losses that are legally unrecognized for tax offset purposes under current Israeli regulations.
* **Real-Time Data Pipeline & Redundancy:** Integrates directly with the Bank of Israel's official SDMX-JSON API for historical FX data, backed by a resilient, multi-tier market asset pipeline using Finnhub with automated failover routing to Tiingo.
* **Cryptographic State Management:** Protects UI data integrity against reactive layout desynchronization by generating a continuous `SHA-256` hash of ledger and ticker inputs, programmatically locking the analysis until compliance checks are re-verified.
* **Dynamic Liquidation & Breakeven Projections:** Replaces simplified static interest formulas with a Full Liquidation Simulation, charting net portfolio values over time by dynamically re-running the core tax engine under linear interpolation models for future asset growth and FX shifts.
* **The CFO Verdict Matrix:** A rule-based strategic decision engine that classifies complex portfolio states into five high-conviction financial categories (e.g., *The Golden Point*, *Step-Down Trap*, *Unnecessary Tax Advancement*) to deliver contextual, actionable insights.
* **Strategic Report Export:** Native document compilation capabilities allowing users to instantly download a comprehensive, standalone investment strategy manual (PDF) directly from the application interface.
* **Enterprise UI/UX:** Designed with a customized Right-to-Left (RTL) Hebrew presentation layer, utilizing headless mathematical modules combined with fully interactive dynamic vector plotting via Plotly.

## 🛠️ Tech Stack
* **Language:** Python 3.9+
* **Frontend/Framework:** Streamlit
* **Data Processing:** Pandas, NumPy
* **Visualization:** Plotly
* **APIs:** Requests, urllib3 (with custom HTTPAdapter for robust retry mechanisms)

## 🚀 Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YourUsername/YourRepositoryName.git](https://github.com/YourUsername/YourRepositoryName.git)
   cd YourRepositoryName
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure API Keys:**
   The application requires API keys for real-time market data. Create a directory named `.streamlit` in the root folder, and inside it, create a file named `secrets.toml`.
   ```toml
   # .streamlit/secrets.toml
   FINNHUB_API_KEY = "your_finnhub_api_key_here"
   TIINGO_API_KEY = "your_tiingo_api_key_here"
   ```

4. **Run the application:**
   ```bash
   streamlit run app.py
   ```

## ⚖️ Disclaimer
This project is for educational, research, and informational purposes only. It does not constitute financial, tax, or investment advice. The tax logic and algorithms implemented within this simulator may not cover all edge cases found in the Israeli Income Tax Ordinance (e.g., specific corporate offsets, historical rule changes, etc.). Always consult with a certified CPA or tax advisor before making any financial decisions or executing trades in your brokerage account.