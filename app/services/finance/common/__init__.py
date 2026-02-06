"""
Common services shared across IFRS modules.

Provides reusable helpers for:
- Entity validation and retrieval (helpers.py)
- Date, currency, and enum formatting (formatters.py)
- Search and filter utilities (search.py)
- File attachments (attachment.py)
- Document numbering (numbering.py)
- Structured logging with context (logging.py)
"""

from app.services.finance.common.attachment import AttachmentService, attachment_service

# Formatters
from app.services.finance.common.formatters import (
    format_boolean,
    format_currency,
    format_currency_compact,
    format_date,
    format_date_display,
    format_enum,
    format_enum_display,
    format_file_size,
    format_percentage,
    parse_date,
    parse_decimal,
    parse_enum_safe,
    truncate_text,
)

# Entity helpers
from app.services.finance.common.helpers import (
    activate_entity,
    deactivate_entity,
    get_entity_display_name,
    get_model_pk_column,
    get_org_scoped_entity,
    get_org_scoped_entity_by_field,
    toggle_entity_status,
    validate_unique_code,
)

# Logging utilities
from app.services.finance.common.logging import (
    ContextualLogger,
    ServiceLogger,
    clear_log_context,
    get_log_context,
    get_logger,
    log_db_error,
    log_service_call,
    log_slow_operation,
    set_log_context,
)
from app.services.finance.common.numbering import NumberingService, SyncNumberingService

# Search utilities
from app.services.finance.common.search import (
    apply_amount_range_filter,
    apply_code_name_search,
    apply_date_range_filter,
    apply_multi_field_filter,
    apply_search_filter,
    apply_status_filter,
    build_search_pattern,
)

__all__ = [
    # Attachment service
    "attachment_service",
    "AttachmentService",
    # Numbering service
    "NumberingService",
    "SyncNumberingService",
    # Entity helpers
    "validate_unique_code",
    "get_org_scoped_entity",
    "get_org_scoped_entity_by_field",
    "toggle_entity_status",
    "activate_entity",
    "deactivate_entity",
    "get_model_pk_column",
    "get_entity_display_name",
    # Formatters
    "parse_date",
    "format_date",
    "format_date_display",
    "parse_decimal",
    "format_currency",
    "format_currency_compact",
    "parse_enum_safe",
    "format_enum",
    "format_enum_display",
    "format_file_size",
    "format_percentage",
    "format_boolean",
    "truncate_text",
    # Search utilities
    "build_search_pattern",
    "apply_search_filter",
    "apply_code_name_search",
    "apply_multi_field_filter",
    "apply_date_range_filter",
    "apply_amount_range_filter",
    "apply_status_filter",
    # Logging utilities
    "get_logger",
    "ContextualLogger",
    "set_log_context",
    "clear_log_context",
    "get_log_context",
    "log_slow_operation",
    "log_service_call",
    "log_db_error",
    "ServiceLogger",
]
