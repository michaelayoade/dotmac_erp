# UI Conventions

This document captures the UI patterns and conventions for consistent, high‑quality screens.

## App Shell
- Use the shared sidebar header partial: `templates/partials/_sidebar_header.html`.
- Use the shared top bar macro: `topbar()` from `templates/components/macros.html`.
- Sidebar behavior:
  - Desktop: `sidebarCollapsed` controls width, stored in `localStorage`.
  - Mobile: `sidebarOpen` controls overlay and slide‑in.
  - Only show collapse/expand buttons on desktop.
  - Provide a close button on mobile.

## Branding
- Branding variables are normalized in `templates/partials/_brand_context.html`.
- Org branding CSS/fonts are injected with `templates/partials/_org_branding_head.html`.
- Document headers use `templates/partials/_document_header.html`.

## Typography
- Headings: `font-display`.
- Numeric values: `font-mono`.
- Body text: default font stack from `app.css`.

## Buttons
- Primary CTA: `btn btn-primary`.
- Secondary CTA: `btn btn-secondary`.
- Destructive actions: `btn btn-danger-outline` or `btn btn-danger`.

## Sidebar
- Sidebar nav items use `rounded-lg` and consistent spacing.
- Section dividers should follow the established pattern: uppercase, muted, `tracking-wider`.
- Use `x-show="!sidebarCollapsed"` to hide labels in collapsed mode.

## Reports / Documents
- Use `document-card` for document layouts.
- Always include `_document_header.html` on print/PDF views.
- Keep print styles clean: avoid shadows and complex layouts for print.

## Responsiveness
- Mobile breakpoints follow Tailwind conventions:
  - `sm`: 640px, `md`: 768px, `lg`: 1024px, `xl`: 1280px.
- Avoid nested scrollbars. Sidebar scrolling should be on `nav`, not the `aside`.

## Color Accents
- Finance: `teal`.
- People: `violet`.
- Inventory/Expense/Operations: `amber`.
- Procurement: `blue`.

## Review Standard
- New screens must match top bar + sidebar patterns.
- No new one‑off header or sidebar layouts without a shared partial.
