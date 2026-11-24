"""PlaceXCore wires DB + OS layers together. The API / CLI / tests all talk to this."""
import logging
import os
import pathlib
import re
import tempfile
import time

from . import db
from .ai.llm import create_llm_service
from .ai.stub import KeywordBackend
from .ai.textract import extract_text
from .config import Config
from .job_sources import JobSearchFilters, create_job_provider
from .matching import (
    EmbeddingSemanticMatcher,
    SentenceTransformerProvider,
    SemanticMatchingError,
    build_matching_report,
)
from .os_layer.filestore import FileStore, UploadRejected
from .os_layer.memory import LRUCache, ModelRegistry
from .os_layer.monitor import ResourceMonitor, default_probe
from .os_layer.policies import make_policy
from .os_layer.queue import BoundedJobQueue, QueuedJob, QueueFull
from .os_layer.worker import PoolStats, WorkerPool
from .repo import Repo
from .research import create_research_service
from .tasks.handlers import make_handlers

logger = logging.getLogger(__name__)

class Backpressure(Exception):
    """Server is saturated: caller should retry later (HTTP 503)."""

class PlaceXCore:
    def __init__(
        self, cfg: Config | None = None, ai=None, probe=default_probe, llm=None,
        semantic_matcher=None,
    ):
        self.cfg = cfg or Config()
        self.llm = llm if llm is not None else create_llm_service(self.cfg)
        self.semantic_matcher = semantic_matcher
        if self.semantic_matcher is None and self.cfg.embeddings_enabled:
            provider = SentenceTransformerProvider(
                self.cfg.embedding_model,
                self.cfg.embedding_device,
                self.cfg.embedding_local_files_only,
            )
            self.semantic_matcher = EmbeddingSemanticMatcher(
                provider, self.cfg.embedding_similarity_threshold
            )
        self.job_provider = None
        try:
            db.init_pool(
                self.cfg.dsn,
                min_size=self.cfg.db_pool_min_size,
                max_size=self.cfg.db_pool_max_size,
                wait_timeout=self.cfg.db_pool_wait_timeout_s,
                connect_timeout=self.cfg.db_connect_timeout_s,
                reconnect_timeout=self.cfg.db_reconnect_timeout_s,
            )
            try:
                db.ensure_auth_schema()
            except db.DatabaseUnavailable as error:
                logger.warning(
                    "database_auth_schema_check_failed category=%s; "
                    "backend is starting in degraded mode",
                    error.category,
                )
            self.job_provider = create_job_provider(self.cfg)
        except Exception:
            db.close_pool()
            if self.llm is not None:
                self.llm.close()
            if self.job_provider is not None:
                self.job_provider.close()
            raise
        self.repo = Repo()
        try:
            self.research = create_research_service(self.cfg, self.repo)
        except Exception:
            db.close_pool()
            if self.llm is not None:
                self.llm.close()
            if self.job_provider is not None:
                self.job_provider.close()
            raise
        self.ai = ai or KeywordBackend()
        self.store = FileStore(self.cfg.upload_dir, self.cfg.max_upload_mb * 1_048_576,
                               self.cfg.user_quota_mb * 1_048_576)
        self.cache = LRUCache(max_items=512, ttl=600)
        self.models = ModelRegistry()
        self.queue = BoundedJobQueue(self.cfg.queue_capacity, make_policy(self.cfg.policy, self.cfg.aging_seconds))
        self.stats = PoolStats()
        self.handlers = make_handlers(
            self.repo,
            self.ai,
            self.store,
            self.cache,
            self.llm,
            matching_weights=self.cfg.matching_weights,
            semantic_matcher=self.semantic_matcher,
            research=self.research,
        )
        self.pool = WorkerPool(self.cfg, self.queue, self.repo, self.handlers, self.stats)
        self.monitor = ResourceMonitor(self.cfg, self.queue, self.stats, on_event=self._log_system, probe=probe)
        self.recovered = 0

    # -------------------------------------------------------- lifecycle
    def start(self):
        try:
            self.recovered = len(self.repo.recover_jobs())
            for r in self.repo.load_ready_jobs():            # jobs that survived a restart
                self.queue.put(QueuedJob(r["id"], r["priority"], r["job_type"]), force=True)
        except db.DatabaseUnavailable as error:
            self.recovered = 0
            logger.warning(
                "database_job_recovery_failed category=%s; "
                "workers will accept jobs when the database is available",
                error.category,
            )
        self.pool.start()
        self.monitor.start()
        return self

    def stop(self):
        self.monitor.stop()
        self.pool.stop()
        if self.llm is not None:
            self.llm.close()
        if self.job_provider is not None:
            self.job_provider.close()
        if self.research is not None:
            self.research.close()
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

    def discover_jobs(self, filters: JobSearchFilters) -> dict:
        if self.job_provider is None:
            return {
                "provider_configured": False,
                "message": "Configure a permitted JSON job API or feed with PLACEX_JOB_PROVIDER_URL.",
                "jobs": self.repo.list_external_jobs(filters),
            }
        listings = self.job_provider.search(filters)
        saved = []
        catalog = self.repo.list_skills()
        for listing in listings:
            found = self.ai.extract_skills(
                " ".join([listing.role_title, listing.description, *listing.skills]),
                catalog,
            )
            saved.append(self.repo.upsert_external_job(
                listing=listing,
                source_name=self.job_provider.name,
                skill_ids=[skill_id for skill_id, _ in found],
            ))
        return {
            "provider_configured": True,
            "provider": self.job_provider.name,
            "fetched": len(saved),
            "jobs": self.repo.list_external_jobs(filters),
        }

    def get_job_recommendations(self, user_id: int, resume_id: int | None = None):
        resume = (
            self.repo.get_owned_resume(user_id, resume_id)
            if resume_id is not None
            else self.repo.latest_readable_resume(user_id)
        )
        if not resume or not (resume["raw_text"] or "").strip():
            return {"resume_id": None, "recommendations": [], "message": "Upload a readable resume to get recommendations."}
        preferences = self.repo.get_job_preferences(user_id)
        catalog = self.repo.list_skills()
        resume_skills = self.ai.extract_skills(resume["raw_text"], catalog)
        skill_names = {item["id"]: item["name"] for item in catalog}
        resume_pairs = {skill_id for skill_id, _ in resume_skills}
        dismissed_job_ids = self.repo.dismissed_recommendation_job_ids(
            user_id, resume["id"]
        )
        recommendations = []
        for job in self.repo.list_external_jobs(limit=100):
            if job["id"] in dismissed_job_ids:
                continue
            if not self._matches_job_preferences(job, preferences):
                continue
            job_text = " ".join([job["role_title"], job["description"], *(job["skills"] or [])])
            job_skills = self.ai.extract_skills(job_text, catalog)
            gap = [{
                "skill_id": skill_id,
                "skill_name": skill_names[skill_id],
                "importance": 3,
                "required": True,
                "matched": skill_id in resume_pairs,
            } for skill_id, _ in job_skills]
            evidence_fn = getattr(self.ai, "extract_skill_evidence", None)
            skill_evidence = evidence_fn(resume["raw_text"], catalog) if evidence_fn else []
            semantic_matches = {}
            semantic_status = "unavailable"
            if self.semantic_matcher is not None:
                try:
                    semantic_matches = self.semantic_matcher.match(
                        resume["raw_text"],
                        [item["skill_name"] for item in gap if not item["matched"]],
                    )
                    semantic_status = "available"
                except SemanticMatchingError:
                    logger.warning("Semantic recommendations unavailable for job listing %s", job["id"])
            details = build_matching_report(
                gap,
                resume["raw_text"],
                job["description"],
                skill_evidence=skill_evidence,
                semantic_matches=semantic_matches,
                semantic_status=semantic_status,
                weights=self.cfg.matching_weights,
            )
            matching_basis = "skills_only_preliminary"
            prior_report = self.repo.get_matching_report(
                user_id, resume["id"], job["jd_id"]
            ) if job["jd_id"] is not None else None
            if prior_report and prior_report["matching_details"]:
                prior_details = prior_report["matching_details"]
                if prior_details.get("overall_match_score") is not None:
                    details = prior_details
                    matching_basis = "existing_hybrid_analysis"
            if details["overall_match_score"] is None:
                continue
            score = details["overall_match_score"]
            category = (
                "STRONG_MATCH" if score >= 75
                else "ACHIEVABLE_MATCH" if score >= 40
                else "SKILL_BUILDING"
            )
            matched = [item["skill_name"] for item in gap if item["matched"]]
            missing = [item["skill_name"] for item in gap if not item["matched"]]
            reasons = []
            if matched:
                reasons.append("Resume evidence matches: " + ", ".join(matched[:8]) + ".")
            if missing:
                reasons.append("Skills to develop: " + ", ".join(missing[:8]) + ".")
            if preferences and preferences.get("preferred_roles"):
                reasons.append("Role is shown alongside your preferred role searches.")
            if matching_basis == "skills_only_preliminary":
                reasons.append(
                    "Preliminary skill-based estimate; run Resume Analyzer for a full evidence-backed report."
                )
            if not reasons:
                reasons.append(
                    "This source-reported listing can help you explore requirements for your target role."
                )
            record = {
                "user_id": user_id,
                "resume_id": resume["id"],
                "external_job_id": job["id"],
                "match_score": score,
                "category": category,
                "reasons": reasons,
                "matching_details": details,
            }
            saved_recommendation = self.repo.save_job_recommendation(record)
            recommendations.append({
                **job,
                "recommendation_id": saved_recommendation["id"],
                "resume_id": resume["id"],
                "matching_basis": matching_basis,
                "match_score": score,
                "category": category,
                "matching_skills": matched,
                "partially_matched_skills": details["partially_matched_skills"],
                "missing_skills": [
                    name for name in missing
                    if name not in details["missing_skills"]
                ] + details["missing_skills"],
                "matching_details": details,
                "reasons": reasons,
                "eligibility_information": {
                    "experience": job["experience_requirements"],
                    "education": job["education_requirements"],
                    "status": (
                        details.get("eligibility_state", "not_assessed")
                        if matching_basis == "existing_hybrid_analysis"
                        else "not_assessed"
                    ),
                    "note": (
                        "Eligibility evidence comes from the existing hybrid analysis; confirm requirements with the employer."
                        if matching_basis == "existing_hybrid_analysis"
                        else "The resume may not state all qualifications; verify eligibility with the employer."
                    ),
                },
                "recommended_next_steps": [
                    f"Review the source listing and confirm its requirements: {job['source_url']}",
                    *(f"Build evidence for {name} through a project or coursework." for name in missing[:3]),
                ],
            })
        recommendations.sort(key=lambda item: (-item["match_score"], item["role_title"].casefold()))
        return {"resume_id": resume["id"], "recommendations": recommendations}

    @staticmethod
    def _matches_job_preferences(job: dict, preferences: dict | None) -> bool:
        if not preferences:
            return True
        remote = preferences["remote_preference"]
        if remote == "REMOTE" and not job["is_remote"]:
            return False
        if remote == "ONSITE" and job["is_remote"]:
            return False
        locations = preferences["preferred_locations"]
        if locations and not any(
            item.casefold() in (job["location"] or "").casefold()
            or item.casefold() == "remote" and job["is_remote"]
            for item in locations
        ):
            return False
        roles = preferences["preferred_roles"]
        if roles and not any(item.casefold() in job["role_title"].casefold() for item in roles):
            return False
        employment_types = preferences["employment_types"]
        if employment_types and (job["employment_type"] or "").casefold() not in {
            item.casefold() for item in employment_types
        }:
            return False
        experience = preferences["experience_level"]
        return not experience or experience.casefold() == (job["experience_level"] or "").casefold()

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
