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