from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnonymizationMap:
    """
    Ephemeral code-to-identifier mapping.

    This must not be persisted to the DB or logs.
    """

    employee_codes: dict[str, str]
    customer_codes: dict[str, str]
    supplier_codes: dict[str, str]


@dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str
    anonymization_map: AnonymizationMap
    cache_key: str


class ContextBuilder:
    """
    Builds structured prompts and anonymizes identifiers.

    Phase 1: minimal scaffolding. Callers provide already-aggregated context.
    """

    def __init__(self) -> None:
        self._employee_codes: dict[str, str] = {}
        self._customer_codes: dict[str, str] = {}
        self._supplier_codes: dict[str, str] = {}

    def _code(self, prefix: str, i: int) -> str:
        return f"{prefix}-{i:03d}"

    def code_employee(self, employee_id: str) -> str:
        for code, eid in self._employee_codes.items():
            if eid == employee_id:
                return code
        code = self._code("EMP", len(self._employee_codes) + 1)
        self._employee_codes[code] = employee_id
        return code

    def code_customer(self, customer_id: str) -> str:
        for code, cid in self._customer_codes.items():
            if cid == customer_id:
                return code
        code = self._code("CUST", len(self._customer_codes) + 1)
        self._customer_codes[code] = customer_id
        return code

    def code_supplier(self, supplier_id: str) -> str:
        for code, sid in self._supplier_codes.items():
            if sid == supplier_id:
                return code
        code = self._code("SUP", len(self._supplier_codes) + 1)
        self._supplier_codes[code] = supplier_id
        return code

    def build(
        self,
        *,
        system_prompt: str,
        template_name: str,
        context: dict[str, Any],
    ) -> PromptBundle:
        # In Phase 1, this is a simple JSON blob. Later we can add rich templating.
        user_prompt = json.dumps(
            {"template": template_name, "context": context},
            ensure_ascii=True,
            separators=(",", ":"),
            default=str,
        )
        anon = AnonymizationMap(
            employee_codes=dict(self._employee_codes),
            customer_codes=dict(self._customer_codes),
            supplier_codes=dict(self._supplier_codes),
        )
        cache_key = hashlib.sha256(
            (system_prompt.strip() + "\n" + user_prompt).encode("utf-8")
        ).hexdigest()
        return PromptBundle(
            system_prompt=system_prompt.strip(),
            user_prompt=user_prompt,
            anonymization_map=anon,
            cache_key=cache_key,
        )
