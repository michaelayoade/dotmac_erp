# ERP Calendar and Nextcloud Talk Notifications

## Decision

Dotmac ERP is the sole owner of organizational and personal ERP calendar
events. Nextcloud Calendar receives no copy and is not a projection or decision system for
these records. Nextcloud Talk is a delivery channel only.

When ERP publishes, changes, cancels, or reaches a configured reminder time for
an event, ERP creates durable notification records for the affected employees.
The existing notification worker sends each record to the employee's mapped
Nextcloud Talk identity. Every actionable message contains an absolute link to
the event in ERP. ERP authentication, tenant scope, and participant visibility
are checked again when that link is opened.

Personal events created directly in Nextcloud remain outside ERP. ERP neither
reads nor modifies Nextcloud calendars.

## Ownership and delivery

- `OrganizationCalendarService` owns event state, participant membership,
  cancellation, reminder definitions, and the decision to notify.
- `public.notification` is the durable queue and delivery record for Talk.
- `process_due_calendar_reminders` is the only writer of reminder consequences.
  It locks each due reminder once, creates participant notifications in the
  same database transaction, and records that the reminder was dispatched.
- `process_pending_nextcloud_notifications` owns Talk delivery. It resolves the
  employee's stored Nextcloud user ID, opens or reuses a one-to-one Talk
  conversation, sends the message, and records provider acceptance.
- Nextcloud Talk transports the message. It does not own event state and does
  not decide who may see an event.
- Dotmac Integrator and CalDAV are not in this path. Retired calendar commands
  are settled without delivery so delayed pre-cutover work cannot restore the
  old projection.

## User behaviour

- Publishing an event sends each selected participant a Talk message.
- Adding a participant sends that person a new-event message.
- Editing a published event sends active participants an update message.
- Removing a participant sends that person a removal message linking to My
  Calendar rather than an event they can no longer open.
- Cancelling sends former participants a cancellation message linking to My
  Calendar.
- Each configured reminder sends active participants a reminder message.
- Drafts never notify.
- Event messages link to the matching My Calendar record in ERP.
- Talk and device notification settings remain under the employee's control;
  ERP records provider acceptance, not proof that a person read the message.

## Configuration, not hardcoding

No deployment address, account, credential, or employee identity is embedded
in the code.

- ERP's public origin comes from `APP_URL`.
- Nextcloud's origin comes from the Notifications domain setting
  `nextcloud_server_url`.
- The dedicated Talk sender comes from `nextcloud_username`.
- Its app password comes from the approved secret-backed
  `nextcloud_password` setting.
- Each recipient comes from the employee's verified `nextcloud_user_id`.
- Reminder times come from each event and its selected offsets.

The worker accepts only ERP-relative action paths and combines them with the
configured absolute `APP_URL`. This prevents a notification record from
turning Talk into a link to an unrelated site.

## Identity and security

Eligible participants must have an active employee and person record plus a
valid work email. A Nextcloud identity is optional; when present it enables
Talk delivery, but it never blocks ERP calendar participation or in-app alerts.

The Talk sender uses a dedicated low-privilege Nextcloud account and an app
password. The credential belongs in the approved secret store and must never be
committed, written to project files, or reused for employee provisioning.

## Calendar behaviour retained in ERP

- Permission-gated organizational month view, details, creation, editing, and
  cancellation.
- Self Service My Calendar with strict owner and participant visibility.
- Participant search, Add Everyone, employment eligibility checks, stable event
  IDs, audit history, and version conflict protection.
- Timed events stored as UTC plus their named time zone; all-day events use
  date-only start and exclusive end dates.
- Up to three reminders per event.
- Recurrence is intentionally excluded from release 1.

## Required setup

1. Configure the deployment's public ERP origin.
2. Configure the Notifications domain with the Nextcloud server, dedicated
   Talk sender, and its secret-store-backed app password.
3. Keep employee-to-Nextcloud identity mappings current when Talk delivery is desired.
4. Run the reminder and Talk delivery jobs every minute.

## Acceptance gate

Before enabling calendar use broadly, prove in staging that:

1. A selected employee receives a one-to-one Talk message for a new event.
2. An unselected employee receives nothing.
3. The message link opens the correct ERP event after login.
4. Another employee cannot open that event by copying the link.
5. Updates, participant additions/removals, cancellations, and reminders each
   produce the expected single message.
6. Re-running either worker does not duplicate an accepted notification.
7. A missing Nextcloud identity remains visible as an undelivered record and
   does not weaken ERP access rules.
8. Talk failure does not roll back or alter the authoritative ERP event.

Production activation requires a dedicated Talk sender, secret-store-backed
credentials, healthy Talk delivery, verified employee mappings, and completion
of this staging gate.
