# DotMac Frontline — Flutter Mobile App Build Spec

**Version:** 1.0 (draft)
**Date:** 2026-06-10
**Backend:** DotMac ERP (FastAPI + SQLAlchemy 2.0 + PostgreSQL/RLS)
**Client:** New standalone Flutter repo, consuming `/api/v1/*`

---

## 1. Overview

DotMac Frontline is a **role-aware mobile app for frontline staff** — not the ERP on a
phone. It covers three personas that are *better* on a phone than on desktop:

1. **Employee Self-Service** — attendance, expense capture, leave, payslips, my tasks/time
2. **Manager / Approver** — one unified pull-to-refresh approval inbox
3. **Field / Warehouse** — barcode stock counts, goods receipt, material requests

Back-office work (multi-line invoices/POs/journals, payroll processing, bank
reconciliation, IPSAS, Gantt, valuation config) stays on desktop and is explicitly
**out of scope**.

### Locked decisions
- **Scope:** personas 1 + 2 + 3 in the MVP (one role-aware app). Persona 4 (exec
  dashboards) deferred.
- **Backend posture:** lean — online-only, **polling** (no push in MVP). Push is the
  first fast-follow.
- **Repo:** separate Flutter repo (`dotmac_mobile`), consuming `/api/v1` via a
  generated Dart client.

---

## 2. Personas & role gating

The home screen branches on the user's RBAC roles/permissions, which are already
present in the JWT claims (`roles`, `scopes`). No separate role lookup needed.

| Persona | Gate (token permission/role) | Home tab |
|---|---|---|
| Employee (everyone) | authenticated | **My Work** |
| Manager | has any `*:approve` permission, or has direct reports | **Approvals** |
| Warehouse | `inventory:*` / warehouse role | **Warehouse** |

A single user can see multiple tabs (a warehouse supervisor who also approves leave
sees all three). Tabs render only when the gate passes.

---

## 3. Architecture

### Stack
| Concern | Choice |
|---|---|
| Language | Dart / Flutter (stable channel) |
| State mgmt | Riverpod |
| HTTP | `dio` + interceptors (auth, retry, error mapping) |
| API client | **OpenAPI-generated** off `GET /openapi.json` (`openapi_generator` or `swagger_parser`) |
| Secure storage | `flutter_secure_storage` (tokens) |
| Barcode | `mobile_scanner` |
| Camera / photos | `image_picker` + `camera` |
| Local cache (lite) | `shared_preferences` (MVP); `drift`/`isar` (phase 3 offline) |
| Push (fast-follow) | `firebase_messaging` |
| Routing | `go_router` |

### Repo structure
```
dotmac_mobile/
  lib/
    core/
      env.dart              # base URL, flavors (dev/staging/prod)
      dio_client.dart       # dio + auth interceptor + error mapping
      token_store.dart      # secure storage wrapper
      result.dart           # Result<T> / failure types
    api/                    # GENERATED Dart client (do not hand-edit)
    auth/
      auth_controller.dart  # login, refresh, logout, claims parsing
      role_gate.dart        # permission checks from token claims
    features/
      self_service/
        attendance/ expense/ leave/ payslip/ tasks/
      approvals/
        approval_item.dart  # polymorphic unified model (see §7)
        approval_inbox.dart
      warehouse/
        stock_count/ grn/ material_request/ item_lookup/
    shared/
      theme.dart            # mirror DotMac teal/parchment tokens
      widgets/              # status_badge, money_text, empty_state, etc.
  test/
  pubspec.yaml
```

### Cross-cutting conventions
- **Pagination:** all list endpoints use `offset`/`limit` and return
  `{items, total, offset, limit}`. Build one `PagedList<T>` helper.
- **Money:** render `font-mono`, 2-decimals, parentheses for negatives, ₦ prefix —
  mirror the ERP financial display rules.
- **Dates:** `DD MMM YYYY` for display, `YYYY-MM-DD` for inputs.
- **Status badges:** mirror the ERP status→color map (amber/blue/emerald/rose/slate).
- **Errors:** map HTTP status → typed failures; 401 triggers silent refresh then retry,
  else logout.

