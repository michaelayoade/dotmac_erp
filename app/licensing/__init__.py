"""DotMac ERP commercial licensing system.

Handles license validation, module gating, and enforcement for on-premise
deployments. Only explicit dev mode (DOTMAC_DEV_MODE=true) bypasses checks;
an omitted flag fails closed into normal license enforcement.
"""
