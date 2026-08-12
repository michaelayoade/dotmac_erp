# Sub operational sync contract

`POST /api/v1/sync/sub/bulk` accepts idempotent projections of projects,
tickets, project tasks, and work orders. The authenticated service credential
must carry `sub:domain:write` (the legacy `crm:sync:write` scope remains a
transition-only compatibility grant).

Processing order is projects, tickets, project tasks, then work orders. Project
tasks resolve their project and optional ticket by the source identifiers sent
in the same or an earlier request. ERP records task identity in `sync.sync_entity`
under `(organization_id, dotmac_sub, sub_project_task, source_id)` and updates
the same `pm.task` on replay.

Responses declare `contract_version: 2`. Selfcare requires that version before
advancing a project-task watermark, preventing silent loss during a staggered
deployment.

ERP owns finance and resource use of the projection. Selfcare remains the
authority for the source workflow and ERP must not write status back to it.