---

## 4. Authentication flow

All endpoints require `Authorization: Bearer <access_token>`. Multi-tenancy is enforced
server-side via RLS using `organization_id` from the token — the client never sends org
explicitly.

```
1. POST /api/v1/auth/login        { username, password, provider? }
     → { access_token, refresh_token, token_type, mfa_required?, mfa_token? }
2. Store both tokens in flutter_secure_storage.
3. Attach access_token to every request (dio interceptor).
4. On 401 → POST /api/v1/auth/refresh { refresh_token }
     → new access_token (rotate). Retry the original request once.
5. Logout → POST /api/v1/auth/logout { refresh_token }; clear secure storage.
6. GET /api/v1/auth/me → profile + roles/permissions for role gating.
```

**MFA:** if `mfa_required`, prompt for code and complete the MFA step before tokens are
issued (see `app/api/auth_flow.py`). MVP can defer MFA UI if the target orgs don't
enforce it — confirm with stakeholders.

---

## 5. API contract reference (verified)

All paths are under `/api/v1`. Signatures verified against the codebase.

### 5.1 Self-service (`app/api/me.py`) — persona 1, **zero backend work**
| Action | Method + path | Payload / params |
|---|---|---|
| Leave balance | `GET /me/leave/balance` | — |
| List my leave | `GET /me/leave/applications` | `status?`, `offset`, `limit` |
| Apply leave | `POST /me/leave/applications` | `{ leave_type_id, from_date, to_date, half_day, half_day_date?, reason }` |
| Leave detail | `GET /me/leave/applications/{id}` | — |
| Cancel leave | `POST /me/leave/applications/{id}/cancel` | `reason?` |
| List payslips | `GET /me/payslips` | `year?`, `status?`, `offset`, `limit` |
| Payslip detail | `GET /me/payslips/{slip_id}` | — (PDF link in response) |
| Attendance list | `GET /me/attendance` | `month=YYYY-MM` |
| Today's attendance | `GET /me/attendance/today` | — |
| **Check in** | `POST /me/attendance/check-in` | `{ check_in_time, notes? }` ⚠️ see §9 |
| **Check out** | `POST /me/attendance/check-out` | `{ check_out_time, notes? }` ⚠️ see §9 |
| Attendance summary | `GET /me/attendance/summary` | `month?` |
| Training history | `GET /me/training/history` | — |
| Appraisals | `GET /me/performance/appraisals` | — |
| My expense claims | `GET /me/expenses/claims` | (read-only list) |
| My cash advances | `GET /me/expenses/advances` | — |

### 5.2 Expense (`app/api/expense.py`) — create/submit lives here, persona 1
| Action | Method + path |
|---|---|
| Categories | `GET /expenses/categories` |
| List claims | `GET /expenses/claims` |
| **Create claim** | `POST /expenses/claims` (multi-item; receipt via file upload) |
| Claim detail | `GET /expenses/claims/{id}` |
| Update draft | `POST /expenses/claims/{id}` (or PATCH) |
| Submit | `POST /expenses/claims/{id}/submit` |
| Approve / Reject | `POST /expenses/claims/{id}/approve` / `/reject` |
| Cancel / Resubmit | `POST /expenses/claims/{id}/cancel` / `/resubmit` |
| Cash advances | `GET/POST /expenses/advances`, `/advances/{id}/approve|reject|disburse|settle` |

