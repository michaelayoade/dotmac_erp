---
name: celery-status
description: "Inspect Celery worker health, active/reserved tasks, queue depth, and beat schedule"
arguments:
  - name: section
    description: "Which check to run: 'active', 'reserved', 'registered', 'queue', 'stats', 'scheduled', or 'all'"
---

# Celery Status

Inspect the DotMac ERP Celery infrastructure using `docker exec` commands.

## Sections

Run the section matching `$ARGUMENTS` (or all sections if `$ARGUMENTS` is "all").

### 1. Active Tasks (`active`)

```bash
docker exec dotmac_erp_worker celery -A app.celery_app inspect active --json 2>/dev/null
```

Parse the JSON output. For each active task, show:
- Task name (short — strip `app.tasks.` prefix)
- Task ID (first 8 chars)
- Runtime (if available)
- Args/kwargs (truncated to 80 chars)

If empty, report "No tasks currently executing".

### 2. Reserved Tasks (`reserved`)

```bash
docker exec dotmac_erp_worker celery -A app.celery_app inspect reserved --json 2>/dev/null
```

These are tasks fetched from the broker but not yet executing (prefetched). Show count and task names.

### 3. Registered Tasks (`registered`)

```bash
docker exec dotmac_erp_worker celery -A app.celery_app inspect registered --json 2>/dev/null
```

List all registered task names grouped by module (e.g., `data_health`, `finance`, `hr`, `coach`). Show total count.

### 4. Queue Depth (`queue`)

```bash
docker exec dotmac_erp_redis redis-cli LLEN celery 2>/dev/null
```

Report the number of messages waiting in the default `celery` queue. Flag:
- 0: OK (idle)
- 1-50: OK (normal)
- 51-200: WARNING (backlog building)
- 200+: CRITICAL (queue backup)

Also check for other known queues:
```bash
docker exec dotmac_erp_redis redis-cli KEYS "celery*" 2>/dev/null
```

### 5. Worker Stats (`stats`)

```bash
docker exec dotmac_erp_worker celery -A app.celery_app inspect stats --json 2>/dev/null
```

Extract and display:
- Worker hostname and PID
- Concurrency (prefork pool size)
- Total tasks executed
- Uptime
- Broker connection status

### 6. Scheduled Tasks (`scheduled`)

```bash
docker exec dotmac_erp_worker celery -A app.celery_app inspect scheduled --json 2>/dev/null
```

Show tasks with ETA (delayed execution). If empty, report "No ETA-scheduled tasks".

Also list the beat schedule configuration:
```bash
docker exec dotmac_erp_worker python -c "
from app.services.scheduler_config import build_beat_schedule
schedule = build_beat_schedule()
for name, config in sorted(schedule.items()):
    print(f'{name}: {config[\"task\"]} @ {config[\"schedule\"]}')
" 2>/dev/null
```

## Output Format

Present as a markdown report:

```
## Celery Status Report

### Workers
| Worker | PID | Concurrency | Uptime | Tasks Executed |
|--------|-----|-------------|--------|----------------|

### Queue Depth
- `celery`: N messages — STATUS

### Active Tasks
(table or "No tasks currently executing")

### Reserved Tasks
(count or "None prefetched")

### Beat Schedule
(task list with schedules)

### Verdict: HEALTHY / DEGRADED / DOWN
```

**Verdict logic**:
- DOWN: Worker container not running or inspect commands fail
- DEGRADED: Queue depth > 200 or no registered tasks
- HEALTHY: Everything else
