/* PlaceX frontend configuration — the only place URLs and dev defaults live. */
window.PLACEX_CONFIG = {
  API_BASE_URL: "http://127.0.0.1:8000",
  DEV_USER_ID: 1,            // used by "Continue as dev user" (backend has no real auth yet)
  DEFAULT_JD_ID: 1,          // seeded sample JD: Backend Developer Intern
  POLL_INTERVAL_MS: 700,
  POLL_TIMEOUT_MS: 60000,
  RESUME_DOWNLOAD_PATH: "/resumes/{id}/download/{format}"
};
