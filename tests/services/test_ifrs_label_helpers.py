from app.models.finance.gl.account_category import IFRSCategory
from app.services.finance.gl.web.base import ifrs_label
from app.services.finance.rpt.common import _ifrs_label


def test_gl_ifrs_label_accepts_string_value() -> None:
    assert ifrs_label("ASSETS") == "ASSET"


def test_gl_ifrs_label_accepts_unknown_string() -> None:
    assert ifrs_label("CUSTOM") == "CUSTOM"


def test_reports_ifrs_label_accepts_string_value() -> None:
    assert _ifrs_label("EXPENSES") == "Expenses"


def test_reports_ifrs_label_accepts_enum_value() -> None:
    assert _ifrs_label(IFRSCategory.REVENUE) == "Revenue"
