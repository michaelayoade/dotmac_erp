from pathlib import Path


def test_reviewer_approvers_page_only_exposes_approver_filter():
    html = Path("templates/expense/limits/reviewer_approvers.html").read_text(
        encoding="utf-8"
    )

    assert 'name="q"' in html
    assert 'name="from_date"' not in html
    assert 'name="to_date"' not in html
