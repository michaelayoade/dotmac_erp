# ERP Organizational Calendar: Implementation and Deployment Plan

## Decision

Dotmac ERP is the source of truth for organizational events. Dotmac Integrator
is the only component that should hold the dedicated Nextcloud calendar service
account and perform CalDAV/iCalendar operations. Nextcloud displays the event,
maintains attendee scheduling copies, and delivers reminders.

Personal events created directly in Nextcloud remain outside this integration.
ERP neither reads nor modifies those calendars. Employees may also create
private ERP events from Self Service My Calendar; those records are visible in
ERP only to their creator and explicitly invited participants and synchronize
through the same controlled event contract.

## Implemented in this ERP branch

### Organizational Calendar workspace

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
  stable UID, and version.
- Central synchronization-issues page at `/admin/calendar/sync-issues` with
  failed service-hook deliveries, safe errors, identity-readiness exclusions,
  and permission-gated retry actions.

The workspace is not restricted to hard-coded Admin or Finance role names.
Access, creation, participant management, editing, cancellation, audit, and
retry actions are independently permission-gated, so any approved role can be
configured to use the appropriate capabilities.

### Self Service My Calendar

- A My Calendar card and month view use the existing People Self Service shell,
  navigation, colours, cards, spacing, and responsive layout.
- Employees see only published organizational events where they are active
  participants, personal events they own, and personal events to which they
  were explicitly invited.
- Employees with `calendar:personal:create` can create, update, and cancel their
  own personal events. `calendar:personal:invite` independently controls
  whether they can invite other eligible employees.
- The creator is always retained as a participant. An invited employee has
  read-only access; only the creator can edit or cancel the personal event.
- Organizational calendar users, including administrators, receive no
  application-level override to view another employee's unshared personal
  event.
- Publishing an organizational event creates an ERP in-app notification for
  each newly involved participant. The notification opens the assigned event
  in My Calendar. Drafts do not notify, and an ordinary update does not send a
  duplicate notification to an unchanged participant.

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
- `calendar:personal:access`
- `calendar:personal:create`
- `calendar:personal:invite`

The migration provides initial grants for existing Admin, Finance Manager,
Finance Director, and Employee roles. These are defaults only, not hard-coded
authorization rules. Administrators can grant the explicit permissions to any
approved role. Employee receives assigned-event read and My Calendar defaults.

Every route enforces permissions server-side. Menu visibility is not treated as
authorization. Every database query is tenant-scoped and the four calendar
tables have forced PostgreSQL row-level security.

### Data and lifecycle controls

- Calendar tables own business intent and participant membership only. The
  existing outbox and service-hook execution records are the sole owners of
  queue and delivery state; calendar rows do not duplicate that state.
- Participant membership uses `ACTIVE`, `REMOVED`, and `CANCELLED`.
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
an Integrator idempotency key. A terminal webhook failure remains in the
canonical service-hook execution log and is surfaced as a retryable delivery
issue. Retrying emits a fresh desired-state command; ERP does not maintain a
parallel delivery checkpoint or accept a result callback.

The payload also carries `event_scope` so the connector preserves the privacy
boundary between organizational and personal ERP events. ERP delivery status
is derived from the canonical service-hook execution log rather than a separate
calendar synchronization checkpoint.

ERP never stores the Nextcloud calendar service-account password.

### Required ERP service-hook configuration

ERP needs one active `WEBHOOK` service hook for each calendar event name above.
Configure the hooks as asynchronous, use `payload_only: true`, and point them
to the Integrator calendar receiver. Store the shared signing value in the ERP
runtime environment as `ERP_INTEGRATOR_CALENDAR_SECRET`; the hook configuration
must reference the environment-variable name and must never contain the secret
itself. The Integrator hostname must also be present in the platform outbound
webhook allowlist before the hook can send commands.

The Integrator must verify `X-Dotmac-Signature` and deduplicate on
`X-Dotmac-Delivery` and the payload `idempotency_key`. The existing `erp-employees` account
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
10. Keep remote resource identity, ETag, participant outcomes, and safe operator
    diagnostics in the Integrator, which owns CalDAV execution state. Never
    expose credentials or raw sensitive responses.
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
2. Repair Nextcloud background jobs and create the dedicated service account.
3. Configure the ERP service hook to the Integrator calendar receiver.
4. Run the mandatory prototype gate with two employees.
5. Run authorization, tenant isolation, optimistic concurrency, stale-job,
   duplicate, cancellation, all-day/time-zone, offboarding, and failure/recovery
   tests.
6. Load-test Add Everyone before choosing production attendee and worker limits.
7. Pilot with a small permission-approved group and monitor queue age, failed events,
   participant failures, 401/403, 429, 5xx, latency, and Nextcloud cron health.
8. Enable broader use only after the pilot acceptance criteria pass.

## Production acceptance blockers

The ERP implementation can be deployed dark, but event publication must remain
operationally disabled until background jobs are healthy, `erp-calendar` exists,
identity reconciliation is complete, automatic attendee appearance is proven,
and the Integrator consumer path has passed end-to-end testing.
