from app.web.deps import _resolve_mode_policy_banner


def test_mode_policy_banner_uses_profile_message_for_pms_block() -> None:
    variant, message = _resolve_mode_policy_banner(
        "MODE_POLICY_BLOCKED:pms_write_requires_government_or_hybrid (current_mode=PRIVATE)",
        ui_messages={"mode_blocked_pms_write": "Custom PMS block message"},
    )
    assert variant == "pms"
    assert message == "Custom PMS block message"


def test_mode_policy_banner_uses_profile_message_for_private_block() -> None:
    variant, message = _resolve_mode_policy_banner(
        "MODE_POLICY_BLOCKED:private_write_requires_private_or_hybrid (current_mode=GOVERNMENT_PMS)",
        ui_messages={"mode_blocked_private_write": "Custom private block message"},
    )
    assert variant == "private"
    assert message == "Custom private block message"