### 5.3 Approvals — actions exist per module (persona 2)
| Approvable | List (pending) | Approve / Reject | RBAC permission |
|---|---|---|---|
| Team leave | `GET /me/team/leave-requests?status=SUBMITTED` | `POST /me/team/leave-requests/{id}/approve` · `/reject?reason=` | `_require_leave_approval_permission` (leave approve tier) |
| Expense claim | `GET /expenses/claims?status=SUBMITTED` | `POST /expenses/claims/{id}/approve` · `/reject` | expense approval rules / `expense:*` |
| Cash advance | `GET /expenses/advances?status=SUBMITTED` | `POST /expenses/advances/{id}/approve` · `/reject` | expense approval |
| AP supplier invoice | `GET /ap/invoices?status=SUBMITTED` | `POST /ap/invoices/{id}/approve` | `ap:invoices:approve` |
| AP payment batch | `GET /ap/payment-batches?status=...` | `POST /ap/payment-batches/{id}/approve` | `ap:payment_batches:approve` |
| Purchase requisition | `GET /procurement/requisitions?status=SUBMITTED` | `POST /procurement/requisitions/{id}/approve` | requisition approve |
| Bid evaluation | `GET /procurement/evaluations` | `POST /procurement/evaluations/{id}/approve` | evaluation approve |

> **Each approvable carries its own RBAC permission.** The inbox must only surface the
> categories the current user is permitted to approve (derive from token `scopes` +
> `auth/me`). See §7 + §8 for how this is unified.

### 5.4 Warehouse / Inventory (persona 3)
| Action | Method + path |
|---|---|
| Item lookup by barcode | `GET /api/inventory/items?barcode=…` |
| Stock on hand | `GET /api/inventory/stock/item/{item_id}` / `…/warehouse/{warehouse_id}` |
| Low stock | `GET /api/inventory/stock/low-stock` |
| Stock count record line | `POST /inventory/counts/{count_id}/lines/{line_id}/record` |
| Bulk count record | `POST /inventory/counts/{count_id}/bulk-record` |
| Stock movement | `POST /api/inventory/transactions` (RECEIPT/ISSUE/TRANSFER) |
| GRN receipt approval | `POST /inventory/receipt-approvals/{id}/approve|reject` |
| Material requests | `GET/POST /inventory/material-requests` |

### 5.5 Project time (persona 1, field)
| Action | Method + path |
|---|---|
| My tasks | `GET /api/v1/pm/tasks?assignee=…&status=…` |
| Update task | `PATCH /api/v1/pm/tasks/{id}` · `/start` · `/complete` |
| Time entries | `GET/POST /api/v1/pm/time-entries` |

---

## 6. Screen-by-screen inventory

### Shell
- **Splash / auth gate** → checks token, refreshes, routes to home or login.
- **Login** → username/password (+ MFA if required).
- **Home (role-aware bottom nav)** → tabs: My Work · Approvals · Warehouse · Profile
  (each gated per §2).

### My Work (persona 1)
| Screen | Endpoints | Notes |
|---|---|---|
| Dashboard | `me/attendance/today`, `me/leave/balance` | "Clock in" CTA, leave balance chips, pending items |
| Attendance | `me/attendance/check-in|out`, `me/attendance` | Big clock button; month history list |
| Expense list | `me/expenses/claims` | Status badges, filter |
| **New expense** | `expenses/categories`, `expenses/claims` (multipart) | **Camera receipt capture per line** — killer feature |
| Expense detail | `expenses/claims/{id}` | Line items, receipts, approval chain, submit/cancel |
| Leave list | `me/leave/applications`, `me/leave/balance` | Balance per type |
| New leave | `me/leave/applications` | Date-range + half-day toggle, reason |
| Payslips | `me/payslips`, `me/payslips/{id}` | YTD, PDF view/download |
| My tasks / time | `pm/tasks`, `pm/time-entries` | Mark complete; log time |

### Approvals (persona 2)
| Screen | Endpoints | Notes |
|---|---|---|
| **Unified inbox** | aggregate per §5.3 (or `me/approvals` aggregator §8) | Grouped by type; pull-to-refresh; badge counts |
| Approval detail | type-specific GET | Summary card + Approve / Reject(reason) buttons |
| History | filtered lists `status=APPROVED/REJECTED` | Audit of my decisions |

### Warehouse (persona 3)
| Screen | Endpoints | Notes |
|---|---|---|
| Scan & lookup | `inventory/items?barcode=`, `stock/item/{id}` | `mobile_scanner`; show SOH, lot/expiry |
| Stock count | `inventory/counts/{id}/lines/{line}/record`, `/bulk-record` | Scan → enter qty → next |
| GRN receiving | `inventory/receipt-approvals/{id}/approve|reject` | Approve incoming stock at dock |
| Material request | `inventory/material-requests` | Raise from floor |

