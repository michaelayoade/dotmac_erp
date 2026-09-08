# Admin automation

Automation is a cross-module Admin capability. Access is controlled by the
`automation:*` permissions; Finance permissions do not grant it implicitly to
new roles.

## Execution contract

- Registered business models emit create, update, delete, field-change,
  threshold, status, approval, rejection, and overdue events immediately before
  their transaction commits. Explicit events from domain services take
  precedence and are de-duplicated.
- Blocking and validation actions run in the caller transaction and can reject
  the change. Other actions are written to `platform.event_outbox` and handled
  after commit with bounded retries and dead-letter visibility.
- Scheduled, due-date, and overdue rules are evaluated every five minutes.
  `schedule_config.max_entities` defaults to 5,000 and is capped at 50,000.
- Failed/dead async requests can be replayed from **Admin → Automation →
  Workflows → Monitoring**. Replay is tenant checked and permission guarded.

The `/automation/capabilities` endpoint is the source of truth for UI clients.
Only entity types with a model registry entry are returned.

## Entity configuration

Organization administrators can enable or disable registered connectors in
**Admin â†’ Automation â†’ Entity Configuration**. A missing configuration row
means enabled, preserving existing behavior for every organization after the
migration. Disabling an entity:

- stops immediate, queued, and scheduled workflow actions for that entity;
- removes it from new workflow and custom-field forms;
- leaves rules, definitions, assignments, execution history, and stored custom
  field values intact.

Enabling the entity makes the retained configuration available again. The UI
cannot register arbitrary models or table names: it only controls the
application's verified entity registry.

## Custom fields

Definitions are created in **Admin → Automation → Custom Fields**. Values live
in `automation.custom_field_value`, not in individual module tables. That keeps
business schemas stable while providing tenant, entity, and field indexes.

Module forms can load `/automation/fields/render/{entity_type}?entity_id=...`
as an HTML fragment, or use `/automation/fields/schema/{entity_type}` for the
typed JSON contract. Submit values to
`/automation/fields/values/{entity_type}/{entity_id}`. The service validates the
entity belongs to the current organization, applies defaults and type rules,
then emits `ON_FIELD_CHANGE` with changed names such as
`custom.service_tier`.

Use `custom.<field_code>` in workflow conditions. The
`UPDATE_CUSTOM_FIELD` action uses the same validation and event path, so API,
form, and automation writes behave consistently. Definitions with stored
values can be deactivated but cannot be hard-deleted.

## Assignment rules

The `ASSIGN` action supports a specific person, the entity owner, or a role.
`DIRECT` chooses deterministically; `LEAST_LOADED` uses active assignments in
the organization. Results are stored in `automation.entity_assignment`, giving
all modules one ownership contract without bypassing their state machines.

## Adding an entity

1. Add its enum value and model/primary-key entry to `entity_registry.py`.
2. Add the same value to `CustomFieldEntityType` if custom fields are supported.
3. Add an additive PostgreSQL enum migration.
4. Add a module label in `capabilities.py` and a UI label in `web.py`.
5. Keep protected state-machine fields in `_PROTECTED_FIELDS`; automation must
   call the owning domain service instead of writing them generically.
6. Update the connector parity tests before exposing the entity in Admin.

Once deployed, each organization can enable or disable that supported entity
without another migration. A genuinely new model connector still requires the
code and enum steps above.

Apply migrations through `20260911_automation_entity_configuration` before
starting web or worker processes with this feature stack.
