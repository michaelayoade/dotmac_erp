from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
from app.models.auth import SessionStatus
from app.models.auth import Session as AuthSession
from app.models.finance.audit.audit_log import AuditAction, AuditLog
from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.models.scheduler import ScheduledTask, ScheduleType
from app.services.formatters import format_datetime as _format_datetime

from .common import (
    DEFAULT_PAGE_SIZE,
    _build_pagination,
    _format_interval,
    _format_request_summary,
    _humanize_actor_type,
    _humanize_http_action,
    _humanize_path,
    _parse_actor_type,
    _parse_success_filter,
    _resolve_person_name_map,
    _safe_json_dump,
    _truncate,
)


class AdminOperationsMixin:
    @staticmethod
    def dashboard_context(db: Session) -> dict:
        now = datetime.now(UTC)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
        total_users = db.scalar(select(func.count(Person.id))) or 0
        new_users_week = db.scalar(select(func.count(Person.id)).where(Person.created_at >= week_start)) or 0
        active_sessions = db.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.status == SessionStatus.active,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
        ) or 0
        unique_users_today = db.scalar(
            select(func.count(func.distinct(AuthSession.person_id))).where(
                AuthSession.last_seen_at.isnot(None),
                AuthSession.last_seen_at >= start_of_day,
            )
        ) or 0
        total_organizations = db.scalar(select(func.count(Organization.organization_id))) or 0
        active_organizations = db.scalar(
            select(func.count(Organization.organization_id)).where(Organization.is_active.is_(True))
        ) or 0
        recent_users_query = list(db.scalars(select(Person).order_by(Person.created_at.desc()).limit(5)).all())
        recent_users = []
        for person in recent_users_query:
            name = person.name or person.email or "Unknown"
            initials = "".join(word[0].upper() for word in name.split()[:2]) if name else "?"
            recent_users.append(
                {
                    "id": str(person.id),
                    "name": name,
                    "email": person.email,
                    "initials": initials,
                    "status": "active" if person.is_active else "inactive",
                }
            )
        return {
            "stats": {
                "total_users": total_users,
                "new_users_week": new_users_week,
                "active_sessions": active_sessions,
                "unique_users_today": unique_users_today,
                "total_organizations": total_organizations,
                "active_organizations": active_organizations,
                "system_health": "Good",
            },
            "recent_users": recent_users,
            "recent_activity": [],
        }

    @staticmethod
    def audit_logs_context(
        db: Session,
        organization_id,
        search: str | None,
        actor_type: str | None,
        status: str | None,
        start_date: str | None,
        end_date: str | None,
        page: int,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> dict:
        offset = (page - 1) * limit
        conditions = []
        if organization_id:
            conditions.append(AuditEvent.organization_id == organization_id)
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(
                or_(
                    AuditEvent.action.ilike(search_pattern),
                    AuditEvent.entity_type.ilike(search_pattern),
                    AuditEvent.actor_id.ilike(search_pattern),
                    AuditEvent.request_id.ilike(search_pattern),
                    AuditEvent.ip_address.ilike(search_pattern),
                )
            )
        actor_type_filter_value = _parse_actor_type(actor_type)
        if actor_type_filter_value:
            conditions.append(AuditEvent.actor_type == actor_type_filter_value)
        success_value = _parse_success_filter(status)
        if success_value is not None:
            conditions.append(AuditEvent.is_success == success_value)
        start_date_value = (start_date or "").strip()
        end_date_value = (end_date or "").strip()
        if start_date_value:
            try:
                parsed_start = datetime.strptime(start_date_value, "%Y-%m-%d")
                conditions.append(AuditEvent.occurred_at >= parsed_start.replace(tzinfo=UTC))
            except ValueError:
                start_date_value = ""
        if end_date_value:
            try:
                parsed_end = datetime.strptime(end_date_value, "%Y-%m-%d")
                conditions.append(AuditEvent.occurred_at < (parsed_end + timedelta(days=1)).replace(tzinfo=UTC))
            except ValueError:
                end_date_value = ""
        total_count = db.scalar(select(func.count(AuditEvent.id)).where(*conditions)) or 0
        events = list(
            db.scalars(
                select(AuditEvent)
                .where(*conditions)
                .order_by(AuditEvent.occurred_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        actor_ids = [str(event.actor_person_id) for event in events if event.actor_person_id is not None]
        actor_ids.extend([event.actor_id for event in events if event.actor_id and not event.actor_person_id])
        actor_name_map = _resolve_person_name_map(db=db, person_ids=actor_ids, organization_id=organization_id)
        events_view = []
        for event in events:
            actor_lookup_key = str(event.actor_person_id) if event.actor_person_id else event.actor_id if event.actor_id else ""
            actor_name = actor_name_map.get(actor_lookup_key) if actor_lookup_key else None
            if not actor_name:
                if (event.actor_id or event.actor_person_id) and event.actor_type == AuditActorType.user:
                    actor_name = "Unknown User"
                elif event.actor_type == AuditActorType.system:
                    actor_name = "System"
                elif event.actor_type == AuditActorType.service:
                    actor_name = "Service"
                elif event.actor_type == AuditActorType.api_key:
                    actor_name = "API Key"
                else:
                    actor_name = "Unknown User"
            event_actor_type_value = event.actor_type.value if event.actor_type else ""
            request_meta = []
            if event.request_id:
                request_meta.append(f"Request {event.request_id}")
            if event.ip_address:
                request_meta.append(f"IP {event.ip_address}")
            entity_source = event.metadata_.get("path") if isinstance(event.metadata_, dict) else event.entity_type
            events_view.append(
                {
                    "event_id": event.id,
                    "occurred_at": _format_datetime(event.occurred_at),
                    "actor_type": event_actor_type_value,
                    "actor_type_label": _humanize_actor_type(event_actor_type_value),
                    "actor_id": event.actor_id,
                    "actor_name": actor_name,
                    "action": event.action,
                    "action_label": _humanize_http_action(event.action),
                    "entity_type": event.entity_type,
                    "entity_label": _humanize_path(str(entity_source or "")),
                    "entity_id": event.entity_id,
                    "status_code": event.status_code,
                    "is_success": event.is_success,
                    "request_id": event.request_id,
                    "ip_address": event.ip_address,
                    "request_summary": _format_request_summary(event.action, event.metadata_, event.request_id),
                    "request_meta": " | ".join(request_meta) if request_meta else "-",
                }
            )
        return {
            "events": events_view,
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "status_filter": status or "",
            "start_date_filter": start_date_value,
            "end_date_filter": end_date_value,
            "actor_type_filter": actor_type_filter_value.value if actor_type_filter_value else "",
            "actor_types": [value.value for value in AuditActorType],
        }

    @staticmethod
    def tasks_context(db: Session, search: str | None, status: str | None, page: int, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        offset = (page - 1) * limit
        conditions = []
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(or_(ScheduledTask.name.ilike(search_pattern), ScheduledTask.task_name.ilike(search_pattern)))
        status_flag = True if status == "enabled" else False if status == "disabled" else None
        if status_flag is not None:
            conditions.append(ScheduledTask.enabled == status_flag)
        total_count = db.scalar(select(func.count(ScheduledTask.id)).where(*conditions)) or 0
        tasks = list(
            db.scalars(
                select(ScheduledTask).where(*conditions).order_by(ScheduledTask.name).limit(limit).offset(offset)
            ).all()
        )
        tasks_view = []
        for task in tasks:
            args_display = _truncate(_safe_json_dump(task.args_json)) if task.args_json else ""
            kwargs_display = _truncate(_safe_json_dump(task.kwargs_json)) if task.kwargs_json else ""
            schedule_label = _format_interval(task.interval_seconds) if task.schedule_type == ScheduleType.interval else "-"
            tasks_view.append(
                {
                    "task_id": task.id,
                    "name": task.name,
                    "task_name": task.task_name,
                    "schedule": schedule_label,
                    "args": args_display,
                    "kwargs": kwargs_display,
                    "enabled": task.enabled,
                    "last_run_at": _format_datetime(task.last_run_at),
                    "updated_at": _format_datetime(task.updated_at or task.created_at),
                }
            )
        return {
            "tasks": tasks_view,
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "status_filter": status or "",
        }

    @staticmethod
    def task_form_context(db: Session, task_id: str | None = None) -> dict:
        from app.services.common import coerce_uuid

        task_data = None
        if task_id:
            task = db.get(ScheduledTask, coerce_uuid(task_id))
            if task:
                task_data = {
                    "id": str(task.id),
                    "name": task.name,
                    "task_name": task.task_name,
                    "schedule_type": task.schedule_type.value,
                    "interval_seconds": task.interval_seconds,
                    "args_json": json.dumps(task.args_json, indent=2) if task.args_json else "",
                    "kwargs_json": json.dumps(task.kwargs_json, indent=2) if task.kwargs_json else "",
                    "enabled": task.enabled,
                    "last_run_at": _format_datetime(task.last_run_at),
                }
        return {
            "task_data": task_data,
            "schedule_types": [st.value for st in ScheduleType],
            "interval_presets": [
                {"value": 60, "label": "Every minute"},
                {"value": 300, "label": "Every 5 minutes"},
                {"value": 600, "label": "Every 10 minutes"},
                {"value": 900, "label": "Every 15 minutes"},
                {"value": 1800, "label": "Every 30 minutes"},
                {"value": 3600, "label": "Every hour"},
                {"value": 7200, "label": "Every 2 hours"},
                {"value": 14400, "label": "Every 4 hours"},
                {"value": 21600, "label": "Every 6 hours"},
                {"value": 43200, "label": "Every 12 hours"},
                {"value": 86400, "label": "Every day"},
                {"value": 604800, "label": "Every week"},
            ],
        }

    @staticmethod
    def create_task(db: Session, name: str, task_name: str, schedule_type: str, interval_seconds: int, args_json: str = "", kwargs_json: str = "", enabled: bool = True) -> tuple[ScheduledTask | None, str | None]:
        try:
            schedule_type_enum = ScheduleType(schedule_type)
        except ValueError:
            return None, f"Invalid schedule type: {schedule_type}"
        existing = db.scalar(select(ScheduledTask).where(ScheduledTask.name == name))
        if existing:
            return None, f"A task with name '{name}' already exists"
        args_list = None
        kwargs_dict = None
        if args_json and args_json.strip():
            try:
                args_list = json.loads(args_json)
                if not isinstance(args_list, list):
                    return None, "Args must be a JSON array"
            except json.JSONDecodeError as exc:
                return None, f"Invalid args JSON: {str(exc)}"
        if kwargs_json and kwargs_json.strip():
            try:
                kwargs_dict = json.loads(kwargs_json)
                if not isinstance(kwargs_dict, dict):
                    return None, "Kwargs must be a JSON object"
            except json.JSONDecodeError as exc:
                return None, f"Invalid kwargs JSON: {str(exc)}"
        try:
            task = ScheduledTask(
                name=name,
                task_name=task_name,
                schedule_type=schedule_type_enum,
                interval_seconds=interval_seconds,
                args_json=args_list,
                kwargs_json=kwargs_dict,
                enabled=enabled,
            )
            db.add(task)
            db.commit()
            return task, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to create task: {str(exc)}"

    @staticmethod
    def update_task(db: Session, task_id: str, name: str, task_name: str, schedule_type: str, interval_seconds: int, args_json: str = "", kwargs_json: str = "", enabled: bool = True) -> tuple[ScheduledTask | None, str | None]:
        from app.services.common import coerce_uuid

        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            return None, "Task not found"
        try:
            schedule_type_enum = ScheduleType(schedule_type)
        except ValueError:
            return None, f"Invalid schedule type: {schedule_type}"
        existing = db.scalar(select(ScheduledTask).where(ScheduledTask.name == name, ScheduledTask.id != task.id))
        if existing:
            return None, f"A task with name '{name}' already exists"
        args_list = None
        kwargs_dict = None
        if args_json and args_json.strip():
            try:
                args_list = json.loads(args_json)
                if not isinstance(args_list, list):
                    return None, "Args must be a JSON array"
            except json.JSONDecodeError as exc:
                return None, f"Invalid args JSON: {str(exc)}"
        if kwargs_json and kwargs_json.strip():
            try:
                kwargs_dict = json.loads(kwargs_json)
                if not isinstance(kwargs_dict, dict):
                    return None, "Kwargs must be a JSON object"
            except json.JSONDecodeError as exc:
                return None, f"Invalid kwargs JSON: {str(exc)}"
        try:
            task.name = name
            task.task_name = task_name
            task.schedule_type = schedule_type_enum
            task.interval_seconds = interval_seconds
            task.args_json = args_list
            task.kwargs_json = kwargs_dict
            task.enabled = enabled
            db.commit()
            return task, None
        except Exception as exc:
            db.rollback()
            return None, f"Failed to update task: {str(exc)}"

    @staticmethod
    def delete_task(db: Session, task_id: str) -> str | None:
        from app.services.common import coerce_uuid

        task = db.get(ScheduledTask, coerce_uuid(task_id))
        if not task:
            return "Task not found"
        try:
            db.delete(task)
            db.commit()
            return None
        except Exception as exc:
            db.rollback()
            return f"Failed to delete task: {str(exc)}"

    @staticmethod
    def data_changes_context(db: Session, organization_id, module: str | None, entity: str | None, action: str | None, search: str | None, page: int, limit: int = DEFAULT_PAGE_SIZE) -> dict:
        offset = (page - 1) * limit
        conditions = []
        if organization_id:
            conditions.append(AuditLog.organization_id == organization_id)
        if module:
            conditions.append(AuditLog.table_schema == module)
        if entity:
            conditions.append(AuditLog.table_name == entity)
        if action:
            try:
                conditions.append(AuditLog.action == AuditAction(action))
            except ValueError:
                pass
        search_value = search.strip() if search else ""
        if search_value:
            search_pattern = f"%{search_value}%"
            conditions.append(
                or_(
                    AuditLog.record_id.ilike(search_pattern),
                    AuditLog.reason.ilike(search_pattern),
                    AuditLog.correlation_id.ilike(search_pattern),
                )
            )
        total_count = db.scalar(select(func.count(AuditLog.audit_id)).where(*conditions)) or 0
        logs = list(
            db.scalars(
                select(AuditLog)
                .where(*conditions)
                .order_by(AuditLog.occurred_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )
        user_ids = [str(log.user_id) for log in logs if log.user_id]
        user_name_map = _resolve_person_name_map(db=db, person_ids=user_ids, organization_id=organization_id)
        logs_view = []
        for log in logs:
            user_id_value = str(log.user_id) if log.user_id else None
            logs_view.append(
                {
                    "audit_id": str(log.audit_id),
                    "occurred_at": _format_datetime(log.occurred_at),
                    "table_schema": log.table_schema,
                    "table_name": log.table_name,
                    "record_id": log.record_id,
                    "action": log.action.value if log.action else "",
                    "changed_fields": log.changed_fields or [],
                    "old_values": log.old_values or {},
                    "new_values": log.new_values or {},
                    "user_id": user_id_value,
                    "user_name": user_name_map.get(user_id_value) if user_id_value else "System",
                    "ip_address": log.ip_address,
                    "reason": log.reason,
                    "correlation_id": log.correlation_id,
                    "has_hash": bool(log.hash_chain),
                }
            )
        modules = sorted({row[0] for row in db.execute(select(AuditLog.table_schema).distinct()).all() if row[0]})
        entities = sorted({row[0] for row in db.execute(select(AuditLog.table_name).distinct()).all() if row[0]})
        return {
            "logs": logs_view,
            "pagination": _build_pagination(page, max(1, (total_count + limit - 1) // limit), total_count, limit),
            "search": search_value,
            "module_filter": module or "",
            "entity_filter": entity or "",
            "action_filter": action or "",
            "modules": modules,
            "entities": entities,
            "actions": [a.value for a in AuditAction],
        }
