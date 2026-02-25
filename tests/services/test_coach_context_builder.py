"""Tests for ContextBuilder: anonymization codes, prompt building, cache keys."""

from __future__ import annotations

import json

from app.services.coach.context_builder import AnonymizationMap, ContextBuilder


class TestContextBuilderAnonymization:
    def test_employee_codes_sequential(self) -> None:
        cb = ContextBuilder()
        code1 = cb.code_employee("emp-aaa")
        code2 = cb.code_employee("emp-bbb")
        assert code1 == "EMP-001"
        assert code2 == "EMP-002"

    def test_same_employee_returns_same_code(self) -> None:
        cb = ContextBuilder()
        code1 = cb.code_employee("emp-aaa")
        code2 = cb.code_employee("emp-aaa")
        assert code1 == code2 == "EMP-001"

    def test_customer_codes_sequential(self) -> None:
        cb = ContextBuilder()
        assert cb.code_customer("cust-x") == "CUST-001"
        assert cb.code_customer("cust-y") == "CUST-002"

    def test_supplier_codes_sequential(self) -> None:
        cb = ContextBuilder()
        assert cb.code_supplier("sup-1") == "SUP-001"
        assert cb.code_supplier("sup-2") == "SUP-002"

    def test_different_entity_types_independent_counters(self) -> None:
        cb = ContextBuilder()
        assert cb.code_employee("e1") == "EMP-001"
        assert cb.code_customer("c1") == "CUST-001"
        assert cb.code_supplier("s1") == "SUP-001"
        # All are -001, counters don't interfere
        assert cb.code_employee("e2") == "EMP-002"
        assert cb.code_customer("c2") == "CUST-002"


class TestContextBuilderBuild:
    def test_build_returns_prompt_bundle(self) -> None:
        cb = ContextBuilder()
        cb.code_employee("emp-abc")
        bundle = cb.build(
            system_prompt="You are an analyst.",
            template_name="cash_flow",
            context={"revenue": 100_000, "expenses": 80_000},
        )
        assert bundle.system_prompt == "You are an analyst."
        assert "cash_flow" in bundle.user_prompt
        assert "100000" in bundle.user_prompt
        assert bundle.cache_key  # Non-empty hash

    def test_build_user_prompt_is_valid_json(self) -> None:
        cb = ContextBuilder()
        bundle = cb.build(
            system_prompt="sys",
            template_name="test",
            context={"key": "value"},
        )
        parsed = json.loads(bundle.user_prompt)
        assert parsed["template"] == "test"
        assert parsed["context"]["key"] == "value"

    def test_anonymization_map_populated(self) -> None:
        cb = ContextBuilder()
        cb.code_employee("emp-1")
        cb.code_customer("cust-1")
        cb.code_supplier("sup-1")
        bundle = cb.build(
            system_prompt="sys",
            template_name="test",
            context={},
        )
        anon = bundle.anonymization_map
        assert isinstance(anon, AnonymizationMap)
        assert anon.employee_codes == {"EMP-001": "emp-1"}
        assert anon.customer_codes == {"CUST-001": "cust-1"}
        assert anon.supplier_codes == {"SUP-001": "sup-1"}

    def test_cache_key_deterministic(self) -> None:
        cb1 = ContextBuilder()
        cb2 = ContextBuilder()
        b1 = cb1.build(system_prompt="sys", template_name="t", context={"a": 1})
        b2 = cb2.build(system_prompt="sys", template_name="t", context={"a": 1})
        assert b1.cache_key == b2.cache_key

    def test_cache_key_differs_for_different_prompts(self) -> None:
        cb = ContextBuilder()
        b1 = cb.build(system_prompt="sys A", template_name="t", context={})
        b2 = cb.build(system_prompt="sys B", template_name="t", context={})
        assert b1.cache_key != b2.cache_key
