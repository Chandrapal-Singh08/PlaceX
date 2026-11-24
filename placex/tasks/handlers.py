"""One handler per job type. Signature: handler(job: dict, ctx: JobContext) -> JSON-able dict.
Handlers never touch threads or queues: that is the OS layer's job."""
import logging
import re
from datetime import date
from math import ceil
from urllib.parse import urlparse

from psycopg import Error as PsycopgError

from .. import db
from ..ai.llm import LLMError
from ..matching import SemanticMatchingError, build_matching_report
from ..resume_pdf import generate_resume_pdf

class JobError(Exception):
    """Permanent, non-retryable-in-spirit validation error (still goes through retry policy)."""

logger = logging.getLogger(__name__)

_RESUME_SECTIONS = {
    "education": ("education", "academic background"),
    "skills": ("skills", "technical skills", "skills and tools"),
    "projects": ("projects", "personal projects", "academic projects"),
    "experience": ("experience", "work experience", "employment", "internships", "internship"),
    "certifications": ("certifications", "certificates"),
    "achievements": ("achievements", "awards"),
    "coursework": ("coursework", "relevant coursework"),
}
_PII_LINE = re.compile(
    r"(?i)(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}|https?://\S+|www\.\S+|"
    r"(?:\+?\d[\d(). -]{7,}\d))"
)


def _resume_sections(text: str | None) -> dict[str, list[str]]:
    sections = {name: [] for name in _RESUME_SECTIONS}
    aliases = {
        alias: section
        for section, names in _RESUME_SECTIONS.items()
        for alias in names
    }
    active = None
    for raw_line in (text or "").splitlines():
        line = " ".join(raw_line.split()).strip(" •\t")
        if not line or _PII_LINE.search(line):
            continue
        heading = re.match(r"^([A-Za-z][A-Za-z &/]{1,39})\s*:\s*(.*)$", line)
        if heading:
            active = aliases.get(heading.group(1).strip().casefold())
            remainder = heading.group(2).strip()
            if active and remainder:
                sections[active].append(remainder[:400])
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z &/]{1,39}", line):
            active = aliases.get(line.casefold())
            if active:
                continue
        if active:
            sections[active].append(line[:400])
    return {key: values[:12] for key, values in sections.items()}


def _safe_resource_url(value: str | None) -> str | None:
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme.lower() not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        parsed.port
    except ValueError:
        return None
    return value.strip()


def _resource_category(title: str, description: str, domain: str) -> str:
    text = f"{title} {description} {domain}".casefold()
    if any(token in text for token in ("leetcode", "codeforces", "coding practice", "problem set", "dsa")):
        return "DSA practice"
    if any(token in text for token in ("operating system", "database", "computer network", "cs50", "open courseware")):
        return "Core CS subjects"
    if any(token in text for token in ("interview", "hiring", "career", "recruitment")):
        return "Interview preparation"
    if any(token in text for token in ("project", "workshop", "hands-on", "hands on")):
        return "Projects"
    if domain.startswith(("docs.", "developer.", "learn.")) or any(
        token in text for token in ("documentation", "docs", "reference")
    ):
        return "Documentation"
    return "Role-specific learning"


def _learning_resources(database_resources: list[dict], research_sources: list[dict]) -> list[dict]:
    resources = []
    seen = set()

    def add(title, description, url, domain, classification, retrieved_at, difficulty, topics):
        safe_url = _safe_resource_url(url)
        if not safe_url:
            return
        parsed = urlparse(safe_url)
        key = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
        if key in seen:
            return
        seen.add(key)
        resources.append({
            "title": str(title or "Learning resource")[:250],
            "description": str(description or "")[:700],
            "category": _resource_category(str(title or ""), str(description or ""), str(domain or "")),
            "difficulty": str(difficulty or "varies"),
            "topics": list(dict.fromkeys(str(topic)[:100] for topic in (topics or []) if topic))[:8],
            "url": safe_url,
            "source_domain": str(domain or parsed.hostname)[:255],
            "source_classification": classification,
            "retrieved_at": retrieved_at,
        })

    for item in database_resources:
        add(
            item.get("title"),
            f"PlaceX resource for {item.get('skill_name') or 'role preparation'}.",
            item.get("url"),
            urlparse(item.get("url") or "").hostname or "",
            "placex_resource",
            item.get("retrieved_at"),
            {1: "beginner", 2: "intermediate", 3: "advanced"}.get(item.get("difficulty"), "varies"),
            [item.get("skill_name")],
        )
    for source in research_sources:
        classification = source.get("source_classification", "secondary_source")
        add(
            source.get("title"),
            source.get("snippet"),
            source.get("url"),
            source.get("source_domain"),
            classification,
            source.get("retrieved_at"),
            "varies",
            [],
        )
    return resources[:20]


