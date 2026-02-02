from app.models.auth import (  # noqa: F401
    ApiKey,
    AuthProvider,
    MFAMethod,
    MFAMethodType,
    Session,
    SessionStatus,
    UserCredential,
)
from app.models.audit import AuditActorType, AuditEvent  # noqa: F401
from app.models.notification import (  # noqa: F401
    EntityType,
    Notification,
    NotificationChannel,
    NotificationType,
)
from app.models.domain_settings import (  # noqa: F401
    DomainSetting,
    SettingDomain,
    SettingValueType,
)
from app.models.person import ContactMethod, Gender, Person, PersonStatus  # noqa: F401
from app.models.batch_operation import (  # noqa: F401
    BatchOperation,
    BatchOperationStatus,
    BatchOperationType,
)
from app.models.rbac import Permission, PersonRole, Role, RolePermission  # noqa: F401
from app.models.scheduler import ScheduleType, ScheduledTask  # noqa: F401
from app.models.workflow_task import (  # noqa: F401
    WorkflowTask,
    WorkflowTaskStatus,
    WorkflowTaskPriority,
)

# Finance Models - 111 tables across 16 schemas
import app.models.finance as finance  # noqa: F401

# People Models - HR, Payroll, Leave, etc.
# Note: Expense models are included in people.exp submodule
import app.models.people as people  # noqa: F401

# Support Models - Tickets/Issues synced from ERPNext
import app.models.support as support  # noqa: F401

# Project Management Models - Tasks, Milestones, Resource Allocation, Time Tracking
import app.models.pm as pm  # noqa: F401

# Sync Models - External system sync state tracking
from app.models.sync import (  # noqa: F401
    IntegrationConfig,
    IntegrationType,
    SyncEntity,
    SyncStatus,
    SyncHistory,
    SyncJobStatus,
    SyncType,
)
