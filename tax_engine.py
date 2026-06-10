def calculate_portfolio_tax(lots: list, current_price: float, current_rate: float, tax_rate: float = 0.25):
    """
    Calculates Section 91(b) tax, applies the Moses Ruling for offsetting,
    and calculates the exact ILS Base Erosion in actual cash value (Burned Shield Cash).
    """
    total_taxable_profit = 0.0
    total_recognized_loss = 0.0
    total_burned_shield_cash = 0.0
    lot_results = []

    for index, lot in enumerate(lots):
        units = lot["Units"]
        buy_price = lot["Price"]
        buy_rate = lot["Rate"]

        # Calculate raw USD and ILS differences
        usd_profit = (current_price - buy_price) * units
        nominal_ils_profit = (current_price * units * current_rate) - (buy_price * units * buy_rate)

        taxable_profit = 0.0
        recognized_loss = 0.0
        burned_shield_cash = 0.0

        # Scenario A: Profit in both USD and ILS (Section 91b protection)
        if usd_profit > 0 and nominal_ils_profit > 0:
            taxable_profit = min(usd_profit * current_rate, nominal_ils_profit)

        # Scenario B: Moses Ruling (Double Loss)
        elif usd_profit < 0 and nominal_ils_profit < 0:
            real_ils_loss = abs(usd_profit) * current_rate
            nominal_ils_loss = abs(nominal_ils_profit)
            recognized_loss = min(real_ils_loss, nominal_ils_loss)

            # The unrecognized portion of the nominal loss, converted to lost cash tax shield
            unrecognized_nominal_loss = nominal_ils_loss - recognized_loss
            burned_shield_cash = unrecognized_nominal_loss * tax_rate

        # Scenario C: Step-Down Trap (USD Profit, ILS Loss)
        elif usd_profit > 0 and nominal_ils_profit < 0:
            # The entire nominal loss is unrecognized. Convert to lost cash tax shield.
            burned_shield_cash = abs(nominal_ils_profit) * tax_rate

        total_taxable_profit += taxable_profit
        total_recognized_loss += recognized_loss
        total_burned_shield_cash += burned_shield_cash

        lot_results.append({
            "Lot #": index + 1,
            "Orig. Date": lot.get("Date", "N/A"),
            "Rem. Units": round(units, 4),
            "USD Profit ($)": round(usd_profit, 2),
            "Nominal ILS (₪)": round(nominal_ils_profit, 2),
            "Taxable Profit (₪)": round(taxable_profit, 2),
            "Recognized Loss (₪)": round(recognized_loss, 2),
            "Lost Cash Shield (₪)": round(burned_shield_cash, 2)  # UI-friendly label
        })

    # Netting and final liability
    net_taxable = max(0, total_taxable_profit - total_recognized_loss)
    final_tax_liability = net_taxable * tax_rate

    return final_tax_liability, lot_results, total_taxable_profit, total_recognized_loss, total_burned_shield_cash