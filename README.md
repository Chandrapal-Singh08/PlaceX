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

Analyze, build, prep-plan, and journey-guide flows can use an existing JD, pasted text, or a PDF/DOCX/TXT/MD upload. Existing JDs load from `GET /job-descriptions`; paste and file inputs submit to `POST /job-descriptions`. Files are limited to 5 MB, and JDs with no recognized skills receive a clear validation error.

All network calls go through `js/api.js`. Jobs use `POST /jobs` and `GET /jobs/{id}`; dashboards use `GET /users/{id}/dashboard`; company and status pages use `GET /companies` and `GET /system`.

## Resume downloads

Resume builds produce a structured Markdown source and a matching PDF, stored together in the owning user's generated-resume directory. The builder accepts education, skills, projects, experience, certifications, achievements, and coursework; it only emphasizes JD skills supported by the supplied profile and does not invent results or credentials.

Download either format with `GET /resumes/{id}/download/pdf` or `GET /resumes/{id}/download/md`. Both endpoints require the owning user's bearer session and resolve the files from the stored Markdown content hash rather than accepting a filesystem path. The legacy `GET /resumes/{id}/download` route remains an alias for Markdown.

## Remaining limitations

- Recent activity is based on job IDs stored in this browser; there is no job-list endpoint.
- Roadmap item status updates are not exposed over HTTP.
- Hiring rounds are shown only for companies with configured records. Custom JDs that cannot be matched to a known company do not receive invented rounds.
