# ERP Organization Calendar: Implementation and Deployment Plan

## Decision

Dotmac ERP is the source of truth for organizational events. Dotmac Integrator
is the only component that should hold the dedicated Nextcloud calendar service
account and perform CalDAV/iCalendar operations. Nextcloud displays the event,
maintains attendee scheduling copies, and delivers reminders.

Personal events created by employees in Nextcloud are outside this integration.
ERP neither reads nor modifies personal calendars.

## Implemented in this ERP branch

### Admin calendar workspace

- Permission-gated month view at `/admin/calendar` using the existing ERP admin
  design system.
- Previous month, Today, and next month navigation.
- Month grid with compact coloured event chips. Clicking a chip opens the event
  detail page.
- Event sidebar with participant counts and permission-aware Edit and Delete
  actions.
- Create and edit forms for title, description, additional details, location,
  meeting link, timed/all-day dates, time zone, colour, reminders, and
  participants.
- Employee search, Add Everyone, Clear, eligible/excluded counts, and exclusion
  reasons.
- Up to three reminders per event.
- Event detail page with participants, reminder configuration, business state,
  synchronization state, stable UID, version, and remote ETag when available.
- Central synchronization-issues page at `/admin/calendar/sync-issues` with
  participant-level state counts, safe errors, identity-readiness exclusions,
  last remote synchronization time, and permission-gated retry actions.

### Permissions

The migration provisions explicit permissions:

- `calendar:events:access`
- `calendar:events:read_assigned`
- `calendar:events:read_all`
- `calendar:events:create`
- `calendar:events:update_own`
- `calendar:events:update_all`
- `calendar:events:cancel_own`
- `calendar:events:cancel_all`
- `calendar:participants:manage`
- `calendar:participants:add_all`
- `calendar:sync:retry`
- `calendar:audit:read`

Admin roles receive all calendar permissions. Finance Manager and Finance
Director receive calendar access, create, read-all, manage-own, participant, and
Add Everyone permissions. The employee role receives assigned-event read
permission for the later Self Service calendar surface.

Every route enforces permissions server-side. Menu visibility is not treated as
authorization. Every database query is tenant-scoped and the five calendar
tables have forced PostgreSQL row-level security.

### Data and lifecycle controls

- Business state (`DRAFT`, `PUBLISHED`, `CANCELLED`) is separate from
  integration health (`NOT_REQUIRED`, `PENDING`, `SYNCING`, `SYNCED`,
  `PARTIAL_FAILURE`, `FAILED`).
- Participant membership (`ACTIVE`, `REMOVED`, `CANCELLED`) is separate from
  participant synchronization state.
- Published events are cancelled, not hard-deleted. Cancellation and all other
  material changes are retained in a durable calendar audit table.
- Each event has a stable UID and monotonically increasing version.
- Edit forms use optimistic version checks. A stale browser edit is rejected.
- Timed events are stored as UTC instants plus their IANA time zone. All-day
  events use date-only start and exclusive-end fields, preventing date shifts.
- Participant identities are snapshotted at publication time.
- Add Everyone creates an explicit snapshot. Employees hired later are not
  silently added to an existing event.
- Eligible employees must have active employee and person records, no completed
  employment exit, a valid work email, and an ERP-bound Nextcloud user ID.
  Known failed Nextcloud provisioning identities are excluded.
- Offboarding removes the employee from future ERP calendar events before the
  Nextcloud account is disabled and queues updated desired state for published
  events.

### Integrator boundary

Publishing or changing an event writes one of these versioned business events
to the existing ERP transactional outbox in the same database transaction:

- `organization.calendar.upserted`
- `organization.calendar.cancelled`

The ERP outbox relay performs a current-version check before sending a command
through the existing service-hook boundary. A stale queued version is settled
without being sent. The payload action is `UPSERT_EVENT` or `CANCEL_EVENT` and
contains the stable UID, business event version, date/time representation,
reminders, the complete desired participant membership, a correlation ID, and
an Integrator idempotency key. ERP marks a command `SYNCING` only after an
active webhook execution accepts it. A missing hook, exhausted async delivery,
or terminal webhook failure is surfaced as a retryable ERP synchronization
failure instead of leaving the event indefinitely in flight.

Dotmac Integrator reports outcomes to:

```text
POST /api/v1/sync/calendar/events/{event_id}/result
Required service scope: calendar:sync:write
```

The result is rejected as stale when its `event_version` is not the current ERP
version. Valid results can update event and participant synchronization states,
the organizer-side Nextcloud event URL, calendar URI, ETag, and last sync time.

ERP never stores the Nextcloud calendar service-account password.

### Required ERP service-hook configuration

The existing callback endpoint does not need to be replaced. The Integrator
needs a tenant-bound API key carrying only `calendar:sync:write`, while ERP
needs one active `WEBHOOK` service hook for each calendar event name above.
Configure the hooks as asynchronous, use `payload_only: true`, and point them
to the Integrator calendar receiver. Store the shared signing value in the ERP
runtime environment as `ERP_INTEGRATOR_CALENDAR_SECRET`; the hook configuration
must reference the environment-variable name and must never contain the secret
itself. The Integrator hostname must also be present in the platform outbound
webhook allowlist before the hook can send commands.

