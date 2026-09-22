# ERP Calendar Notifications

## Decision

Dotmac ERP is the sole owner of organizational and personal ERP calendar
events. Nextcloud Calendar receives no copy and is not a projection or decision system for
these records. Nextcloud Talk is a delivery channel only.

When ERP publishes, changes, cancels, or reaches a configured reminder time for
an event, ERP creates durable in-app and email notification records for the
affected employees. The email worker sends each record through the configured
People/Payroll email profile. Employees with a mapped Nextcloud identity also
receive a Talk message. Actionable email and Talk messages link to the event
or My Calendar in ERP. ERP authentication, tenant scope, and participant
visibility are checked again when that link is opened.

Personal events created directly in Nextcloud remain outside ERP. ERP neither
reads nor modifies Nextcloud calendars.

## Ownership and delivery

- `OrganizationCalendarService` owns event state, participant membership,
  cancellation, reminder definitions, and the decision to notify.
- `public.notification` is the durable queue and delivery record for email and Talk.
- `process_due_calendar_reminders` is the only writer of reminder consequences.
  It locks each due reminder once, creates participant notifications in the
  same database transaction, and records that the reminder was dispatched.
- `process_pending_notification_emails` owns email delivery and its retry state.
- `process_pending_nextcloud_notifications` owns Talk delivery. It resolves the
  employee's stored Nextcloud user ID, opens or reuses a one-to-one Talk
  conversation, sends the message, and records provider acceptance.
- Nextcloud Talk transports the message. It does not own event state and does
  not decide who may see an event.
- Dotmac Integrator and CalDAV are not in this path. Retired calendar commands
  are settled without delivery so delayed pre-cutover work cannot restore the
  old projection.

## User behaviour

- Publishing an event sends each selected participant an email and in-app alert.
- Direct, department, and designation selections resolve to unique, eligible
  participants when the event is saved.
- Adding a participant sends that person a new-event message.
- Editing a published event sends active participants an update message.
- Removing a participant sends that person a removal message linking to My
  Calendar rather than an event they can no longer open.
- Cancelling sends former participants a cancellation message linking to My
  Calendar.
- Each configured reminder sends active participants its own email and in-app
  alert at the selected time, with wording for that interval (for example, one
  week or one day before).
- An edit does not repeat a reminder already dispatched for the same event time;
  reminder periods already past are not sent retroactively.
- Event emails include the title, date and time with time zone, optional location
  and details, and an absolute link back to ERP.
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

The workers turn ERP-relative action paths into absolute links using `APP_URL`.
Talk delivery accepts only ERP-relative action paths, so a notification record
cannot turn Talk into a link to an unrelated site.

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
2. Configure an active People/Payroll email profile or working SMTP fallback.
3. If Talk delivery is desired, configure the Notifications domain with the
   Nextcloud server, dedicated Talk sender, and its secret-store-backed app
   password.
4. Keep employee-to-Nextcloud identity mappings current when Talk delivery is desired.
5. Run the reminder, email, and Talk delivery jobs every minute.

## Acceptance gate

Before enabling calendar use broadly, prove in staging that:

1. A directly selected employee and employees selected through department or
   designation each receive one new-event email with the event facts and link.
2. An unselected employee receives nothing.
3. One-week and one-day selections each produce a separate email at their
   scheduled time, with the correct interval and event facts.
4. The email link opens the correct ERP event after login.
5. Another employee cannot open that event by copying the link.
6. Updates, participant additions/removals, and cancellations each produce
   the expected single notification.
7. Re-running the reminder and email workers does not duplicate an accepted
   email.
8. Editing an event with no meeting link succeeds without supplying one.
9. If Talk is enabled, a selected employee receives one Talk message; a
   missing Nextcloud identity does not block email or ERP access.

Production activation requires a working email profile and completion of this
staging gate. Talk activation also requires a dedicated sender,
secret-store-backed credentials, healthy delivery, and verified employee
mappings.
