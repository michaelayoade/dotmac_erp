# Email delivery content retention

**Policy (Michael, 2026-09-29):** keep tenant-private email bodies, recipient
fields and attachments for 30 days after the delivery event settles as
`PUBLISHED` (sent) or `DEAD` (terminal failure). Pending and retryable failed
events are still actionable and are not eligible for content deletion.

`platform.event_outbox` is the durable delivery owner. It holds only a random
`delivery_id` reference and organization identity for email commands; rendered
content lives in `public.email_delivery` under forced tenant RLS. The current
`app_user` role has no schema or table grant on the shared, non-RLS outbox.
The relay stages terminal settlement, and a database trigger stamps
`terminal_at` from the database clock on the transition to `PUBLISHED` or
`DEAD`. An `app_user` UPDATE cannot backdate it. Replaying a dead event clears
the terminal clock; a later terminal failure starts a fresh 30-day window.
Once private content is purged, both replay APIs reject the event. A published
event also retains
`published_at` for the general outbox lifecycle. Historical
dead events without a reliable terminal timestamp are not guessed into the
retention window.

`app.tasks.outbox_relay.cleanup_terminal_email_deliveries` runs hourly at
minute 20, up to 5,000 terminal candidates per run. It discovers candidates
from the non-RLS outbox, then opens one tenant session per event, locks and
rechecks the event, and deletes its private row. The database's restrictive
DELETE policy calls an `app_admin`-owned security-definer predicate that returns
only a yes/no retention decision for the current tenant and matching event.
PUBLIC cannot execute it, and it gives `app_user` no direct outbox access. The
policy refuses a delete before 30 days or outside the tenant. For a published
event, the task deletes the outbox row in the same transaction, so neither
record can be removed alone. A dead event keeps its outbox error/status
evidence and records `email_payload_purged_at` to prevent repeat work. The
database trigger refuses deletion of DEAD email events and
of PUBLISHED email events before their retention window. Invalid references
remain queued for investigation; the task reports an `invalid` count and logs
only the event UUID.

The general `cleanup_published_outbox_events` task excludes email events. Its
2:30 daily schedule cannot erase an email reference before the private row is
purged, even when hourly email cleanup is delayed. A failed email cleanup
transaction rolls back both private deletion and published-event deletion.

## Read-only checks

The task result reports `purged`, `published_deleted`, `invalid` and `locked`.
Investigate a nonzero `invalid` count and a growing eligible backlog. A
read-only database check can count matured, unpurged commands without reading
any email content:

```sql
SELECT status, count(*)
FROM platform.event_outbox
WHERE event_name = 'email.delivery.requested'
  AND status IN ('PUBLISHED', 'DEAD')
  AND terminal_at < now() - interval '30 days'
  AND email_payload_purged_at IS NULL
GROUP BY status;
```

A backlog can result from a stopped scheduler, failed tenant mapping, invalid
reference, or an outbox event held by another cleanup worker. Resolve the
cause and rerun the existing task; do not delete or alter outbox rows manually
to make the count pass. The PostgreSQL integration canaries cover tenant
visibility, rollback, unique-key contention, timestamp forgery and the
restrictive DELETE policy.

The new outbox relay logs only its event UUID and exception class on SMTP
failure. The older synchronous `send_email` callers still log recipient and
raw exception text; migrate those callers with their outcome contracts.
