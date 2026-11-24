# PlaceX frontend

Plain HTML, CSS and vanilla JavaScript; there is no frontend build step.

## Run

```bash
uvicorn placex.api:app --reload
cd frontend && python dev_server.py
```

Open `http://127.0.0.1:5500`. The backend's CORS middleware allows this origin. The development server disables browser caching so local HTML and JavaScript changes are picked up on reload. The API base URL is configured in `js/config.js`; set `window.PLACEX_API_BASE_URL` before that script to override the local default. `GET /health/live` checks backend liveness; `/health/ready` checks database readiness; `/health` retains the combined status response.

## Accounts
Signup creates an account with a password of at least 12 characters. Login verifies that password and returns a bearer session token; login never creates accounts. User-specific APIs require the token and enforce user ownership. Sessions expire after 14 days and logout revokes the current session.

Accounts created before password authentication have no password hash and cannot sign in until an administrator provisions a password/reset flow. Do not restore the removed public email lookup route.

## Job descriptions

Analyze, build, and prep-plan flows can use an existing JD, pasted text, or a PDF/DOCX/TXT/MD upload. Existing JDs load from `GET /job-descriptions`; paste and file inputs submit to `POST /job-descriptions`. Files are limited to 5 MB, and JDs with no recognized skills receive a clear validation error. Prep Plan conditionally displays one JD input method at a time, selects an owner's readable resume from `GET /users/{id}/resumes` (or accepts a new upload), and reuses an analysis only when it matches that resume and JD through `GET /users/{id}/matching-analysis`. Daily hours and supported deadline choices are stored in the existing prep job payload and shown as workload estimates. Journey Guide accepts arbitrary company and role text, uses database records only when available, and labels information by source.

All network calls go through `js/api.js`. Jobs use `POST /jobs` and `GET /jobs/{id}`; dashboards use `GET /users/{id}/dashboard`; saved prep plans use `GET /users/{id}/roadmaps` and item progress uses `PATCH /roadmap-items/{id}`. Resume-upload responses return only display metadata and a `has_text` flag, not stored resume text or filesystem paths. Company and status pages use `GET /companies` and `GET /system`.

Journey Guide accepts free-text company, role, location, experience, employment type, and job description; company and role suggestions are optional. Guides are submitted to `POST /journey-guides`, polled through the returned job ID, and saved per authenticated user. A polling retry checks the existing job rather than submitting a duplicate. Database and backend connectivity errors are reported separately; Groq failures retain the general preparation fallback and are labeled in the guide.

The dashboard's job search uses authenticated `POST /job-discovery/search`, which requires a configured permitted JSON API/feed to retrieve live results; otherwise it clearly reports that discovery is unconfigured and only stored listings are available. `GET /job-listings` searches stored source-reported listings. Job preferences use `GET/PUT /users/{id}/job-preferences`, recommendations use `GET /users/{id}/recommendations`, and saved opportunities/application tracking use `POST/GET /users/{id}/applications`, `PATCH /applications/{id}`, `GET /applications/{id}/events`, and `GET /users/{id}/placement-dashboard`. All user-owned routes check session ownership. Application status transitions are validated by the API.

## Resume downloads

Resume builds produce a structured Markdown source and a matching PDF, stored together in the owning user's generated-resume directory. The builder accepts education, skills, projects, experience, certifications, achievements, and coursework; it only emphasizes JD skills supported by the supplied profile and does not invent results or credentials.

Download either format with `GET /resumes/{id}/download/pdf` or `GET /resumes/{id}/download/md`. Both endpoints require the owning user's bearer session and resolve the files from the stored Markdown content hash rather than accepting a filesystem path. The legacy `GET /resumes/{id}/download` route remains an alias for Markdown.

## Remaining limitations

- Recent activity is based on job IDs stored in this browser.
- Journey Guide displays hiring information only when it is stored with source classifications; missing records are not treated as company requirements. AI-generated preparation is clearly separated from verified company information.
- A prep plan is generated from a user's report and its real JD skill gaps. If no gaps are found, no empty roadmap record is created.
- Saved prep roadmaps and per-item `TODO` / `IN_PROGRESS` / `DONE` status are loaded from and persisted to PostgreSQL.
- Prep Plan workload figures are estimates based on stored skill-hour values, saved completion statuses, and the daily-time/deadline preferences from a successful generation job; there is no skill-level input because the backend does not use one.
- Live job discovery remains unavailable until an approved source API/feed is configured. A provider returning `is_verified=true` is a source assertion; students should still confirm the current listing at its source.
