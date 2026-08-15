"""
Training web routes.

Lists training programs and events with full CRUD operations.
All business logic is delegated to the training_web_service.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.people.training.web import training_web_service
from app.templates import templates
from app.web.deps import (
    WebAuthContext,
    base_context,
    get_db_for_org,
    require_self_service_access,
    require_training_access,
)

router = APIRouter(prefix="/training", tags=["people-training-web"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def training_index(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training landing page."""
    context = base_context(request, auth, "Training", "training", db=db)
    return templates.TemplateResponse(request, "people/training/index.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# Learning & Assessment Module - Authoring
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/courses", response_class=HTMLResponse)
def list_learning_courses(
    request: Request,
    search: str | None = None,
    department_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.courses_response(
        request, auth, db, search, department_id, status, page
    )


@router.get("/courses/new", response_class=HTMLResponse)
def new_learning_course_form(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.course_form_response(request, auth, db)


@router.post("/courses/new", response_class=HTMLResponse)
async def create_learning_course(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_course_response(request, auth, db)


@router.get("/assignments", response_class=HTMLResponse)
def list_learning_assignments(
    request: Request,
    course_id: str | None = None,
    assignment_source: str | None = None,
    success: str | None = None,
    error: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.assignments_response(
        request, auth, db, course_id, assignment_source, page, success, error
    )


@router.post("/assignments/employee", response_class=HTMLResponse)
async def assign_learning_course_employee(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.assign_employee_response(request, auth, db)


@router.post("/assignments/department", response_class=HTMLResponse)
async def assign_learning_course_department(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.assign_department_response(request, auth, db)


@router.post("/assignments/role", response_class=HTMLResponse)
async def assign_learning_course_role(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.assign_role_response(request, auth, db)


@router.post("/assignments/designation", response_class=HTMLResponse)
async def assign_learning_course_designation(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.assign_designation_response(request, auth, db)


@router.post("/assignments/team", response_class=HTMLResponse)
async def assign_learning_course_team(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.assign_team_response(request, auth, db)


@router.get("/academy/requirements", response_class=HTMLResponse)
def list_academy_requirements(
    request: Request,
    designation_id: str | None = None,
    include_inactive: str | None = None,
    success: str | None = None,
    error: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.academy_requirements_response(
        request,
        auth,
        db,
        designation_id,
        include_inactive,
        success,
        error,
        page,
    )


@router.post("/academy/requirements", response_class=HTMLResponse)
async def create_academy_requirement(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_academy_requirement_response(
        request,
        auth,
        db,
    )


@router.post(
    "/academy/requirements/{requirement_id}/archive",
    response_class=HTMLResponse,
)
def archive_academy_requirement(
    requirement_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.deactivate_academy_requirement_response(
        auth,
        db,
        requirement_id,
    )


@router.get("/academy/progress", response_class=HTMLResponse)
def list_academy_progress(
    request: Request,
    employee_id: str | None = None,
    designation_id: str | None = None,
    academy_course_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.academy_progress_response(
        request,
        auth,
        db,
        employee_id,
        designation_id,
        academy_course_id,
        status,
        page,
    )


@router.get("/academy/reports/{report_name}", response_class=HTMLResponse)
def academy_report(
    request: Request,
    report_name: str,
    designation_id: str | None = None,
    academy_course_id: str | None = None,
    export: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.academy_report_response(
        request,
        auth,
        db,
        report_name,
        designation_id,
        academy_course_id,
        export,
    )


@router.get("/my-courses", response_class=HTMLResponse)
def my_learning_courses(
    request: Request,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.my_courses_response(request, auth, db, status, page)


@router.get("/my-courses/{course_id}", response_class=HTMLResponse)
def my_learning_course_detail(
    request: Request,
    course_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.learner_course_response(
        request, auth, db, course_id, success, error
    )


@router.get("/lessons/{lesson_id}", response_class=HTMLResponse)
def view_learning_lesson(
    request: Request,
    lesson_id: str,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.lesson_response(request, auth, db, lesson_id, error)


@router.post("/lessons/{lesson_id}/complete", response_class=HTMLResponse)
def complete_learning_lesson(
    lesson_id: str,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.complete_lesson_response(auth, db, lesson_id)


@router.post("/assessments/{assessment_id}/start", response_class=HTMLResponse)
def start_learning_assessment(
    assessment_id: str,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.start_assessment_response(auth, db, assessment_id)


@router.get("/attempts/{attempt_id}", response_class=HTMLResponse)
def take_learning_assessment(
    request: Request,
    attempt_id: str,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.attempt_response(request, auth, db, attempt_id, error)


@router.post("/attempts/{attempt_id}/submit", response_class=HTMLResponse)
async def submit_learning_assessment(
    request: Request,
    attempt_id: str,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.submit_attempt_response(
        request, auth, db, attempt_id
    )


@router.get("/attempts/{attempt_id}/result", response_class=HTMLResponse)
def view_learning_assessment_result(
    request: Request,
    attempt_id: str,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.result_response(request, auth, db, attempt_id)


@router.get("/grading", response_class=HTMLResponse)
def learning_grading_queue(
    request: Request,
    course_id: str | None = None,
    assessment_id: str | None = None,
    success: str | None = None,
    error: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.grading_queue_response(
        request, auth, db, course_id, assessment_id, page, success, error
    )


@router.post("/grading/answers/{answer_id}", response_class=HTMLResponse)
async def grade_learning_answer(
    request: Request,
    answer_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.grade_answer_response(
        request, auth, db, answer_id
    )


@router.post("/attempts/{attempt_id}/finalize-grading", response_class=HTMLResponse)
def finalize_learning_attempt_grading(
    attempt_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.finalize_grading_response(auth, db, attempt_id)


@router.get("/learning-dashboard", response_class=HTMLResponse)
def learning_operational_dashboard(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.learning_dashboard_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/learning/{report_name}", response_class=HTMLResponse)
def learning_report(
    request: Request,
    report_name: str,
    employee_id: str | None = None,
    department_id: str | None = None,
    course_id: str | None = None,
    assessment_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    export: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.learning_report_response(
        request,
        auth,
        db,
        report_name,
        employee_id,
        department_id,
        course_id,
        assessment_id,
        start_date,
        end_date,
        export,
    )


@router.get("/courses/{course_id}", response_class=HTMLResponse)
def view_learning_course(
    request: Request,
    course_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.course_detail_response(
        request, auth, db, course_id, success, error
    )


@router.get("/courses/{course_id}/edit", response_class=HTMLResponse)
def edit_learning_course_form(
    request: Request,
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.course_form_response(request, auth, db, course_id)


@router.post("/courses/{course_id}/edit", response_class=HTMLResponse)
async def update_learning_course(
    request: Request,
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_course_response(
        request, auth, db, course_id
    )


@router.post("/courses/{course_id}/publish", response_class=HTMLResponse)
def publish_learning_course(
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.publish_course_response(auth, db, course_id)


@router.post("/courses/{course_id}/archive", response_class=HTMLResponse)
def archive_learning_course(
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.archive_course_response(auth, db, course_id)


@router.post("/courses/{course_id}/prerequisites", response_class=HTMLResponse)
async def add_learning_course_prerequisite(
    request: Request,
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.add_prerequisite_response(
        request, auth, db, course_id
    )


@router.post(
    "/courses/{course_id}/prerequisites/{prerequisite_course_id}/remove",
    response_class=HTMLResponse,
)
def remove_learning_course_prerequisite(
    course_id: str,
    prerequisite_course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.remove_prerequisite_response(
        auth, db, course_id, prerequisite_course_id
    )


@router.get("/courses/{course_id}/modules/new", response_class=HTMLResponse)
def new_learning_module_form(
    request: Request,
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.module_form_response(
        request, auth, db, course_id=course_id
    )


@router.post("/courses/{course_id}/modules/new", response_class=HTMLResponse)
async def create_learning_module(
    request: Request,
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_module_response(
        request, auth, db, course_id
    )


@router.post("/courses/{course_id}/modules/reorder", response_class=HTMLResponse)
async def reorder_learning_modules(
    request: Request,
    course_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.reorder_modules_response(
        request, auth, db, course_id
    )


@router.get("/modules/{module_id}/edit", response_class=HTMLResponse)
def edit_learning_module_form(
    request: Request,
    module_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.module_form_response(
        request, auth, db, module_id=module_id
    )


@router.post("/modules/{module_id}/edit", response_class=HTMLResponse)
async def update_learning_module(
    request: Request,
    module_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_module_response(
        request, auth, db, module_id
    )


@router.get("/modules/{module_id}/lessons/new", response_class=HTMLResponse)
def new_learning_lesson_form(
    request: Request,
    module_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.lesson_form_response(
        request, auth, db, module_id=module_id
    )


@router.post("/modules/{module_id}/lessons/new", response_class=HTMLResponse)
async def create_learning_lesson(
    request: Request,
    module_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_lesson_response(
        request, auth, db, module_id
    )


@router.get("/lessons/{lesson_id}/edit", response_class=HTMLResponse)
def edit_learning_lesson_form(
    request: Request,
    lesson_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.lesson_form_response(
        request, auth, db, lesson_id=lesson_id
    )


@router.post("/lessons/{lesson_id}/edit", response_class=HTMLResponse)
async def update_learning_lesson(
    request: Request,
    lesson_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_lesson_response(
        request, auth, db, lesson_id
    )


@router.get("/question-banks", response_class=HTMLResponse)
def list_question_banks(
    request: Request,
    search: str | None = None,
    department_id: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.question_banks_response(
        request, auth, db, search, department_id, page
    )


@router.get("/question-banks/new", response_class=HTMLResponse)
def new_question_bank_form(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.question_bank_form_response(request, auth, db)


@router.post("/question-banks/new", response_class=HTMLResponse)
async def create_question_bank(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_question_bank_response(request, auth, db)


@router.get("/question-banks/{bank_id}", response_class=HTMLResponse)
def view_question_bank(
    request: Request,
    bank_id: str,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.question_bank_detail_response(
        request, auth, db, bank_id, success, error
    )


@router.get("/question-banks/{bank_id}/edit", response_class=HTMLResponse)
def edit_question_bank_form(
    request: Request,
    bank_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.question_bank_form_response(request, auth, db, bank_id)


@router.post("/question-banks/{bank_id}/edit", response_class=HTMLResponse)
async def update_question_bank(
    request: Request,
    bank_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_question_bank_response(
        request, auth, db, bank_id
    )


@router.get("/question-banks/{bank_id}/questions/new", response_class=HTMLResponse)
def new_question_form(
    request: Request,
    bank_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.question_form_response(
        request, auth, db, bank_id=bank_id
    )


@router.post("/question-banks/{bank_id}/questions/new", response_class=HTMLResponse)
async def create_question(
    request: Request,
    bank_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_question_response(
        request, auth, db, bank_id
    )


@router.get("/questions/{question_id}/edit", response_class=HTMLResponse)
def edit_question_form(
    request: Request,
    question_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.question_form_response(
        request, auth, db, question_id=question_id
    )


@router.post("/questions/{question_id}/edit", response_class=HTMLResponse)
async def update_question(
    request: Request,
    question_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_question_response(
        request, auth, db, question_id
    )


@router.get("/modules/{module_id}/assessments/new", response_class=HTMLResponse)
def new_assessment_form(
    request: Request,
    module_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.assessment_form_response(
        request, auth, db, module_id=module_id
    )


@router.post("/modules/{module_id}/assessments/new", response_class=HTMLResponse)
async def create_assessment(
    request: Request,
    module_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.create_assessment_response(
        request, auth, db, module_id
    )


@router.get("/assessments/{assessment_id}/edit", response_class=HTMLResponse)
def edit_assessment_form(
    request: Request,
    assessment_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.assessment_form_response(
        request, auth, db, assessment_id=assessment_id
    )


@router.post("/assessments/{assessment_id}/edit", response_class=HTMLResponse)
async def update_assessment(
    request: Request,
    assessment_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_assessment_response(
        request, auth, db, assessment_id
    )


@router.post("/assessments/{assessment_id}/publish", response_class=HTMLResponse)
def publish_assessment(
    assessment_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.publish_assessment_response(auth, db, assessment_id)


@router.post("/assessments/{assessment_id}/archive", response_class=HTMLResponse)
def archive_assessment(
    assessment_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.archive_assessment_response(auth, db, assessment_id)


@router.get("/assessments/{assessment_id}/questions", response_class=HTMLResponse)
def manage_assessment_questions(
    request: Request,
    assessment_id: str,
    search: str | None = None,
    bank_id: str | None = None,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.assessment_questions_response(
        request, auth, db, assessment_id, search, bank_id, success, error
    )


@router.post("/assessments/{assessment_id}/questions", response_class=HTMLResponse)
async def attach_assessment_question(
    request: Request,
    assessment_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.attach_question_response(
        request, auth, db, assessment_id
    )


@router.post(
    "/assessments/{assessment_id}/questions/{question_id}/detach",
    response_class=HTMLResponse,
)
def detach_assessment_question(
    assessment_id: str,
    question_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return training_web_service.detach_question_response(
        auth, db, assessment_id, question_id
    )


@router.post(
    "/assessments/{assessment_id}/questions/{question_id}/edit",
    response_class=HTMLResponse,
)
async def update_assessment_question(
    request: Request,
    assessment_id: str,
    question_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.update_assessment_question_response(
        request, auth, db, assessment_id, question_id
    )


@router.post(
    "/assessments/{assessment_id}/questions/reorder", response_class=HTMLResponse
)
async def reorder_assessment_questions(
    request: Request,
    assessment_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    return await training_web_service.reorder_assessment_questions_response(
        request, auth, db, assessment_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Training Programs
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/programs", response_class=HTMLResponse)
def list_programs(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    category: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training programs list page."""
    return training_web_service.list_programs_response(
        request, auth, db, search, status, category, page
    )


@router.get("/programs/new", response_class=HTMLResponse)
def new_program_form(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """New training program form."""
    return training_web_service.program_new_form_response(request, auth, db)


@router.post("/programs/new", response_class=HTMLResponse)
async def create_program(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new training program."""
    return await training_web_service.create_program_response(request, auth, db)


@router.get("/programs/{program_id}", response_class=HTMLResponse)
def view_program(
    request: Request,
    program_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """View training program detail."""
    return training_web_service.program_detail_response(
        request, auth, db, program_id, success
    )


@router.get("/programs/{program_id}/edit", response_class=HTMLResponse)
def edit_program_form(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit training program form."""
    return training_web_service.program_edit_form_response(
        request, auth, db, program_id
    )


@router.post("/programs/{program_id}/edit", response_class=HTMLResponse)
async def update_program(
    request: Request,
    program_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Update a training program."""
    return await training_web_service.update_program_response(
        request, auth, db, program_id
    )


@router.post("/programs/{program_id}/activate", response_class=HTMLResponse)
def activate_program(
    program_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Activate a training program."""
    return training_web_service.activate_program_response(auth, db, program_id)


@router.post("/programs/{program_id}/retire", response_class=HTMLResponse)
def retire_program(
    program_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Retire a training program."""
    return training_web_service.retire_program_response(auth, db, program_id)


# ─────────────────────────────────────────────────────────────────────────────
# Training Events
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/events", response_class=HTMLResponse)
def list_events(
    request: Request,
    search: str | None = None,
    status: str | None = None,
    program_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training events list page."""
    return training_web_service.list_events_response(
        request, auth, db, search, status, program_id, start_date, end_date, page
    )


@router.get("/events/new", response_class=HTMLResponse)
def new_event_form(
    request: Request,
    program_id: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """New training event form."""
    return training_web_service.event_new_form_response(request, auth, db, program_id)


@router.post("/events/new", response_class=HTMLResponse)
async def create_event(
    request: Request,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Create a new training event."""
    return await training_web_service.create_event_response(request, auth, db)


@router.get("/events/{event_id}", response_class=HTMLResponse)
def view_event(
    request: Request,
    event_id: str,
    success: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """View training event detail."""
    return training_web_service.event_detail_response(
        request, auth, db, event_id, success
    )


@router.get("/events/{event_id}/edit", response_class=HTMLResponse)
def edit_event_form(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Edit training event form."""
    return training_web_service.event_edit_form_response(request, auth, db, event_id)


@router.post("/events/{event_id}/edit", response_class=HTMLResponse)
async def update_event(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Update a training event."""
    return await training_web_service.update_event_response(request, auth, db, event_id)


@router.post("/events/{event_id}/schedule", response_class=HTMLResponse)
def schedule_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Schedule a draft event."""
    return training_web_service.schedule_event_response(auth, db, event_id)


@router.post("/events/{event_id}/start", response_class=HTMLResponse)
def start_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Start a training event."""
    return training_web_service.start_event_response(auth, db, event_id)


@router.post("/events/{event_id}/complete", response_class=HTMLResponse)
def complete_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Complete a training event."""
    return training_web_service.complete_event_response(auth, db, event_id)


@router.post("/events/{event_id}/cancel", response_class=HTMLResponse)
def cancel_event(
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Cancel a training event."""
    return training_web_service.cancel_event_response(auth, db, event_id)


@router.get("/events/{event_id}/invite", response_class=HTMLResponse)
def invite_attendees_form(
    request: Request,
    event_id: str,
    search: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Invite attendees to a training event."""
    return training_web_service.invite_attendees_form_response(
        request, auth, db, event_id, search
    )


@router.post("/events/{event_id}/invite", response_class=HTMLResponse)
async def invite_attendees(
    request: Request,
    event_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Invite selected attendees to a training event."""
    return await training_web_service.invite_attendees_response(
        request, auth, db, event_id
    )


@router.post(
    "/events/{event_id}/attendees/{attendee_id}/confirm", response_class=HTMLResponse
)
def confirm_attendee(
    event_id: str,
    attendee_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Confirm attendance for an invited attendee."""
    return training_web_service.confirm_attendee_response(
        auth, db, event_id, attendee_id
    )


@router.post(
    "/events/{event_id}/attendees/{attendee_id}/attend", response_class=HTMLResponse
)
def mark_attendee_attended(
    event_id: str,
    attendee_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Mark an attendee as attended."""
    return training_web_service.mark_attended_response(auth, db, event_id, attendee_id)


@router.post(
    "/events/{event_id}/attendees/{attendee_id}/certificate",
    response_class=HTMLResponse,
)
def issue_attendee_certificate(
    event_id: str,
    attendee_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Issue a certificate to an attendee."""
    return training_web_service.issue_certificate_response(
        auth, db, event_id, attendee_id
    )


@router.post(
    "/events/{event_id}/attendees/{attendee_id}/remove", response_class=HTMLResponse
)
def remove_attendee(
    event_id: str,
    attendee_id: str,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Remove an attendee from an event."""
    return training_web_service.remove_attendee_response(
        auth, db, event_id, attendee_id
    )


# ─────────────────────────────────────────────────────────────────────────────
# Training Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/completion", response_class=HTMLResponse)
def report_completion(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training completion rates report."""
    return training_web_service.completion_report_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training participation by department report."""
    return training_web_service.by_department_report_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/cost-analysis", response_class=HTMLResponse)
def report_cost_analysis(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training cost analysis report."""
    return training_web_service.cost_analysis_report_response(
        request, auth, db, start_date, end_date
    )


@router.get("/reports/effectiveness", response_class=HTMLResponse)
def report_effectiveness(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    auth: WebAuthContext = Depends(require_training_access),
    db: Session = Depends(get_db_for_org),
):
    """Training effectiveness/feedback report."""
    return training_web_service.effectiveness_report_response(
        request, auth, db, start_date, end_date
    )
