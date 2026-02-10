"""Seed HR workflow rules and document templates.

Adds ATTENDANCE to the workflow_entity_type PostgreSQL enum, then seeds
7 notification workflow rules and 4 document templates for every existing
organization.  Idempotent: skips rows whose rule_name / template_name
already exist for a given org.

Revision ID: 20260210_seed_hr_workflow_rules
Revises: 20260210_rename_rule_priority_to_sort_order
Create Date: 2026-02-10
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

revision = "20260210_seed_hr_workflow_rules"
down_revision = "20260210_rename_rule_priority_to_sort_order"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Workflow rule definitions (inserted per org)
# ---------------------------------------------------------------------------
WORKFLOW_RULES: list[dict] = [
    {
        "rule_name": "Leave Request — Notify Manager",
        "description": "Notify the reporting manager when an employee submits a leave request.",
        "entity_type": "LEAVE_REQUEST",
        "trigger_event": "ON_STATUS_CHANGE",
        "action_type": "SEND_NOTIFICATION",
        "trigger_conditions": {"status_to": ["SUBMITTED"]},
        "action_config": {
            "recipient_role": "manager",
            "title": "Leave Request Submitted",
            "message": "{{ employee_name }} has submitted a leave request from {{ from_date }} to {{ to_date }}",
            "channel": "BOTH",
        },
        "cooldown_seconds": None,
    },
    {
        "rule_name": "Leave Approved — Notify Employee",
        "description": "Notify the employee when their leave request is approved.",
        "entity_type": "LEAVE_REQUEST",
        "trigger_event": "ON_APPROVAL",
        "action_type": "SEND_NOTIFICATION",
        "trigger_conditions": {},
        "action_config": {
            "recipient": "entity_owner",
            "title": "Leave Approved",
            "message": "Your leave from {{ from_date }} to {{ to_date }} has been approved",
            "channel": "BOTH",
        },
        "cooldown_seconds": None,
    },
    {
        "rule_name": "Leave Rejected — Notify Employee",
        "description": "Notify the employee when their leave request is rejected.",
        "entity_type": "LEAVE_REQUEST",
        "trigger_event": "ON_REJECTION",
        "action_type": "SEND_NOTIFICATION",
        "trigger_conditions": {},
        "action_config": {
            "recipient": "entity_owner",
            "title": "Leave Rejected",
            "message": "Your leave request has been rejected. Reason: {{ rejection_reason }}",
            "channel": "BOTH",
        },
        "cooldown_seconds": None,
    },
    {
        "rule_name": "Absence Alert — Notify Employee",
        "description": "Email the employee when they are marked absent.",
        "entity_type": "ATTENDANCE",
        "trigger_event": "ON_STATUS_CHANGE",
        "action_type": "SEND_EMAIL",
        "trigger_conditions": {"status_to": ["ABSENT"]},
        "action_config": {
            "recipient": "entity_owner",
            "template_type": "EMAIL_NOTIFICATION",
            "subject": "Absence Notification",
            "message": (
                "You were marked absent on {{ attendance_date }}. "
                "If this is incorrect, please submit an attendance "
                "regularization request."
            ),
        },
        "cooldown_seconds": 86400,
    },
    {
        "rule_name": "Disciplinary Query Issued — Notify Employee",
        "description": "Email the employee when a disciplinary query is issued.",
        "entity_type": "DISCIPLINARY_CASE",
        "trigger_event": "ON_STATUS_CHANGE",
        "action_type": "SEND_EMAIL",
        "trigger_conditions": {"status_to": ["QUERY_ISSUED"]},
        "action_config": {
            "recipient": "entity_owner",
            "template_type": "EMAIL_NOTIFICATION",
            "subject": "Disciplinary Query",
            "message": (
                "A disciplinary query has been issued regarding case "
                "{{ case_number }}. Please respond within 24 hours."
            ),
        },
        "cooldown_seconds": None,
    },
    {
        "rule_name": "Salary Slip Posted — Notify Employee",
        "description": "Notify the employee when their salary slip is posted.",
        "entity_type": "SALARY_SLIP",
        "trigger_event": "ON_STATUS_CHANGE",
        "action_type": "SEND_NOTIFICATION",
        "trigger_conditions": {"status_to": ["POSTED"]},
        "action_config": {
            "recipient": "entity_owner",
            "title": "Payslip Available",
            "message": "Your salary slip for {{ start_date }} to {{ end_date }} is now available",
            "channel": "BOTH",
        },
        "cooldown_seconds": None,
    },
    {
        "rule_name": "Payroll Submitted — Notify Finance",
        "description": "Notify the finance manager when a payroll run is submitted for review.",
        "entity_type": "PAYROLL_RUN",
        "trigger_event": "ON_STATUS_CHANGE",
        "action_type": "SEND_NOTIFICATION",
        "trigger_conditions": {"status_to": ["SUBMITTED"]},
        "action_config": {
            "recipient_role": "finance_manager",
            "title": "Payroll Submitted for Review",
            "message": "Payroll for {{ entry_name }} has been submitted for verification",
            "channel": "BOTH",
        },
        "cooldown_seconds": None,
    },
]

# ---------------------------------------------------------------------------
# Document template definitions (inserted per org)
# ---------------------------------------------------------------------------

_OFFER_LETTER_CONTENT = """\
<div style="font-family: 'DM Sans', Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 40px;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h2 style="margin: 0; color: #0f172a;">{{ organization_name }}</h2>
    <p style="margin: 4px 0 0; color: #64748b; font-size: 13px;">OFFER OF EMPLOYMENT</p>
  </div>

  <p style="color: #334155;">{{ date }}</p>

  <p style="color: #334155;">Dear <strong>{{ employee_name }}</strong>,</p>

  <p style="color: #334155;">
    We are pleased to offer you the position of <strong>{{ position }}</strong>
    in the <strong>{{ department }}</strong> department at {{ organization_name }}.
  </p>

  <table style="width: 100%; border-collapse: collapse; margin: 24px 0;">
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b; width: 40%;">Position</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ position }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Department</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ department }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Start Date</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ start_date }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Reporting To</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ reporting_manager }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Compensation</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ compensation }}</td>
    </tr>
  </table>

  <p style="color: #334155;">
    This offer is contingent upon satisfactory completion of any pre-employment
    requirements. Please confirm your acceptance by signing and returning this
    letter by <strong>{{ acceptance_deadline }}</strong>.
  </p>

  <p style="color: #334155;">We look forward to welcoming you to the team.</p>

  <div style="margin-top: 48px;">
    <p style="color: #334155; margin-bottom: 4px;">Yours sincerely,</p>
    <p style="color: #334155; font-weight: 600;">{{ authorized_signatory }}</p>
    <p style="color: #64748b; font-size: 13px;">{{ signatory_title }}</p>
  </div>
