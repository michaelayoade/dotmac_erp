"""Settings services."""

# Setting domain(s) this module owns — the settings surface's own configuration.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS: tuple[str, ...] = ("settings",)