### Profile
- `auth/me`, logout, app/version, (later) notification settings + tax-info update.

---

## 7. The unified `ApprovalItem` model (heart of persona 2)

All six approvable types share one shape: *fetch pending → show summary → approve/reject
with optional reason*. Model it polymorphically so the inbox is **one screen**, not six.

```dart
enum ApprovalType { leave, expense, cashAdvance, apInvoice, apPaymentBatch, requisition, evaluation }

class ApprovalItem {
  final ApprovalType type;
  final String id;            // entity UUID
  final String title;         // "Leave: Ada O. — 3 days" / "Invoice INV-00421 — ₦48,375"
  final String subtitle;      // requester, date, amount
  final String? amount;       // formatted money where relevant
  final String status;        // SUBMITTED / PENDING_APPROVAL
  final DateTime submittedAt;
  final String requesterName;
  final Map<String, dynamic> raw; // full payload for the detail screen
}

abstract class ApprovalAdapter {
  ApprovalType get type;
  bool canApprove(TokenClaims claims);          // RBAC gate per §5.3
  Future<List<ApprovalItem>> fetchPending(int offset, int limit);
  Future<void> approve(String id);
  Future<void> reject(String id, String reason);
}
```

One adapter per type maps its module endpoint into `ApprovalItem`. The inbox iterates
adapters whose `canApprove(claims)` is true, merges + sorts by `submittedAt`, and renders
a single list. Adding a new approvable later = one new adapter, zero UI changes.

---

## 8. Required backend additions (lean mode = 2 thin endpoints)

Even in lean/polling mode, two small JSON endpoints are needed. Both wrap **existing**
services — no new business logic.

### 8.1 `GET /api/v1/me/notifications` (REQUIRED for polling inbox/badges)
Notifications today exist **only** as an HTML route (`app/web/notifications.py`).
`NotificationService.list_notifications()` already returns the data; expose it as JSON.

```python
# app/api/me.py  (thin wrapper; service already exists)
@router.get("/notifications")
def my_notifications(
    unread_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db_with_org),
):
    org_id = UUID(auth["organization_id"]); person_id = UUID(auth["person_id"])
    items = NotificationService().list_notifications(
        db, organization_id=org_id, recipient_id=person_id,
        unread_only=unread_only, offset=offset, limit=limit + 1,
    )
    has_more = len(items) > limit
    return {"items": [NotificationRead.model_validate(n) for n in items[:limit]],
            "offset": offset, "limit": limit, "has_more": has_more}

@router.post("/notifications/{notification_id}/read")
def mark_read(...):  # wrap NotificationService mark-read
    ...
```
*Add `NotificationRead` Pydantic schema (id, type, title, message, action_url,
entity_type, entity_id, read_at, created_at).*

### 8.2 `GET /api/v1/me/approvals` (RECOMMENDED — makes the inbox trivial)
Optional but high-value: one aggregator that fans out to the per-module pending lists the
caller is permitted to see, returning a uniform `ApprovalItem` JSON. Without it, the
client aggregates across N endpoints (works, but chattier). Build it as a thin web-service
method that calls existing services with status filters and maps to a shared schema.

> Both endpoints are routes-as-thin-wrappers per the project rules — all logic stays in
> services.

---

## 9. ⚠️ Correction: attendance geolocation is NOT captured via `/me`

An earlier scan suggested the attendance API stores lat/long. **Verified caveat:** the
**self-service** endpoint `POST /api/v1/me/attendance/check-in` accepts only
`{ check_in_time, notes }` and passes only those to `AttendanceService.check_in()`
(`app/api/me.py:459-481`). It does **not** capture latitude/longitude.

Geolocation *is* supported by the other attendance API
(`app/api/people/attendance.py` `CheckInRequest` has lat/long), but that endpoint is not
employee-self-scoped the same way.

