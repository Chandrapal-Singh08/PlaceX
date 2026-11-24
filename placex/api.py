"""Thin HTTP layer.  Run:  uvicorn placex.api:app --reload
Every endpoint just calls PlaceXCore; no business logic lives here."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging
import os
import re
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from psycopg.errors import UndefinedColumn, UndefinedTable, UniqueViolation

from .auth import hash_password, new_session_token, token_digest, verify_password
from .config import Config
from .core import Backpressure, PlaceXCore
from .os_layer.filestore import UploadRejected
from .job_sources import JobProviderError, JobSearchFilters
from . import db

core: PlaceXCore | None = None
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global core
    core = PlaceXCore().start()
    yield
    core.stop()


_api_config = Config()
_production = os.environ.get("PLACEX_ENV", "development").lower() in {"production", "prod"}
app = FastAPI(
    title="PlaceX",
    version="0.1",
    docs_url=None if _production else "/docs",
    openapi_url=None if _production else "/openapi.json",
    lifespan=lifespan
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

# ---------------------------------------------------------
# Request Models
# ---------------------------------------------------------

class UserIn(BaseModel):
    email: str
    full_name: str
    roll_no: str | None = None
    password: str = Field(min_length=12, max_length=128)

    @field_validator("full_name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("full_name must not be blank")
        return value


class LoginIn(BaseModel):
    email: str
    password: str


class JobIn(BaseModel):
    user_id: int
    job_type: str = Field(
        pattern="^(RESUME_ANALYSIS|RESUME_BUILD|JOURNEY_GUIDE|PREP_PLAN)$"
    )
    payload: dict = {}
    priority: int = Field(5, ge=0, le=10)


class JourneyGuideIn(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    job_role: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    experience_level: str | None = Field(default=None, max_length=100)
    employment_type: str | None = Field(default=None, max_length=100)
    job_description: str | None = Field(default=None, max_length=15000)

    @field_validator("company_name", "job_role")
    @classmethod
    def require_nonblank_journey_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("company_name and job_role must not be blank")
        return value

    @field_validator("location", "experience_level", "employment_type", "job_description")
    @classmethod
    def normalize_optional_journey_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class JourneyRoadmapIn(BaseModel):
    daily_study_hours: int = Field(default=2, ge=1, le=8)
    deadline_days: int | None = None

    @field_validator("deadline_days")
    @classmethod
    def validate_deadline_days(cls, value: int | None) -> int | None:
        if value is not None and value not in (7, 15, 30, 60):
            raise ValueError("deadline_days must be one of 7, 15, 30, or 60")
        return value


class RoadmapItemStatusIn(BaseModel):
    status: Literal["TODO", "IN_PROGRESS", "DONE"]


class JobSearchIn(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    remote: bool | None = None
    experience_level: str | None = Field(default=None, max_length=100)
    skill: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


class JobPreferencesIn(BaseModel):
    preferred_roles: list[str] = Field(default_factory=list, max_length=20)
    preferred_locations: list[str] = Field(default_factory=list, max_length=20)
    remote_preference: Literal["ANY", "REMOTE", "ONSITE"] = "ANY"
    employment_types: list[str] = Field(default_factory=list, max_length=10)
    experience_level: str | None = Field(default=None, max_length=100)

    @field_validator("preferred_roles", "preferred_locations", "employment_types")
    @classmethod
    def clean_preference_items(cls, values):
        cleaned = [item.strip()[:200] for item in values if item.strip()]
        return list(dict.fromkeys(cleaned))


class ApplicationCreateIn(BaseModel):
    external_job_id: int | None = Field(default=None, gt=0)
    jd_id: int | None = Field(default=None, gt=0)

    @field_validator("jd_id")
    @classmethod
    def require_one_job_source(cls, value, info):
        external_id = info.data.get("external_job_id")
        if (external_id is None) == (value is None):
            raise ValueError("provide exactly one of external_job_id or jd_id")
        return value


class InterviewDetailIn(BaseModel):
    kind: str = Field(min_length=1, max_length=100)
    scheduled_at: datetime
    notes: str = Field(default="", max_length=2000)
    outcome: str | None = Field(default=None, max_length=500)


class ApplicationUpdateIn(BaseModel):
    status: Literal[
        "SAVED", "APPLIED", "ONLINE_ASSESSMENT", "TECHNICAL_INTERVIEW",
        "HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED",
    ] | None = None
    applied_at: datetime | None = None
    next_action_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=5000)
    related_resume_id: int | None = Field(default=None, gt=0)
    interview_details: list[InterviewDetailIn] | None = Field(default=None, max_length=30)


APPLICATION_TRANSITIONS = {
    "SAVED": {"APPLIED"},
    "APPLIED": {"ONLINE_ASSESSMENT", "TECHNICAL_INTERVIEW", "HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED"},
    "ONLINE_ASSESSMENT": {"TECHNICAL_INTERVIEW", "HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED"},
    "TECHNICAL_INTERVIEW": {"HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED"},
    "HR_INTERVIEW": {"REJECTED", "OFFER_RECEIVED"},
    "REJECTED": set(),
    "OFFER_RECEIVED": set(),
}


def current_user(authorization: str | None = Header(None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    token = authorization[7:].strip()
    if not token:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    digest = token_digest(token)
    user = core.repo.get_session_user(digest)
    if not user:
        raise HTTPException(401, "session is invalid or expired", headers={"WWW-Authenticate": "Bearer"})
    return {**user, "_token_hash": digest}


def start_session(user):
    token = new_session_token()
    core.repo.create_session(user["id"], token_digest(token), datetime.now(timezone.utc) + timedelta(days=14))
    return {
        "user_id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "access_token": token,
        "token_type": "bearer",
    }


# ---------------------------------------------------------
# Exception Handlers
# ---------------------------------------------------------

@app.exception_handler(Backpressure)
async def _busy(request, exc):
    return JSONResponse(
        {
            "detail": "server busy, retry shortly"
        },
        status_code=503,
        headers={
            "Retry-After": "2"
        }
    )


@app.exception_handler(db.DatabaseUnavailable)
async def _database_unavailable(request, exc):
    logger.warning(
        "database_unavailable method=%s path=%s category=%s",
        request.method,
        request.url.path,
        exc.category,
    )
    return JSONResponse(
        {
            "detail": "The PlaceX database is temporarily unavailable. Please retry shortly.",
            "code": "database_unavailable",
        },
        status_code=503,
        headers={"Retry-After": "2"},
    )


@app.exception_handler(UploadRejected)
async def _rejected(request, exc):
    return JSONResponse(
        {
            "detail": str(exc)
        },
        status_code=exc.code
    )


@app.exception_handler(UndefinedColumn)
@app.exception_handler(UndefinedTable)
async def _schema_not_updated(request, exc):
    logger.exception(
        "Database schema mismatch serving %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        {
            "detail": (
                "The PlaceX database schema needs an update. Apply the documented "
                "additive migrations, then retry."
            )
        },
        status_code=503,
    )


@app.exception_handler(Exception)
async def _unexpected_error(request, exc):
    logger.exception(
        "Unhandled API error serving %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        {"detail": "The request could not be completed. Please try again shortly."},
        status_code=500,
    )


# ---------------------------------------------------------
# User
# ---------------------------------------------------------

@app.post("/users", status_code=201)
def create_user(u: UserIn):
    try:
        user = core.repo.register_user(u.email.strip(), u.full_name.strip(), u.roll_no, hash_password(u.password))
    except UniqueViolation as exc:
        raise HTTPException(409, "email or roll number already registered") from exc
    return start_session(user)


@app.post("/auth/login")
def login(credentials: LoginIn):
    user = core.repo.get_auth_user_by_email(credentials.email.strip())
    if not user or not verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(401, "invalid email or password", headers={"WWW-Authenticate": "Bearer"})
    return start_session(user)


@app.get("/auth/me")
def who_am_i(user=Depends(current_user)):
    return {key: user[key] for key in ("id", "email", "full_name", "role")}


@app.post("/auth/logout")
def logout(user=Depends(current_user)):
    core.repo.revoke_session(user["_token_hash"])
    return {"ok": True}


@app.get("/job-descriptions")
def job_descriptions(user=Depends(current_user)):
    return core.repo.list_job_descriptions()


@app.post("/job-descriptions", status_code=201)
async def create_jd(text: str | None = Form(None), file: UploadFile | None = File(None), user=Depends(current_user)):
    """Create or resolve a JD from pasted text or an uploaded file."""
    if file is not None and file.filename:
        data = await file.read(core.store.max_bytes + 1)
        return core.resolve_jd(filename=file.filename, data=data)
    return core.resolve_jd(text=text)


@app.get("/job-listings")
def job_listings(
    query: str | None = Query(None, max_length=200),
    company: str | None = Query(None, max_length=200),
    location: str | None = Query(None, max_length=200),
    remote: bool | None = None,
    experience_level: str | None = Query(None, max_length=100),
    skill: str | None = Query(None, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    user=Depends(current_user),
):
    filters = JobSearchFilters(
        query=query,
        company=company,
        location=location,
        remote=remote,
        experience_level=experience_level,
        skill=skill,
        limit=limit,
    )
    return {
        "provider_configured": core.job_provider is not None,
        "jobs": core.repo.list_external_jobs(filters, offset=offset),
    }


@app.post("/job-discovery/search")
def search_external_jobs(request: JobSearchIn, user=Depends(current_user)):
    try:
        return core.discover_jobs(JobSearchFilters.model_validate(request.model_dump()))
    except JobProviderError as error:
        raise HTTPException(502, str(error)) from error
    except UniqueViolation as error:
        raise HTTPException(
            502,
            "configured job source returned a listing that conflicts with an existing source listing",
        ) from error


# ---------------------------------------------------------
# Resume Upload
# ---------------------------------------------------------

@app.post("/users/{user_id}/resumes", status_code=201)
async def upload(
    user_id: int,
    file: UploadFile = File(...),
    user=Depends(current_user),
):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's account")
    data = await file.read(
        core.store.max_bytes + 1
    )

    resume = core.upload_resume(
        user_id,
        file.filename,
        data
    )
    return {
        "id": resume["id"],
        "title": resume["title"],
        "kind": resume["kind"],
        "created_at": resume["created_at"],
        "has_text": bool((resume["raw_text"] or "").strip()),
    }


@app.get("/resumes/{resume_id}/download")
def download_resume(resume_id: int, user=Depends(current_user)):
    return _generated_resume_download(resume_id, "md", user)


@app.get("/resumes/{resume_id}/download/{file_format}")
def download_resume_format(resume_id: int, file_format: str, user=Depends(current_user)):
    if file_format not in ("pdf", "md"):
        raise HTTPException(404, "resume format not found")
    return _generated_resume_download(resume_id, file_format, user)


def _generated_resume_download(resume_id: int, file_format: str, user):
    resume = core.repo.get_resume(resume_id)
    if not resume or resume["user_id"] != user["id"] or resume["kind"] != "GENERATED":
        raise HTTPException(404, "generated resume not found")
    if not resume["file_hash"] or not re.fullmatch(r"[0-9a-f]{64}", resume["file_hash"]):
        raise HTTPException(404, "generated resume file not found")

    user_root = core.store.root / "users" / str(user["id"])
    generated_root = user_root / "generated"
    markdown_path = generated_root / f"{resume['file_hash']}.md"
    expected_path = generated_root / f"{resume['file_hash']}.{file_format}"
    if (
        not resume["file_path"]
        or user_root.resolve() != user_root
        or generated_root.resolve() != generated_root
    ):
        raise HTTPException(404, "generated resume file not found")
    stored_markdown = Path(resume["file_path"]).resolve()
    if (
        stored_markdown != markdown_path
        or not markdown_path.is_file()
        or expected_path.resolve() != expected_path
        or not expected_path.is_file()
    ):
        raise HTTPException(404, "generated resume file not found")

    return FileResponse(
        expected_path,
        media_type="application/pdf" if file_format == "pdf" else "text/markdown",
        filename=f"resume-{resume_id}.{file_format}",
    )


# ---------------------------------------------------------
# Jobs
# ---------------------------------------------------------

@app.post("/jobs", status_code=202)
def submit(j: JobIn, user=Depends(current_user)):
    if j.user_id != user["id"]:
        raise HTTPException(403, "cannot submit jobs for another user")
    return core.submit(
        j.user_id,
        j.job_type,
        j.payload,
        j.priority
    )


@app.get("/jobs/{job_id}")
def job(job_id: int, user=Depends(current_user)):
    row = core.repo.get_job(job_id)

    if not row:
        raise HTTPException(
            404,
            "no such job"
        )
    if row["user_id"] != user["id"]:
        raise HTTPException(404, "no such job")

    if row.get("error"):
        error = row["error"]
        row = dict(row)
        prefix = "JobError: "
        row["error"] = (
            error[len(prefix):]
            if error.startswith(prefix)
            else (
                "Journey Guide could not be generated. Please retry shortly; "
                "if it persists, contact support with the job ID."
                if row.get("job_type") == "JOURNEY_GUIDE"
                else "The job failed because of a server error. Please try again later."
            )
        )
    return row


@app.delete("/jobs/{job_id}")
def cancel(
    job_id: int,
    user=Depends(current_user),
):
    row = core.repo.cancel_job(
        job_id,
        user["id"]
    )

    if not row:
        raise HTTPException(
            409,
            "job is not cancellable (already running or finished)"
        )

    return row


def _require_journey_schema(operation: str) -> None:
    missing_columns = core.repo.journey_schema_missing_columns()
    if missing_columns:
        logger.error(
            "journey_schema_unavailable operation=%s missing_columns=%s",
            operation,
            ",".join(missing_columns),
        )
        raise HTTPException(
            503,
            "Journey Guide is temporarily unavailable. Please retry shortly.",
        )


def _require_journey_roadmap_schema() -> None:
    missing_columns = core.repo.journey_roadmap_schema_missing_columns()
    if missing_columns:
        logger.error(
            "journey_roadmap_schema_unavailable missing_columns=%s",
            ",".join(missing_columns),
        )
        raise HTTPException(
            503,
            "Prep Planner integration is temporarily unavailable. Please retry shortly.",
        )


@app.post("/journey-guides", status_code=202)
def generate_journey_guide(request: JourneyGuideIn, user=Depends(current_user)):
    _require_journey_schema("generate")
    return core.submit(
        user["id"],
        "JOURNEY_GUIDE",
        request.model_dump(),
        priority=5,
    )


@app.get("/users/{user_id}/journey-guides")
def user_journey_guides(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's journey guides")
    _require_journey_schema("list_saved")
    return core.repo.list_user_journey_guides(user_id)


@app.get("/journey-guides/{guide_id}")
def get_journey_guide(guide_id: int, user=Depends(current_user)):
    _require_journey_schema("get_saved")
    guide = core.repo.get_user_journey_guide(user["id"], guide_id)
    if not guide:
        raise HTTPException(404, "journey guide not found")
    return guide


@app.post("/journey-guides/{guide_id}/roadmap", status_code=201)
def create_journey_roadmap(
    guide_id: int,
    request: JourneyRoadmapIn,
    user=Depends(current_user),
):
    _require_journey_roadmap_schema()
    roadmap = core.repo.create_journey_roadmap(
        user["id"],
        guide_id,
        request.daily_study_hours,
        request.deadline_days,
    )
    if not roadmap:
        raise HTTPException(404, "Journey Guide not found")
    return {
        **roadmap,
        "prep_url": "/prep-plan.html",
        "message": (
            "Preparation plan created."
            if roadmap["created"]
            else "A preparation plan already exists for this guide."
        ),
    }


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.get("/users/{user_id}/dashboard")
def dashboard(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's dashboard")
    return core.repo.dashboard(user_id) or {}


@app.get("/users/{user_id}/resumes")
def user_resumes(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's resumes")
    return core.repo.list_user_resumes(user_id)


@app.get("/users/{user_id}/job-preferences")
def get_job_preferences(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's preferences")
    return core.repo.get_job_preferences(user_id) or {
        "user_id": user_id,
        "preferred_roles": [],
        "preferred_locations": [],
        "remote_preference": "ANY",
        "employment_types": [],
        "experience_level": None,
    }


@app.put("/users/{user_id}/job-preferences")
def put_job_preferences(user_id: int, request: JobPreferencesIn, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot update another user's preferences")
    return core.repo.save_job_preferences(user_id, request.model_dump())


@app.get("/users/{user_id}/recommendations")
def job_recommendations(
    user_id: int, resume_id: int | None = Query(None, gt=0),
    user=Depends(current_user),
):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's recommendations")
    if resume_id is not None and not core.repo.get_owned_resume(user_id, resume_id):
        raise HTTPException(404, "resume not found")
    return core.get_job_recommendations(user_id, resume_id)


@app.post("/users/{user_id}/recommendations/{recommendation_id}/dismiss")
def dismiss_recommendation(user_id: int, recommendation_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot update another user's recommendations")
    row = core.repo.dismiss_job_recommendation(user_id, recommendation_id)
    if not row:
        raise HTTPException(404, "recommendation not found")
    return {"ok": True, "recommendation_id": row["id"]}


@app.get("/users/{user_id}/applications")
def applications(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's applications")
    return core.repo.list_user_applications(user_id)


@app.post("/users/{user_id}/applications", status_code=201)
def create_application(user_id: int, request: ApplicationCreateIn, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot create applications for another user")
    if (request.external_job_id is None) == (request.jd_id is None):
        raise HTTPException(422, "provide exactly one of external_job_id or jd_id")
    if request.external_job_id is not None:
        listing = core.repo.get_external_job(request.external_job_id)
        if not listing or listing["status"] != "ACTIVE":
            raise HTTPException(404, "active job listing not found")
    elif not core.repo.get_jd(request.jd_id):
        raise HTTPException(404, "job description not found")
    row = core.repo.save_application(
        user_id,
        external_job_id=request.external_job_id,
        jd_id=request.jd_id,
    )
    return row


@app.patch("/applications/{application_id}")
def update_application(
    application_id: int, request: ApplicationUpdateIn, user=Depends(current_user)
):
    updates = request.model_dump(exclude_unset=True, mode="json")
    if not updates:
        raise HTTPException(422, "provide at least one application field to update")
    for field in ("status", "notes", "interview_details"):
        if field in updates and updates[field] is None:
            raise HTTPException(422, f"{field} cannot be null")
    with db.transaction() as conn:
        current = core.repo.get_owned_application(
            application_id, user["id"], conn, for_update=True
        )
        if not current:
            raise HTTPException(404, "application not found")
        requested_status = updates.get("status")
        if requested_status and requested_status != current["status"]:
            if requested_status not in APPLICATION_TRANSITIONS[current["status"]]:
                raise HTTPException(
                    409,
                    f"application cannot move from {current['status']} to {requested_status}",
                )
        resume_id = updates.get("related_resume_id")
        if resume_id is not None and not core.repo.get_owned_resume(
            user["id"], resume_id, conn
        ):
            raise HTTPException(404, "resume not found")
        if requested_status and requested_status != current["status"] and requested_status != "SAVED":
            if not updates.get("applied_at"):
                updates["applied_at"] = current["applied_at"] or datetime.now(timezone.utc).isoformat()
        updated = core.repo.update_application(
            application_id, user["id"], updates, conn
        )
        changed_status = requested_status and requested_status != current["status"]
        event_type = "status_changed" if changed_status else "details_updated"
        core.repo.add_application_event(
            application_id,
            user["id"],
            event_type,
            current["status"],
            updated["status"],
            updates.get("notes", ""),
            {"updated_fields": sorted(updates)},
            conn,
        )
    return updated


@app.get("/applications/{application_id}/events")
def application_events(application_id: int, user=Depends(current_user)):
    if not core.repo.get_owned_application(application_id, user["id"]):
        raise HTTPException(404, "application not found")
    return core.repo.list_application_events(application_id, user["id"])


@app.get("/users/{user_id}/placement-dashboard")
def placement_dashboard(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's placement dashboard")
    return core.repo.placement_dashboard(user_id)


@app.get("/users/{user_id}/matching-analysis")
def matching_analysis(user_id: int, resume_id: int, jd_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's analysis")
    resume = core.repo.get_resume(resume_id)
    if not resume or resume["user_id"] != user["id"]:
        raise HTTPException(404, "resume not found")
    return core.repo.get_matching_report(user["id"], resume_id, jd_id)


@app.get("/users/{user_id}/roadmaps")
def user_roadmaps(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's roadmaps")
    return core.repo.list_user_roadmaps(user_id)


@app.patch("/roadmap-items/{item_id}")
def update_roadmap_item(item_id: int, update: RoadmapItemStatusIn, user=Depends(current_user)):
    row = core.repo.set_user_roadmap_item_status(item_id, user["id"], update.status)
    if not row:
        raise HTTPException(404, "roadmap item not found")
    return row


# ---------------------------------------------------------
# Companies
# ---------------------------------------------------------

@app.get("/companies")
def companies():
    return core.repo.list_companies()


# ---------------------------------------------------------
# System Status
# ---------------------------------------------------------

def _readiness_response():
    try:
        db.check_connection()
    except db.DatabaseUnavailable as error:
        logger.warning(
            "health_check_database_unavailable category=%s",
            error.category,
        )
        return JSONResponse(
            {
                "status": "degraded",
                "backend": "available",
                "database": "unavailable",
            },
            status_code=503,
        )
    return {
        "status": "ok",
        "backend": "available",
        "database": "available",
    }


@app.get("/health/live")
def health_live():
    return {"status": "ok", "backend": "available"}


@app.get("/health/ready")
def health_ready():
    return _readiness_response()


@app.get("/health")
def health():
    return _readiness_response()


@app.get("/health/database")
def health_database():
    diagnostic = db.diagnose_connection(core.cfg.dsn)
    status_code = 200 if diagnostic["query"] == "ok" else 503
    if status_code != 200:
        logger.warning(
            "database_diagnostic_failed category=%s",
            diagnostic["failure_category"],
        )
    return JSONResponse(diagnostic, status_code=status_code)


@app.get("/system")
def system(user=Depends(current_user)):
    return core.system_status()


# Wrap the whole FastAPI app so even unhandled 500 responses carry CORS headers.
fastapi_app = app
app = CORSMiddleware(
    app=fastapi_app,
    allow_origins=[
        origin.strip()
        for origin in _api_config.allowed_origins.split(",")
        if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.openapi = fastapi_app.openapi