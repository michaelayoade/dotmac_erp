"""Create Fleet Management schema and tables.

Revision ID: 20260202_create_fleet_management_schema
Revises: 20260202_add_phase4_optimization_indexes
Create Date: 2026-02-02

This migration creates:
- fleet schema for vehicle fleet management module
- Enums for vehicle status, types, maintenance, incidents, etc.
- Tables: vehicle, vehicle_assignment, vehicle_document, maintenance_record,
          fuel_log_entry, vehicle_incident, vehicle_reservation
"""
from alembic import op
from app.alembic_utils import ensure_enum

revision = "20260202_create_fleet_management_schema"
down_revision = "20260202_add_phase4_optimization_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create fleet schema
    op.execute("CREATE SCHEMA IF NOT EXISTS fleet")

    # Create enums
    bind = op.get_bind()

    ensure_enum(
        bind,
        "vehicle_status",
        "ACTIVE",
        "MAINTENANCE",
        "OUT_OF_SERVICE",
        "RESERVED",
        "DISPOSED",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "vehicle_type",
        "SEDAN",
        "SUV",
        "PICKUP",
        "VAN",
        "TRUCK",
        "MOTORCYCLE",
        "BUS",
        "MINIBUS",
        "HEAVY_EQUIPMENT",
        "OTHER",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "fuel_type",
        "PETROL",
        "DIESEL",
        "ELECTRIC",
        "HYBRID",
        "CNG",
        "LPG",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "ownership_type",
        "OWNED",
        "LEASED",
        "RENTED",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "assignment_type",
        "PERSONAL",
        "DEPARTMENT",
        "POOL",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "maintenance_type",
        "PREVENTIVE",
        "CORRECTIVE",
        "INSPECTION",
        "TIRE",
        "BODY",
        "ACCIDENT_REPAIR",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "maintenance_status",
        "SCHEDULED",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "incident_type",
        "ACCIDENT",
        "THEFT",
        "VANDALISM",
        "BREAKDOWN",
        "TRAFFIC_VIOLATION",
        "OTHER",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "incident_severity",
        "MINOR",
        "MODERATE",
        "MAJOR",
        "TOTAL_LOSS",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "incident_status",
        "REPORTED",
        "INVESTIGATING",
        "INSURANCE_FILED",
        "RESOLVED",
        "CLOSED",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "document_type",
        "REGISTRATION",
        "INSURANCE",
        "INSPECTION",
        "ROAD_WORTHINESS",
        "PERMIT",
        "LICENSE",
        "OTHER",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "reservation_status",
        "PENDING",
        "APPROVED",
        "REJECTED",
        "ACTIVE",
        "COMPLETED",
        "CANCELLED",
        "NO_SHOW",
        schema="fleet",
    )

    ensure_enum(
        bind,
        "disposal_method",
        "SOLD",
        "SCRAPPED",
        "TRADED_IN",
        "RETURNED",
        "DONATED",
        "TRANSFERRED",
        schema="fleet",
    )

    # -------------------------------------------------------------------------
    # Create fleet.vehicle table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.vehicle (
            vehicle_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),

            -- Identifiers
            vehicle_code VARCHAR(30) NOT NULL,
            registration_number VARCHAR(20) NOT NULL,
            vin VARCHAR(50),
            engine_number VARCHAR(50),

            -- Specifications
            make VARCHAR(50) NOT NULL,
            model VARCHAR(50) NOT NULL,
            year INTEGER NOT NULL,
            color VARCHAR(30),
            vehicle_type fleet.vehicle_type NOT NULL DEFAULT 'SEDAN',
            fuel_type fleet.fuel_type NOT NULL DEFAULT 'PETROL',
            transmission VARCHAR(20),
            engine_capacity_cc INTEGER,
            seating_capacity INTEGER NOT NULL DEFAULT 5,
            fuel_tank_capacity_liters NUMERIC(6,2),
            expected_fuel_efficiency NUMERIC(6,2),

            -- Ownership
            ownership_type fleet.ownership_type NOT NULL DEFAULT 'OWNED',
            purchase_date DATE,
            purchase_price NUMERIC(18,2),
            lease_start_date DATE,
            lease_end_date DATE,
            lease_monthly_cost NUMERIC(18,2),
            vendor_id UUID REFERENCES ap.supplier(supplier_id),

            -- Assignment
            assignment_type fleet.assignment_type NOT NULL DEFAULT 'POOL',
            assigned_employee_id UUID REFERENCES hr.employee(employee_id),
            assigned_department_id UUID REFERENCES hr.department(department_id),
            assigned_cost_center_id UUID REFERENCES core_org.cost_center(cost_center_id),

            -- Status & Tracking
            status fleet.vehicle_status NOT NULL DEFAULT 'ACTIVE',
            current_odometer INTEGER NOT NULL DEFAULT 0 CHECK (current_odometer >= 0),
            last_odometer_date DATE,

            -- GPS/Telematics
            has_gps_tracker BOOLEAN NOT NULL DEFAULT FALSE,
            gps_device_id VARCHAR(50),
            last_known_location VARCHAR(200),
            last_location_update TIMESTAMPTZ,

            -- Disposal
            disposal_date DATE,
            disposal_method fleet.disposal_method,
            disposal_amount NUMERIC(18,2),
            disposal_notes TEXT,

            -- Notes
            notes TEXT,

            -- Audit fields
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id),
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,

            CONSTRAINT uq_fleet_vehicle_org_reg UNIQUE (organization_id, registration_number),
            CONSTRAINT uq_fleet_vehicle_org_code UNIQUE (organization_id, vehicle_code)
        );

        CREATE INDEX idx_fleet_vehicle_org ON fleet.vehicle(organization_id);
        CREATE INDEX idx_fleet_vehicle_status ON fleet.vehicle(organization_id, status);
        CREATE INDEX idx_fleet_vehicle_type ON fleet.vehicle(organization_id, vehicle_type);
        CREATE INDEX idx_fleet_vehicle_assignment ON fleet.vehicle(organization_id, assignment_type);
        CREATE INDEX idx_fleet_vehicle_employee ON fleet.vehicle(assigned_employee_id);
        CREATE INDEX idx_fleet_vehicle_deleted ON fleet.vehicle(is_deleted) WHERE is_deleted = FALSE;

        COMMENT ON TABLE fleet.vehicle IS 'Organization vehicle fleet registry';
        COMMENT ON COLUMN fleet.vehicle.vehicle_code IS 'Internal fleet code (e.g., FLT-001)';
        COMMENT ON COLUMN fleet.vehicle.registration_number IS 'License plate / registration number';
        COMMENT ON COLUMN fleet.vehicle.vin IS 'Vehicle Identification Number (chassis number)';
        COMMENT ON COLUMN fleet.vehicle.current_odometer IS 'Current odometer reading in kilometers';
        COMMENT ON COLUMN fleet.vehicle.expected_fuel_efficiency IS 'Expected km/liter';
    """)

    # -------------------------------------------------------------------------
    # Create fleet.vehicle_assignment table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.vehicle_assignment (
            assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            vehicle_id UUID NOT NULL REFERENCES fleet.vehicle(vehicle_id) ON DELETE CASCADE,
            employee_id UUID REFERENCES hr.employee(employee_id),
            department_id UUID REFERENCES hr.department(department_id),

            assignment_type fleet.assignment_type NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE,
            start_odometer INTEGER,
            end_odometer INTEGER,
            reason VARCHAR(200),
            notes TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,

            -- Audit fields
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_fleet_assignment_vehicle_dates ON fleet.vehicle_assignment(vehicle_id, start_date, end_date);
        CREATE INDEX idx_fleet_assignment_employee ON fleet.vehicle_assignment(employee_id);
        CREATE INDEX idx_fleet_assignment_active ON fleet.vehicle_assignment(organization_id, is_active);

        COMMENT ON TABLE fleet.vehicle_assignment IS 'Vehicle assignment history to employees/departments';
        COMMENT ON COLUMN fleet.vehicle_assignment.end_date IS 'NULL means assignment is still active';
    """)

    # -------------------------------------------------------------------------
    # Create fleet.vehicle_document table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.vehicle_document (
            document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            vehicle_id UUID NOT NULL REFERENCES fleet.vehicle(vehicle_id) ON DELETE CASCADE,

            document_type fleet.document_type NOT NULL,
            document_number VARCHAR(50),
            description VARCHAR(200) NOT NULL,

            -- Validity
            issue_date DATE,
            expiry_date DATE,

            -- Insurance-specific
            provider_name VARCHAR(100),
            policy_number VARCHAR(50),
            coverage_amount NUMERIC(18,2),
            premium_amount NUMERIC(18,2),

            -- File attachment
            file_path VARCHAR(500),
            file_name VARCHAR(200),

            -- Reminder
            reminder_days_before INTEGER NOT NULL DEFAULT 30,
            reminder_sent BOOLEAN NOT NULL DEFAULT FALSE,

            notes TEXT,

            -- Audit fields
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_fleet_doc_vehicle_type ON fleet.vehicle_document(vehicle_id, document_type);
        CREATE INDEX idx_fleet_doc_expiry ON fleet.vehicle_document(organization_id, expiry_date);
        CREATE INDEX idx_fleet_doc_reminder ON fleet.vehicle_document(organization_id, reminder_sent, expiry_date);

        COMMENT ON TABLE fleet.vehicle_document IS 'Vehicle documents: insurance, registration, permits';
        COMMENT ON COLUMN fleet.vehicle_document.document_number IS 'Policy number, certificate number, etc.';
        COMMENT ON COLUMN fleet.vehicle_document.reminder_days_before IS 'Days before expiry to send reminder';
    """)

    # -------------------------------------------------------------------------
    # Create fleet.maintenance_record table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.maintenance_record (
            maintenance_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            vehicle_id UUID NOT NULL REFERENCES fleet.vehicle(vehicle_id) ON DELETE CASCADE,

            maintenance_type fleet.maintenance_type NOT NULL,
            description VARCHAR(500) NOT NULL,
            scheduled_date DATE NOT NULL,
            completed_date DATE,
            status fleet.maintenance_status NOT NULL DEFAULT 'SCHEDULED',

            -- Odometer
            odometer_at_service INTEGER,
            next_service_odometer INTEGER,
            next_service_date DATE,

            -- Cost tracking
            estimated_cost NUMERIC(18,2),
            actual_cost NUMERIC(18,2),
            supplier_id UUID REFERENCES ap.supplier(supplier_id),
            invoice_id UUID REFERENCES ap.supplier_invoice(invoice_id),
            invoice_number VARCHAR(50),

            -- Work details
            work_performed TEXT,
            parts_replaced TEXT,
            technician_name VARCHAR(100),

            notes TEXT,

            -- Audit fields
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_fleet_maint_vehicle_date ON fleet.maintenance_record(vehicle_id, scheduled_date);
        CREATE INDEX idx_fleet_maint_status ON fleet.maintenance_record(organization_id, status);
        CREATE INDEX idx_fleet_maint_type ON fleet.maintenance_record(organization_id, maintenance_type);
        CREATE INDEX idx_fleet_maint_scheduled ON fleet.maintenance_record(organization_id, status, scheduled_date);

        COMMENT ON TABLE fleet.maintenance_record IS 'Vehicle maintenance and service records';
        COMMENT ON COLUMN fleet.maintenance_record.next_service_odometer IS 'Odometer reading for next service (e.g., +5000km)';
    """)

    # -------------------------------------------------------------------------
    # Create fleet.fuel_log_entry table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.fuel_log_entry (
            fuel_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            vehicle_id UUID NOT NULL REFERENCES fleet.vehicle(vehicle_id) ON DELETE CASCADE,
            employee_id UUID REFERENCES hr.employee(employee_id),

            log_date DATE NOT NULL,
            fuel_type fleet.fuel_type NOT NULL,
            quantity_liters NUMERIC(10,3) NOT NULL,
            price_per_liter NUMERIC(10,4) NOT NULL,
            total_cost NUMERIC(18,2) NOT NULL,

            odometer_reading INTEGER NOT NULL,

            -- Station details
            station_name VARCHAR(100),
            station_location VARCHAR(200),
            receipt_number VARCHAR(50),

            is_full_tank BOOLEAN NOT NULL DEFAULT TRUE,

            -- Expense link
            expense_claim_id UUID REFERENCES expense.expense_claim(claim_id),

            notes TEXT,

            -- Audit fields
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_fleet_fuel_vehicle_date ON fleet.fuel_log_entry(vehicle_id, log_date);
        CREATE INDEX idx_fleet_fuel_employee ON fleet.fuel_log_entry(employee_id);
        CREATE INDEX idx_fleet_fuel_org_date ON fleet.fuel_log_entry(organization_id, log_date);

        COMMENT ON TABLE fleet.fuel_log_entry IS 'Fuel purchase and consumption records';
        COMMENT ON COLUMN fleet.fuel_log_entry.is_full_tank IS 'True if filled to full tank (needed for efficiency calc)';
    """)

    # -------------------------------------------------------------------------
    # Create fleet.vehicle_incident table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.vehicle_incident (
            incident_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            vehicle_id UUID NOT NULL REFERENCES fleet.vehicle(vehicle_id) ON DELETE CASCADE,
            reported_by_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            driver_id UUID REFERENCES hr.employee(employee_id),

            incident_type fleet.incident_type NOT NULL,
            severity fleet.incident_severity NOT NULL,
            incident_date DATE NOT NULL,
            incident_time VARCHAR(10),
            location VARCHAR(300),
            description TEXT NOT NULL,

            status fleet.incident_status NOT NULL DEFAULT 'REPORTED',

            -- Police/legal
            police_report_number VARCHAR(50),
            police_report_date DATE,
            third_party_involved BOOLEAN NOT NULL DEFAULT FALSE,
            third_party_details TEXT,

            -- Insurance
            insurance_claim_number VARCHAR(50),
            insurance_claim_date DATE,
            insurance_claim_status VARCHAR(30),
            insurance_payout NUMERIC(18,2),

            -- Costs
            estimated_repair_cost NUMERIC(18,2),
            actual_repair_cost NUMERIC(18,2),
            other_costs NUMERIC(18,2),
            expense_claim_id UUID REFERENCES expense.expense_claim(claim_id),

            -- Resolution
            resolution_date DATE,
            resolution_notes TEXT,

            notes TEXT,

            -- Audit fields
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id),
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_fleet_incident_vehicle_date ON fleet.vehicle_incident(vehicle_id, incident_date);
        CREATE INDEX idx_fleet_incident_status ON fleet.vehicle_incident(organization_id, status);
        CREATE INDEX idx_fleet_incident_type ON fleet.vehicle_incident(organization_id, incident_type);
        CREATE INDEX idx_fleet_incident_driver ON fleet.vehicle_incident(driver_id);

        COMMENT ON TABLE fleet.vehicle_incident IS 'Vehicle incidents: accidents, theft, violations';
        COMMENT ON COLUMN fleet.vehicle_incident.insurance_claim_status IS 'PENDING, APPROVED, REJECTED, SETTLED';
    """)

    # -------------------------------------------------------------------------
    # Create fleet.vehicle_reservation table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE fleet.vehicle_reservation (
            reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            vehicle_id UUID NOT NULL REFERENCES fleet.vehicle(vehicle_id) ON DELETE CASCADE,
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),

            -- Reservation period
            start_datetime TIMESTAMPTZ NOT NULL,
            end_datetime TIMESTAMPTZ NOT NULL,
            actual_start_datetime TIMESTAMPTZ,
            actual_end_datetime TIMESTAMPTZ,

            -- Trip details
            purpose VARCHAR(500) NOT NULL,
            destination VARCHAR(300),
            estimated_distance_km INTEGER,

            status fleet.reservation_status NOT NULL DEFAULT 'PENDING',

            -- Approval
            approved_by_id UUID REFERENCES hr.employee(employee_id),
            approved_at TIMESTAMPTZ,
            rejection_reason VARCHAR(300),

            -- Odometer
            start_odometer INTEGER,
            end_odometer INTEGER,

            notes TEXT,

            -- Audit fields
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_fleet_reservation_vehicle_dates ON fleet.vehicle_reservation(vehicle_id, start_datetime, end_datetime);
        CREATE INDEX idx_fleet_reservation_employee ON fleet.vehicle_reservation(employee_id);
        CREATE INDEX idx_fleet_reservation_status ON fleet.vehicle_reservation(organization_id, status);
        CREATE INDEX idx_fleet_reservation_pending ON fleet.vehicle_reservation(organization_id, status, start_datetime);

        COMMENT ON TABLE fleet.vehicle_reservation IS 'Pool vehicle reservation requests';
        COMMENT ON COLUMN fleet.vehicle_reservation.actual_start_datetime IS 'When vehicle was actually picked up';
        COMMENT ON COLUMN fleet.vehicle_reservation.actual_end_datetime IS 'When vehicle was actually returned';
    """)


def downgrade() -> None:
    # Drop tables in reverse order (respect foreign key dependencies)
    op.execute("DROP TABLE IF EXISTS fleet.vehicle_reservation CASCADE")
    op.execute("DROP TABLE IF EXISTS fleet.vehicle_incident CASCADE")
    op.execute("DROP TABLE IF EXISTS fleet.fuel_log_entry CASCADE")
    op.execute("DROP TABLE IF EXISTS fleet.maintenance_record CASCADE")
    op.execute("DROP TABLE IF EXISTS fleet.vehicle_document CASCADE")
    op.execute("DROP TABLE IF EXISTS fleet.vehicle_assignment CASCADE")
    op.execute("DROP TABLE IF EXISTS fleet.vehicle CASCADE")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS fleet.disposal_method CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.reservation_status CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.document_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.incident_status CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.incident_severity CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.incident_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.maintenance_status CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.maintenance_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.assignment_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.ownership_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.fuel_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.vehicle_type CASCADE")
    op.execute("DROP TYPE IF EXISTS fleet.vehicle_status CASCADE")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS fleet CASCADE")
