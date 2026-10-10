"""PlaceXCore wires DB + OS layers together. The API / CLI / tests all talk to this."""
import os
import pathlib
import re
import tempfile
import time

from . import db
from .ai.stub import KeywordBackend
from .ai.textract import extract_text
from .config import Config
from .os_layer.filestore import FileStore, UploadRejected
from .os_layer.memory import LRUCache, ModelRegistry
from .os_layer.monitor import ResourceMonitor, default_probe
from .os_layer.policies import make_policy
from .os_layer.queue import BoundedJobQueue, QueuedJob, QueueFull
from .os_layer.worker import PoolStats, WorkerPool
from .repo import Repo
from .tasks.handlers import make_handlers

class Backpressure(Exception):
    """Server is saturated: caller should retry later (HTTP 503)."""

class PlaceXCore:
    def __init__(self, cfg: Config | None = None, ai=None, probe=default_probe):
        self.cfg = cfg or Config()
        db.init_pool(self.cfg.dsn, min_size=2, max_size=self.cfg.workers * 2 + 8)
        try:
            db.ensure_auth_schema()
        except Exception:
            db.close_pool()
            raise
        self.repo = Repo()
        self.ai = ai or KeywordBackend()
        self.store = FileStore(self.cfg.upload_dir, self.cfg.max_upload_mb * 1_048_576,
                               self.cfg.user_quota_mb * 1_048_576)
        self.cache = LRUCache(max_items=512, ttl=600)
        self.models = ModelRegistry()
        self.queue = BoundedJobQueue(self.cfg.queue_capacity, make_policy(self.cfg.policy, self.cfg.aging_seconds))
        self.stats = PoolStats()
        self.handlers = make_handlers(self.repo, self.ai, self.store, self.cache)
        self.pool = WorkerPool(self.cfg, self.queue, self.repo, self.handlers, self.stats)
        self.monitor = ResourceMonitor(self.cfg, self.queue, self.stats, on_event=self._log_system, probe=probe)
        self.recovered = 0

    # -------------------------------------------------------- lifecycle
    def start(self):
        self.recovered = len(self.repo.recover_jobs())
        for r in self.repo.load_ready_jobs():                # jobs that survived a restart
            self.queue.put(QueuedJob(r["id"], r["priority"], r["job_type"]), force=True)
        self.pool.start()
        self.monitor.start()
        return self

    def stop(self):
        self.monitor.stop()
        self.pool.stop()
        db.close_pool()

    def _log_system(self, action, details):
        try:
            self.repo.log_activity(None, "system", None, action, details)
        except Exception:                                      # noqa: BLE001
            pass

    # --------------------------------------------------------------- API
    def submit(self, user_id, job_type, payload, priority=5) -> dict:
        if self.queue.full():
            raise Backpressure("queue full")
        row = self.repo.enqueue_job(user_id, job_type, payload, priority)
        try:
            self.queue.put(QueuedJob(row["id"], row["priority"], row["job_type"]))
        except QueueFull:
            self.repo.cancel_job(row["id"])
            raise Backpressure("queue full")
        return row

    def upload_resume(self, user_id, filename, data: bytes) -> dict:
        used = self.repo.user_storage_bytes(user_id)
        f = self.store.save_upload(user_id, filename, data, used)
        text = extract_text(f["path"], f["ext"])
        return self.repo.add_resume(user_id, "UPLOADED", pathlib.Path(filename).name,
                                    f["path"], f["sha256"], f["size"], text)

    MAX_JD_CHARS = 50_000

    def resolve_jd(self, text=None, filename=None, data: bytes | None = None) -> dict:
        """Create or find a JD from text or a validated PDF, DOCX, TXT, or MD upload."""
        if data is not None:
            ext = self.store.check_upload(filename, data)
            fd, tmp = tempfile.mkstemp(suffix=ext)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                text = extract_text(tmp, ext)
            finally:
                os.unlink(tmp)
            if not (text or "").strip():
                raise UploadRejected("could not read any text from this file (PDFs need pypdf installed)", 422)
        text = (text or "").replace("\x00", "").strip()
        if not text:
            raise UploadRejected("job description is empty", 400)
        if len(text) > self.MAX_JD_CHARS:
            raise UploadRejected(f"job description is too long (max {self.MAX_JD_CHARS} characters)", 413)

        catalog = self.repo.list_skills()
        found = self.ai.extract_skills(text, catalog)
        if not found:
            raise UploadRejected("no known skills were found in this job description; paste the full JD", 422)
        names = {skill["id"]: skill["name"] for skill in catalog}

        company = None
        for candidate in sorted(self.repo.list_companies(), key=lambda item: -len(item["name"])):
            base_name = re.sub(r"\s*\(.*?\)", "", candidate["name"]).strip()
            if base_name and re.search(r"(?<!\w)" + re.escape(base_name) + r"(?!\w)", text, re.I):
                company = candidate
                break

        with db.transaction() as conn:
            company_id = company["id"] if company else self.repo.custom_company_id(conn)
            jd = self.repo.find_jd(company_id, text, conn)
            if not jd:
                first_line = next((line for line in text.splitlines() if line.strip()), "Job description")
                title = re.sub(r"^[#\s*\-]+", "", first_line).strip()[:80] or "Job description"
                title = self.repo.unique_role_title(company_id, title, conn)
                jd = self.repo.create_jd(
                    company_id,
                    title,
                    text,
                    [(skill_id, max(1, min(5, round(confidence * 5))), confidence >= 0.8)
                     for skill_id, confidence in found],
                    conn,
                )

        return {
            "id": jd["id"],
            "title": jd["role_title"],
            "company_id": company["id"] if company else None,
            "company_name": company["name"] if company else None,
            "company_matched": company is not None,
            "skills": [names[skill_id] for skill_id, _ in found],
        }

    def wait(self, job_id, timeout=30.0, poll=0.05) -> dict:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            j = self.repo.get_job(job_id)
            if j["status"] in ("DONE", "FAILED", "CANCELLED"):
                return j
            time.sleep(poll)
        raise TimeoutError(f"job {job_id} still running after {timeout}s")

    def cleanup_generated(self, days=None) -> int:
        n = 0
        for r in self.repo.stale_generated(days if days is not None else self.cfg.generated_ttl_days):
            self.store.delete_generated(r["user_id"], r["file_hash"])
            self.repo.delete_resume(r["id"])
            n += 1
        return n

    def system_status(self) -> dict:
        return {"queue": self.queue.snapshot(), "pool": self.stats.snapshot(),
                "cache": self.cache.stats(), "monitor": self.monitor.latest(),
                "throttled": self.monitor.throttled, "jobs_by_status": self.repo.job_counts(),
                "recovered_on_start": self.recovered}