The Integrator must verify `X-Dotmac-Signature`, deduplicate on
`X-Dotmac-Delivery` and the payload `idempotency_key`, and return the same
`correlation_id` in its result callback. The existing `erp-employees` account
may continue employee provisioning, but its Nextcloud credentials must not be
placed in ERP or used directly by this webhook path.

## Explicit release-one scope

- Recurrence is intentionally excluded from release 1. This avoids shipping
  ambiguous "this occurrence / future occurrences / entire series" semantics.
- A maximum of three reminders is supported.
- Attendee RSVP responses remain Nextcloud-only. ERP does not infer attendance.
- One logical organizer event is sent with a full attendee set. The Integrator
  must not split a large event into unrelated event resources merely to batch
  participants.

## Work required in Dotmac Integrator

1. Add consumers for `organization.calendar.upserted` and
   `organization.calendar.cancelled` service-hook deliveries.
2. Reject a command when its version is older than the last applied version for
   the ERP event.
3. Generate standards-compliant iCalendar with one stable `UID`, organizer,
   attendees, explicit time zone/all-day semantics, and one `VALARM` per ERP
   reminder.
4. Apply create/update/cancel operations to Nextcloud CalDAV and retain the
   organizer resource URL and ETag.
5. Make operations idempotent by ERP event ID, UID, event version, and action.
6. Use retry delays of immediate, 30 seconds, 2 minutes, 10 minutes, 1 hour, and
   6 hours for network failures, HTTP 429, and HTTP 5xx responses. Respect
   `Retry-After` when provided.
7. Treat invalid payloads/users and HTTP 401/403 as intervention-required rather
   than retrying indefinitely.
8. For HTTP 412, fetch the remote event, preserve Nextcloud-owned attendee
   scheduling state, reapply the current ERP-owned fields with the new ETag, and
   retry once. Return `REMOTE_CONFLICT` if that merge still fails.
9. For an unexpected 404, verify the organizer calendar and recreate the event
   only when ERP's desired state says it must exist.
10. Return participant-level results and safe operator messages through the ERP
    result endpoint. Never return credentials or raw sensitive responses.
11. Apply configurable rate limiting and worker concurrency. Start performance
    testing at 25 attendee-processing units per controlled batch while retaining
    one logical event.

## Work required in Nextcloud

1. Repair and monitor background jobs. Calendar reminders cannot be accepted
   while the server scheduler is not running reliably.
2. Correct `.well-known/caldav` discovery while retaining the direct DAV endpoint
   `https://next.dotmac.ng/remote.php/dav/`.
3. Create a dedicated `erp-calendar` account with an app password and only the
   calendar access needed for this connector. Do not reuse the employee
   provisioning account.
4. Confirm in-app and email reminder delivery; confirm push only if it is an
   approved requirement.
5. Confirm calendar server limits for attendee count, request size, scheduling
   fan-out, and rate limiting.

## Mandatory prototype gate

Do not enable production event publication until a real Nextcloud test proves:

1. Employee A automatically receives an organizer-created attendee event.
2. Employee B does not receive it until added.
3. Updating the ERP event updates the same calendar item without duplication.
4. Adding B delivers the event to B.
5. Removing A removes or cancels A's active attendee copy without cancelling B.
6. Multiple reminders are delivered by the repaired background scheduler.
7. Cancellation removes/cancels participant copies.
8. Replaying the same UID/version does not create duplicates.
9. The organizer resource URL and ETag are stable and 412 behaviour matches the
   policy above.
10. Personal events created by A and B cannot be read through the ERP or
    Integrator path.

If attendee scheduling does not automatically place events in participant
calendars, pause rollout and approve a shared service-owned organizational
calendar fallback before changing the schema or synchronization model.

## Deployment order

1. Apply the ERP migration and verify permissions/RLS.
2. Configure a tenant-bound Integrator API key with only
   `calendar:sync:write` for result callbacks.
3. Repair Nextcloud background jobs and create the dedicated service account.
4. Configure the ERP service hook to the Integrator calendar receiver.
5. Run the mandatory prototype gate with two employees.
6. Run authorization, tenant isolation, optimistic concurrency, stale-job,
   duplicate, cancellation, all-day/time-zone, offboarding, and failure/recovery
   tests.
7. Load-test Add Everyone before choosing production attendee and worker limits.
8. Pilot with a small Admin/Finance group and monitor queue age, failed events,
   participant failures, 401/403, 429, 5xx, latency, and Nextcloud cron health.
9. Enable broader use only after the pilot acceptance criteria pass.

## Production acceptance blockers

The ERP implementation can be deployed dark, but event publication must remain
operationally disabled until background jobs are healthy, `erp-calendar` exists,
identity reconciliation is complete, automatic attendee appearance is proven,
and the Integrator consumer/result callback path has passed end-to-end testing.
