from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "erp.application.lifecycle.v1.json"
COMPOSITION = ROOT / "contracts" / "erp.identity-user-application-lifecycle.v1.json"
SCHEMAS = ROOT / "contracts" / "schemas" / "erp.application.lifecycle.v1"


def test_contract_is_product_owned_and_namespaced() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert document["schema"] == "dotmac.capability-contract/v1"
    assert document["owner_code"] == "dotmac-erp"
    assert document["capability_code"] == "erp.application.lifecycle"
    assert document["schema_version"] == 1
    assert [item["operation_code"] for item in document["operations"]] == [
        "apply",
        "cancel",
        "observe",
        "plan",
    ]


def test_contract_schemas_are_strict_and_forbid_identity_authorization_fields() -> None:
    forbidden = {
        "email",
        "name",
        "roles",
        "groups",
        "scopes",
        "claims",
        "password",
        "employment",
        "enrolment",
    }
    for path in sorted(SCHEMAS.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
        assert '"additionalProperties":false' in text
        assert forbidden.isdisjoint(_property_names(document)), path


def test_capability_inputs_contain_only_desired_values_not_http_envelope_pins() -> None:
    forbidden = {
        "expected_state_digest",
        "idempotency_key",
        "operation_ref",
        "plan_digest",
        "target",
        "target_digest",
    }
    for filename in ("apply-input.json", "plan-input.json", "reference-input.json"):
        document = json.loads((SCHEMAS / filename).read_text(encoding="utf-8"))
        assert forbidden.isdisjoint(document["properties"]), filename
        assert set(document["properties"]) == {
            "desired_state",
            "external_subject",
            "organization_id",
            "person_id",
        }


def test_capability_outputs_are_public_evidence_not_http_result_envelopes() -> None:
    forbidden = {
        "expected_state_digest",
        "failure_code",
        "idempotency_key",
        "operation_ref",
        "operation_state",
        "outcome",
        "plan_digest",
        "result_state_digest",
        "target_digest",
    }
    for path in sorted(SCHEMAS.glob("*-output.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert forbidden.isdisjoint(document["properties"]), path.name
        assert document["properties"]
        assert all(
            shape.get("x-dotmac-data-classification") == "public_non_secret"
            for shape in document["properties"].values()
        ), path.name


def test_product_composition_maps_only_verified_identity_receipt_values() -> None:
    from dotmac_kernel import (
        CapabilityCompositionSnapshot,
        CapabilityContractSnapshot,
        CapabilitySchemaDocument,
        ProductManifestSnapshot,
    )
    from dotmac_managed_identity_contracts import (
        CAPABILITY_SCHEMAS as IDENTITY_SCHEMAS,
    )
    from dotmac_managed_identity_contracts import USER_LIFECYCLE

    composition = CapabilityCompositionSnapshot.from_json_bytes(
        COMPOSITION.read_bytes()
    )
    target_contract = CapabilityContractSnapshot.from_json_bytes(CONTRACT.read_bytes())
    target_schema = CapabilitySchemaDocument.from_json_bytes(
        (SCHEMAS / "apply-input.json").read_bytes(),
        expected_ref=target_contract.require_operation("apply").input_schema_ref,
    )
    composition.require_owned_by(
        ProductManifestSnapshot(
            product_code="dotmac-erp",
            product_version="1.32.0",
            capability_codes=("erp.application.lifecycle.v1",),
        )
    )
    composition.require_compatible_with(
        contracts=(USER_LIFECYCLE, target_contract),
        schemas=(*IDENTITY_SCHEMAS, target_schema),
    )
    assert [
        (item.source_pointer, item.target_pointer)
        for item in composition.evidence_bindings
    ] == [
        ("/issuer_url", "/external_subject/issuer"),
        ("/subject", "/external_subject/subject"),
    ]
    assert all(
        item.target_pointer != "/external_subject/provider_binding"
        for item in composition.evidence_bindings
    )


def test_contract_attests_the_exact_checked_in_schema_bytes() -> None:
    from dotmac_kernel.capability_contract import (
        CapabilityContractSnapshot,
        CapabilitySchemaDocument,
    )

    snapshot = CapabilityContractSnapshot.from_json_bytes(CONTRACT.read_bytes())
    by_operation = {
        operation.operation_code: operation for operation in snapshot.operations
    }
    expected_files = {
        "apply": "apply-input.json",
        "cancel": "reference-input.json",
        "observe": "reference-input.json",
        "plan": "plan-input.json",
    }
    output_files = {
        "apply": "apply-output.json",
        "cancel": "cancel-output.json",
        "observe": "observe-output.json",
        "plan": "plan-output.json",
    }

    for operation_code, filename in expected_files.items():
        operation = by_operation[operation_code]
        input_schema = CapabilitySchemaDocument.from_json_bytes(
            (SCHEMAS / filename).read_bytes(),
            expected_ref=operation.input_schema_ref,
            expected_digest=operation.input_schema_digest,
        )
        output_schema = CapabilitySchemaDocument.from_json_bytes(
            (SCHEMAS / output_files[operation_code]).read_bytes(),
            expected_ref=operation.output_schema_ref,
            expected_digest=operation.output_schema_digest,
        )
        assert input_schema.to_json_bytes() == (SCHEMAS / filename).read_bytes()
        assert (
            output_schema.to_json_bytes()
            == (SCHEMAS / output_files[operation_code]).read_bytes()
        )

    target_path = SCHEMAS / "target.json"
    target_schema = CapabilitySchemaDocument.from_json_bytes(
        target_path.read_bytes(),
        expected_ref="schema:dotmac-erp/erp/application/lifecycle/target@v1",
    )
    assert target_schema.to_json_bytes() == target_path.read_bytes()


def _property_names(value: object) -> set[str]:
    if isinstance(value, dict):
        found = set(value.get("properties", {}))
        for nested in value.values():
            found.update(_property_names(nested))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for nested in value:
            found.update(_property_names(nested))
        return found
    return set()


def test_capability_document_is_parseable_by_the_kernel_a69_grammar() -> None:
    from dotmac_kernel.capability_contract import CapabilityContractSnapshot

    payload = CONTRACT.read_bytes()
    snapshot = CapabilityContractSnapshot.from_json_bytes(payload)

    assert snapshot.identity == ("dotmac-erp", "erp.application.lifecycle", 1)
    assert snapshot.to_json_bytes() == payload


def test_http_adapter_is_thin_and_has_no_direct_persistence() -> None:
    path = ROOT / "app" / "api" / "application_lifecycle.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_calls = {
        "add",
        "commit",
        "delete",
        "execute",
        "flush",
        "query",
        "scalar",
        "scalars",
        "select",
    }
    hits = [
        (node.lineno, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden_calls
    ]

    assert hits == []


def test_http_surface_is_v1_only_and_requires_an_explicit_service_scope() -> None:
    api_source = (ROOT / "app" / "api" / "application_lifecycle.py").read_text(
        encoding="utf-8"
    )
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert '"erp:application-lifecycle"' in api_source
    assert (
        'app.include_router(application_lifecycle_router, prefix="/api/v1")'
        in main_source
    )
    assert "_include_api_router(application_lifecycle_router" not in main_source


def test_existing_local_account_callers_delegate_to_the_one_owner() -> None:
    sources = {
        "admin_facade": ROOT / "app" / "services" / "admin" / "web.py",
        "admin_identity": (ROOT / "app" / "services" / "admin" / "web" / "identity.py"),
        "hr_offboarding": (
            ROOT / "app" / "services" / "people" / "hr" / "offboarding.py"
        ),
    }
    for label, path in sources.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ApplicationAccessLifecycle"
        ]
        assert calls, label

        lifecycle_functions = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"activate_user_account", "offboard_employee"}
        ]
        transaction_calls = [
            node.func.attr
            for function in lifecycle_functions
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"commit", "rollback"}
        ]
        assert transaction_calls == [], label

    offboarding = sources["hr_offboarding"].read_text(encoding="utf-8")
    assert "def _disable_erp_credentials" not in offboarding
    assert "def _deactivate_person" not in offboarding

    web_route = (ROOT / "app" / "web" / "admin.py").read_text(encoding="utf-8")
    route_start = web_route.index("def admin_users_activate(")
    route_end = web_route.index("\n\n@router.", route_start)
    assert "Depends(get_db_for_org)" in web_route[route_start:route_end]


def test_lifecycle_target_has_no_provider_or_authorization_branches() -> None:
    paths = (
        ROOT / "app" / "schemas" / "application_lifecycle.py",
        ROOT / "app" / "services" / "application_lifecycle.py",
    )
    forbidden_identifiers = {
        "keycloak",
        "entra",
        "google",
        "auth0",
        "role",
        "roles",
        "group",
        "groups",
        "scope",
        "scopes",
        "claim",
        "claims",
    }
    hits: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id.lower() in forbidden_identifiers:
                hits.append(f"{path.name}:{node.lineno}:{node.id}")
            elif (
                isinstance(node, ast.Attribute)
                and node.attr.lower() in forbidden_identifiers
            ):
                hits.append(f"{path.name}:{node.lineno}:{node.attr}")

    assert hits == []
