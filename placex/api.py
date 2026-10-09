"""Thin HTTP layer.  Run:  uvicorn placex.api:app --reload
Every endpoint just calls PlaceXCore; no business logic lives here."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator
from psycopg.errors import UniqueViolation

from .auth import hash_password, new_session_token, token_digest, verify_password
from .core import Backpressure, PlaceXCore
from .os_layer.filestore import UploadRejected

core: PlaceXCore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global core
    core = PlaceXCore().start()
    yield
    core.stop()


app = FastAPI(
    title="PlaceX",
    version="0.1",
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


@app.exception_handler(UploadRejected)
async def _rejected(request, exc):
    return JSONResponse(
        {
            "detail": str(exc)
        },
        status_code=exc.code
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

    return core.upload_resume(
        user_id,
        file.filename,
        data
    )


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


# ---------------------------------------------------------
# Dashboard
# ---------------------------------------------------------

@app.get("/users/{user_id}/dashboard")
def dashboard(user_id: int, user=Depends(current_user)):
    if user_id != user["id"]:
        raise HTTPException(403, "cannot access another user's dashboard")
    return core.repo.dashboard(user_id) or {}


# ---------------------------------------------------------
# Companies
# ---------------------------------------------------------

@app.get("/companies")
def companies():
    return core.repo.list_companies()


# ---------------------------------------------------------
# System Status
# ---------------------------------------------------------

@app.get("/system")
def system():
    return core.system_status()


# Wrap the whole FastAPI app so even unhandled 500 responses carry CORS headers.
fastapi_app = app
app = CORSMiddleware(
    app=fastapi_app,
    allow_origins=[
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.openapi = fastapi_app.openapi