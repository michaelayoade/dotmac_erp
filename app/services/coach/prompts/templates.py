"""
Prompt templates for coach analyzers and report generation.

Each domain has a system prompt + user prompt template.
Deterministic analyzers work without LLM — these templates are used
for optional LLM-powered narration and weekly report generation.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# System prompts (role definition)
# ---------------------------------------------------------------------------

COACH_SYSTEM_PROMPT = """
You are DotMac Coach, an AI business advisor for African SMEs.
You analyze business metrics and provide concise, evidence-based coaching.
You must return ONLY valid JSON matching the requested schema.
Never fabricate data. Only reference metrics provided in the context.
Use plain language appropriate for a busy business owner or department head.
""".strip()

REPORT_SYSTEM_PROMPT = """
You are DotMac Coach generating a weekly digest report.
Summarize the week's key metrics and provide 3-5 actionable recommendations.
Write in a professional but accessible tone. Use bullet points.
Return valid JSON matching the requested schema.
""".strip()

# ---------------------------------------------------------------------------
# Finance domain
# ---------------------------------------------------------------------------

CASH_FLOW_USER_PROMPT = """
Analyze the following cash-flow metrics for the organization:

- DSO (Days Sales Outstanding): {dso} days
- DPO (Days Payable Outstanding): {dpo} days
- Cash Conversion Cycle: {ccc} days
- AR Outstanding: {currency} {ar_outstanding}
- AP Outstanding: {currency} {ap_outstanding}
- Revenue (90d): {currency} {revenue_90d}
- COGS/Purchases (90d): {currency} {cogs_90d}
- 30-day Net Cash Forecast: {currency} {net_30d_forecast}

Provide:
1. A 2-sentence executive summary
2. The single most impactful coaching action
3. Risk level: LOW, MEDIUM, or HIGH
""".strip()

AR_OVERDUE_USER_PROMPT = """
Analyze these overdue receivables:

- Overdue invoices: {overdue_count}
- Total overdue balance: {currency} {overdue_balance}
- Oldest overdue: {max_days_overdue} days
- Top overdue customers: {top_customers}

Provide:
1. A 2-sentence summary of the collection risk
2. Prioritized collection actions (top 3)
3. Risk level: LOW, MEDIUM, or HIGH
""".strip()

AP_DUE_USER_PROMPT = """
Analyze these upcoming/overdue payables:

- Due within 7 days: {due_7d_count} invoices ({currency} {due_7d_amount})
- Overdue: {overdue_count} invoices ({currency} {overdue_amount})
- Top suppliers by amount due: {top_suppliers}

Provide:
1. A 2-sentence summary of the payment obligations
2. Payment prioritization advice
3. Cash impact assessment
""".strip()

# ---------------------------------------------------------------------------
# Compliance domain
# ---------------------------------------------------------------------------

COMPLIANCE_USER_PROMPT = """
Analyze the fiscal period compliance posture:

- Open fiscal periods: {open_count}
- Overdue-to-close periods: {overdue_count}
- Oldest overdue period: {oldest_period} ({days_overdue} days overdue)

Provide:
1. A 2-sentence compliance risk summary
2. Recommended close sequence
3. Risk level: LOW, MEDIUM, or HIGH
""".strip()

# ---------------------------------------------------------------------------
# Workforce domain
# ---------------------------------------------------------------------------

WORKFORCE_USER_PROMPT = """
Analyze these workforce health indicators:

- Active headcount: {headcount}
- 90-day departures: {departures} (annualized turnover: {turnover_pct}%)
- Departments without head: {depts_no_head}
- Employees without manager: {no_manager}
- Pending leave requests: {pending_leave}
- Average leave approval time: {avg_approval_days} days

Provide:
1. A 2-sentence workforce health summary
2. Top 3 HR priorities
3. Retention risk level: LOW, MEDIUM, or HIGH
""".strip()

# ---------------------------------------------------------------------------
# Supply chain domain
# ---------------------------------------------------------------------------

SUPPLY_CHAIN_USER_PROMPT = """
Analyze these supply chain indicators:

- Items at zero stock: {zero_stock}
- Items below reorder point: {below_reorder}
- Total tracked items: {total_tracked}
- Dead stock items (90+ days no movement): {dead_stock_count}
- Dead stock value: {currency} {dead_stock_value}

Provide:
1. A 2-sentence supply chain health summary
2. Immediate procurement actions needed
3. Inventory optimization recommendations
""".strip()

# ---------------------------------------------------------------------------
# Revenue domain
# ---------------------------------------------------------------------------

REVENUE_USER_PROMPT = """
Analyze these revenue and pipeline indicators:

- Open quotes: {open_quotes} ({currency} {open_quote_value})
- Expired quotes: {expired_quotes}
- Quote conversion rate: {conversion_rate}%
- Open sales orders: {open_sos} ({currency} {open_so_value})
- Top customer concentration: {top_customer_pct}% ({top_customer_name})
- Top 3 customer concentration: {top_3_pct}%
- Active customers: {active_customers}

Provide:
1. A 2-sentence pipeline health summary
2. Revenue diversification advice
3. Pipeline acceleration actions (top 3)
""".strip()

# ---------------------------------------------------------------------------
# Weekly report prompts
# ---------------------------------------------------------------------------

WEEKLY_FINANCE_REPORT_PROMPT = """
Generate a weekly finance digest based on these metrics:

Cash Flow:
{cash_flow_section}

Receivables:
{ar_section}

Payables:
{ap_section}

Compliance:
{compliance_section}

Revenue Pipeline:
{revenue_section}

Active insights this week: {insight_count}
Top severity: {top_severity}

Provide a JSON response with:
- executive_summary: 3-4 sentence overview
- key_metrics: list of {{metric, value, trend}} objects
- recommendations: list of 3-5 actionable items
- risk_areas: list of areas needing attention
""".strip()

WEEKLY_HR_REPORT_PROMPT = """
Generate a weekly HR digest based on these metrics:

Workforce:
{workforce_section}

Leave & Attendance:
{leave_section}

Data Quality:
{data_quality_section}

Active insights this week: {insight_count}

Provide a JSON response with:
- executive_summary: 3-4 sentence overview
- key_metrics: list of {{metric, value, trend}} objects
- recommendations: list of 3-5 actionable items
- risk_areas: list of areas needing attention
""".strip()
