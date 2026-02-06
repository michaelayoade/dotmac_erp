# UI Consistency Checklist

Use this before merging UI changes.

## App Shell
- [ ] Sidebar header uses `templates/partials/_sidebar_header.html` (non‑admin).
- [ ] Top bar uses `topbar()` macro from `templates/components/macros.html`.
- [ ] Collapse/expand controls only visible on desktop.
- [ ] Mobile sidebar has a close button.

## Branding
- [ ] `templates/partials/_brand_context.html` included where org branding is needed.
- [ ] `templates/partials/_org_branding_head.html` included in module bases.
- [ ] Document views include `templates/partials/_document_header.html`.

## Layout
- [ ] No nested scrollbars (sidebar scroll only in `nav`).
- [ ] Truncation applied to long org names (`title` attribute present).
- [ ] Module pill hidden on small screens (`hidden sm:inline-flex`).

## Color + Typography
- [ ] Correct accent color for the module.
- [ ] Page title uses `font-display`.
- [ ] Numeric values use `font-mono`.

## Print/PDF
- [ ] Print views include branding header.
- [ ] Print styles remove unnecessary shadows.

## Regression Scan
- [ ] Verify sidebar collapse/expand in each module.
- [ ] Verify mobile sidebar open/close.
- [ ] Verify top bar in Finance/People/Inventory/Expense/Procurement/Operations.
