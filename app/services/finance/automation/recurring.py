"""
Recurring Transaction Service.

Handles recurring template management and transaction generation.
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from dateutil.relativedelta import relativedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.automation import (
    RecurringEntityType,
    RecurringFrequency,
    RecurringLog,
    RecurringLogStatus,
    RecurringStatus,
    RecurringTemplate,
)

logger = logging.getLogger(__name__)


@dataclass
class RecurringTemplateInput:
    """Input for creating a recurring template."""
    template_name: str
    entity_type: RecurringEntityType
    template_data: Dict[str, Any]
    frequency: RecurringFrequency
    start_date: date
    end_date: Optional[date] = None
    schedule_config: Optional[Dict[str, Any]] = None
    occurrences_limit: Optional[int] = None
    auto_post: bool = False
    auto_send: bool = False
    days_before_due: int = 30
    notify_on_generation: bool = True
    notify_email: Optional[str] = None
    description: Optional[str] = None


@dataclass
class GenerationResult:
    """Result of generating a recurring transaction."""
    success: bool
    entity_type: Optional[str] = None
    entity_id: Optional[UUID] = None
    entity_number: Optional[str] = None
    error_message: Optional[str] = None


class RecurringService:
    """Service for managing recurring transactions."""

    def calculate_next_run_date(
        self,
        current_date: date,
        frequency: RecurringFrequency,
        schedule_config: Optional[Dict[str, Any]] = None,
    ) -> date:
        """Calculate the next run date based on frequency."""
        config = schedule_config or {}

        if frequency == RecurringFrequency.DAILY:
            return current_date + timedelta(days=1)

        elif frequency == RecurringFrequency.WEEKLY:
            # Default to same day next week, or specific day if configured
            day_of_week = config.get("day_of_week", current_date.weekday())
            next_date = current_date + timedelta(days=7)
            # Adjust to specific day of week
            days_diff = day_of_week - next_date.weekday()
            if days_diff < 0:
                days_diff += 7
            return next_date + timedelta(days=days_diff)

        elif frequency == RecurringFrequency.BIWEEKLY:
            return current_date + timedelta(days=14)

        elif frequency == RecurringFrequency.MONTHLY:
            day_of_month = config.get("day_of_month", current_date.day)
            next_date = current_date + relativedelta(months=1)
            # Handle day overflow (e.g., 31st in February)
            try:
                next_date = next_date.replace(day=min(day_of_month, 28))
            except ValueError:
                next_date = next_date.replace(day=28)
            return next_date

        elif frequency == RecurringFrequency.QUARTERLY:
            return current_date + relativedelta(months=3)

        elif frequency == RecurringFrequency.SEMI_ANNUALLY:
            return current_date + relativedelta(months=6)

        elif frequency == RecurringFrequency.ANNUALLY:
            return current_date + relativedelta(years=1)

        return current_date + timedelta(days=30)  # Default fallback

    def create_template(
        self,
        db: Session,
        organization_id: UUID,
        input_data: RecurringTemplateInput,
        created_by: UUID,
        source_entity_type: Optional[str] = None,
        source_entity_id: Optional[UUID] = None,
    ) -> RecurringTemplate:
        """Create a new recurring template."""
        # Check for duplicate name
        existing = db.execute(
            select(RecurringTemplate).where(
                and_(
                    RecurringTemplate.organization_id == organization_id,
                    RecurringTemplate.template_name == input_data.template_name,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Template with name '{input_data.template_name}' already exists",
            )

        # Calculate first run date
        next_run_date = input_data.start_date
        if next_run_date < date.today():
            next_run_date = self.calculate_next_run_date(
                date.today(),
                input_data.frequency,
                input_data.schedule_config,
            )

        template = RecurringTemplate(
            organization_id=organization_id,
            template_name=input_data.template_name,
            description=input_data.description,
            entity_type=input_data.entity_type,
            template_data=input_data.template_data,
            frequency=input_data.frequency,
            schedule_config=input_data.schedule_config or {},
            start_date=input_data.start_date,
            end_date=input_data.end_date,
            next_run_date=next_run_date,
            occurrences_limit=input_data.occurrences_limit,
            auto_post=input_data.auto_post,
            auto_send=input_data.auto_send,
            days_before_due=input_data.days_before_due,
            notify_on_generation=input_data.notify_on_generation,
            notify_email=input_data.notify_email,
            source_entity_type=source_entity_type,
            source_entity_id=source_entity_id,
            created_by=created_by,
        )

        db.add(template)
        db.flush()
        return template

    def create_from_invoice(
        self,
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        template_name: str,
        frequency: RecurringFrequency,
        start_date: date,
        created_by: UUID,
        **kwargs,
    ) -> RecurringTemplate:
        """Create a recurring template from an existing invoice."""
        from app.models.finance.ar import Invoice

        invoice = db.get(Invoice, invoice_id)
        if not invoice:
            raise HTTPException(status_code=404, detail="Invoice not found")

        if invoice.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Extract template data from invoice
        template_data = {
            "customer_id": str(invoice.customer_id),
            "currency_code": invoice.currency_code,
            "payment_terms_id": str(invoice.payment_terms_id) if invoice.payment_terms_id else None,
            "ar_control_account_id": str(invoice.ar_control_account_id),
            "billing_address": invoice.billing_address,
            "shipping_address": invoice.shipping_address,
            "notes": invoice.notes,
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "account_id": str(line.account_id),
                    "tax_code_id": str(line.tax_code_id) if line.tax_code_id else None,
                }
                for line in invoice.lines
            ],
        }

        input_data = RecurringTemplateInput(
            template_name=template_name,
            entity_type=RecurringEntityType.INVOICE,
            template_data=template_data,
            frequency=frequency,
            start_date=start_date,
            **kwargs,
        )

        return self.create_template(
            db,
            organization_id,
            input_data,
            created_by,
            source_entity_type="INVOICE",
            source_entity_id=invoice_id,
        )

    def create_from_bill(
        self,
        db: Session,
        organization_id: UUID,
        bill_id: UUID,
        template_name: str,
        frequency: RecurringFrequency,
        start_date: date,
        created_by: UUID,
        **kwargs,
    ) -> RecurringTemplate:
        """Create a recurring template from an existing supplier invoice."""
        from app.models.finance.ap import SupplierInvoice

        bill = db.get(SupplierInvoice, bill_id)
        if not bill:
            raise HTTPException(status_code=404, detail="Bill not found")

        if bill.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="Access denied")

        template_data = {
            "supplier_id": str(bill.supplier_id),
            "currency_code": bill.currency_code,
            "ap_control_account_id": str(bill.ap_control_account_id),
            "lines": [
                {
                    "description": line.description,
                    "quantity": str(line.quantity),
                    "unit_price": str(line.unit_price),
                    "account_id": str(line.account_id),
                    "tax_code_id": str(line.tax_code_id) if line.tax_code_id else None,
                }
                for line in bill.lines
            ],
        }

        input_data = RecurringTemplateInput(
            template_name=template_name,
            entity_type=RecurringEntityType.BILL,
            template_data=template_data,
            frequency=frequency,
            start_date=start_date,
            **kwargs,
        )

        return self.create_template(
            db,
            organization_id,
            input_data,
            created_by,
            source_entity_type="BILL",
            source_entity_id=bill_id,
        )

    def create_from_expense(
        self,
        db: Session,
        organization_id: UUID,
        expense_id: UUID,
        template_name: str,
        frequency: RecurringFrequency,
        start_date: date,
        created_by: UUID,
        **kwargs,
    ) -> RecurringTemplate:
        """Create a recurring template from an existing expense."""
        from app.models.finance.exp import ExpenseEntry

        expense = db.get(ExpenseEntry, expense_id)
        if not expense:
            raise HTTPException(status_code=404, detail="Expense not found")

        if expense.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="Access denied")

        template_data = {
            "description": expense.description,
            "expense_account_id": str(expense.expense_account_id),
            "payment_account_id": str(expense.payment_account_id) if expense.payment_account_id else None,
            "amount": str(expense.amount),
            "currency_code": expense.currency_code,
            "tax_code_id": str(expense.tax_code_id) if expense.tax_code_id else None,
            "project_id": str(expense.project_id) if expense.project_id else None,
            "cost_center_id": str(expense.cost_center_id) if expense.cost_center_id else None,
            "payment_method": expense.payment_method.value,
            "payee": expense.payee,
        }

        input_data = RecurringTemplateInput(
            template_name=template_name,
            entity_type=RecurringEntityType.EXPENSE,
            template_data=template_data,
            frequency=frequency,
            start_date=start_date,
            **kwargs,
        )

        return self.create_template(
            db,
            organization_id,
            input_data,
            created_by,
            source_entity_type="EXPENSE",
            source_entity_id=expense_id,
        )

    def get(self, db: Session, template_id: UUID) -> Optional[RecurringTemplate]:
        """Get a template by ID."""
        return db.get(RecurringTemplate, template_id)

    def list(
        self,
        db: Session,
        organization_id: UUID,
        entity_type: Optional[RecurringEntityType] = None,
        status: Optional[RecurringStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[RecurringTemplate]:
        """List recurring templates."""
        query = select(RecurringTemplate).where(
            RecurringTemplate.organization_id == organization_id
        )

        if entity_type:
            query = query.where(RecurringTemplate.entity_type == entity_type)
        if status:
            query = query.where(RecurringTemplate.status == status)

        query = query.order_by(RecurringTemplate.created_at.desc())
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def get_due_templates(self, db: Session, as_of_date: Optional[date] = None) -> List[RecurringTemplate]:
        """Get all templates due for generation."""
        check_date = as_of_date or date.today()

        query = select(RecurringTemplate).where(
            and_(
                RecurringTemplate.status == RecurringStatus.ACTIVE,
                RecurringTemplate.next_run_date <= check_date,
            )
        )

        return list(db.execute(query).scalars().all())

    def generate_invoice(
        self,
        db: Session,
        template: RecurringTemplate,
    ) -> GenerationResult:
        """Generate an invoice from a template."""
        from app.models.finance.ar import Invoice, InvoiceLine, InvoiceStatus, InvoiceType
        from app.services.finance.numbering import numbering_service

        try:
            data = template.template_data
            invoice_date = date.today()
            due_date = invoice_date + timedelta(days=template.days_before_due)

            # Get next number
            invoice_number = numbering_service.get_next_number_sync(
                db, template.organization_id, "INVOICE"
            )

            # Calculate amounts from lines
            subtotal = Decimal("0")
            tax_amount = Decimal("0")
            lines_data = data.get("lines", [])

            for line_data in lines_data:
                qty = Decimal(line_data.get("quantity", "1"))
                price = Decimal(line_data.get("unit_price", "0"))
                subtotal += qty * price
                # TODO: Calculate tax from tax_code

            total_amount = subtotal + tax_amount

            invoice = Invoice(
                organization_id=template.organization_id,
                customer_id=UUID(data["customer_id"]),
                invoice_number=invoice_number,
                invoice_type=InvoiceType.STANDARD,
                invoice_date=invoice_date,
                due_date=due_date,
                currency_code=data.get(
                    "currency_code",
                    settings.default_functional_currency_code,
                ),
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=total_amount,
                functional_currency_amount=total_amount,
                status=InvoiceStatus.DRAFT,
                ar_control_account_id=UUID(data["ar_control_account_id"]),
                billing_address=data.get("billing_address"),
                shipping_address=data.get("shipping_address"),
                notes=data.get("notes"),
                created_by_user_id=template.created_by,
            )
            db.add(invoice)
            db.flush()

            # Create lines
            for idx, line_data in enumerate(lines_data, 1):
                qty = Decimal(line_data.get("quantity", "1"))
                price = Decimal(line_data.get("unit_price", "0"))
                line_amount = qty * price

                line = InvoiceLine(
                    invoice_id=invoice.invoice_id,
                    line_number=idx,
                    description=line_data.get("description", ""),
                    quantity=qty,
                    unit_price=price,
                    line_amount=line_amount,
                    tax_amount=Decimal("0"),
                    total_amount=line_amount,
                    account_id=UUID(line_data["account_id"]),
                )
                db.add(line)

            db.flush()

            return GenerationResult(
                success=True,
                entity_type="INVOICE",
                entity_id=invoice.invoice_id,
                entity_number=invoice_number,
            )

        except Exception as e:
            logger.exception("Failed to generate invoice from template %s", template.template_id)
            return GenerationResult(
                success=False,
                error_message=str(e),
            )

    def generate_expense(
        self,
        db: Session,
        template: RecurringTemplate,
    ) -> GenerationResult:
        """Generate an expense from a template."""
        from app.models.finance.exp import ExpenseEntry, ExpenseStatus, PaymentMethod
        from app.services.finance.numbering import numbering_service

        try:
            data = template.template_data

            expense_number = numbering_service.get_next_number_sync(
                db, template.organization_id, "EXPENSE"
            )

            expense = ExpenseEntry(
                organization_id=template.organization_id,
                expense_number=expense_number,
                description=data.get("description", "Recurring expense"),
                expense_date=date.today(),
                expense_account_id=UUID(data["expense_account_id"]),
                payment_account_id=UUID(data["payment_account_id"]) if data.get("payment_account_id") else None,
                amount=Decimal(data.get("amount", "0")),
                currency_code=data.get(
                    "currency_code",
                    settings.default_functional_currency_code,
                ),
                tax_code_id=UUID(data["tax_code_id"]) if data.get("tax_code_id") else None,
                project_id=UUID(data["project_id"]) if data.get("project_id") else None,
                cost_center_id=UUID(data["cost_center_id"]) if data.get("cost_center_id") else None,
                payment_method=PaymentMethod(data.get("payment_method", "CASH")),
                payee=data.get("payee"),
                status=ExpenseStatus.DRAFT,
                created_by=template.created_by,
            )
            db.add(expense)
            db.flush()

            return GenerationResult(
                success=True,
                entity_type="EXPENSE",
                entity_id=expense.expense_id,
                entity_number=expense_number,
            )

        except Exception as e:
            logger.exception("Failed to generate expense from template %s", template.template_id)
            return GenerationResult(
                success=False,
                error_message=str(e),
            )

    def generate_next(
        self,
        db: Session,
        template: RecurringTemplate,
    ) -> RecurringLog:
        """Generate the next occurrence for a template."""
        scheduled_date = template.next_run_date or date.today()

        # Check if still valid
        if template.status != RecurringStatus.ACTIVE:
            log = RecurringLog(
                template_id=template.template_id,
                scheduled_date=scheduled_date,
                status=RecurringLogStatus.SKIPPED,
                skip_reason=f"Template status is {template.status.value}",
            )
            db.add(log)
            db.flush()
            return log

        if template.end_date and date.today() > template.end_date:
            template.status = RecurringStatus.EXPIRED
            log = RecurringLog(
                template_id=template.template_id,
                scheduled_date=scheduled_date,
                status=RecurringLogStatus.SKIPPED,
                skip_reason="Template has expired",
            )
            db.add(log)
            db.flush()
            return log

        if template.occurrences_limit and template.occurrences_count >= template.occurrences_limit:
            template.status = RecurringStatus.COMPLETED
            log = RecurringLog(
                template_id=template.template_id,
                scheduled_date=scheduled_date,
                status=RecurringLogStatus.SKIPPED,
                skip_reason="Occurrence limit reached",
            )
            db.add(log)
            db.flush()
            return log

        # Generate the entity
        result: GenerationResult
        if template.entity_type == RecurringEntityType.INVOICE:
            result = self.generate_invoice(db, template)
        elif template.entity_type == RecurringEntityType.EXPENSE:
            result = self.generate_expense(db, template)
        else:
            result = GenerationResult(
                success=False,
                error_message=f"Entity type {template.entity_type.value} not yet supported",
            )

        # Create log entry
        log = RecurringLog(
            template_id=template.template_id,
            scheduled_date=scheduled_date,
            status=RecurringLogStatus.SUCCESS if result.success else RecurringLogStatus.FAILED,
            generated_entity_type=result.entity_type,
            generated_entity_id=result.entity_id,
            generated_entity_number=result.entity_number,
            error_message=result.error_message,
        )
        db.add(log)

        if result.success:
            # Update template
            template.occurrences_count += 1
            template.last_generated_at = datetime.utcnow()
            template.last_generated_id = result.entity_id
            template.next_run_date = self.calculate_next_run_date(
                date.today(),
                template.frequency,
                template.schedule_config,
            )

        db.flush()
        return log

    def run_due_templates(self, db: Session) -> List[RecurringLog]:
        """Run all due templates and return logs."""
        templates = self.get_due_templates(db)
        logs = []

        for template in templates:
            try:
                log = self.generate_next(db, template)
                logs.append(log)
            except Exception as e:
                logger.exception("Error processing template %s", template.template_id)
                log = RecurringLog(
                    template_id=template.template_id,
                    scheduled_date=template.next_run_date or date.today(),
                    status=RecurringLogStatus.FAILED,
                    error_message=str(e),
                )
                db.add(log)
                logs.append(log)

        db.flush()
        return logs

    def pause(self, db: Session, template_id: UUID) -> RecurringTemplate:
        """Pause a recurring template."""
        template = db.get(RecurringTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        if template.status != RecurringStatus.ACTIVE:
            raise HTTPException(status_code=400, detail="Template is not active")

        template.status = RecurringStatus.PAUSED
        db.flush()
        return template

    def resume(self, db: Session, template_id: UUID) -> RecurringTemplate:
        """Resume a paused recurring template."""
        template = db.get(RecurringTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        if template.status != RecurringStatus.PAUSED:
            raise HTTPException(status_code=400, detail="Template is not paused")

        template.status = RecurringStatus.ACTIVE
        # Recalculate next run date if in the past
        if template.next_run_date and template.next_run_date < date.today():
            template.next_run_date = self.calculate_next_run_date(
                date.today(),
                template.frequency,
                template.schedule_config,
            )
        db.flush()
        return template

    def cancel(self, db: Session, template_id: UUID) -> RecurringTemplate:
        """Cancel a recurring template."""
        template = db.get(RecurringTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found")

        template.status = RecurringStatus.CANCELLED
        db.flush()
        return template

    def delete(self, db: Session, template_id: UUID) -> bool:
        """Delete a recurring template."""
        template = db.get(RecurringTemplate, template_id)
        if not template:
            return False

        db.delete(template)
        db.flush()
        return True


# Singleton instance
recurring_service = RecurringService()