</div>
"""

_QUERY_LETTER_CONTENT = """\
<div style="font-family: 'DM Sans', Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 40px;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h2 style="margin: 0; color: #0f172a;">{{ organization_name }}</h2>
    <p style="margin: 4px 0 0; color: #64748b; font-size: 13px;">SHOW CAUSE NOTICE</p>
  </div>

  <p style="color: #334155;">Date: {{ date }}</p>
  <p style="color: #334155;">Ref: {{ case_number }}</p>

  <p style="color: #334155;">Dear <strong>{{ employee_name }}</strong>,</p>

  <p style="color: #334155;">
    This letter serves as a formal show cause notice regarding the following:
  </p>

  <div style="background: #fef2f2; border-left: 4px solid #e11d48; padding: 16px; margin: 16px 0; border-radius: 4px;">
    <p style="color: #334155; margin: 0;"><strong>Violation:</strong> {{ violation_description }}</p>
    <p style="color: #334155; margin: 8px 0 0;"><strong>Date of Incident:</strong> {{ incident_date }}</p>
  </div>

  <p style="color: #334155;">
    You are required to provide a written explanation within
    <strong>{{ response_deadline }}</strong> as to why disciplinary action
    should not be taken against you.
  </p>

  <p style="color: #334155;">
    Your response should be addressed to the undersigned. Failure to respond
    within the stipulated time may result in a decision being taken based on
    the available information.
  </p>

  <div style="margin-top: 48px;">
    <p style="color: #334155; margin-bottom: 4px;">Issued by,</p>
    <p style="color: #334155; font-weight: 600;">{{ issued_by }}</p>
    <p style="color: #64748b; font-size: 13px;">{{ issuer_title }}</p>
  </div>