def _validate_student_personalization(advice: dict, student_context: dict) -> dict:
    projects = student_context.get("projects") or []
    valid_project_indexes = {
        item["index"] for item in projects if isinstance(item, dict) and "index" in item
    }
    advice["project_preparation"] = [
        item for item in advice.get("project_preparation", [])
        if item.get("project_index") in valid_project_indexes
    ]

    evidence_sources = [
        str(value)
        for key in (
            "education_evidence", "experience_evidence",
            "certifications_evidence", "achievement_evidence",
            "coursework_evidence", "skill_evidence",
        )
        for value in (student_context.get(key) or [])
        if isinstance(value, str)
    ]
    for project in student_context.get("projects", []):
        if isinstance(project, dict) and project.get("evidence"):
            evidence_sources.append(str(project["evidence"]))
    evidence_sources = [" ".join(value.split()) for value in evidence_sources]
    known_skills = {
        str(skill).casefold() for skill in student_context.get("skills", [])
    }
    ats_missing = {
        str(skill).casefold() for skill in student_context.get("missing_skills", [])
    }
    for assessment in advice.get("skill_assessment", []):
        skill_key = str(assessment.get("skill", "")).casefold()
        evidence = []
        for claim in assessment.get("evidence", []):
            normalized = " ".join(str(claim).split()).casefold()
            if len(normalized) >= 12 and any(
                normalized in source.casefold() for source in evidence_sources
            ):
                evidence.append(claim)
        assessment["evidence"] = evidence[:3]
        if assessment.get("status") == "demonstrated" and (
            skill_key not in known_skills or not evidence
        ):
            assessment["status"] = "requires_student_confirmation"
        elif assessment.get("status") == "partially_demonstrated" and not evidence:
            assessment["status"] = "requires_student_confirmation"
        elif (
            assessment.get("status") == "not_demonstrated_in_available_information"
            and skill_key not in ats_missing
        ):
            assessment["status"] = "requires_student_confirmation"
    return advice


def _safe_journey_error(error: Exception) -> str:
    if isinstance(error, (JobError, LLMError)):
        message = str(error)
    elif isinstance(error, PsycopgError):
        message = (
            getattr(getattr(error, "diag", None), "message_primary", None)
            or f"Database operation failed (SQLSTATE {error.sqlstate or 'unknown'})"
        )
    else:
        message = "Unexpected backend failure"
    return " ".join(message.split())[:240]


def _ai_unavailable(result: dict, feature: str, error: LLMError) -> None:
    logger.warning(
        "llm_failure operation=%s failure_stage=structured_generation "
        "exception_class=%s category=%s http_status=%s provider_code=%s safe_error=%s",
        feature,
        type(error).__name__,
        error.category,
        error.http_status,
        error.provider_code or "none",
        _safe_journey_error(error),
    )
    result["ai_status"] = "unavailable"
    result["ai_error_category"] = error.category
    result["ai_notice"] = {
        "authentication_failure": "Groq rejected its configured credentials; general fallback guidance is shown.",
        "rate_limit": "Groq is rate-limited; general fallback guidance is shown.",
        "timeout": "The Groq request timed out; general fallback guidance is shown.",
        "network_failure": "Groq could not be reached; general fallback guidance is shown.",
        "invalid_model": "The configured Groq model is unavailable; general fallback guidance is shown.",
        "invalid_structured_response": "Groq did not return a valid structured guide; general fallback guidance is shown.",
        "output_truncated": "Groq's response exceeded the configured output limit; general fallback guidance is shown.",
        "provider_unavailable": "Groq is temporarily unavailable; general fallback guidance is shown.",
    }.get(error.category, "Groq could not generate AI guidance; general fallback guidance is shown.")


def _journey_fallback(job_role: str) -> dict:
    return {
        "company_role_overview": (
            f"Prepare for the {job_role} role by reviewing its core responsibilities, "
            "practicing relevant problems, and preparing evidence from your own work."
        ),
        "role_responsibilities": [],
        "relevant_technologies": [],
        "required_skills": [],
        "desirable_skills": [],
        "estimated_preparation_hours": 5.0,
        "preparation_sequence": ["Review role fundamentals", "Practice with actual project evidence"],
        "potential_stages": [
            {
                "stage_name": "Role fundamentals",
                "evidence_classification": "ai_generated_preparation",
                "expectation": "A general preparation area, not a confirmed employer interview stage.",
                "preparation_topics": ["Core concepts for the selected role", "Problem-solving"],
                "practical_tasks": ["Explain one role-relevant concept with an example."],
                "common_mistakes": ["Claiming experience or knowledge you cannot support."],
                "practice_questions": ["Explain a core concept you have used in a project."],
            },
            {
                "stage_name": "Practical work and discussion",
                "evidence_classification": "ai_generated_preparation",
                "expectation": "A general preparation area, not a confirmed employer interview stage.",
                "preparation_topics": ["Project decisions", "Trade-offs", "Testing and communication"],
                "practical_tasks": ["Practice a walkthrough of a real project."],
                "common_mistakes": ["Describing team work without clarifying your own contribution."],
                "practice_questions": ["Describe a challenge in a project and how you approached it."],
            },
        ],
        "technical_subjects": [],
        "coding_roadmap": [],
        "technical_questions": [],
        "project_preparation": [],
        "behavioral_competencies": ["Communication", "Learning from feedback", "Teamwork"],
        "behavioral_questions": [
            {
                "question": "Describe a time you received difficult feedback.",
                "competency": "Adaptability",
                "star_guidance": "Explain the context, your responsibility, action, and result.",
            },
            {
                "question": "How do you approach a task when requirements are unclear?",
                "competency": "Problem-solving",
                "star_guidance": "Describe how you clarified assumptions, communicated, and checked the result.",
            },
        ],
        "practical_assignments": [{
            "title": "Role-relevant practice task",
            "description": f"Complete a small practice exercise related to {job_role}.",
            "deliverables": ["A working result", "A brief explanation of decisions"],
            "estimated_hours": 3.0,
        }],
        "preparation_tasks": [{
            "title": f"Review core {job_role} concepts",
            "description": "Use the supplied role information to choose relevant topics.",
            "related_skill": "",
            "level": "BEGINNER",
            "estimated_hours": 3.0,
            "milestone": "Explain three role-relevant concepts in your own words.",
        }, {
            "title": "Prepare a truthful project walkthrough",
            "description": "Choose a project from your actual experience and explain its decisions.",
            "related_skill": "",
            "level": "INTERMEDIATE",
            "estimated_hours": 2.0,
            "milestone": "Complete a concise project explanation and follow-up practice.",
        }],
        "skill_assessment": [],
        "preparation_phases": [
            {"name": "Start", "timeframe": "First", "priorities": ["Review role fundamentals"], "reason": "Build a focused baseline."},
            {"name": "Practice", "timeframe": "Next", "priorities": ["Explain a real project"], "reason": "Connect knowledge to evidence."},
        ],
        "prioritized_strategy": [
            "Review the role description and identify its core responsibilities.",
            "Practice the most relevant role fundamentals.",
            "Prepare truthful examples from your own projects and experience.",
        ],
    }


