"""Employee Extended Data routes - Documents, Qualifications, Certifications, Dependents, Skills."""

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.hr import Employee, DocumentType, QualificationType, RelationshipType, SkillCategory
from app.services.people.hr import (
    EmployeeService,
    EmployeeDocumentService,
    EmployeeQualificationService,
    EmployeeCertificationService,
    EmployeeDependentService,
    SkillService,
    EmployeeSkillService,
)
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext

from ._common import _parse_bool


router = APIRouter()


# =============================================================================
# Employee Documents
# =============================================================================


@router.get("/employees/{employee_id}/documents", response_class=HTMLResponse)
def list_employee_documents(
    request: Request,
    employee_id: str,
    document_type: Optional[str] = None,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List documents for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    doc_type = DocumentType(document_type) if document_type else None
    documents = doc_svc.list_documents(emp_id, document_type=doc_type)

    context = base_context(request, auth, f"Documents - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "documents": documents,
        "document_types": list(DocumentType),
        "selected_type": document_type,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/documents.html", context)


@router.get("/employees/{employee_id}/documents/new", response_class=HTMLResponse)
def new_document_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New document upload form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Upload Document - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "document_types": list(DocumentType),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/document_form.html", context)


@router.post("/employees/{employee_id}/documents/new", response_class=HTMLResponse)
def create_document(
    request: Request,
    employee_id: str,
    document_type: str = Form(...),
    document_name: str = Form(...),
    file_path: str = Form(...),
    file_name: str = Form(...),
    description: Optional[str] = Form(None),
    issue_date: Optional[str] = Form(None),
    expiry_date: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new document record."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    from datetime import datetime as dt

    try:
        doc_svc.create_document(
            employee_id=emp_id,
            document_type=DocumentType(document_type),
            document_name=document_name,
            file_path=file_path,
            file_name=file_name,
            description=description or None,
            issue_date=dt.strptime(issue_date, "%Y-%m-%d").date() if issue_date else None,
            expiry_date=dt.strptime(expiry_date, "%Y-%m-%d").date() if expiry_date else None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?success=Document+uploaded",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        emp_svc = EmployeeService(db, org_id)
        employee = emp_svc.get_employee(emp_id)
        context = base_context(request, auth, f"Upload Document - {employee.full_name}", "employees", db=db)
        context.update({
            "employee": employee,
            "document_types": list(DocumentType),
            "form_data": {
                "document_type": document_type,
                "document_name": document_name,
                "file_path": file_path,
                "file_name": file_name,
                "description": description,
                "issue_date": issue_date,
                "expiry_date": expiry_date,
            },
            "error": str(e),
        })
        return templates.TemplateResponse(request, "people/hr/employee/document_form.html", context)


@router.post("/employees/{employee_id}/documents/{document_id}/verify", response_class=HTMLResponse)
def verify_document(
    request: Request,
    employee_id: str,
    document_id: str,
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Verify a document."""
    org_id = coerce_uuid(auth.organization_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    try:
        verifier = db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.person_id == auth.user_id,
            )
        )
        verifier_id = verifier.employee_id if verifier else None

        doc_svc.verify_document(
            document_id=coerce_uuid(document_id),
            verified_by_id=verifier_id,
            notes=notes,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?success=Document+verified",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/documents/{document_id}/delete", response_class=HTMLResponse)
def delete_document(
    request: Request,
    employee_id: str,
    document_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a document."""
    org_id = coerce_uuid(auth.organization_id)
    doc_svc = EmployeeDocumentService(db, org_id)

    try:
        doc_svc.delete_document(coerce_uuid(document_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?success=Document+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/documents?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Qualifications
# =============================================================================


@router.get("/employees/{employee_id}/qualifications", response_class=HTMLResponse)
def list_employee_qualifications(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List qualifications for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    qual_svc = EmployeeQualificationService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    qualifications = qual_svc.list_qualifications(emp_id)

    context = base_context(request, auth, f"Qualifications - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "qualifications": qualifications,
        "qualification_types": list(QualificationType),
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/qualifications.html", context)


@router.get("/employees/{employee_id}/qualifications/new", response_class=HTMLResponse)
def new_qualification_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New qualification form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Add Qualification - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "qualification_types": list(QualificationType),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/qualification_form.html", context)


@router.post("/employees/{employee_id}/qualifications/new", response_class=HTMLResponse)
def create_qualification(
    request: Request,
    employee_id: str,
    qualification_type: str = Form(...),
    qualification_name: str = Form(...),
    institution_name: str = Form(...),
    field_of_study: Optional[str] = Form(None),
    start_date: Optional[str] = Form(None),
    end_date: Optional[str] = Form(None),
    is_ongoing: Optional[str] = Form(None),
    grade: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new qualification."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    qual_svc = EmployeeQualificationService(db, org_id)

    from datetime import datetime as dt

    try:
        qual_svc.create_qualification(
            employee_id=emp_id,
            qualification_type=QualificationType(qualification_type),
            qualification_name=qualification_name,
            institution_name=institution_name,
            field_of_study=field_of_study or None,
            start_date=dt.strptime(start_date, "%Y-%m-%d").date() if start_date else None,
            end_date=dt.strptime(end_date, "%Y-%m-%d").date() if end_date else None,
            is_ongoing=_parse_bool(is_ongoing),
            grade=grade or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?success=Qualification+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/qualifications/{qualification_id}/delete")
def delete_qualification(
    request: Request,
    employee_id: str,
    qualification_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a qualification."""
    org_id = coerce_uuid(auth.organization_id)
    qual_svc = EmployeeQualificationService(db, org_id)

    try:
        qual_svc.delete_qualification(coerce_uuid(qualification_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?success=Qualification+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/qualifications?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Certifications
# =============================================================================


@router.get("/employees/{employee_id}/certifications", response_class=HTMLResponse)
def list_employee_certifications(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List certifications for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    cert_svc = EmployeeCertificationService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    certifications = cert_svc.list_certifications(emp_id)

    context = base_context(request, auth, f"Certifications - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "certifications": certifications,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/certifications.html", context)


@router.get("/employees/{employee_id}/certifications/new", response_class=HTMLResponse)
def new_certification_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New certification form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Add Certification - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/certification_form.html", context)


@router.post("/employees/{employee_id}/certifications/new", response_class=HTMLResponse)
def create_certification(
    request: Request,
    employee_id: str,
    certification_name: str = Form(...),
    issuing_authority: str = Form(...),
    issue_date: str = Form(...),
    expiry_date: Optional[str] = Form(None),
    does_not_expire: Optional[str] = Form(None),
    credential_id: Optional[str] = Form(None),
    credential_url: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new certification."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    cert_svc = EmployeeCertificationService(db, org_id)

    from datetime import datetime as dt

    try:
        cert_svc.create_certification(
            employee_id=emp_id,
            certification_name=certification_name,
            issuing_authority=issuing_authority,
            issue_date=dt.strptime(issue_date, "%Y-%m-%d").date(),
            expiry_date=dt.strptime(expiry_date, "%Y-%m-%d").date() if expiry_date else None,
            does_not_expire=_parse_bool(does_not_expire),
            credential_id=credential_id or None,
            credential_url=credential_url or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?success=Certification+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/certifications/{certification_id}/delete")
def delete_certification(
    request: Request,
    employee_id: str,
    certification_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a certification."""
    org_id = coerce_uuid(auth.organization_id)
    cert_svc = EmployeeCertificationService(db, org_id)

    try:
        cert_svc.delete_certification(coerce_uuid(certification_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?success=Certification+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/certifications?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Dependents
# =============================================================================


@router.get("/employees/{employee_id}/dependents", response_class=HTMLResponse)
def list_employee_dependents(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List dependents for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    dep_svc = EmployeeDependentService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    dependents = dep_svc.list_dependents(emp_id)

    context = base_context(request, auth, f"Dependents - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "dependents": dependents,
        "relationship_types": list(RelationshipType),
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/dependents.html", context)


@router.get("/employees/{employee_id}/dependents/new", response_class=HTMLResponse)
def new_dependent_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New dependent form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    context = base_context(request, auth, f"Add Dependent - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "relationship_types": list(RelationshipType),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/dependent_form.html", context)


@router.post("/employees/{employee_id}/dependents/new", response_class=HTMLResponse)
def create_dependent(
    request: Request,
    employee_id: str,
    full_name: str = Form(...),
    relationship: str = Form(...),
    date_of_birth: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    is_emergency_contact: Optional[str] = Form(None),
    emergency_contact_priority: Optional[str] = Form(None),
    is_beneficiary: Optional[str] = Form(None),
    beneficiary_percentage: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new dependent."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    dep_svc = EmployeeDependentService(db, org_id)

    from datetime import datetime as dt

    try:
        dep_svc.create_dependent(
            employee_id=emp_id,
            full_name=full_name,
            relationship=RelationshipType(relationship),
            date_of_birth=dt.strptime(date_of_birth, "%Y-%m-%d").date() if date_of_birth else None,
            gender=gender or None,
            phone=phone or None,
            email=email or None,
            is_emergency_contact=_parse_bool(is_emergency_contact),
            emergency_contact_priority=int(emergency_contact_priority) if emergency_contact_priority else None,
            is_beneficiary=_parse_bool(is_beneficiary),
            beneficiary_percentage=float(beneficiary_percentage) if beneficiary_percentage else None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?success=Dependent+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/dependents/{dependent_id}/delete")
def delete_dependent(
    request: Request,
    employee_id: str,
    dependent_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a dependent."""
    org_id = coerce_uuid(auth.organization_id)
    dep_svc = EmployeeDependentService(db, org_id)

    try:
        dep_svc.delete_dependent(coerce_uuid(dependent_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?success=Dependent+deleted",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/dependents?error={str(e)}",
            status_code=303,
        )


# =============================================================================
# Employee Skills
# =============================================================================


@router.get("/employees/{employee_id}/skills", response_class=HTMLResponse)
def list_employee_skills(
    request: Request,
    employee_id: str,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List skills for an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)

    emp_svc = EmployeeService(db, org_id)
    skill_svc = EmployeeSkillService(db, org_id)

    try:
        employee = emp_svc.get_employee(emp_id)
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    skills = skill_svc.list_employee_skills(emp_id)

    context = base_context(request, auth, f"Skills - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "employee_skills": skills,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/employee/skills.html", context)


@router.get("/employees/{employee_id}/skills/new", response_class=HTMLResponse)
def new_skill_form(
    request: Request,
    employee_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add skill form."""
    org_id = coerce_uuid(auth.organization_id)
    emp_svc = EmployeeService(db, org_id)
    catalog_svc = SkillService(db, org_id)

    try:
        employee = emp_svc.get_employee(coerce_uuid(employee_id))
    except Exception:
        return RedirectResponse(url="/people/hr/employees?error=Employee+not+found", status_code=303)

    skills = catalog_svc.list_skills()

    context = base_context(request, auth, f"Add Skill - {employee.full_name}", "employees", db=db)
    context.update({
        "employee": employee,
        "skills": skills,
        "skill_categories": list(SkillCategory),
        "form_data": {},
    })
    return templates.TemplateResponse(request, "people/hr/employee/skill_form.html", context)


@router.post("/employees/{employee_id}/skills/new", response_class=HTMLResponse)
def add_employee_skill(
    request: Request,
    employee_id: str,
    skill_id: str = Form(...),
    proficiency_level: int = Form(...),
    years_experience: Optional[str] = Form(None),
    is_primary: Optional[str] = Form(None),
    is_certified: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Add a skill to an employee."""
    org_id = coerce_uuid(auth.organization_id)
    emp_id = coerce_uuid(employee_id)
    skill_svc = EmployeeSkillService(db, org_id)

    try:
        skill_svc.add_skill(
            employee_id=emp_id,
            skill_id=coerce_uuid(skill_id),
            proficiency_level=proficiency_level,
            years_experience=float(years_experience) if years_experience else None,
            is_primary=_parse_bool(is_primary),
            is_certified=_parse_bool(is_certified),
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?success=Skill+added",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?error={str(e)}",
            status_code=303,
        )


@router.post("/employees/{employee_id}/skills/{employee_skill_id}/delete")
def remove_employee_skill(
    request: Request,
    employee_id: str,
    employee_skill_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Remove a skill from an employee."""
    org_id = coerce_uuid(auth.organization_id)
    skill_svc = EmployeeSkillService(db, org_id)

    try:
        skill_svc.remove_skill(coerce_uuid(employee_skill_id))
        db.commit()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?success=Skill+removed",
            status_code=303,
        )
    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/people/hr/employees/{employee_id}/skills?error={str(e)}",
            status_code=303,
        )
