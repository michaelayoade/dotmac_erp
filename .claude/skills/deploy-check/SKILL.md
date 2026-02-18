---
name: deploy-check
description: "Pre-deploy validation: git status, migrations, containers, disk, connectivity — returns READY or BLOCKED verdict"
arguments:
  - name: section
    description: "'all' for full check, or specific: 'git', 'migrations', 'containers', 'disk', 'connectivity'"
---

# Pre-Deploy Check

Validate that the DotMac ERP environment is ready for deployment.

## Sections

Run the section matching `$ARGUMENTS` (or all sections if `$ARGUMENTS` is "all").

### 1. Git Status (`git`)

```bash
# Current branch
git -C /root/dotmac branch --show-current

# Uncommitted changes
git -C /root/dotmac status --porcelain

# Last 5 commits
git -C /root/dotmac log --oneline -5

# Check if branch is ahead/behind remote
git -C /root/dotmac status -sb
```

Report:
- Current branch name
- Count of uncommitted changes (staged + unstaged + untracked)
- Last 5 commit messages
- Ahead/behind status vs remote

**BLOCK if**: uncommitted changes exist (risk of deploying stale code)

### 2. Migration Status (`migrations`)

```bash
# Current migration head in database
docker exec dotmac_erp_app alembic current 2>&1

# Latest migration in code
docker exec dotmac_erp_app alembic heads 2>&1

# Check for multiple heads (branching)
docker exec dotmac_erp_app alembic branches 2>&1

# Check for pending migrations
docker exec dotmac_erp_app alembic check 2>&1
```

Report:
- Current DB revision vs code head
- Whether they match
- Any branch conflicts

**BLOCK if**: DB is behind code head (migrations need to run) or branches exist

### 3. Container Health (`containers`)

```bash
docker ps --filter "name=dotmac_erp_" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>&1
```

Expected containers (7):
- `dotmac_erp_app` — FastAPI application
- `dotmac_erp_worker` — Celery worker
- `dotmac_erp_beat` — Celery beat scheduler
- `dotmac_erp_db` — PostgreSQL
- `dotmac_erp_redis` — Redis broker
- `dotmac_erp_openbao` — Secret management
- `dotmac_erp_minio` — Object storage (optional)

Report each container's status. Flag any not in "Up" state.

**BLOCK if**: app, db, or redis containers are down

### 4. Disk Usage (`disk`)

```bash
# Host disk
df -h / /root 2>/dev/null | tail -n +2

# Docker disk usage
docker system df 2>&1

# Check uploads directory size
du -sh /root/dotmac/uploads 2>/dev/null || echo "No uploads dir"
```

Report:
- Host disk usage percentage
- Docker images/containers/volumes sizes
- Uploads directory size

**BLOCK if**: any filesystem > 85% usage

### 5. Connectivity (`connectivity`)

```bash
# PostgreSQL
docker exec dotmac_erp_db pg_isready -U postgres 2>&1

# Redis
docker exec dotmac_erp_redis redis-cli ping 2>&1

# App health (HTTP)
curl -sf http://localhost:8003/health 2>&1 || echo "App health check failed"

# OpenBao
curl -sf http://localhost:8200/v1/sys/health 2>&1 || echo "OpenBao not reachable"
```

Report each service's connectivity status.

**BLOCK if**: PostgreSQL or Redis unreachable

## Output Format

```
## Pre-Deploy Check — {date} {time}

### Git
- Branch: `main`
- Uncommitted changes: 0
- Last commit: abc1234 Fix CI pipeline
- Remote: up to date

### Migrations
- DB: abc123 (head)
- Code: abc123 (head)
- Status: IN SYNC

### Containers (N/7 running)
| Container | Status | Ports |
|-----------|--------|-------|

### Disk
| Mount | Used | Available | Pct |
|-------|------|-----------|-----|

### Connectivity
| Service | Status |
|---------|--------|
| PostgreSQL | OK |
| Redis | OK |
| App (8003) | OK |
| OpenBao (8200) | OK |

---

## Verdict: READY / BLOCKED

**Blockers:**
- (list any blocking issues)

**Warnings:**
- (list non-blocking concerns)
```

**Verdict logic**:
- BLOCKED: Any section has a blocking condition (see BLOCK IF rules above)
- READY: All checks pass, may have non-blocking warnings
