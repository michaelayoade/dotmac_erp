#!/usr/bin/env python3
"""Migrate inline SVG icons to icon_svg() macro calls.

Scans Jinja2 templates for inline <svg> outline icons, matches their
<path d="..."> data against a registry of ~65 named icons, and replaces
the entire <svg>…</svg> block with {{ icon_svg("name", "classes") }}.

Usage:
    python scripts/fix_inline_svgs.py --dry-run      # Preview changes
    python scripts/fix_inline_svgs.py --execute       # Apply changes
    python scripts/fix_inline_svgs.py --generate-macro  # Print icon_path Jinja2

Idempotent: re-running after --execute produces 0 changes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
#  ICON REGISTRY — SVG path d= values → icon names
#  Organised by category.  Every value is the EXACT d= string found
#  in the codebase (extracted via `grep -rhoP 'd="[^"]*"' templates/`).
# ──────────────────────────────────────────────────────────────────────

SINGLE_PATH_MAP: dict[str, str] = {
    # ── Navigation ────────────────────────────────────────────────
    "M9 5l7 7-7 7": "chevron-right",
    "M19 9l-7 7-7-7": "chevron-down",
    "M15 19l-7-7 7-7": "chevron-left",
    "M5 15l7-7 7 7": "chevron-up",
    "M11 19l-7-7 7-7m8 14l-7-7 7-7": "chevron-double-left",
    "M10 19l-7-7m0 0l7-7m-7 7h18": "arrow-left",
    "M13 7l5 5m0 0l-5 5m5-5H6": "arrow-right",
    "M17 8l4 4m0 0l-4 4m4-4H3": "arrow-narrow-right",
    "M5 10l7-7m0 0l7 7m-7-7v18": "arrow-up",
    "M19 14l-7 7m0 0l-7-7m7 7V3": "arrow-down",
    # ── Action icons ──────────────────────────────────────────────
    "M5 13l4 4L19 7": "check",
    "M6 18L18 6M6 6l12 12": "x-mark",
    "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16": "trash",
    "M12 4v16m8-8H4": "plus",
    "M12 6v6m0 0v6m0-6h6m-6 0H6": "plus",
    "M12 6v12m6-6H6": "plus",  # macro's original variant
    "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4": "download",
    "M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12": "upload",
    "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15": "refresh",
    "M20 12H4": "minus",
    # ── Status / Feedback ─────────────────────────────────────────
    "M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z": "exclamation-circle",
    "M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z": "information-circle",
    "M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z": "x-circle",
    "M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636": "ban",
    "M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z": "check-badge",
    # ── Already in macro (outline) ────────────────────────────────
    "M13 7h8m0 0v8m0-8l-8 8-4-4-6 6": "trending-up",
    "M13 17h8m0 0V9m0 8l-8-8-4 4-6-6": "trending-down",
    "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z": "document",
    "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z": "chart",
    "M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z": "users",
    "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z": "currency",
    "M9 14l6-6m-5.5.5h.01m4.99 5h.01M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16l3.5-2 3.5 2 3.5-2 3.5 2z": "receipt",
    "M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z": "credit-card",
    "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z": "folder",
    "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z": "clock",
    "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z": "check-circle",
    "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z": "warning",
    "M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4": "box",
    "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z": "edit",
    "M4 6h16M4 12h16M4 18h16": "menu",
    # ── People ────────────────────────────────────────────────────
    "M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z": "user",
    "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z": "user-group",
    "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z": "user-group-lg",
    "M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z": "user-add",
    # ── Documents / Data ──────────────────────────────────────────
    "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2": "clipboard",
    "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4": "clipboard-check",
    "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01": "clipboard-list",
    "M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z": "document-report",
    "M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z": "document-download",
    "M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z": "document-text",
    "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z": "calculator",
    # ── Calendar / Time ───────────────────────────────────────────
    "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z": "calendar",
    # ── Finance / Money ───────────────────────────────────────────
    "M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z": "cash",
    "M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2z": "banknotes",
    "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z": "shield-check",
    "M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3": "scale",
    # ── UI Elements ───────────────────────────────────────────────
    "M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z": "search",
    "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z": "cog",
    "M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z": "filter",
    "M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z": "star",
    "M4 6h16M4 10h16M4 14h16M4 18h16": "list-bullet",
    "M21 12a9 9 0 11-18 0 9 9 0 0118 0z": "circle",
    # ── Buildings / Locations ─────────────────────────────────────
    "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4": "building",
    "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6": "home",
    "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z": "location-marker",
    "M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7": "map",
    # ── Security / Access ─────────────────────────────────────────
    "M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z": "lock-closed",
    "M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z": "key",
    "M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1": "logout",
    # ── Communication ─────────────────────────────────────────────
    "M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z": "mail",
    "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z": "chat-bubble",
    "M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z": "chat",
    "M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9": "bell",
    # ── Automation / System ───────────────────────────────────────
    "M13 10V3L4 14h7v7l9-11h-7z": "lightning-bolt",
    "M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z": "play",
    "M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z": "pause-circle",
    "M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4": "switch-horizontal",
    # ── Printing / Export ─────────────────────────────────────────
    "M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z": "printer",
    "M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4": "archive",
    # ── Collections / Misc ────────────────────────────────────────
    "M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10": "collection",
    "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z": "tag",
    "M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1": "link",
    "M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14": "external-link",
    "M12 19l9 2-9-18-9 18 9-2zm0 0v-8": "paper-airplane",
    "M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6": "reply",
    "M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13": "paperclip",
    "M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z": "briefcase",
    "M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4": "inbox",
    "M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z": "check-lg",
    "M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z": "emoji-sad",
    "M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z": "photograph",
    "M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01": "color-swatch",
    "M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z": "question-mark-circle",
    "M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z": "duplicate",
    "M3 21v-4m0 0V5a2 2 0 012-2h6.5l1 1H21l-3 6 3 6h-8.5l-1-1H5a2 2 0 00-2 2zm9-13.5V9": "flag",
    "M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z": "fire",
    "M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z": "ticket",
    "M12 14l9-5-9-5-9 5 9 5zm0 0l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14zm-4 6v-7.5l4-2.222": "academic-cap",
    "M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z": "desktop-computer",
    "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253": "book-open",
    "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4": "database",
    "M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z": "view-boards",
    # ── Theme toggles ─────────────────────────────────────────────
    "M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z": "moon",
    "M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z": "sun",
    # ── Visibility ────────────────────────────────────────────────
    "M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21": "eye-off",
    "M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12": "cloud-upload",
    # ── Download variants ─────────────────────────────────────────
    "M4 16v1a2 2 0 002 2h12a2 2 0 002-2v-1M12 12l4-4m-4 4l-4-4m4 4V4": "download-alt",
    "M4 16v1a2 2 0 002 2h12a2 2 0 002-2v-1M12 12l4 4m-4-4l-4 4m4-4V4": "upload-alt",
    # ── Receipt variant with filled dots ──────────────────────────
    "M9 14l6-6m-5.5.5h.01m4.99 5h.01M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16l3.5-2 3.5 2 3.5-2 3.5 2zM10 8.5a.5.5 0 11-1 0 .5.5 0 011 0zm5 5a.5.5 0 11-1 0 .5.5 0 011 0z": "receipt",
}

# Multi-path icons: frozenset of (d1, d2) → icon_name.
# These SVGs contain TWO <path> elements that together form one icon.
MULTI_PATH_MAP: dict[frozenset[str], str] = {
    # eye — iris + outer shape
    frozenset(
        {
            "M15 12a3 3 0 11-6 0 3 3 0 016 0z",
            "M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z",
        }
    ): "eye",
    # cog — gear outline + inner circle
    frozenset(
        {
            "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
            "M15 12a3 3 0 11-6 0 3 3 0 016 0z",
        }
    ): "cog",
    # location-marker — outer pin + inner dot
    frozenset(
        {
            "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z",
            "M15 11a3 3 0 11-6 0 3 3 0 016 0z",
        }
    ): "location-marker",
    # pie-chart — main circle + wedge
    frozenset(
        {
            "M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z",
            "M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z",
        }
    ): "pie-chart",
}

# ──────────────────────────────────────────────────────────────────────
#  SKIP list — files where we should NOT replace SVGs
# ──────────────────────────────────────────────────────────────────────

SKIP_FILES: set[str] = {
    "components/macros.html",  # the macro definitions themselves
    "components/_file_upload.html",  # upload widget has special SVGs
    "components/_import_wizard.html",  # import wizard uses unique SVGs
    "components/app_topbar.html",  # topbar has theme toggle SVGs
}

# Alpine.js directive markers — skip SVGs that have these in their attrs
ALPINE_MARKERS = frozenset(
    [
        "x-show",
        "x-cloak",
        "x-transition",
        "x-bind",
        ":class",
        "@click",
        "x-if",
        "x-ref",
    ]
)

# ──────────────────────────────────────────────────────────────────────
#  REGEX PATTERNS
# ──────────────────────────────────────────────────────────────────────

# Match a complete <svg ...> ... </svg> block (DOTALL for multiline)
SVG_BLOCK_RE = re.compile(
    r"<svg\s+"  # opening tag
    r"([^>]*?)"  # group 1: attributes
    r">\s*"  # close of opening tag
    r"((?:\s*<(?:path|circle)[^>]*/?>\s*)+)"  # group 2: path/circle children
    r"\s*</svg>",  # closing tag
    re.DOTALL,
)

# Extract d="..." from a <path> element
PATH_D_RE = re.compile(r'd="([^"]*)"')

# Extract class="..." from SVG attributes
CLASS_RE = re.compile(r'class="([^"]*)"')

# Extract stroke-width="..." from path content
STROKE_WIDTH_RE = re.compile(r'stroke-width="([^"]*)"')

# Detect existing macro import line
IMPORT_RE = re.compile(
    r"(\{%[-\s]+from\s+\"components/macros\.html\"\s+import\s+)(.*?)(\s*[-]?%\})",
    re.DOTALL,
)

# Detect {% extends ... %} line
EXTENDS_RE = re.compile(r"\{%\s*extends\s+")


# ──────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────


def normalize_path(d: str) -> str:
    """Normalise an SVG path d= value for comparison."""
    return " ".join(d.strip().split())


def should_skip_svg(attrs: str, content: str) -> bool:
    """Return True if this SVG block should not be migrated."""
    # Skip filled icons (solid variant)
    if 'fill="currentColor"' in attrs:
        return True
    # Must be an outline icon
    if 'fill="none"' not in attrs or 'stroke="currentColor"' not in attrs:
        return True
    # Must have standard 24×24 viewBox
    if 'viewBox="0 0 24 24"' not in attrs:
        return True
    # Skip Alpine.js controlled SVGs
    for marker in ALPINE_MARKERS:
        if marker in attrs:
            return True
    # Skip SVGs with id= (may be referenced by JS)
    if ' id="' in attrs:
        return True
    # Skip spinners
    if "animate-spin" in attrs:
        return True
    # Skip SVGs containing <circle> elements (spinners)
    if "<circle" in content:
        return True
    # Skip SVGs with inline style
    if 'style="' in attrs:
        return True
    return False


def identify_icon(content: str) -> str | None:
    """Match path d= data to an icon name.  Returns None if no match."""
    paths = PATH_D_RE.findall(content)
    if not paths:
        return None

    # Normalise all paths
    normed = [normalize_path(p) for p in paths]

    # Single-path icons
    if len(normed) == 1:
        return SINGLE_PATH_MAP.get(normed[0])

    # Two-path icons
    if len(normed) == 2:
        key = frozenset(normed)
        name = MULTI_PATH_MAP.get(key)
        if name:
            return name
        # Try each path individually — some multi-path SVGs have one
        # decorative path that we can ignore (rare)
        for p in normed:
            name = SINGLE_PATH_MAP.get(p)
            if name:
                return name

    return None


def extract_stroke_width(content: str) -> str:
    """Get the stroke-width from path elements (default '2')."""
    m = STROKE_WIDTH_RE.search(content)
    return m.group(1) if m else "2"


def build_replacement(icon_name: str, css_classes: str, stroke_width: str) -> str:
    """Build the {{ icon_svg(...) }} macro call string."""
    # Default size
    if not css_classes:
        css_classes = "h-5 w-5"

    if stroke_width != "2":
        return f'{{{{ icon_svg("{icon_name}", "{css_classes}", stroke_width="{stroke_width}") }}}}'
    if css_classes == "h-5 w-5":
        return f'{{{{ icon_svg("{icon_name}") }}}}'
    return f'{{{{ icon_svg("{icon_name}", "{css_classes}") }}}}'


def ensure_import(content: str) -> str:
    """Add icon_svg to the macros.html import line if missing."""
    # Already has icon_svg import?
    if "icon_svg" in content.split("import", 1)[1] if "import" in content else "":
        # More precise check
        m = IMPORT_RE.search(content)
        if m and "icon_svg" in m.group(2):
            return content

    m = IMPORT_RE.search(content)
    if m:
        # Add icon_svg to existing import
        current_imports = m.group(2).rstrip().rstrip(",")
        new_imports = current_imports + ", icon_svg"
        return content[: m.start(2)] + new_imports + content[m.end(2) :]

    # No existing import — add after {% extends %} or at top
    extends_m = EXTENDS_RE.search(content)
    if extends_m:
        # Find end of extends line
        line_end = content.index("\n", extends_m.start()) + 1
        import_line = '{% from "components/macros.html" import icon_svg %}\n'
        return content[:line_end] + import_line + content[line_end:]

    # No extends either — add at very top
    import_line = '{% from "components/macros.html" import icon_svg %}\n'
    return import_line + content


# ──────────────────────────────────────────────────────────────────────
#  MAIN PROCESSING
# ──────────────────────────────────────────────────────────────────────


def process_file(filepath: Path, templates_dir: Path) -> tuple[str, int]:
    """Process one template file.  Returns (new_content, change_count)."""
    relative = filepath.relative_to(templates_dir).as_posix()

    # Skip protected files
    for skip in SKIP_FILES:
        if relative == skip:
            return filepath.read_text(), 0

    content = filepath.read_text()
    original = content
    changes = 0

    def replace_svg(match: re.Match[str]) -> str:
        nonlocal changes
        attrs = match.group(1)
        inner = match.group(2)

        # Check skip conditions
        if should_skip_svg(attrs, inner):
            return match.group(0)

        # Already replaced?
        if "icon_svg" in match.group(0):
            return match.group(0)

        # Identify icon
        icon_name = identify_icon(inner)
        if icon_name is None:
            return match.group(0)

        # Extract CSS classes from the SVG element
        class_m = CLASS_RE.search(attrs)
        css_classes = class_m.group(1) if class_m else "h-5 w-5"

        # Extract stroke-width
        stroke_width = extract_stroke_width(inner)

        changes += 1
        return build_replacement(icon_name, css_classes, stroke_width)

    content = SVG_BLOCK_RE.sub(replace_svg, content)

    # If we made changes, ensure icon_svg is imported
    if changes > 0:
        content = ensure_import(content)

    return content, changes


def scan_templates(templates_dir: Path, *, dry_run: bool = True) -> dict[str, int]:
    """Scan all .html files under templates_dir.  Returns {file: count}."""
    results: dict[str, int] = {}
    total_changes = 0
    total_files = 0

    html_files = sorted(templates_dir.rglob("*.html"))
    for filepath in html_files:
        new_content, changes = process_file(filepath, templates_dir)

        if changes > 0:
            results[str(filepath.relative_to(templates_dir))] = changes
            total_changes += changes
            total_files += 1

            if dry_run:
                print(f"  {filepath.relative_to(templates_dir)}: {changes} SVG(s)")
            else:
                filepath.write_text(new_content)
                print(f"  ✓ {filepath.relative_to(templates_dir)}: {changes} SVG(s)")

    print(
        f"\n{'[DRY RUN] ' if dry_run else ''}Total: {total_changes} SVGs "
        f"across {total_files} files"
    )
    return results


# ──────────────────────────────────────────────────────────────────────
#  MACRO GENERATOR — outputs the expanded icon_path for macros.html
# ──────────────────────────────────────────────────────────────────────

# Canonical path per icon name (for generating the Jinja2 macro).
# For icons with multiple path variants (e.g. "plus"), pick the most
# common one.  Multi-path icons list all paths.
ICON_PATHS: dict[str, list[str]] = {
    # ── Navigation ──
    "chevron-right": ["M9 5l7 7-7 7"],
    "chevron-down": ["M19 9l-7 7-7-7"],
    "chevron-left": ["M15 19l-7-7 7-7"],
    "chevron-up": ["M5 15l7-7 7 7"],
    "chevron-double-left": ["M11 19l-7-7 7-7m8 14l-7-7 7-7"],
    "arrow-left": ["M10 19l-7-7m0 0l7-7m-7 7h18"],
    "arrow-right": ["M13 7l5 5m0 0l-5 5m5-5H6"],
    "arrow-narrow-right": ["M17 8l4 4m0 0l-4 4m4-4H3"],
    "arrow-up": ["M5 10l7-7m0 0l7 7m-7-7v18"],
    "arrow-down": ["M19 14l-7 7m0 0l-7-7m7 7V3"],
    # ── Actions ──
    "check": ["M5 13l4 4L19 7"],
    "check-lg": [
        "M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
    ],
    "x-mark": ["M6 18L18 6M6 6l12 12"],
    "trash": [
        "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
    ],
    "plus": ["M12 4v16m8-8H4"],
    "minus": ["M20 12H4"],
    "download": ["M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"],
    "download-alt": ["M4 16v1a2 2 0 002 2h12a2 2 0 002-2v-1M12 12l4-4m-4 4l-4-4m4 4V4"],
    "upload": ["M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12"],
    "upload-alt": ["M4 16v1a2 2 0 002 2h12a2 2 0 002-2v-1M12 12l4 4m-4-4l-4 4m4-4V4"],
    "refresh": [
        "M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
    ],
    # ── Status ──
    "check-circle": ["M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"],
    "exclamation-circle": ["M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"],
    "information-circle": ["M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"],
    "x-circle": [
        "M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z"
    ],
    "warning": [
        "M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
    ],
    "ban": [
        "M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
    ],
    "check-badge": [
        "M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z"
    ],
    "circle": ["M21 12a9 9 0 11-18 0 9 9 0 0118 0z"],
    "pause-circle": ["M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z"],
    "emoji-sad": [
        "M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    ],
    # ── Already in macro ──
    "trending-up": ["M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"],
    "trending-down": ["M13 17h8m0 0V9m0 8l-8-8-4 4-6-6"],
    "document": [
        "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
    ],
    "document-report": [
        "M9 17v-2m3 2v-4m3 4v-6m2 10H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
    ],
    "document-download": [
        "M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
    ],
    "document-text": [
        "M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"
    ],
    "chart": [
        "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
    ],
    "users": [
        "M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"
    ],
    "currency": [
        "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    ],
    "receipt": [
        "M9 14l6-6m-5.5.5h.01m4.99 5h.01M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16l3.5-2 3.5 2 3.5-2 3.5 2z"
    ],
    "credit-card": [
        "M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z"
    ],
    "folder": [
        "M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
    ],
    "clock": ["M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"],
    "box": ["M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"],
    "edit": [
        "M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"
    ],
    "menu": ["M4 6h16M4 12h16M4 18h16"],
    # ── Multi-path ──
    "eye": [
        "M15 12a3 3 0 11-6 0 3 3 0 016 0z",
        "M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z",
    ],
    "cog": [
        "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
        "M15 12a3 3 0 11-6 0 3 3 0 016 0z",
    ],
    "location-marker": [
        "M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z",
        "M15 11a3 3 0 11-6 0 3 3 0 016 0z",
    ],
    "pie-chart": [
        "M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z",
        "M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z",
    ],
    # ── People ──
    "user": ["M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"],
    "user-group": [
        "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"
    ],
    "user-group-lg": [
        "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"
    ],
    "user-add": [
        "M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z"
    ],
    # ── Data ──
    "clipboard": [
        "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
    ],
    "clipboard-check": [
        "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
    ],
    "clipboard-list": [
        "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"
    ],
    "calculator": [
        "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"
    ],
    # ── Calendar ──
    "calendar": [
        "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
    ],
    # ── Finance ──
    "cash": [
        "M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"
    ],
    "banknotes": [
        "M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2z"
    ],
    "shield-check": [
        "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"
    ],
    "scale": [
        "M3 6l3 1m0 0l-3 9a5.002 5.002 0 006.001 0M6 7l3 9M6 7l6-2m6 2l3-1m-3 1l-3 9a5.002 5.002 0 006.001 0M18 7l3 9m-3-9l-6-2m0-2v2m0 16V5m0 16H9m3 0h3"
    ],
    # ── UI ──
    "search": ["M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"],
    "filter": [
        "M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"
    ],
    "star": [
        "M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"
    ],
    "list-bullet": ["M4 6h16M4 10h16M4 14h16M4 18h16"],
    # ── Buildings ──
    "building": [
        "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4"
    ],
    "home": [
        "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"
    ],
    "map": [
        "M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"
    ],
    # ── Security ──
    "lock-closed": [
        "M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
    ],
    "key": [
        "M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"
    ],
    "logout": [
        "M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
    ],
    # ── Communication ──
    "mail": [
        "M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
    ],
    "chat-bubble": [
        "M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"
    ],
    "chat": [
        "M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z"
    ],
    "bell": [
        "M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"
    ],
    "paper-airplane": ["M12 19l9 2-9-18-9 18 9-2zm0 0v-8"],
    # ── Automation ──
    "lightning-bolt": ["M13 10V3L4 14h7v7l9-11h-7z"],
    "play": [
        "M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"
    ],
    "switch-horizontal": ["M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"],
    # ── Print / Export ──
    "printer": [
        "M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"
    ],
    "archive": [
        "M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4"
    ],
    # ── Collections ──
    "collection": [
        "M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
    ],
    "tag": [
        "M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"
    ],
    "link": [
        "M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
    ],
    "external-link": [
        "M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
    ],
    "reply": ["M3 10h10a8 8 0 018 8v2M3 10l6 6m-6-6l6-6"],
    "paperclip": [
        "M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13"
    ],
    "briefcase": [
        "M21 13.255A23.931 23.931 0 0112 15c-3.183 0-6.22-.62-9-1.745M16 6V4a2 2 0 00-2-2h-4a2 2 0 00-2 2v2m4 6h.01M5 20h14a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
    ],
    "inbox": [
        "M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
    ],
    "duplicate": [
        "M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
    ],
    "flag": [
        "M3 21v-4m0 0V5a2 2 0 012-2h6.5l1 1H21l-3 6 3 6h-8.5l-1-1H5a2 2 0 00-2 2zm9-13.5V9"
    ],
    "fire": [
        "M17.657 18.657A8 8 0 016.343 7.343S7 9 9 10c0-2 .5-5 2.986-7C14 5 16.09 5.777 17.656 7.343A7.975 7.975 0 0120 13a7.975 7.975 0 01-2.343 5.657z"
    ],
    "ticket": [
        "M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z"
    ],
    "academic-cap": [
        "M12 14l9-5-9-5-9 5 9 5zm0 0l6.16-3.422a12.083 12.083 0 01.665 6.479A11.952 11.952 0 0012 20.055a11.952 11.952 0 00-6.824-2.998 12.078 12.078 0 01.665-6.479L12 14zm-4 6v-7.5l4-2.222"
    ],
    "desktop-computer": [
        "M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
    ],
    "photograph": [
        "M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"
    ],
    "color-swatch": [
        "M7 21a4 4 0 01-4-4V5a2 2 0 012-2h4a2 2 0 012 2v12a4 4 0 01-4 4zm0 0h12a2 2 0 002-2v-4a2 2 0 00-2-2h-2.343M11 7.343l1.657-1.657a2 2 0 012.828 0l2.829 2.829a2 2 0 010 2.828l-8.486 8.485M7 17h.01"
    ],
    "question-mark-circle": [
        "M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
    ],
    "book-open": [
        "M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
    ],
    "database": [
        "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"
    ],
    "view-boards": [
        "M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"
    ],
    "eye-off": [
        "M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21"
    ],
    "cloud-upload": [
        "M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
    ],
    # ── Theme ──
    "moon": [
        "M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"
    ],
    "sun": [
        "M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
    ],
}


def generate_macro() -> str:
    """Generate the Jinja2 icon_path macro from ICON_PATHS."""
    lines: list[str] = []
    lines.append("{# ============================================")
    lines.append("   ICON SVG — render a named icon as inline SVG")
    lines.append('   Usage: {{ icon_svg("chevron-right", "h-4 w-4") }}')
    lines.append("   All 65+ icons: see icon_path() below")
    lines.append("   ============================================ #}")
    lines.append('{% macro icon_svg(name, size="h-5 w-5", stroke_width="2") %}')
    lines.append(
        '<svg class="{{ size }}" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="{{ stroke_width }}" viewBox="0 0 24 24" aria-hidden="true">{{ icon_path(name) }}</svg>'
    )
    lines.append("{% endmacro %}")
    lines.append("")
    lines.append("{% macro icon_path(name) %}")

    first = True
    for icon_name, paths in ICON_PATHS.items():
        prefix = "{% if" if first else "{% elif"
        first = False
        path_elements = "".join(f'<path d="{p}"/>' for p in paths)
        lines.append(f'{prefix} name == "{icon_name}" %}}{path_elements}')

    lines.append('{% else %}<path d="M4 6h16M4 12h16M4 18h16"/>')
    lines.append("{% endif %}")
    lines.append("{% endmacro %}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview without writing")
    group.add_argument("--execute", action="store_true", help="Apply changes")
    group.add_argument(
        "--generate-macro", action="store_true", help="Print icon_path Jinja2"
    )
    args = parser.parse_args()

    if args.generate_macro:
        print(generate_macro())
        return

    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    if not templates_dir.is_dir():
        print(f"ERROR: templates dir not found: {templates_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {templates_dir} ...")
    results = scan_templates(templates_dir, dry_run=args.dry_run)

    if not results:
        print("No changes needed — already migrated or no matching SVGs.")
        sys.exit(0)

    if args.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    main()
