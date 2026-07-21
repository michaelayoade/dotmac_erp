from pathlib import Path


def test_extended_profile_templates_include_csrf_and_no_employee_id_field():
    for name in [
        "qualifications.html",
        "certifications.html",
        "skills.html",
        "dependents.html",
    ]:
        template = Path(f"templates/people/self/{name}").read_text(encoding="utf-8")
        assert "request.state.csrf_form | safe" in template
        assert 'name="employee_id"' not in template
        assert "employee_id" not in template.split("<form", 1)[1]


def test_extended_profile_templates_do_not_offer_delete_actions():
    for name in [
        "qualifications.html",
        "certifications.html",
        "skills.html",
        "dependents.html",
    ]:
        template = Path(f"templates/people/self/{name}").read_text(encoding="utf-8")
        assert "Delete" not in template
        assert "/delete" not in template
