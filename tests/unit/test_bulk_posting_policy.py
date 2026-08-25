"""The bulk-posting kill switch, and the two things it must not be mistaken for.

The rule had one implementation in `app.tasks.gl_posting` and a copy in
`scripts/post_stranded_bank_fees.py`. Two copies of a safety rule are one rule
and one future divergence, so both now delegate to
`app.services.finance.gl.bulk_posting_policy` — and this file tests the owner
rather than either caller.
"""

from __future__ import annotations

import pathlib

import pytest

from app.services.finance.gl.bulk_posting_policy import (
    BULK_POSTING_ENV_FLAG,
    BulkPostingDisabled,
    bulk_posting_enabled,
    require_bulk_posting_allowed,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestTheGate:
    def test_a_dry_run_is_always_allowed(self) -> None:
        require_bulk_posting_allowed("caller", dry_run=True, env={})

    def test_a_live_run_is_refused_by_default(self) -> None:
        with pytest.raises(BulkPostingDisabled) as excinfo:
            require_bulk_posting_allowed("caller", dry_run=False, env={})
        message = str(excinfo.value)
        assert BULK_POSTING_ENV_FLAG in message
        assert "caller" in message

    def test_a_live_run_is_allowed_once_the_switch_is_set(self) -> None:
        require_bulk_posting_allowed(
            "caller", dry_run=False, env={BULK_POSTING_ENV_FLAG: "true"}
        )

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
    def test_truthy_spellings(self, value: str) -> None:
        assert bulk_posting_enabled({BULK_POSTING_ENV_FLAG: value})

    @pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe", "TRUE-ish"])
    def test_everything_else_is_off(self, value: str) -> None:
        assert not bulk_posting_enabled({BULK_POSTING_ENV_FLAG: value})

    def test_it_raises_rather_than_downgrading_to_a_dry_run(self) -> None:
        """Silently turning a live run into a no-op would leave an operator
        believing the work was done — the same class of mistake as a caller that
        cannot tell "I posted" from "someone already had"."""
        with pytest.raises(BulkPostingDisabled):
            require_bulk_posting_allowed("caller", dry_run=False, env={})


class TestItIsNotMistakenForAuthorization:
    def test_the_refusal_says_the_flag_is_not_finance_authorization(self) -> None:
        """An operator reading only the error must not conclude that setting the
        flag makes a bulk post correct."""
        with pytest.raises(BulkPostingDisabled) as excinfo:
            require_bulk_posting_allowed("caller", dry_run=False, env={})
        message = str(excinfo.value).lower()
        assert "not finance authorization" in message
        assert "kill switch" in message

    def test_the_module_states_it_too(self) -> None:
        import app.services.finance.gl.bulk_posting_policy as policy

        assert policy.__doc__ is not None
        doc = policy.__doc__.lower()
        assert "not finance authorization" in doc


class TestThereIsExactlyOneImplementation:
    """A safety rule with two copies has one rule and one future divergence."""

    CALLERS = (
        "app/tasks/gl_posting.py",
        "scripts/post_stranded_bank_fees.py",
    )

    def test_every_live_path_delegates_to_the_owner(self) -> None:
        for relative in self.CALLERS:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            assert "require_bulk_posting_allowed" in source, relative

    def test_no_caller_re_implements_the_check(self) -> None:
        for relative in self.CALLERS:
            source = (REPO_ROOT / relative).read_text(encoding="utf-8")
            body = source.replace(
                "from app.services.finance.gl.bulk_posting_policy", ""
            )
            assert f'getenv("{BULK_POSTING_ENV_FLAG}"' not in body, relative
            assert f"getenv({BULK_POSTING_ENV_FLAG}" not in body, relative
            assert f"{BULK_POSTING_ENV_FLAG} =" not in body, relative