</div>
"""

_ABSENCE_NOTIFICATION_CONTENT = """\
<div style="font-family: 'DM Sans', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 32px;">
  <h3 style="color: #0f172a; margin-top: 0;">Absence Notification</h3>

  <p style="color: #334155;">Dear <strong>{{ employee_name }}</strong>,</p>

  <p style="color: #334155;">
    Our records indicate that you were marked <strong>absent</strong> on
    <strong>{{ attendance_date }}</strong>.
  </p>

  <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b; width: 40%;">Date</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ attendance_date }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Expected Check-in</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ expected_check_in }}</td>
    </tr>
  </table>

  <p style="color: #334155;">
    If this is incorrect, please submit an attendance regularization request
    through the self-service portal or contact your supervisor.
  </p>

  <p style="color: #64748b; font-size: 13px;">
    This is an automated notification from {{ organization_name }}.
  </p>
</div>
"""

_LEAVE_APPROVAL_CONTENT = """\
<div style="font-family: 'DM Sans', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 32px;">
  <h3 style="color: #0f172a; margin-top: 0;">Leave Approval Confirmation</h3>

  <p style="color: #334155;">Dear <strong>{{ employee_name }}</strong>,</p>

  <p style="color: #334155;">
    Your leave request has been <strong style="color: #059669;">approved</strong>.
    Below are the details:
  </p>

  <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b; width: 40%;">Leave Type</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ leave_type }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">From</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ from_date }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">To</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ to_date }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Total Days</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ total_days }}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #64748b;">Return Date</td>
      <td style="padding: 8px 12px; border: 1px solid #e2e8f0; color: #0f172a;">{{ return_date }}</td>
    </tr>
  </table>

  <p style="color: #334155;">
    Please ensure all pending tasks are handed over before your leave begins.
  </p>

  <p style="color: #64748b; font-size: 13px;">
    This is an automated notification from {{ organization_name }}.
  </p>
