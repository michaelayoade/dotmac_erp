# Selfcare Attendance API Boundary

Status: implemented locally; production rollout disabled.

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
