# Self-Service Info Change Batches

## Scope

Employee self-service bulk submission applies to:

- qualifications
- certifications
- skills
- dependants
- documents

Bulk submission is create-only. Existing-record edits remain one-at-a-time.

## Ownership

- Self-service web routes stay thin and only adapt authenticated employee input.
- `InfoChangeService` owns batch creation, duplicate checks, conflict checks, approval application, and evidence cleanup.
- HR approval remains item-authoritative through `EmployeeInfoChangeRequest.status`.
- Batch status is derived from child request statuses. There is no separate mutable batch status field.

## Data Model

The batch envelope is stored in `hr.employee_info_change_batch` with:

- `batch_id`
- `organization_id`
- `employee_id`
- `change_type`
- `requester_notes`
- `created_at`
- `expires_at`

Child requests continue using `hr.employee_info_change_request` and now include:

- nullable `batch_id`
- nullable `batch_item_order`

Legacy non-batched requests continue to work unchanged.

## Submission Flow

For repeatable self-service submissions, the flow is:

1. Parse indexed multipart rows from the Jinja/Alpine form.
2. Reject empty batches and rows that are completely blank.
3. Validate each row and preserve entered values on failure.
4. Enforce section-specific duplicate and pending-conflict rules inside `InfoChangeService`.
5. Upload evidence or document files using the existing secure upload service.
6. Create one batch and one child request per row.
7. Commit only after the whole batch succeeds.
8. On any failure, roll back and delete every newly uploaded pending file.

Limits:

- maximum 20 items per submission
- total upload cap enforced at the batch level
- existing per-file validation remains active

## Approval Semantics

- HR can approve or reject individual child requests.
- HR can approve all actionable items in a batch atomically.
- HR can reject all actionable items in a batch atomically.
- Batch-wide approve-all and reject-all go through `InfoChangeService`, not route logic.
- Batch approval revalidates actionable items before applying them.
- Reject and expiry paths clean up pending evidence for every affected child request.

## Notifications

- Submission sends one pending-batch notification for the batch instead of one pending notification per child.
- Individual approvals and rejections still notify for single-item review paths.
- Batch approve-all and reject-all send one employee-facing decision notification for the batch action.