</div>
"""

DOCUMENT_TEMPLATES: list[dict] = [
    {
        "template_type": "OFFER_LETTER",
        "template_name": "Default Offer Letter",
        "description": "Standard offer of employment letter with position, compensation, and start date details.",
        "template_content": _OFFER_LETTER_CONTENT,
        "email_subject": "Offer of Employment — {{ position }} at {{ organization_name }}",
        "is_default": True,
    },
    {
        "template_type": "SHOW_CAUSE_NOTICE",
        "template_name": "Default Query Letter",
        "description": "Formal show cause notice for disciplinary proceedings.",
        "template_content": _QUERY_LETTER_CONTENT,
        "email_subject": "Show Cause Notice — {{ case_number }}",
        "is_default": True,
    },
    {
        "template_type": "EMAIL_NOTIFICATION",
        "template_name": "Absence Notification",
        "description": "Automated email sent to employees marked absent.",
        "template_content": _ABSENCE_NOTIFICATION_CONTENT,
        "email_subject": "Absence Notification — {{ attendance_date }}",
        "is_default": False,
    },
    {
        "template_type": "EMAIL_NOTIFICATION",
        "template_name": "Leave Approval Letter",
        "description": "Confirmation email sent when a leave request is approved.",
        "template_content": _LEAVE_APPROVAL_CONTENT,
        "email_subject": "Leave Approved — {{ from_date }} to {{ to_date }}",
        "is_default": False,
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_rules_for_org(conn: sa.Connection, org_id: str, admin_id: str) -> int:
    """Insert workflow rules for one organisation. Returns count inserted."""
    inserted = 0
    for rule in WORKFLOW_RULES:
        # Idempotency: skip if rule_name already exists for this org
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM automation.workflow_rule "
                "WHERE organization_id = :org_id AND rule_name = :name LIMIT 1"
            ),
            {"org_id": org_id, "name": rule["rule_name"]},
        ).fetchone()
        if exists:
            continue

        conn.execute(
            sa.text("""
                INSERT INTO automation.workflow_rule (
                    organization_id, rule_name, description,
                    entity_type, trigger_event, trigger_conditions,
                    action_type, action_config,
                    priority, execute_async, is_active,
                    cooldown_seconds, created_by
                ) VALUES (
                    :org_id, :rule_name, :description,
                    :entity_type, :trigger_event, CAST(:trigger_conditions AS jsonb),
                    :action_type, CAST(:action_config AS jsonb),
                    100, true, true,
                    :cooldown_seconds, :admin_id
                )
            """),
            {
                "org_id": org_id,
                "rule_name": rule["rule_name"],
                "description": rule["description"],
                "entity_type": rule["entity_type"],
                "trigger_event": rule["trigger_event"],
                "trigger_conditions": json.dumps(rule["trigger_conditions"]),
                "action_type": rule["action_type"],
                "action_config": json.dumps(rule["action_config"]),
                "cooldown_seconds": rule["cooldown_seconds"],
                "admin_id": admin_id,
            },
        )
        inserted += 1
    return inserted


def _seed_templates_for_org(conn: sa.Connection, org_id: str, admin_id: str) -> int:
    """Insert document templates for one organisation. Returns count inserted."""
    inserted = 0
    for tmpl in DOCUMENT_TEMPLATES:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM automation.document_template "
                "WHERE organization_id = :org_id "
                "  AND template_type = :ttype "
                "  AND template_name = :tname "
                "LIMIT 1"
            ),
            {
                "org_id": org_id,
                "ttype": tmpl["template_type"],
                "tname": tmpl["template_name"],
            },
        ).fetchone()
        if exists:
            continue

        conn.execute(
            sa.text("""
                INSERT INTO automation.document_template (
                    organization_id, template_type, template_name,
                    description, template_content,
                    email_subject, is_default, is_active,
                    created_by
                ) VALUES (
                    :org_id, :ttype, :tname,
                    :description, :content,
                    :email_subject, :is_default, true,
                    :admin_id
                )
            """),
            {
                "org_id": org_id,
                "ttype": tmpl["template_type"],
                "tname": tmpl["template_name"],
                "description": tmpl["description"],
                "content": tmpl["template_content"],
                "email_subject": tmpl["email_subject"],
                "is_default": tmpl["is_default"],
                "admin_id": admin_id,
            },
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# upgrade / downgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Extend PG enum with ATTENDANCE (idempotent — check first)
    row = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_enum "
            "WHERE enumtypid = 'workflow_entity_type'::regtype "
            "  AND enumlabel = 'ATTENDANCE'"
        )
    ).fetchone()
    if not row:
        conn.execute(
            sa.text(
                "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'ATTENDANCE'"
            )
        )
        # COMMIT is required before the new enum value can be used in DML.
        # Alembic runs in a transaction; we must commit to make the new value
        # visible, then the rest of the migration continues in a new txn.
        conn.execute(sa.text("COMMIT"))

    # 2. Find a valid person_id to use as created_by (no FK, just needs a UUID)
    admin_row = conn.execute(
        sa.text("SELECT person_id FROM public.user_credentials LIMIT 1")
    ).fetchone()
    if not admin_row:
        # No users exist — nothing to seed
        return
    admin_id = str(admin_row[0])

    # 3. Seed rules + templates for every org
    orgs = conn.execute(
        sa.text("SELECT organization_id FROM core_org.organization")
    ).fetchall()

    for (org_id,) in orgs:
        _seed_rules_for_org(conn, str(org_id), admin_id)
        _seed_templates_for_org(conn, str(org_id), admin_id)


def downgrade() -> None:
    conn = op.get_bind()

    # Remove seeded rules by name
    rule_names = [r["rule_name"] for r in WORKFLOW_RULES]
    for name in rule_names:
        conn.execute(
            sa.text("DELETE FROM automation.workflow_rule WHERE rule_name = :name"),
            {"name": name},
        )

    # Remove seeded templates by name
    for tmpl in DOCUMENT_TEMPLATES:
        conn.execute(
            sa.text(
                "DELETE FROM automation.document_template "
                "WHERE template_type = :ttype AND template_name = :tname"
            ),
            {"ttype": tmpl["template_type"], "tname": tmpl["template_name"]},
        )

    # Note: PG does not support removing enum values — ATTENDANCE remains.