**RESOLVED → (a).** Extend the `/me/attendance/check-in|out` payloads with optional
`latitude`/`longitude`. Verified smaller than first estimated: the service layer already
accepts both (`AttendanceService.check_in/check_out` kwargs,
`attendance_service.py:730-740, 793-802`) — only the Pydantic payload schemas and the two
route pass-throughs in `app/api/me.py` change (~6 lines total).

---

## 10. Notifications & realtime (lean mode)

- **MVP:** poll `GET /me/notifications?unread_only=true` on app resume + every 60s while
  foregrounded. Drive a badge count and an inbox list.
- **Fast-follow:** FCM/APNs push. Requires a new `device_tokens` table
  (`person_id, token, platform, created_at`), `POST /api/v1/notifications/devices/register`,
  and a Celery hook on notification creation to call FCM. This turns "check the app" into
  "the app tells you" — the natural pairing with persona 2.

---

## 11. Roadmap

| Phase | Ships | Backend |
|---|---|---|
| **MVP-A — Self-Service** | Auth **incl. MFA screen**, attendance **+ GPS**, expense+receipt, leave, payslips, tasks/time | geo pass-through in `me.py` (~6 lines, §9) |
| **MVP-B — Approvals** | Unified inbox (leave/expense/AP/requisition), approve/reject | `me/notifications` (§8.1) + **`me/approvals` aggregator** (§8.2) |
| **MVP-C — Warehouse** | Barcode counts, GRN, material requests, lookup | none |
| **FF-1 — Push** *(committed, starts at MVP-B ship)* | FCM/APNs, device registration | device-token table + Celery hook |
| **FF-2 — Offline** | Draft & sync queue, conflict versioning | optimistic `version` fields |

---

## 12. Repo bootstrap checklist

- [ ] `flutter create dotmac_mobile` (org id, package name)
- [ ] Add deps: `dio`, `riverpod`/`flutter_riverpod`, `go_router`,
      `flutter_secure_storage`, `mobile_scanner`, `image_picker`, `intl`
- [ ] Flavors: dev / staging / prod (base URLs)
- [ ] Generate Dart API client from `GET /openapi.json` → `lib/api/` (wire into CI)
- [ ] `core/dio_client.dart`: auth interceptor (bearer + 401 refresh-retry), error mapping
- [ ] `auth/`: login screen, token store, claims parser, `role_gate`
- [ ] `shared/theme.dart`: port DotMac teal/parchment tokens + status colors
- [ ] Feature scaffolds: self_service, approvals, warehouse
- [ ] `ApprovalItem` + adapters (§7)
- [ ] CI: `flutter analyze`, `flutter test`, build artifacts
- [ ] Backend PR: `me/notifications` JSON endpoint (+ `NotificationRead`); decide geo (§9)

---

## 13. Decisions — RESOLVED 2026-06-10

1. **Attendance geolocation → (a).** Extend `/me/attendance/check-in|out` payloads with
   optional `latitude`/`longitude` and pass through. Verified:
   `AttendanceService.check_in/check_out` already accept both kwargs
   (`attendance_service.py:730-740, 793-802`) — the change is schema + pass-through only
   (~6 lines). Location is captured at clock moments only, never tracked.
2. **MFA → build in MVP-A.** Login already returns `mfa_required`/`mfa_token`; the app
   ships the MFA code screen so enrolled users are never hard-blocked.
3. **Approvals aggregator → build `GET /me/approvals` in MVP-B.** One-request inbox,
   server-side RBAC filtering + counts; payload shape reused for FF-1 push.
4. **Push → FF-1 committed immediately after MVP-B.** FCM/APNs + `device_tokens` table +
   register endpoint + Celery hook on notification creation.
5. **Deployment/branding → single hosted base URL, DotMac brand.** Base URL baked per
   flavor (dev/staging/prod); teal/parchment theme as tokens; post-login org logo/accent
   skinning is a later enhancement (branding service already exists:
   `app/services/finance/branding.py`). No white-label builds, no server-address field.
