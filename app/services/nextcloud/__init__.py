from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — the Nextcloud integration's credentials and endpoint.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("notifications",))

# The domain is named `notifications` for historical reasons — every one of its
# settings is a `nextcloud_*` credential or endpoint, which is why this package
# owns it. Renaming the domain is a data migration and belongs in its own change.
