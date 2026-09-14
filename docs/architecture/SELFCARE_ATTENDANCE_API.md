# Selfcare Attendance API Boundary

Status: implemented. Production activation is controlled by Selfcare connector
capability bindings, ERP service-principal scopes, and employee eligibility;
it is not a background-job rollout flag.

ERP is the authoritative attendance system. The service-to-service routes
`GET /api/v1/sync/sub/attendance/today`,
`POST /api/v1/sync/sub/attendance/check-in`, and
`POST /api/v1/sync/sub/attendance/check-out` are narrow adapters around
`app.services.people.attendance.AttendanceService`; they are not generic HR
CRUD endpoints.

The API key's service principal supplies the organization and RLS context and
must explicitly carry `sub:attendance:read` or `sub:attendance:write`. Legacy
unscoped keys are rejected. `X-Selfcare-Subject` is a trusted server-to-server
Selfcare `SystemUser.id`; ERP resolves it to exactly one active employee with
matching `Employee.dotmac_sub_account_id` and enabled Selfcare access in that
organization. The resolver never falls back to email.

## Production prerequisites

Every condition below is required:

- Selfcare has an enabled, version-pinned `dotmac.erp` installation with
  enabled `workforce.attendance.read.v1` and
  `workforce.attendance.punch.v1` bindings.
- The ERP service principal has `sub:attendance:read` for the today route and
  `sub:attendance:write` for both punch routes. Legacy unscoped keys are not
  accepted.
- The authenticated Selfcare user has the local `attendance:self:use`
  permission.
- Exactly one active ERP employee in the service principal's organization has
  `dotmac_sub_account_id` equal to the Selfcare `SystemUser.id`.
- That employee has `dotmac_sub_access_enabled = true`.

The stable response codes identify the corrective action:

- `employee_not_linked` (404): create the subject-to-employee mapping.
- `employee_mapping_ambiguous` (409): leave exactly one matching employee.
- `employee_inactive` (403): reactivate the mapped employee or remove the
  Selfcare permission until the employee is eligible.
- `attendance_disabled` (403): enable Selfcare access on the mapped employee
  or remove the Selfcare permission.
- `authorization_failed` (403): correct the API key's explicit scope or
  organization context.

If one service credential succeeds for other subjects, a per-subject 403 is an
employee eligibility failure rather than evidence that the credential lacks a
global scope. Operators must not create an email fallback to hide missing
identity mappings.

## Rollout and verification workflow

1. Keep the connector installation and both attendance capability bindings
   enabled while checking an eligible reference subject. A successful today
   response proves the read path without changing attendance state.
2. Verify the write scope with an authorized, non-mutating contract check or a
   controlled punch for an eligible test employee. Do not use production staff
   attendance as an ad hoc probe.
3. Reconcile each `employee_not_linked`, `employee_mapping_ambiguous`,
   `employee_inactive`, or `attendance_disabled` response in ERP using the
   corrective actions above. Mapping and employee-access changes do not require
   an application deployment.
4. Recheck corrected subjects from Selfcare and verify that the previous stable
   response code no longer recurs in logs. Do not add email fallback identity or
   broaden the service principal's scope to mask a per-subject eligibility
   failure.

Repository source changes follow the normal immutable release workflow: merge
the feature PR with the required version-impact label, allow automation to own
version files, promote the resulting image digest to staging, and complete
staging acceptance before production promotion. Documentation-only changes use
`version:none` and do not edit version files.

Punch bodies contain only latitude, longitude, optional accuracy, and optional
browser observation time. ERP server time, organization timezone, shift,
geofence, status, lateness, early exit, and persistence remain owned by
`AttendanceService`. Mutations require `Idempotency-Key`, share a punch keyspace
across actions, and bind the request hash to action, subject, and location.

Canonical checkout locks the daily attendance row and returns an existing
completed checkout without rewriting its timestamp or working hours. Selfcare
punches carry `marked_by=SELFCARE` for check-in and explicit audit evidence that
omits precise coordinates from generic audit metadata.

The v1 adapter rejects overnight shifts and prior-day open overnight attendance
with `overnight_shift_not_supported`. Correct next-morning overnight checkout is
a separate ERP domain change and must precede lifting that pilot exclusion.
