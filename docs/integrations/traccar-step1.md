# Traccar Step 1 boundary

ERP remains authoritative for vehicles, employees, permissions and Fleet
workflows. Traccar will own historical positions, trips, route playback, raw
telemetry and tracker events. Step 1 adds no ERP position-history table and
makes no Traccar network call.

## Identifier ownership

- `fleet.vehicle_tracker.unique_id` is the tracker IMEI / Traccar `uniqueId`.
- `fleet.vehicle_tracker.external_device_id` is Traccar's internal device ID.
- The values are never interchangeable.

Only active mappings are unique. Unlinking a tracker ends the assignment and
retains the row, allowing a physical tracker to be assigned elsewhere later
without losing mapping history.

## Existing Fleet GPS fields

The existing `fleet.vehicle.has_gps_tracker`, `gps_device_id`,
`last_known_location` and `last_location_update` columns remain in place.
Their historical values are not bulk migrated because `gps_device_id` has no
verified repository-wide IMEI semantics.

For mappings created after Step 1, the service sets `has_gps_tracker` and
mirrors the known IMEI into an empty `gps_device_id`. It never overwrites a
different non-empty legacy value. Updates mirror a changed IMEI only when the
legacy field is empty or still matches the prior mapping. Unlinking marks the
legacy flag inactive only when the legacy identifier is empty or matches the
mapping being unlinked.

Before a future backfill, existing `gps_device_id` values must be classified
and validated against the Traccar device inventory. Ambiguous values remain
unchanged for manual resolution.

## Connector activation

`TraccarClient` is a fail-closed, transport-injected boundary. It defines
server-side authentication, timeout and structured error behavior, but Step 1
does not provide an HTTP or WebSocket adapter. This preserves the repository's
Integrator-only external-connector governance while keeping Fleet code behind
one client interface. Credentials are resolved server-side and are absent from
Fleet schemas, templates and API responses.