def make_handlers(
    repo, ai, store, cache, llm=None, matching_weights=None, semantic_matcher=None,
    research=None,
):

    def catalog():
        return cache.get_or_compute("skill_catalog", repo.list_skills)

    def search_research(query, company_name=None):
        if research is None:
            from ..research import ResearchService

            return ResearchService(None).search(query, company_name=company_name)
        return research.search(query, company_name=company_name)

    def research_sources(result):
        return (result or {}).get("sources", [])

    def compact_research(result, limit=5):
        return {
            "status": result.get("status", "unavailable"),
            "notice": result.get("notice", ""),
            "sources": [
                {
                    "title": source.get("title", "")[:300],
                    "url": source.get("url", ""),
                    "source_domain": source.get("source_domain", ""),
                    "snippet": source.get("snippet", "")[:1000],
                    "published_at": source.get("published_at"),
                    "retrieved_at": source.get("retrieved_at"),
                    "source_classification": source.get("source_classification", "secondary_source"),
                    "evidence_type": source.get("evidence_type", "search_provider_snippet"),
                }
                for source in research_sources(result)[:limit]
            ],
        }

    CORE_CATEGORIES = [
        ("language", "programming language"),
        ("framework", "framework"),
        ("database", "database"),
        ("tool", "developer tool"),
        ("cs_fundamental", "computer science fundamentals"),
    ]

    def general_readiness(found):
        by_id = {skill["id"]: skill for skill in catalog()}
        by_category = {}
        for skill_id, _ in found:
            by_category.setdefault(by_id[skill_id]["category"], []).append(by_id[skill_id]["name"])
        missing = [label for category, label in CORE_CATEGORIES if category not in by_category]
        score = round(100.0 * (len(CORE_CATEGORIES) - len(missing)) / len(CORE_CATEGORIES), 2)
        components = [
            {
                "category": category,
                "status": "matched" if category in by_category else "not_evidenced",
                "evidence": by_category.get(category, []),
            }
            for category, _ in CORE_CATEGORIES
        ]
        return {
            "mode": "GENERAL",
            "report_id": None,
            "ats_score": score,
            "score_explanation": (
                f"General readiness is the share of five core skill categories detected "
                f"by exact or catalog-alias matching: {len(CORE_CATEGORIES) - len(missing)} of "
                f"{len(CORE_CATEGORIES)} categories."
            ),
            "score_evidence": components,
            "found": [name for names in by_category.values() for name in names],
            "by_category": by_category,
            "missing_categories": missing,
            "suggestions": [f"No {label} skills detected. Add a project or coursework that shows one."
                            for label in missing],
        }

    # ------------------------------------------------ 1. resume analyzer
    def resume_analysis(job, ctx):
        p = job["payload"]
        mode = p.get("mode", "JD_COMPARISON")
        if mode not in ("JD_COMPARISON", "GENERAL"):
            raise JobError("unknown analysis mode")
        with db.transaction() as conn:                       # report + skills saved atomically
            # row lock = per-resume mutex: two jobs re-analysing the SAME resume are serialized,
            # otherwise their delete-then-insert of resume_skills collides (UniqueViolation)
            resume = repo.get_resume(p["resume_id"], conn, for_update=True)
            jd = None if mode == "GENERAL" else repo.get_jd(p["jd_id"], conn)
            if not resume or (mode != "GENERAL" and not jd):
                raise JobError("resume or job description not found")
            if resume["user_id"] != job["user_id"]:
                raise JobError("resume does not belong to this user")
            if not resume["raw_text"]:
                raise JobError("resume has no extractable text (install pypdf for PDFs or upload .docx/.txt)")
            ctx.check()
            key = ("skills", resume["file_hash"] or f"r{resume['id']}")
            found = cache.get_or_compute(key, lambda: ai.extract_skills(resume["raw_text"], catalog()))
            ctx.check()
            repo.set_resume_skills(resume["id"], found, conn)
            if mode == "GENERAL":
                return general_readiness(found)
            score = repo.ats_score(resume["id"], jd["id"], conn)      # computed inside PostgreSQL
            gap = repo.skill_gap(resume["id"], jd["id"], conn)
            tips = ai.suggest_improvements(gap, score)
            skill_weight_total = sum(int(item["importance"]) for item in gap)
            matched_skill_weight = sum(
                int(item["importance"]) for item in gap if item["matched"]
            )
            skill_evidence_fn = getattr(ai, "extract_skill_evidence", None)
            skill_evidence = (
                skill_evidence_fn(resume["raw_text"], catalog())
                if skill_evidence_fn else []
            )
            matching_report = build_matching_report(
                gap,
                resume["raw_text"],
                jd["description"],
                skill_evidence=skill_evidence,
                weights=matching_weights,
            )
            matching_report["evidence_extraction_status"] = (
                "pending" if llm is not None else "disabled"
            )
            report = repo.save_report(
                job["user_id"],
                resume["id"],
                jd["id"],
                job["id"],
                score,
                tips,
                conn,
                matching_details=matching_report,
            )
        result = {
            "report_id": report["id"],
            "ats_score": score,
            "ats_score_explanation": (
                f"Legacy ATS baseline: {matched_skill_weight} of {skill_weight_total} "
                "importance points matched using exact or catalog-alias skill matching."
            ),
            "matched": [g["skill_name"] for g in gap if g["matched"]],
            "missing": [g["skill_name"] for g in gap if not g["matched"]],
            "suggestions": tips,
            "gap": [{"skill": g["skill_name"], "importance": g["importance"],
                     "required": g["required"], "matched": g["matched"]} for g in gap],
        }
        extracted_requirements = []
        extraction_status = "disabled"
        extraction_usage = None
        if llm is not None:
            try:
                extracted, extraction_usage = llm.extract_matching_evidence(
                    resume["raw_text"], jd["description"], jd["role_title"]
                )
                extracted_requirements = [
                    item.model_dump() for item in extracted.requirements
                ]
                extraction_status = "generated"
            except LLMError as error:
                extraction_status = "unavailable"
                logger.warning("Structured matching evidence unavailable: %s", error)

        semantic_matches = {}
        semantic_status = "unavailable"
        if semantic_matcher is not None:
            try:
                semantic_matches = semantic_matcher.match(
                    resume["raw_text"], [row["skill_name"] for row in gap if not row["matched"]]
                )
                semantic_status = "available"
            except SemanticMatchingError as error:
                logger.warning("Semantic skill matching unavailable: %s", error)

        matching_report = build_matching_report(
            gap,
            resume["raw_text"],
            jd["description"],
            skill_evidence=skill_evidence,
            extracted_requirements=extracted_requirements,
            semantic_matches=semantic_matches,
            semantic_status=semantic_status,
            weights=matching_weights,
        )
        matching_report["evidence_extraction_status"] = extraction_status
        if extraction_usage is not None:
            matching_report["evidence_extraction_usage"] = extraction_usage
        company_name = jd.get("company_name") or ""
        missing_names = ", ".join(
            row["skill_name"] for row in gap if not row["matched"]
        )
        external_research = search_research(
            f"{company_name} {jd['role_title']} role requirements "
            f"technologies skills {missing_names}".strip(),
            company_name or None,
        )
        research_context = compact_research(external_research)
        matching_report["external_research"] = research_context
        repo.update_matching_details(report["id"], matching_report)
        result["matching_report"] = matching_report
        result["external_research"] = external_research
        if llm is not None:
            try:
                advice, usage = llm.explain_resume(
                    resume["raw_text"], jd["role_title"], gap, catalog(),
                    research_context=research_context,
                )
                result.update({
                    "ai_status": "generated",
                    "ai_explanations": [item.model_dump() for item in advice.explanations],
                    "ai_suggestions": advice.suggestions,
                    "potentially_relevant_skills": advice.potentially_relevant_skills,
                    "ai_usage": usage,
                })
            except LLMError as error:
                _ai_unavailable(result, "resume analysis", error)
        return result

    # --------------------------------------------------- 2. resume builder
    def resume_build(job, ctx):
        p = job["payload"]
        jd = repo.get_jd(p["jd_id"])
        if not jd:
            raise JobError("job description not found")
        profile = p.get("profile", {})
        evidence = [
            *profile.get("skills", []),
            *profile.get("education", []),
            *[x.get("description", "") + " " + x.get("name", "") for x in profile.get("projects", [])],
            *profile.get("experience", []),
            *profile.get("certifications", []),
            *profile.get("achievements", []),
            *profile.get("coursework", []),
        ]
        text_for_skills = " ".join(evidence)
        found = ai.extract_skills(text_for_skills, catalog())
        by_id = {s["id"]: s["name"] for s in catalog()}
        have = {by_id[i] for i, _ in found}
        supported = [by_id[i] for i, _ in found]
        with db.transaction() as conn:
            gap = repo.skill_gap(-1, jd["id"], conn)           # all JD skills
        wanted = [g["skill_name"] for g in gap]
        matched = [s for s in wanted if s in have]
        missing = [s for s in wanted if s not in have]
        ctx.check()
        research_company = jd.get("company_name") or ""
        external_research = search_research(
            f"{research_company} {jd['role_title']} required skills "
            f"{', '.join(missing)} official documentation tutorials".strip(),
            research_company or None,
        )
        research_context = compact_research(external_research)
        enriched_profile = {**profile, "_supported_skills": supported}
        ai_result = {}
        if llm is not None:
            try:
                rewrite, usage = llm.rewrite_resume(
                    profile, jd["role_title"], matched, missing,
                    research_context=research_context,
                )
                enriched_profile["_llm_summary"] = rewrite.professional_summary
                enriched_profile["_llm_experience_bullets"] = rewrite.experience_bullets
                enriched_profile["_llm_project_bullets"] = {
                    item.index: item.bullets for item in rewrite.projects
                }
                ai_result = {"ai_status": "generated", "ai_usage": usage}
            except LLMError as error:
                _ai_unavailable(ai_result, "resume builder", error)
        ai_result["external_research"] = external_research
        text = ai.build_resume(enriched_profile, jd, matched, missing)
        pdf = generate_resume_pdf(text)
        f = store.write_generated(job["user_id"], "resume", text)
        store.write_generated_pdf(job["user_id"], f["sha256"], pdf)
        with db.transaction() as conn:
            used = repo.user_storage_bytes(job["user_id"], conn)
            row = repo.add_resume(job["user_id"], "GENERATED", f"{jd['role_title']} resume (generated)",
                                  f["path"], f["sha256"], f["size"], text, conn)
            repo.set_resume_skills(row["id"], [(i, c) for i, c in ai.extract_skills(text, catalog())], conn)
        return {"resume_id": row["id"], "matched_skills": matched,
                "missing_skills": missing, **ai_result}

    # ------------------------------------------------- 3. journey guide
    def journey_guide(job, ctx):
        state = {"stage": "request_validation"}
        try:
            return _run_journey_guide(job, ctx, state)
        except Exception as error:
            logger.error(
                "job_failure job_id=%s operation=journey_guide failure_stage=%s "
                "exception_class=%s safe_error=%s",
                job.get("id", "unknown"),
                state["stage"],
                type(error).__name__,
                _safe_journey_error(error),
            )
            raise

    def _run_journey_guide(job, ctx, state):
        p = job["payload"]
        company_name = str(p.get("company_name") or "").strip()
        job_role = str(p.get("job_role") or p.get("role_title") or "").strip()
        jd = None
        company_id = p.get("company_id")
        state["stage"] = "database_retrieval"
        if p.get("jd_id"):
            jd = repo.get_jd(p["jd_id"])
            if not jd:
                raise JobError("job description not found")
            if company_id is not None and company_id != jd["company_id"]:
                raise JobError("job description does not belong to the selected company")
            company_id = jd["company_id"]
            job_role = job_role or jd["role_title"]
        if company_id is not None and not company_name:
            company = repo.get_company(company_id)
            if not company:
                raise JobError("company not found")
            company_name = company["name"]
        if not job_role and company_id is not None:
            job_role = "General interview preparation"
        if not company_name:
            raise JobError("enter a company name")
        if not job_role:
            raise JobError("enter a job role")

        ctx.check()
        context = repo.journey_context(company_name, job_role)
        company = context["company"]
        if jd:
            contextual_jd = next(
                (row for row in context["job_descriptions"] if row["id"] == jd["id"]),
                None,
            )
            jd = contextual_jd or {
                **jd,
                "context_scope": "company_role",
                "requirements": [],
            }
            matched_jds = [jd, *[
                row for row in context["job_descriptions"] if row["id"] != jd["id"]
            ]]
        else:
            matched_jds = context["job_descriptions"]
            jd = matched_jds[0] if matched_jds and matched_jds[0]["context_scope"] == "company_role" else None
        if company_id is not None and company and company["id"] != company_id:
            raise JobError("job description does not belong to the selected company")

        state["stage"] = "student_personalization"
        rounds = context["hiring_rounds"]
        resume = repo.latest_readable_resume(job["user_id"])
        resume_sections = _resume_sections(resume["raw_text"] if resume else "")
        project_evidence = [
            {"index": index, "evidence": item}
            for index, item in enumerate(resume_sections["projects"][:10])
        ]
        student_context = {
            "resume_available": bool(resume),
            "skills": [],
            "education_evidence": resume_sections["education"],
            "projects": project_evidence,
            "experience_evidence": resume_sections["experience"],
            "certifications_evidence": resume_sections["certifications"],
            "achievement_evidence": resume_sections["achievements"],
            "coursework_evidence": resume_sections["coursework"],
            "project_and_experience_excerpts": [
                *resume_sections["projects"][:5],
                *resume_sections["experience"][:5],
            ],
            "ats_analysis_available": False,
            "matched_skills": [],
            "missing_skills": [],
            "skill_evidence": [],
            "prior_preparation": [],
        }
        if resume:
            resume_skills = repo.resume_skill_names(resume["id"])
            if not resume_skills:
                extracted = ai.extract_skills(resume["raw_text"], catalog())
                names_by_id = {skill["id"]: skill["name"] for skill in catalog()}
                student_context["skills"] = [
                    names_by_id[skill_id] for skill_id, _ in extracted if skill_id in names_by_id
                ]
            else:
                student_context["skills"] = [row["name"] for row in resume_skills]
            student_context["skill_evidence"] = [
                item for item in resume_sections["skills"][:10]
            ]

            if jd:
                if repo.has_analysis_report(job["user_id"], resume["id"], jd["id"]):
                    gaps = repo.skill_gap(resume["id"], jd["id"])
                    student_context["ats_analysis_available"] = True
                    student_context["matched_skills"] = [
                        row["skill_name"] for row in gaps if row["matched"]
                    ]
                    student_context["missing_skills"] = [
                        row["skill_name"] for row in gaps if not row["matched"]
                    ]
        previous_roadmaps = repo.list_user_roadmaps(job["user_id"])
        student_context["prior_preparation"] = [{
            "role_title": roadmap.get("role_title"),
            "company_name": roadmap.get("company_name"),
            "completed_items": roadmap.get("completed_items", 0),
            "total_items": len(roadmap.get("items") or []),
            "incomplete_topics": [
                item.get("title") or item.get("skill")
                for item in (roadmap.get("items") or [])
                if item.get("status") != "DONE"
            ][:8],
        } for roadmap in previous_roadmaps[:5]]

        state["stage"] = "resource_retrieval"
        inferred_skills = ai.extract_skills(
            f"{job_role}\n{p.get('job_description') or ''}", catalog()
        )
        resource_skills = {skill_id for skill_id, _ in inferred_skills}
        extra_resources = repo.list_resources_for_skills(sorted(resource_skills))
        resources_by_id = {row["id"]: row for row in context["resources"]}
        resources_by_id.update({row["id"]: row for row in extra_resources})
        resources = list(resources_by_id.values())[:20]

        def timestamp(value):
            return value.isoformat() if hasattr(value, "isoformat") else value

        database_information = {
            "company": ({
                "name": company["name"],
                "industry": company["industry"],
                "website": company["website"],
                "data_classification": company["data_classification"],
            } if company else None),
            "job_descriptions": [{
                "role_title": row["role_title"],
                "description": row["description"][:6000],
                "location": row["location"],
                "company_name": row["company_name"],
                "context_scope": row["context_scope"],
                "data_classification": row["data_classification"],
                "source_url": row["source_url"],
                "retrieved_at": timestamp(row["retrieved_at"]),
                "last_verified_at": timestamp(row["last_verified_at"]),
                "requirements": row["requirements"] or [],
            } for row in matched_jds[:5]],
            "hiring_rounds": [{
                "round_no": row["round_no"],
                "name": row["name"],
                "round_type": row["round_type"],
                "description": row["description"],
                "duration_min": row["duration_min"],
                "tips": row["tips"],
                "data_classification": row["data_classification"],
            } for row in rounds],
            "resources": [{
                "title": row["title"],
                "url": row["url"],
                "kind": row["kind"],
                "skill_name": row["skill_name"],
                "data_classification": "database_resource",
            } for row in resources],
        }
        state["stage"] = "external_research"
        external_research = search_research(
            " ".join(filter(None, [
                company_name,
                job_role,
                p.get("location"),
                p.get("experience_level"),
                p.get("job_description"),
                "recruitment careers interview requirements role technologies",
            ])),
            company_name,
        )
        resource_research = search_research(
            " ".join(filter(None, [
                job_role,
                ", ".join(student_context["missing_skills"]),
                ", ".join(student_context["skills"]),
                "official documentation course coding practice projects learning resources",
            ])),
            None,
        )
        combined_sources = []
        seen_source_urls = set()
        for source in [
            *research_sources(external_research),
            *research_sources(resource_research),
        ]:
            url = _safe_resource_url(source.get("url"))
            if not url:
                continue
            parsed = urlparse(url)
            source_key = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path.rstrip('/')}"
            if source_key in seen_source_urls:
                continue
            seen_source_urls.add(source_key)
            combined_sources.append({**source, "url": url})
        research_statuses = {
            external_research.get("status"),
            resource_research.get("status"),
        }
        live_research_status = next(
            (
                status for status in ("live_search", "cached", "stale_cache")
                if status in research_statuses
            ),
            (
                "provider_unavailable"
                if "provider_unavailable" in research_statuses
                else "not_configured"
                if research_statuses == {"not_configured"}
                else "not_available"
            ),
        )
        if live_research_status == "not_configured":
            research_notice = (
                "Live web research is not configured; recommendations use PlaceX records "
                "and the role details supplied for this guide."
            )
        elif live_research_status == "provider_unavailable":
            research_notice = (
                "Live web research could not be completed; recommendations use available "
                "PlaceX records and the role details supplied for this guide."
            )
        else:
            research_notice = (
                "External source links are attributed and unverified unless a source is explicitly identified otherwise."
                if combined_sources
                else "No external sources were available for this guide; recommendations use the role information provided."
            )
        external_research = {
            "status": live_research_status,
            "searched_for_this_request": any(
                item.get("searched_for_this_request")
                for item in (external_research, resource_research)
            ),
            "sources": combined_sources,
            "retrieved_at": max(
                (
                    source["retrieved_at"] for source in combined_sources
                    if source.get("retrieved_at")
                ),
                default=None,
            ),
            "notice": research_notice,
        }
        research_context = compact_research(external_research, limit=12)
        learning_resources = _learning_resources(resources, combined_sources)
        sources = [{
            "name": row["company_name"],
            "url": row["source_url"],
            "retrieved_at": timestamp(row["retrieved_at"]),
            "last_verified_at": timestamp(row["last_verified_at"]),
            "data_classification": row["data_classification"],
            "context_scope": row["context_scope"],
        } for row in matched_jds if row["source_url"]]
        verified_rounds = [
            row for row in rounds if row["data_classification"] == "verified"
        ]
        result = {
            "company_name": company_name,
            "job_role": job_role,
            "location": p.get("location"),
            "experience_level": p.get("experience_level"),
            "employment_type": p.get("employment_type"),
            "company_id": company["id"] if company else None,
            "jd_id": jd["id"] if jd else None,
            "role_title": job_role,
            "data_available": bool(rounds),
            "note": None if rounds else "No hiring-round information is currently stored for this company.",
            "total_rounds": len(rounds),
            "rounds": [{
                "no": row["round_no"],
                "name": row["name"],
                "type": row["round_type"],
                "what_to_expect": row["description"],
                "minutes": row["duration_min"],
                "tips": row["tips"],
                "source": "database",
                "data_classification": row["data_classification"],
            } for row in rounds],
            "information_availability": {
                "company_database_record": bool(company),
                "matching_job_descriptions": len(matched_jds),
                "stored_hiring_rounds": len(rounds),
                "verified_hiring_process": bool(verified_rounds),
                "approved_external_hiring_source_used": False,
            },
            "database_information": database_information,
            "external_research": external_research,
            "source_references": sources,
            "resources": learning_resources,
            "personalization": {
                "resume_available": student_context["resume_available"],
                "skills_used": student_context["skills"],
                "ats_analysis_available": student_context["ats_analysis_available"],
                "matched_skills": student_context["matched_skills"],
                "missing_skills": student_context["missing_skills"],
                "education_evidence": student_context["education_evidence"],
                "projects": student_context["projects"],
                "experience_evidence": student_context["experience_evidence"],
                "certifications_evidence": student_context["certifications_evidence"],
                "prior_preparation": student_context["prior_preparation"],
            },
            "verification_notice": (
                "Company and hiring information is labeled by its source. Preparation stages and practice questions "
                "are recommendations, not confirmed employer interview rounds."
            ),
        }

        state["stage"] = "groq_generation"
        if llm is not None:
            try:
                advice, usage = llm.prepare_journey(
                    company_name,
                    job_role,
                    p.get("location"),
                    p.get("experience_level"),
                    p.get("employment_type"),
                    p.get("job_description"),
                    {
                        "database_information": database_information,
                        "external_research": research_context,
                    },
                    student_context,
                )
                advice_data = _validate_student_personalization(
                    advice.model_dump(), student_context
                )
                result.update({
                    "ai_status": "generated",
                    "guidance_status": "ai_generated",
                    "ai_guidance_source": "AI-generated general preparation guidance; not verified company hiring information.",
                    "ai_guidance": advice_data,
                    "ai_usage": usage,
                })
            except LLMError as error:
                _ai_unavailable(result, "journey guide", error)
                result.update({
                    "guidance_status": "fallback",
                    "ai_guidance_source": "General fallback preparation guidance; not verified company hiring information.",
                    "ai_guidance": _journey_fallback(job_role),
                })
        else:
            logger.warning(
                "llm_unavailable operation=journey_guide failure_stage=service_initialization "
                "category=not_configured"
            )
            result.update({
                "ai_status": "unavailable",
                "guidance_status": "fallback",
                "ai_error_category": "not_configured",
                "ai_notice": "Groq is not configured; general fallback preparation guidance is shown.",
                "ai_guidance_source": "General fallback preparation guidance; not verified company hiring information.",
                "ai_guidance": _journey_fallback(job_role),
            })

        ctx.check()
        state["stage"] = "guide_persistence"
        saved = repo.save_journey_guide(
            job["user_id"],
            company_name,
            job_role,
            p.get("location"),
            p.get("experience_level"),
            p.get("employment_type"),
            result,
            {
                "database_information": database_information,
                "external_research": external_research,
            },
        )
        result["guide_id"] = saved["id"]
        result["created_at"] = timestamp(saved["created_at"])
        return result

    # ------------------------------------------------ 4. prep planner
    def prep_plan(job, ctx):
        p = job["payload"]
        try:
            daily_hours = int(p.get("daily_study_hours", 2))
        except (TypeError, ValueError) as error:
            raise JobError("daily study time must be between 1 and 8 hours") from error
        if not 1 <= daily_hours <= 8:
            raise JobError("daily study time must be between 1 and 8 hours")

        deadline_days = p.get("deadline_days")
        deadline_date = p.get("deadline_date")
        if deadline_days is not None:
            try:
                deadline_days = int(deadline_days)
            except (TypeError, ValueError) as error:
                raise JobError("choose a supported preparation deadline") from error
            if deadline_days not in (7, 15, 30, 60):
                raise JobError("choose a supported preparation deadline")
        if deadline_date:
            try:
                deadline = date.fromisoformat(deadline_date)
            except (TypeError, ValueError) as error:
                raise JobError("enter a valid preparation deadline") from error
            deadline_days = (deadline - date.today()).days
            if deadline_days < 1:
                raise JobError("preparation deadline must be at least one day from today")

        def schedule(total_hours):
            details = {
                "daily_study_hours": daily_hours,
                "estimated_study_days": ceil(total_hours / daily_hours) if total_hours else 0,
            }
            if deadline_days:
                required_daily_hours = round(total_hours / deadline_days, 1)
                details.update({
                    "deadline_days": deadline_days,
                    "deadline_date": deadline_date,
                    "required_daily_hours": required_daily_hours,
                    "deadline_feasible": required_daily_hours <= daily_hours,
                })
            return details

        with db.transaction() as conn:
            report = repo.get_report(p["report_id"], conn, for_update=True)
            if not report or report["user_id"] != job["user_id"]:
                raise JobError("report not found for this user")
            resume = repo.get_resume(report["resume_id"], conn, for_update=True)
            if not resume or resume["user_id"] != job["user_id"]:
                raise JobError("resume not found for this user")
            regenerate = p.get("regenerate") is True
            existing = repo.get_roadmap_for_report(job["user_id"], report["id"], conn)
            if existing and not regenerate:
                return {
                    "roadmap_id": existing["id"],
                    "reused_existing": True,
                    "title": existing["title"],
                    "total_hours": float(existing["total_hours"]),
                    **schedule(float(existing["total_hours"])),
                }
            gap = repo.skill_gap(report["resume_id"], report["jd_id"], conn)
            hours = {s["id"]: s["est_hours"] for s in catalog()}
            missing = sorted((g for g in gap if not g["matched"]),
                             key=lambda g: (not g["required"], -g["importance"], g["skill_name"]))
            if not missing:
                return {"roadmap_id": None, "items": [],
                        "matched_skills": [g["skill_name"] for g in gap if g["matched"]],
                        "note": "No skill gaps were found in this analysis.",
                        **schedule(0)}
            items, picked = [], []
            for g in missing:
                r = repo.pick_resource(g["skill_id"], conn)
                picked.append(r)
                difficulty = int(r["difficulty"]) if r else 1
                level = {1: "BEGINNER", 2: "INTERMEDIATE", 3: "ADVANCED"}.get(
                    difficulty, "INTERMEDIATE"
                )
                skill_name = g["skill_name"]
                practice_task = (
                    f"Complete a small {skill_name} exercise and save the working result."
                    if level == "BEGINNER"
                    else f"Build a focused project feature using {skill_name} and document your decisions."
                    if level == "INTERMEDIATE"
                    else f"Complete an end-to-end {skill_name} project and evaluate its trade-offs."
                )
                items.append({
                    "skill_id": g["skill_id"],
                    "resource_id": r["id"] if r else None,
                    "est_hours": hours.get(g["skill_id"], 10),
                    "level": level,
                    "practice_task": practice_task,
                    "milestone": f"{level.title()} {skill_name} milestone",
                })
            jd = repo.get_jd(report["jd_id"], conn)
            rm = repo.create_roadmap(job["user_id"], jd["id"], report["id"],
                             f"Prep plan: {jd['role_title']}" +
                             (f" @ {jd['company_name']}" if jd["company_name"] else ""), items, conn)
        total_hours = sum(item["est_hours"] for item in items)
        sequence = [
            {
                "skill": gap_item["skill_name"],
                "level": item["level"],
                "position": index + 1,
            }
            for index, (gap_item, item) in enumerate(zip(missing, items))
        ]
        sequence.sort(key=lambda entry: (
            {"BEGINNER": 1, "INTERMEDIATE": 2, "ADVANCED": 3}[entry["level"]],
            entry["position"],
        ))
        result = {
            "roadmap_id": rm["id"],
            "total_hours": total_hours,
            **schedule(total_hours),
            "learning_sequence": sequence,
            "items": [{
                "position": index + 1,
                "skill": gap_item["skill_name"],
                "required": gap_item["required"],
                "est_hours": item["est_hours"],
                "level": item["level"],
                "practice_task": item["practice_task"],
                "milestone": item["milestone"],
                "resource": ({
                    "title": picked[index]["title"],
                    "url": picked[index]["url"],
                    "kind": picked[index]["kind"],
                } if picked[index] else None),
            } for index, (gap_item, item) in enumerate(zip(missing, items))],
        }
        research_company = jd.get("company_name") or ""
        external_research = search_research(
            f"{research_company} {jd['role_title']} "
            f"{', '.join(item['skill_name'] for item in missing)} "
            "official documentation learning resources practice".strip(),
            research_company or None,
        )
        research_context = compact_research(external_research)
        result["external_research"] = external_research
        if llm is not None:
            try:
                advice, usage = llm.explain_prep_plan(
                    jd["role_title"],
                    [
                        {
                            "skill": gap_item["skill_name"],
                            "required": gap_item["required"],
                            "importance": gap_item["importance"],
                            "estimated_hours": items[index]["est_hours"],
                            "resource": picked[index]["title"] if picked[index] else None,
                        }
                        for index, gap_item in enumerate(missing)
                    ],
                    daily_hours,
                    deadline_days,
                    research_context=research_context,
                )
                result.update({
                    "ai_status": "generated",
                    "ai_guidance": {
                        "source": "AI-generated study suggestions",
                        "sequence": advice.sequence,
                        "explanations": [item.model_dump() for item in advice.explanations],
                        "weekly_schedule": [item.model_dump() for item in advice.weekly_schedule],
                    },
                    "ai_usage": usage,
                })
            except LLMError as error:
                _ai_unavailable(result, "prep planner", error)
        return result

    return {"RESUME_ANALYSIS": resume_analysis, "RESUME_BUILD": resume_build,
            "JOURNEY_GUIDE": journey_guide, "PREP_PLAN": prep_plan}
