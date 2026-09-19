# Mobile push delivery state

Apply the additive Alembic revision before starting workers with this code.
`push_sent` now means at least one FCM send was reported successful; it does not
prove the handset received or displayed it. `push_status` distinguishes sent,
partial, no_device, retry, failed, expired and legacy_processed records.

Historical true flags cannot establish delivery and are backfilled as
legacy_processed without replay. Old records are expired after 24 hours without
setting a new successful-send flag. Zero sends and exceptions receive three
bounded retries (5 minutes, 15 minutes, 1 hour), then become terminal failures.
A partially accepted fan-out is terminal to avoid re-sending to accepted devices.
A process crash after external acceptance but before database commit can still
cause redelivery; this change does not claim exactly-once external delivery.

Push batches now use the same explicit organization discovery and scoped-session
pattern as email dispatch. Existing service return types, device registration,
FCM configuration and in-app notification availability are unchanged.

Downgrade restores old terminal suppression flags to avoid broadcasting already
processed records. Configure or repair real device registrations separately;
this patch does not create tokens, send test pushes, or change credentials.
