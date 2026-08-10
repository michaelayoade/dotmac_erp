"""
Automation Services.

Cross-cutting services for document generation, workflows, and templates.
"""

from app.services.automation.document_generator import DocumentGeneratorService

__all__ = [
    "DocumentGeneratorService",
]

from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — automation rules and their execution limits.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("automation",))
