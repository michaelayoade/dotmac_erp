"""Finance expense form coverage for imported operational context."""

from pathlib import Path


def test_finance_expense_form_posts_project_ticket_and_task() -> None:
    template = Path("templates/expense/form.html").read_text(encoding="utf-8")

    assert 'name="project_id"' in template
    assert 'name="ticket_id"' in template
    assert 'name="task_id"' in template
    assert 'data-project-id="{{ task.project_id }}"' in template
