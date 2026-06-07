# 📊 Capital Gains Tax Simulator: Tax Basis Step-Up Strategy

An advanced, interactive Python-based web application designed for Israeli investors holding USD-denominated assets. This simulator evaluates the financial viability of a **Tax Basis Step-Up** strategy under Section 91(b) of the Israeli Income Tax Ordinance and the Moses Ruling.

## 🎯 Executive Summary
Israeli tax law provides a "nominal protection" mechanism: capital gains tax is capped at the nominal ILS profit. In environments where the USD depreciates against the ILS, investors may have significant real USD profits but zero nominal ILS profits. This creates a legal loophole to perform a "wash sale" (selling and immediately rebuying the asset) to reset the tax basis higher, at zero tax cost. 

However, executing this strategy involves friction costs (commissions, bid-ask spreads) and impacts compound interest. This simulator acts as a robust Tax Engine and CFO dashboard to calculate the exact breakeven point and determine if the strategy is mathematically sound for your portfolio.

## ✨ Key Features
* **Advanced Tax Engine:** Calculates Section 91(b) tax liability per Tax Lot based on the mandatory FIFO (First-In-First-Out) algorithm.
* **The Moses Ruling Filter:** Automatically identifies and zeros out nominal currency losses that are not recognized for tax offset purposes.
* **Real-Time Data Pipeline:** * Fetches historical USD/ILS exchange rates directly from the Bank of Israel (SDMX-JSON API).
  * Pulls real-time asset prices and dividend status using **Finnhub**, with an automatic failover redundancy mechanism routing to **Tiingo** if rate limits are hit.
* **Breakeven Analysis:** Projects the Net ILS portfolio value over time using compound interest calculations, visualizing the exact year the tax savings outweigh the lost capital from friction costs.
* **Responsive UI:** Built with Streamlit and Plotly for a modern, interactive, and seamless user experience.

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