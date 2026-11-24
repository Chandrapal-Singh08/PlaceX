/* Non-secret frontend settings may be injected through PLACEX_API_BASE_URL. */
(function () {
  const localFrontend = ["localhost", "127.0.0.1"].includes(location.hostname);
  const defaultApi = localFrontend
    ? `http://${location.hostname}:8000`
    : "https://placex-backend-7kl6.onrender.com";
  window.PLACEX_CONFIG = {
    API_BASE_URL: (window.PLACEX_API_BASE_URL || defaultApi).replace(/\/+$/, ""),
    POLL_INTERVAL_MS: 700,
    POLL_TIMEOUT_MS: 60000,
    REQUEST_TIMEOUT_MS: 15000,
    RESUME_DOWNLOAD_PATH: "/resumes/{id}/download/{format}"
  };
})();
