from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_new_self_service_expense_item_template_has_blank_required_category_option():
    template = (REPO_ROOT / "templates/people/self/expenses.html").read_text()

    # Robust against attribute reordering — assert on the structural pieces only.
    assert 'name="category_id___KEY__"' in template
    assert 'class="form-select w-full" required data-item-category' in template
    assert '<option value="" selected disabled>Select category...</option>' in template


def test_edit_self_service_expense_item_template_has_blank_required_category_option():
    template = (REPO_ROOT / "templates/people/self/expense_claim_edit.html").read_text()

    # Robust against attribute reordering — assert on the structural pieces only.
    assert 'name="category_id___KEY__"' in template
    assert 'class="form-select w-full" required' in template
    assert '<option value="" selected disabled>Select category...</option>' in template
