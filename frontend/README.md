# PlaceX frontend

Plain HTML, CSS and vanilla JavaScript; there is no frontend build step.

## Run

```bash
uvicorn placex.api:app --reload
cd frontend && python dev_server.py
```

Open `http://127.0.0.1:5500`. The backend's CORS middleware allows this origin. The development server disables browser caching so local HTML and JavaScript changes are picked up on reload. The API base URL is configured in `js/config.js`.

## Accounts
Signup creates an account with a password of at least 12 characters. Login verifies that password and returns a bearer session token; login never creates accounts. User-specific APIs require the token and enforce user ownership. Sessions expire after 14 days and logout revokes the current session.

Accounts created before password authentication have no password hash and cannot sign in until an administrator provisions a password/reset flow. Do not restore the removed public email lookup route.

## Job descriptions

Analyze, build, and prep-plan flows can use an existing JD, pasted text, or a PDF/DOCX/TXT/MD upload. Existing JDs load from `GET /job-descriptions`; paste and file inputs submit to `POST /job-descriptions`. Files are limited to 5 MB, and JDs with no recognized skills receive a clear validation error. Prep Plan conditionally displays one JD input method at a time, selects an owner's readable resume from `GET /users/{id}/resumes` (or accepts a new upload), and reuses an analysis only when it matches that resume and JD through `GET /users/{id}/matching-analysis`. Daily hours and supported deadline choices are stored in the existing prep job payload and shown as workload estimates. Journey Guide instead selects a stored company and optionally one of its stored job descriptions; it displays only hiring rounds present in the database.

All network calls go through `js/api.js`. Jobs use `POST /jobs` and `GET /jobs/{id}`; dashboards use `GET /users/{id}/dashboard`; saved prep plans use `GET /users/{id}/roadmaps` and item progress uses `PATCH /roadmap-items/{id}`. Company and status pages use `GET /companies` and `GET /system`.

## Resume downloads

Resume builds produce a structured Markdown source and a matching PDF, stored together in the owning user's generated-resume directory. The builder accepts education, skills, projects, experience, certifications, achievements, and coursework; it only emphasizes JD skills supported by the supplied profile and does not invent results or credentials.

Download either format with `GET /resumes/{id}/download/pdf` or `GET /resumes/{id}/download/md`. Both endpoints require the owning user's bearer session and resolve the files from the stored Markdown content hash rather than accepting a filesystem path. The legacy `GET /resumes/{id}/download` route remains an alias for Markdown.

## Remaining limitations

- Recent activity is based on job IDs stored in this browser; there is no job-list endpoint.
- Journey Guide displays only hiring rounds stored for the selected company; missing records or fields are explicitly shown as unavailable, not treated as company requirements.
- A prep plan is generated from a user's report and its real JD skill gaps. If no gaps are found, no empty roadmap record is created.
- Saved prep roadmaps and per-item `TODO` / `IN_PROGRESS` / `DONE` status are loaded from and persisted to PostgreSQL.
- Prep Plan workload figures are estimates based on stored skill-hour values, saved completion statuses, and the daily-time/deadline preferences from a successful generation job; there is no skill-level input because the backend does not use one.
