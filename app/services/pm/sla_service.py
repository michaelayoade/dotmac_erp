"""Project SLA service.

Configurable SLA timelines, breach notifications, and customer status updates
for project task stages.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain_settings import DomainSetting, SettingDomain, SettingValueType
from app.models.finance.core_org.project import Project, ProjectStatus
from app.models.notification import EntityType, NotificationType
from app.models.person import Person
from app.models.pm.task import Task, TaskStatus
from app.models.rbac import PersonRole, Role
from app.services.email import queue_email
from app.services.notification import notification_service
from app.services.settings_spec import coerce_value, get_spec

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SLAStageRule:
    code: str
    name: str
    match_any: tuple[str, ...]
    anchor: str
    min_hours: int
    max_hours: int


@dataclass(frozen=True)
class SLAConfig:
    enabled: bool
    customer_updates_enabled: bool
    breach_roles: tuple[str, ...]
    stage_rules: tuple[SLAStageRule, ...]


class ProjectSLAService:
    """Evaluate and enforce project stage SLAs."""

    def __init__(self, db: Session, organization_id: uuid.UUID):
        self.db = db
        self.organization_id = organization_id

    def apply_initial_due_dates(self, project: Project) -> None:
        """Apply SLA-derived due dates after project tasks are created."""
        config = self._load_config()
        if not config.enabled or not config.stage_rules:
            return

        tasks = self._project_tasks(project.project_id)
        task_map = self._map_tasks_to_stages(tasks, config.stage_rules)

        for rule in config.stage_rules:
            task = task_map.get(rule.code)
            if not task:
                continue
            deadline = self._deadline_at(project, task_map, rule)
            if not deadline:
                continue
            due_date = deadline.date()
            if task.due_date != due_date:
                task.due_date = due_date

    def on_task_completed(
        self,
        project: Project,
        completed_task: Task,
        actor_person_id: uuid.UUID | None = None,
    ) -> None:
        """Recalculate dependent due dates and send customer updates."""
        config = self._load_config()
        if not config.enabled:
            return

        tasks = self._project_tasks(project.project_id)
        task_map = self._map_tasks_to_stages(tasks, config.stage_rules)

        for rule in config.stage_rules:
            task = task_map.get(rule.code)
            if not task:
                continue
            deadline = self._deadline_at(project, task_map, rule)
            if deadline and task.status != TaskStatus.COMPLETED:
                due_date = deadline.date()
                if task.due_date != due_date:
                    task.due_date = due_date

        stage_rule = self._stage_for_task(completed_task, config.stage_rules)
        if stage_rule and config.customer_updates_enabled:
            self._send_customer_task_update(project, completed_task, stage_rule)
            if self._is_last_stage_completed(stage_rule, task_map, config.stage_rules):
                self._send_customer_project_completion(project)

        if stage_rule:
            self._notify_if_breached(
                project, completed_task, stage_rule, actor_person_id
            )

    def process_breaches(self) -> dict[str, int]:
        """Periodic breach scan for active projects/tasks."""
        results = {"projects": 0, "breaches": 0}
        config = self._load_config()
        if not config.enabled or not config.stage_rules:
            return results

        projects = list(
            self.db.scalars(
                select(Project).where(
                    Project.organization_id == self.organization_id,
                    Project.status.in_(
                        [
                            ProjectStatus.PLANNING,
                            ProjectStatus.ACTIVE,
                            ProjectStatus.ON_HOLD,
                        ]
                    ),
                )
            ).all()
        )
        for project in projects:
            results["projects"] += 1
            tasks = self._project_tasks(project.project_id)
            if not tasks:
                continue
            task_map = self._map_tasks_to_stages(tasks, config.stage_rules)
            for rule in config.stage_rules:
                task = task_map.get(rule.code)
                if not task or task.status == TaskStatus.COMPLETED:
                    continue
                if self._notify_if_breached(project, task, rule, actor_person_id=None):
                    results["breaches"] += 1

        return results

    def _load_config(self) -> SLAConfig:
        enabled = bool(self._get_setting("project_sla_enabled", default=False))
        customer_updates_enabled = bool(
            self._get_setting("project_sla_customer_updates_enabled", default=True)
        )

        breach_roles_raw = self._get_setting(
            "project_sla_breach_roles",
            default=["PM", "ASSISTANT PM", "SPC", "PM SUPERVISOR"],
        )
        breach_roles = tuple(
            str(x).strip() for x in (breach_roles_raw or []) if str(x).strip()
        )

        stage_rules_raw = self._get_setting("project_sla_stage_rules", default=[])
        stage_rules = self._parse_rules(stage_rules_raw)

        return SLAConfig(
            enabled=enabled,
            customer_updates_enabled=customer_updates_enabled,
            breach_roles=breach_roles,
            stage_rules=stage_rules,
        )

    def _get_setting(self, key: str, default: Any) -> Any:
        spec = get_spec(SettingDomain.projects, key)
        if not spec:
            return default

        setting = self.db.scalar(
            select(DomainSetting).where(
                DomainSetting.domain == SettingDomain.projects,
                DomainSetting.key == key,
                DomainSetting.organization_id == self.organization_id,
                DomainSetting.is_active.is_(True),
            )
        )

        raw: Any = default
        if setting:
            if setting.value_type == SettingValueType.json:
                raw = setting.value_json
            else:
                raw = setting.value_text
        value, error = coerce_value(spec, raw)
        if error:
            logger.warning("Invalid project SLA setting for %s: %s", key, error)
            return default
        return default if value is None else value

    def _parse_rules(self, raw_rules: Any) -> tuple[SLAStageRule, ...]:
        if not isinstance(raw_rules, list):
            return tuple()

        rules: list[SLAStageRule] = []
        for item in raw_rules:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip().lower()
            name = str(item.get("name") or code).strip()
            anchor = str(item.get("anchor") or "").strip().lower()
            match_any = item.get("match_any") or []
            if not code or not name or not anchor or not isinstance(match_any, list):
                continue

            matches = tuple(str(v).strip().lower() for v in match_any if str(v).strip())
            if not matches:
                continue

            try:
                min_hours = int(item.get("min_hours", 0))
                max_hours = int(item.get("max_hours", 0))
            except (TypeError, ValueError):
                continue
            if max_hours < 0:
                continue
            if min_hours < 0:
                min_hours = 0
            if max_hours < min_hours:
                max_hours = min_hours

            rules.append(
                SLAStageRule(
                    code=code,
                    name=name,
                    match_any=matches,
                    anchor=anchor,
                    min_hours=min_hours,
                    max_hours=max_hours,
                )
            )

        return tuple(rules)

    def _project_tasks(self, project_id: uuid.UUID) -> list[Task]:
        return list(
            self.db.scalars(
                select(Task).where(
                    Task.organization_id == self.organization_id,
                    Task.project_id == project_id,
                    Task.is_deleted == False,  # noqa: E712
                )
            ).all()
        )

    def _map_tasks_to_stages(
        self,
        tasks: list[Task],
        rules: tuple[SLAStageRule, ...],
    ) -> dict[str, Task]:
        mapped: dict[str, Task] = {}
        for task in sorted(tasks, key=lambda t: t.created_at or datetime.now(UTC)):
            rule = self._stage_for_task(task, rules)
            if not rule:
                continue
            mapped.setdefault(rule.code, task)
        return mapped

    def _stage_for_task(
        self, task: Task, rules: tuple[SLAStageRule, ...]
    ) -> SLAStageRule | None:
        task_name = (task.task_name or "").strip().lower()
        if not task_name:
            return None
        for rule in rules:
            if any(term in task_name for term in rule.match_any):
                return rule
        return None

    def _completed_at(self, task: Task) -> datetime | None:
        if task.status != TaskStatus.COMPLETED:
            return None
        if task.updated_at:
            return (
                task.updated_at
                if task.updated_at.tzinfo
                else task.updated_at.replace(tzinfo=UTC)
            )
        if task.actual_end_date:
            return datetime.combine(task.actual_end_date, time.max, tzinfo=UTC)
        return None

    def _deadline_at(
        self,
        project: Project,
        task_map: dict[str, Task],
        rule: SLAStageRule,
    ) -> datetime | None:
        anchor = rule.anchor
        if anchor == "project_created":
            if not project.created_at:
                return None
            base = (
                project.created_at
                if project.created_at.tzinfo
                else project.created_at.replace(tzinfo=UTC)
            )
            return base + timedelta(hours=rule.max_hours)

        if not anchor.startswith("stage:"):
            return None

        parts = anchor.split(":")
        if len(parts) != 3 or parts[2] != "completed":
            return None
        anchor_stage = parts[1]
        anchor_task = task_map.get(anchor_stage)
        if not anchor_task:
            return None
        completed_at = self._completed_at(anchor_task)
        if not completed_at:
            return None
        return completed_at + timedelta(hours=rule.max_hours)

    def _notify_if_breached(
        self,
        project: Project,
        task: Task,
        rule: SLAStageRule,
        actor_person_id: uuid.UUID | None,
    ) -> bool:
        config = self._load_config()
        task_map = self._map_tasks_to_stages(
            self._project_tasks(project.project_id), config.stage_rules
        )
        deadline = self._deadline_at(project, task_map, rule)
        if not deadline:
            return False
        now = datetime.now(UTC)
        if now <= deadline:
            return False

        recipients = self._breach_recipients(project, task, config.breach_roles)
        if not recipients:
            return False

        any_created = False
        task_ref = task.task_code or str(task.task_id)
        action_url = (
            f"/projects/{project.project_code}/tasks/{task_ref}"
            if project.project_code
            else None
        )
        title = f"[SLA:{rule.code}] Task breach: {rule.name}"
        message = (
            f"Task '{task.task_name}' breached SLA (deadline {deadline.isoformat()}). "
            f"Project: {project.project_code or project.project_name}."
        )
        for recipient_id in recipients:
            if self._has_breach_notification(task.task_id, recipient_id, title):
                continue
            notification_service.create(
                self.db,
                organization_id=self.organization_id,
                recipient_id=recipient_id,
                entity_type=EntityType.SYSTEM,
                entity_id=task.task_id,
                notification_type=NotificationType.OVERDUE,
                title=title,
                message=message,
                action_url=action_url,
                actor_id=actor_person_id,
            )
            any_created = True

        return any_created

    def _has_breach_notification(
        self,
        task_id: uuid.UUID,
        recipient_id: uuid.UUID,
        title: str,
    ) -> bool:
        from app.models.notification import Notification

        existing = self.db.scalar(
            select(Notification.notification_id).where(
                Notification.organization_id == self.organization_id,
                Notification.recipient_id == recipient_id,
                Notification.entity_type == EntityType.SYSTEM,
                Notification.entity_id == task_id,
                Notification.notification_type == NotificationType.OVERDUE,
                Notification.title == title,
            )
        )
        return existing is not None

    def _breach_recipients(
        self,
        project: Project,
        task: Task,
        role_names: tuple[str, ...],
    ) -> set[uuid.UUID]:
        candidate_ids: set[uuid.UUID] = set()
        if project.project_manager_user_id:
            candidate_ids.add(project.project_manager_user_id)

        if task.assigned_to_id:
            from app.models.people.hr.employee import Employee

            assigned_employee = self.db.scalar(
                select(Employee).where(
                    Employee.organization_id == self.organization_id,
                    Employee.employee_id == task.assigned_to_id,
                )
            )
            if assigned_employee:
                candidate_ids.add(assigned_employee.person_id)
                if assigned_employee.reports_to_id:
                    manager = self.db.scalar(
                        select(Employee).where(
                            Employee.organization_id == self.organization_id,
                            Employee.employee_id == assigned_employee.reports_to_id,
                        )
                    )
                    if manager:
                        candidate_ids.add(manager.person_id)

        normalized_roles = {r.strip().lower() for r in role_names if r.strip()}
        if normalized_roles:
            role_rows = self.db.scalars(
                select(PersonRole.person_id)
                .join(Role, Role.id == PersonRole.role_id)
                .join(Person, Person.id == PersonRole.person_id)
                .where(
                    Person.organization_id == self.organization_id,
                    Role.is_active.is_(True),
                )
            ).all()
            if role_rows:
                all_role_names = self.db.execute(
                    select(PersonRole.person_id, Role.name)
                    .join(Role, Role.id == PersonRole.role_id)
                    .join(Person, Person.id == PersonRole.person_id)
                    .where(Person.organization_id == self.organization_id)
                ).all()
                person_roles: dict[uuid.UUID, set[str]] = {}
                for person_id, role_name in all_role_names:
                    person_roles.setdefault(person_id, set()).add(
                        str(role_name).strip().lower()
                    )
                for person_id in role_rows:
                    if person_roles.get(person_id, set()) & normalized_roles:
                        candidate_ids.add(person_id)

        if not candidate_ids:
            return set()
        valid_people = self.db.scalars(
            select(Person.id).where(
                Person.organization_id == self.organization_id,
                Person.id.in_(candidate_ids),
                Person.is_active.is_(True),
            )
        ).all()
        return set(valid_people)

    def _is_last_stage_completed(
        self,
        stage_rule: SLAStageRule,
        task_map: dict[str, Task],
        rules: tuple[SLAStageRule, ...],
    ) -> bool:
        if not rules:
            return False
        last_rule = rules[-1]
        if last_rule.code != stage_rule.code:
            return False
        task = task_map.get(last_rule.code)
        return bool(task and task.status == TaskStatus.COMPLETED)

    def _project_customer_email(self, project: Project) -> str | None:
        customer = project.customer
        if not customer:
            return None

        candidates: list[str | None] = []
        if isinstance(customer.primary_contact, dict):
            candidates.extend(
                [
                    customer.primary_contact.get("email"),
                    customer.primary_contact.get("email_address"),
                ]
            )
        if isinstance(customer.billing_address, dict):
            candidates.append(customer.billing_address.get("email"))

        for candidate in candidates:
            if candidate and str(candidate).strip():
                return str(candidate).strip()
        return None

    def _send_customer_task_update(
        self,
        project: Project,
        task: Task,
        stage_rule: SLAStageRule,
    ) -> None:
        email = self._project_customer_email(project)
        if not email:
            return

        subject = f"Project update: {project.project_code or project.project_name}"
        body_text = (
            f"Task completed: {stage_rule.name}.\n"
            f"Project: {project.project_name} ({project.project_code or '-'})\n"
            f"Status: {task.status.value}"
        )
        body_html = (
            f"<p>Task completed: <strong>{stage_rule.name}</strong></p>"
            f"<p>Project: {project.project_name} ({project.project_code or '-'})</p>"
        )
        queue_email(
            to_email=email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            organization_id=self.organization_id,
        )

    def _send_customer_project_completion(self, project: Project) -> None:
        email = self._project_customer_email(project)
        if not email:
            return

        subject = f"Project completed: {project.project_code or project.project_name}"
        body_text = (
            "Your project has been completed. "
            "Please reply with your feedback or satisfaction confirmation."
        )
        body_html = (
            "<p>Your project has been completed.</p>"
            "<p>Please reply with your feedback or satisfaction confirmation.</p>"
        )
        queue_email(
            to_email=email,
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            organization_id=self.organization_id,
        )
