"""
Generate account-level variance report and journal-level gap report for 2022.
Compares GL balances to audited financial statement breakdowns.
"""

import os
import csv
from decimal import Decimal
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path("/root/projects/dotmac_books/books_backup")

# Audited 2022 account balances from financial statements (Notes to FS)
AUDITED_2022_BALANCES = {
    # BALANCE SHEET - ASSETS
    'Property, Plant & Equipment': {'audited': Decimal('78201206'), 'opening': Decimal('91855808'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '1'},
    'Inventories': {'audited': Decimal('28411728'), 'opening': Decimal('42672377'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '2'},
    'Trade Receivables': {'audited': Decimal('58291409'), 'opening': Decimal('45113657'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '3'},
    'Staff Loan': {'audited': Decimal('0'), 'opening': Decimal('620000'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '3'},
    'WHT Receivable': {'audited': Decimal('65599797'), 'opening': Decimal('37825259'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '3'},
    'Zenith Bank': {'audited': Decimal('105724794'), 'opening': Decimal('9703896'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'Heritage Bank': {'audited': Decimal('100000'), 'opening': Decimal('100000'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'UBA': {'audited': Decimal('208039'), 'opening': Decimal('0'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'Cash at Hand': {'audited': Decimal('1460'), 'opening': Decimal('80616'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'Paystack': {'audited': Decimal('416000'), 'opening': Decimal('0'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'Paystack OPEX': {'audited': Decimal('33772'), 'opening': Decimal('131597'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'Flutterwave': {'audited': Decimal('1394008'), 'opening': Decimal('0'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},
    'Quick Teller': {'audited': Decimal('1057'), 'opening': Decimal('0'), 'type': 'ASSETS', 'normal': 'DEBIT', 'note': '4'},

    # BALANCE SHEET - EQUITY
    'Share Capital': {'audited': Decimal('14650000'), 'opening': Decimal('14650000'), 'type': 'EQUITY', 'normal': 'CREDIT', 'note': '5'},
    'Retained Earnings': {'audited': Decimal('98986276'), 'opening': Decimal('75686098'), 'type': 'EQUITY', 'normal': 'CREDIT', 'note': '6'},

    # BALANCE SHEET - LIABILITIES
    'Long Term Borrowings': {'audited': Decimal('94554299'), 'opening': Decimal('82203961'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '7'},
    'Trade Payables': {'audited': Decimal('107613478'), 'opening': Decimal('14367458'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '8'},
    'WHT Payable': {'audited': Decimal('2211412'), 'opening': Decimal('0'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '8'},
    'Accrued Expenses': {'audited': Decimal('1656670'), 'opening': Decimal('7705496'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '8'},
    'Income Tax Payable': {'audited': Decimal('17800988'), 'opening': Decimal('29798003'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '9'},
    'Education Tax Payable': {'audited': Decimal('1483416'), 'opening': Decimal('2719112'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '9'},
    'IT Levy Payable': {'audited': Decimal('593366'), 'opening': Decimal('928562'), 'type': 'LIABILITIES', 'normal': 'CREDIT', 'note': '9'},
}

# Audited 2022 Income Statement line items
AUDITED_2022_PL = {
    # REVENUE
    'Internet Revenue': {'audited': Decimal('513205564'), 'type': 'REVENUE', 'normal': 'CREDIT', 'note': '10'},
    'Other Business Revenue': {'audited': Decimal('1051801233'), 'type': 'REVENUE', 'normal': 'CREDIT', 'note': '10'},

    # COST OF SALES
    'Opening Inventory': {'audited': Decimal('42672377'), 'type': 'COGS', 'normal': 'DEBIT', 'note': '11'},
    'Purchases': {'audited': Decimal('944502819'), 'type': 'COGS', 'normal': 'DEBIT', 'note': '11'},
    'Internet Cost of Sales': {'audited': Decimal('275961963'), 'type': 'COGS', 'normal': 'DEBIT', 'note': '12'},
    'Closing Inventory': {'audited': Decimal('-28411728'), 'type': 'COGS', 'normal': 'CREDIT', 'note': '11'},

    # ADMIN EXPENSES (Note 13)
    'Staff Training': {'audited': Decimal('6111734'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Subscriptions': {'audited': Decimal('7947574'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Medical Expenses': {'audited': Decimal('2996059'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Printing & Stationery': {'audited': Decimal('6674429'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Rent': {'audited': Decimal('5635500'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Utilities': {'audited': Decimal('25918146'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Telephone': {'audited': Decimal('2110127'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Fuel & Lubricant': {'audited': Decimal('14046903'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Pension Expense': {'audited': Decimal('4353678'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'ITF & NSITF': {'audited': Decimal('802855'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Office Repairs': {'audited': Decimal('17832673'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Tools Hire': {'audited': Decimal('2682720'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'General Repairs': {'audited': Decimal('14674650'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Commission & Fees': {'audited': Decimal('1482667'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Insurance': {'audited': Decimal('943980'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Contract Tender Fees': {'audited': Decimal('1397259'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Base Station Repairs': {'audited': Decimal('2009900'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Legal & Professional': {'audited': Decimal('2100000'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'NCC Operating Licence': {'audited': Decimal('3496454'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Audit Fee': {'audited': Decimal('600000'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},
    'Depreciation': {'audited': Decimal('16158678'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '13'},

    # DISTRIBUTION COSTS (Note 14)
    'Transport & Travelling': {'audited': Decimal('25698354'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '14'},
    'Advertising': {'audited': Decimal('11725860'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '14'},
    'Shipping & Delivery': {'audited': Decimal('42157049'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '14'},

    # PERSONNEL COSTS (Note 15)
    'Salaries & Wages': {'audited': Decimal('58344315'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '15'},
    'Staff Bonus': {'audited': Decimal('6289251'), 'type': 'OPEX', 'normal': 'DEBIT', 'note': '15'},

    # FINANCE COSTS
    'Interest Expense': {'audited': Decimal('2912603'), 'type': 'FINANCE', 'normal': 'DEBIT', 'note': '16'},

    # TAX EXPENSE
    'Income Tax Expense': {'audited': Decimal('17800988'), 'type': 'TAX', 'normal': 'DEBIT', 'note': '9'},
    'Education Tax Expense': {'audited': Decimal('1483416'), 'type': 'TAX', 'normal': 'DEBIT', 'note': '9'},
    'IT Levy Expense': {'audited': Decimal('593366'), 'type': 'TAX', 'normal': 'DEBIT', 'note': '9'},
}


def get_gl_balances(conn, year: int) -> dict:
    """Get GL account balances for a specific year."""
    result = conn.execute(text('''
        SELECT
            a.account_name,
            a.account_code,
            ac.ifrs_category,
            ac.category_name,
            COALESCE(SUM(pll.debit_amount), 0) as total_debit,
            COALESCE(SUM(pll.credit_amount), 0) as total_credit,
            COALESCE(SUM(pll.debit_amount - pll.credit_amount), 0) as net_balance
        FROM gl.account a
        JOIN gl.account_category ac ON ac.category_id = a.category_id
        LEFT JOIN gl.posted_ledger_line pll ON pll.account_id = a.account_id
            AND EXTRACT(YEAR FROM pll.posting_date) = :year
        WHERE a.is_active = true
        GROUP BY a.account_id, a.account_name, a.account_code, ac.ifrs_category, ac.category_name
        ORDER BY ac.ifrs_category, a.account_name
    '''), {'year': year})

    accounts = {}
    for row in result:
        accounts[row[0]] = {
            'code': row[1],
            'ifrs': row[2],
            'category': row[3],
            'debit': Decimal(str(row[4])),
            'credit': Decimal(str(row[5])),
            'net': Decimal(str(row[6])),
        }
    return accounts


def generate_variance_report(conn):
    """Generate Report 1: Account-level variance report."""
    print('=' * 130)
    print(' REPORT 1: ACCOUNT-LEVEL VARIANCE REPORT - 2022')
    print(' GL Account Closing Balances vs Audited Note Breakdowns')
    print('=' * 130)

    gl_accounts = get_gl_balances(conn, 2022)
    report_data = []

    # Combine balance sheet and P&L items
    all_audited = {**AUDITED_2022_BALANCES, **AUDITED_2022_PL}

    # Group by category
    categories = [
        ('ASSETS', 'Balance Sheet - Assets'),
        ('LIABILITIES', 'Balance Sheet - Liabilities'),
        ('EQUITY', 'Balance Sheet - Equity'),
        ('REVENUE', 'Income Statement - Revenue'),
        ('COGS', 'Income Statement - Cost of Sales'),
        ('OPEX', 'Income Statement - Operating Expenses'),
        ('FINANCE', 'Income Statement - Finance Costs'),
        ('TAX', 'Income Statement - Tax Expense'),
    ]

    for cat_code, cat_name in categories:
        items = {k: v for k, v in all_audited.items() if v['type'] == cat_code}
        if not items:
            continue

        print(f'\n{"─" * 130}')
        print(f' {cat_name}')
        print(f'{"─" * 130}')
        print(f'{"Account Name":<35} {"Note":>4} {"Audited 2022":>18} {"GL Balance":>18} {"Variance":>18} {"Var %":>8} {"Status":>12}')
        print(f'{"─" * 130}')

        cat_audited = Decimal('0')
        cat_gl = Decimal('0')

        for acc_name, acc_data in sorted(items.items()):
            audited_val = acc_data['audited']
            note = acc_data['note']

            # Find matching GL account (fuzzy match)
            gl_val = Decimal('0')
            for gl_name, gl_data in gl_accounts.items():
                # Simple fuzzy match
                acc_lower = acc_name.lower().replace('&', 'and').replace(' ', '')
                gl_lower = gl_name.lower().replace('&', 'and').replace(' ', '')

                if acc_lower in gl_lower or gl_lower in acc_lower:
                    # Adjust sign based on normal balance
                    if acc_data['normal'] == 'CREDIT':
                        gl_val = gl_data['credit'] - gl_data['debit']
                    else:
                        gl_val = gl_data['debit'] - gl_data['credit']
                    break

            variance = audited_val - gl_val
            var_pct = (variance / audited_val * 100) if audited_val != 0 else 0

            if abs(variance) < 1:
                status = '✅ Match'
            elif gl_val == 0 and audited_val != 0:
                status = '❌ Missing'
            elif abs(var_pct) < 5:
                status = '⚠️ Minor'
            elif abs(var_pct) < 20:
                status = '⚠️ Gap'
            else:
                status = '❌ Major Gap'

            cat_audited += audited_val
            cat_gl += gl_val

            print(f'{acc_name:<35} {note:>4} {float(audited_val):>18,.2f} {float(gl_val):>18,.2f} {float(variance):>18,.2f} {var_pct:>7.1f}% {status:>12}')

            report_data.append({
                'category': cat_name,
                'account_name': acc_name,
                'note': note,
                'audited_2022': float(audited_val),
                'gl_balance': float(gl_val),
                'variance': float(variance),
                'variance_pct': round(var_pct, 2),
                'status': status.replace('✅', '').replace('❌', '').replace('⚠️', '').strip(),
            })

        print(f'{"─" * 130}')
        cat_var = cat_audited - cat_gl
        cat_var_pct = (cat_var / cat_audited * 100) if cat_audited != 0 else 0
        print(f'{cat_name + " TOTAL":<35} {"":>4} {float(cat_audited):>18,.2f} {float(cat_gl):>18,.2f} {float(cat_var):>18,.2f} {cat_var_pct:>7.1f}%')

    # Save CSV
    csv_path = OUTPUT_DIR / 'account_variance_report_2022.csv'
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['category', 'account_name', 'note', 'audited_2022', 'gl_balance', 'variance', 'variance_pct', 'status'])
        writer.writeheader()
        writer.writerows(report_data)

    print(f'\n✅ Variance report saved to: {csv_path}')
    return report_data


def generate_journal_gap_report():
    """Generate Report 2: Journal-level gap report with opening balance and year-end entries."""
    print('\n\n')
    print('=' * 130)
    print(' REPORT 2: JOURNAL-LEVEL GAP REPORT - 2022')
    print(' Opening Balance and Year-End Adjustment Journals')
    print('=' * 130)

    # JOURNAL 1: Opening Balances (2022-01-01)
    print('''
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ JOURNAL 1: OPENING BALANCE ENTRY                                                                                             │
│ Date: 2022-01-01 | Reference: OB-2022-001 | Description: Opening balances from audited 2021 year-end                         │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                              │
│  Line  Account Name                                     Debit               Credit          Note                             │
│  ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════   │''')

    opening_lines = []
    line_num = 1
    total_debit = Decimal('0')
    total_credit = Decimal('0')

    # Assets (Debit)
    for acc, data in AUDITED_2022_BALANCES.items():
        if data['type'] == 'ASSETS' and data.get('opening', Decimal('0')) != 0:
            amt = data['opening']
            opening_lines.append((line_num, acc, amt, Decimal('0'), data['note']))
            total_debit += amt
            print(f'│  {line_num:>3}   {acc:<40} {float(amt):>18,.2f} {float(0):>18,.2f}    Note {data["note"]:<5}                          │')
            line_num += 1

    # Liabilities (Credit)
    for acc, data in AUDITED_2022_BALANCES.items():
        if data['type'] == 'LIABILITIES' and data.get('opening', Decimal('0')) != 0:
            amt = data['opening']
            opening_lines.append((line_num, acc, Decimal('0'), amt, data['note']))
            total_credit += amt
            print(f'│  {line_num:>3}   {acc:<40} {float(0):>18,.2f} {float(amt):>18,.2f}    Note {data["note"]:<5}                          │')
            line_num += 1

    # Equity (Credit)
    for acc, data in AUDITED_2022_BALANCES.items():
        if data['type'] == 'EQUITY' and data.get('opening', Decimal('0')) != 0:
            amt = data['opening']
            opening_lines.append((line_num, acc, Decimal('0'), amt, data['note']))
            total_credit += amt
            print(f'│  {line_num:>3}   {acc:<40} {float(0):>18,.2f} {float(amt):>18,.2f}    Note {data["note"]:<5}                          │')
            line_num += 1

    print(f'''│  ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════   │
│       TOTALS                                       {float(total_debit):>18,.2f} {float(total_credit):>18,.2f}                                       │
│       BALANCE CHECK                                                  {float(total_debit - total_credit):>18,.2f}  {"✅" if total_debit == total_credit else "❌"}                              │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘''')

    # JOURNAL 2: Year-End Retained Earnings Transfer
    profit_2022 = Decimal('23300178')
    print(f'''

┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ JOURNAL 2: YEAR-END CLOSING ENTRY - PROFIT TRANSFER                                                                          │
│ Date: 2022-12-31 | Reference: YE-2022-001 | Description: Transfer 2022 net profit to retained earnings                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                              │
│  Line  Account Name                                     Debit               Credit          Note                             │
│  ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════   │
│    1   Income Summary / P&L Clearing               {float(profit_2022):>18,.2f} {float(0):>18,.2f}    Audited PAT                          │
│    2   Retained Earnings                           {float(0):>18,.2f} {float(profit_2022):>18,.2f}    Note 6                               │
│  ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════   │
│       TOTALS                                       {float(profit_2022):>18,.2f} {float(profit_2022):>18,.2f}                                       │
│       BALANCE CHECK                                                  {float(0):>18,.2f}  ✅                              │
│                                                                                                                              │
│  RETAINED EARNINGS RECONCILIATION:                                                                                           │
│    Opening Balance (2022-01-01)                                          ₦     75,686,098.00                                │
│    Add: Net Profit for 2022                                              ₦     23,300,178.00                                │
│    ─────────────────────────────────────────────────────────────────────────────────────────                                │
│    Closing Balance (2022-12-31)                                          ₦     98,986,276.00  (Matches audited Note 6)      │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘''')

    # JOURNAL 3: Balance Sheet Adjustments (if GL differs from audited closing)
    print(f'''

┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ JOURNAL 3: BALANCE SHEET ADJUSTMENTS (2022-12-31)                                                                            │
│ Purpose: Adjust GL balances to match audited year-end positions                                                              │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                                              │
│  These adjustments bring GL to audited balances (calculate variance = audited - opening - activity)                          │
│                                                                                                                              │
│  Account                                           Opening         Audited Close       Movement Needed                       │
│  ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════   │''')

    for acc, data in AUDITED_2022_BALANCES.items():
        if data['type'] in ['ASSETS', 'LIABILITIES', 'EQUITY']:
            opening = data.get('opening', Decimal('0'))
            closing = data['audited']
            movement = closing - opening
            if movement != 0:
                direction = 'Dr' if (data['normal'] == 'DEBIT' and movement > 0) or (data['normal'] == 'CREDIT' and movement < 0) else 'Cr'
                print(f'│  {acc:<40} {float(opening):>15,.2f} {float(closing):>15,.2f} {float(abs(movement)):>15,.2f} {direction:<5}                 │')

    print('''│  ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════   │
│                                                                                                                              │
│  NOTE: Most balance movements come from normal business activity (invoices, payments, etc.)                                  │
│        Only post adjustments for items not captured through sub-ledgers.                                                     │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘''')

    # Save journal entries to CSV
    csv_path = OUTPUT_DIR / 'journal_gap_entries_2022.csv'
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['journal_ref', 'journal_date', 'description', 'line_num', 'account_name', 'debit', 'credit', 'note'])

        # Opening balance entries
        for line in opening_lines:
            writer.writerow(['OB-2022-001', '2022-01-01', 'Opening balances from audited 2021', line[0], line[1], float(line[2]), float(line[3]), line[4]])

        # Year-end closing
        writer.writerow(['YE-2022-001', '2022-12-31', 'Transfer net profit to retained earnings', 1, 'Income Summary', float(profit_2022), 0, ''])
        writer.writerow(['YE-2022-001', '2022-12-31', 'Transfer net profit to retained earnings', 2, 'Retained Earnings', 0, float(profit_2022), '6'])

    print(f'\n✅ Journal entries saved to: {csv_path}')


def main():
    engine = create_engine(os.getenv('DATABASE_URL'))

    with engine.connect() as conn:
        generate_variance_report(conn)

    generate_journal_gap_report()

    print('\n\n' + '=' * 130)
    print(' SUMMARY')
    print('=' * 130)
    print('''
Files Generated:
  1. account_variance_report_2022.csv - Detailed account-level variances
  2. journal_gap_entries_2022.csv     - Journal entries ready for posting

Next Steps:
  1. Review variance report to identify accounts needing attention
  2. Post opening balance journal (OB-2022-001)
  3. Post year-end closing journal (YE-2022-001)
  4. Run trial balance to verify
''')


if __name__ == '__main__':
    main()
