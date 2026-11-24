"""Data-access layer. All SQL lives here. Methods accept an optional `conn` so several
calls can share ONE transaction (e.g. report + skills are saved atomically)."""
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from psycopg.types.json import Jsonb
from . import db
CUSTOM_COMPANY = "Custom job description"

def _j(obj):
    return Jsonb(obj, dumps=lambda o: json.dumps(o, default=str))

def listing_timestamp_now():
    return datetime.now(timezone.utc)

def _contains_pattern(value):
    escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"

@contextmanager
def _c(conn=None):
    if conn is not None:
        yield conn
    else:
        with db.transaction() as c:
            yield c

class Repo:
    # ------------------------------------------------------------- users
    def register_user(self, email, full_name, roll_no, password_hash, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO users(email, full_name, roll_no, password_hash)
                   VALUES (%s,%s,%s,%s) RETURNING id, email, full_name, roll_no""",
                (email, full_name, roll_no, password_hash),
            ).fetchone()

    def create_user(self, email, full_name, roll_no=None, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO users(email, full_name, roll_no) VALUES (%s,%s,%s)
                   ON CONFLICT (email) DO UPDATE SET full_name = EXCLUDED.full_name
                   RETURNING *""", (email, full_name, roll_no)).fetchone()

    def create_session(self, user_id, token_hash, expires_at, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO user_sessions(user_id, token_hash, expires_at)
                   VALUES (%s,%s,%s) RETURNING id""",
                (user_id, token_hash, expires_at),
            ).fetchone()

    def get_session_user(self, token_hash, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT u.id, u.email, u.full_name, u.role
                   FROM user_sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at > now()""",
                (token_hash,),
            ).fetchone()

    def revoke_session(self, token_hash, conn=None):
        with _c(conn) as c:
            c.execute(
                "UPDATE user_sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL",
                (token_hash,),
            )

    def get_auth_user_by_email(self, email, conn=None):
        with _c(conn) as c:
            return c.execute(
                "SELECT id, email, full_name, password_hash FROM users WHERE email=%s",
                (email,),
            ).fetchone()

    def dashboard(self, user_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM v_student_dashboard WHERE user_id=%s", (user_id,)).fetchone()

    # ------------------------------------------------ skills / companies / JDs
    def list_skills(self, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT id, name::text AS name, category, aliases, est_hours FROM skills ORDER BY id").fetchall()

    def list_companies(self, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM companies WHERE name <> %s ORDER BY id", (CUSTOM_COMPANY,)).fetchall()

    def get_company(self, company_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                "SELECT * FROM companies WHERE id=%s AND name <> %s",
                (company_id, CUSTOM_COMPANY),
            ).fetchone()

    def list_job_descriptions(self, conn=None):
        with _c(conn) as c:
            return c.execute(
                     """SELECT j.id, j.role_title, j.company_id,
                                  CASE WHEN c.name = %s THEN '' ELSE c.name END AS company_name,
                                  j.location, j.data_classification, j.source_url,
                                  j.retrieved_at, j.last_verified_at
                   FROM job_descriptions j JOIN companies c ON c.id = j.company_id
                         ORDER BY c.name, j.role_title, j.id""",
                     (CUSTOM_COMPANY,),
            ).fetchall()

    def get_company_by_name(self, name, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT * FROM companies
                   WHERE lower(name)=lower(%s) AND data_classification <> 'sample'
                   ORDER BY id LIMIT 1""",
                (name,),
            ).fetchone()

    def create_source_company(self, name, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO companies(name, data_classification)
                   VALUES (%s,'source_reported')
                   ON CONFLICT(name) DO UPDATE SET name=EXCLUDED.name
                   RETURNING *""",
                (name,),
            ).fetchone()

    def custom_company_id(self, conn=None):
        """Return the internal company row required by the JD foreign key."""
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO companies(name) VALUES (%s)
                   ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id""",
                (CUSTOM_COMPANY,),
            ).fetchone()["id"]

    def find_jd(self, company_id, description, conn=None):
        with _c(conn) as c:
            return c.execute(
                "SELECT * FROM job_descriptions WHERE company_id=%s AND description=%s LIMIT 1",
                (company_id, description),
            ).fetchone()

    def unique_role_title(self, company_id, title, conn=None):
        with _c(conn) as c:
            taken = {
                row["role_title"] for row in c.execute(
                    "SELECT role_title FROM job_descriptions WHERE company_id=%s AND role_title LIKE %s",
                    (company_id, title.replace("%", r"\%").replace("_", r"\_") + "%"),
                ).fetchall()
            }
        candidate, number = title, 1
        while candidate in taken:
            number += 1
            candidate = f"{title} ({number})"
        return candidate

    def get_jd(self, jd_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT j.*, CASE WHEN c.name = %s THEN '' ELSE c.name END AS company_name
                   FROM job_descriptions j JOIN companies c ON c.id = j.company_id WHERE j.id=%s""",
                (CUSTOM_COMPANY, jd_id),
            ).fetchone()

    def create_jd(
        self, company_id, role_title, description, skills, conn=None,
        data_classification="user_provided", source_url=None, retrieved_at=None,
        last_verified_at=None,
    ):
        """skills: iterable of (skill_id, importance, required)"""
        with _c(conn) as c:
            jd = c.execute(
                """INSERT INTO job_descriptions
                   (company_id, role_title, description, data_classification, source_url,
                    retrieved_at, last_verified_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    company_id, role_title, description, data_classification,
                    source_url, retrieved_at, last_verified_at,
                ),
            ).fetchone()
            with c.cursor() as cur:
                cur.executemany("INSERT INTO jd_skills(jd_id, skill_id, importance, required) VALUES (%s,%s,%s,%s)",
                                [(jd["id"], s, i, r) for s, i, r in skills])
            return jd

    def get_rounds(self, company_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM hiring_rounds WHERE company_id=%s ORDER BY round_no",
                             (company_id,)).fetchall()

    def journey_context(self, company_name, job_role, conn=None):
        """Return employer-scoped records, or role-only references when the employer is unknown."""
        role_pattern = _contains_pattern(job_role)
        with _c(conn) as c:
            company = c.execute(
                """SELECT id, name, industry, website, data_classification
                   FROM companies
                   WHERE lower(name)=lower(%s)
                   ORDER BY id
                   LIMIT 1""",
                (company_name,),
            ).fetchone()

            if company:
                job_descriptions = c.execute(
                    """SELECT j.id, j.role_title, j.description, j.location,
                              j.data_classification, j.source_url, j.retrieved_at,
                              j.last_verified_at, c.name AS company_name,
                                  'company_role' AS context_scope,
                                  requirements.items AS requirements
                           FROM job_descriptions j
                           JOIN companies c ON c.id=j.company_id
                           LEFT JOIN LATERAL (
                               SELECT jsonb_agg(jsonb_build_object(
                                   'skill', s.name::text,
                                   'importance', js.importance,
                                   'required', js.required
                               ) ORDER BY js.importance DESC, s.name) AS items
                               FROM jd_skills js JOIN skills s ON s.id=js.skill_id
                               WHERE js.jd_id=j.id
                           ) requirements ON true
                           WHERE j.company_id=%s
                         AND (j.role_title ILIKE %s OR j.description ILIKE %s)
                       ORDER BY (lower(j.role_title)=lower(%s)) DESC,
                                j.last_verified_at DESC NULLS LAST, j.id DESC
                       LIMIT 5""",
                    (company["id"], role_pattern, role_pattern, job_role),
                ).fetchall()
                rounds = c.execute(
                    """SELECT round_no, name, round_type, description, duration_min,
                              tips, data_classification
                       FROM hiring_rounds WHERE company_id=%s ORDER BY round_no""",
                    (company["id"],),
                ).fetchall()
            else:
                job_descriptions = c.execute(
                    """SELECT j.id, j.role_title, j.description, j.location,
                              j.data_classification, j.source_url, j.retrieved_at,
                              j.last_verified_at, c.name AS company_name,
                                  'role_reference_only' AS context_scope,
                                  requirements.items AS requirements
                           FROM job_descriptions j
                           JOIN companies c ON c.id=j.company_id
                           LEFT JOIN LATERAL (
                               SELECT jsonb_agg(jsonb_build_object(
                                   'skill', s.name::text,
                                   'importance', js.importance,
                                   'required', js.required
                               ) ORDER BY js.importance DESC, s.name) AS items
                               FROM jd_skills js JOIN skills s ON s.id=js.skill_id
                               WHERE js.jd_id=j.id
                           ) requirements ON true
                       WHERE j.role_title ILIKE %s OR j.description ILIKE %s
                       ORDER BY (lower(j.role_title)=lower(%s)) DESC,
                                j.last_verified_at DESC NULLS LAST, j.id DESC
                       LIMIT 5""",
                    (role_pattern, role_pattern, job_role),
                ).fetchall()
                rounds = []

            jd_ids = [row["id"] for row in job_descriptions]
            resources = []
            if jd_ids:
                resources = c.execute(
                    """SELECT DISTINCT r.id, r.title, r.url, r.kind, r.difficulty,
                              s.name::text AS skill_name
                       FROM jd_skills js
                       JOIN resources r ON r.skill_id=js.skill_id
                       JOIN skills s ON s.id=js.skill_id
                       WHERE js.jd_id = ANY(%s)
                       ORDER BY r.difficulty, r.title, r.id
                       LIMIT 20""",
                    (jd_ids,),
                ).fetchall()

            return {
                "company": company,
                "job_descriptions": job_descriptions,
                "hiring_rounds": rounds,
                "resources": resources,
            }

    def list_resources_for_skills(self, skill_ids, limit=20, conn=None):
        if not skill_ids:
            return []
        with _c(conn) as c:
            return c.execute(
                """SELECT DISTINCT r.id, r.title, r.url, r.kind, r.difficulty,
                          s.name::text AS skill_name
                   FROM resources r JOIN skills s ON s.id=r.skill_id
                   WHERE r.skill_id = ANY(%s)
                   ORDER BY r.difficulty, r.title, r.id
                   LIMIT %s""",
                (skill_ids, max(1, min(int(limit), 50))),
            ).fetchall()

    def resume_skill_names(self, resume_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT s.name::text AS name
                   FROM resume_skills rs JOIN skills s ON s.id=rs.skill_id
                   WHERE rs.resume_id=%s ORDER BY s.name""",
                (resume_id,),
            ).fetchall()

    def get_research_cache(self, query_hash, include_expired=False, conn=None):
        predicate = "" if include_expired else " AND expires_at > now()"
        with _c(conn) as c:
            return c.execute(
                """SELECT provider_name, query_text, results, retrieved_at, expires_at
                   FROM research_cache
                   WHERE query_hash=%s""" + predicate,
                (query_hash,),
            ).fetchone()

    def save_research_cache(
        self, query_hash, provider_name, query_text, results,
        retrieved_at, expires_at, conn=None,
    ):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO research_cache
                   (query_hash, provider_name, query_text, results, retrieved_at, expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (query_hash) DO UPDATE SET
                       provider_name=EXCLUDED.provider_name,
                       query_text=EXCLUDED.query_text,
                       results=EXCLUDED.results,
                       retrieved_at=EXCLUDED.retrieved_at,
                       expires_at=EXCLUDED.expires_at
                   RETURNING query_hash""",
                (
                    query_hash, provider_name, query_text, _j(results),
                    retrieved_at, expires_at,
                ),
            ).fetchone()

    def journey_schema_missing_columns(self, conn=None):
        required_columns = {
            "id", "user_id", "company_name", "job_role", "location",
            "experience_level", "employment_type", "guidance",
            "retrieved_context", "created_at",
        }
        with _c(conn) as c:
            rows = c.execute(
                """SELECT column_name
                   FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='journey_guides'"""
            ).fetchall()
        present_columns = {row["column_name"] for row in rows}
        return [
            f"journey_guides.{column}"
            for column in sorted(required_columns - present_columns)
        ]

    def journey_roadmap_schema_missing_columns(self, conn=None):
        required = {
            "roadmaps": {
                "source_guide_id", "company_name", "role_title",
                "daily_study_hours", "deadline_days", "deadline_date",
            },
            "roadmap_items": {"title", "resource_title", "resource_url"},
        }
        with _c(conn) as c:
            rows = c.execute(
                """SELECT table_name, column_name, is_nullable
                   FROM information_schema.columns
                   WHERE table_schema='public'
                     AND table_name = ANY(%s)""",
                (list(required),),
            ).fetchall()
        present = {
            table: {row["column_name"] for row in rows if row["table_name"] == table}
            for table in required
        }
        missing = [
            f"{table}.{column}"
            for table, columns in required.items()
            for column in sorted(columns - present[table])
        ]
        if not any(row["table_name"] == "roadmaps" and row["column_name"] == "jd_id"
                   and row["is_nullable"] == "YES" for row in rows):
            missing.append("roadmaps.jd_id(nullable)")
        if not any(row["table_name"] == "roadmap_items" and row["column_name"] == "skill_id"
                   and row["is_nullable"] == "YES" for row in rows):
            missing.append("roadmap_items.skill_id(nullable)")
        return missing

    def save_journey_guide(
        self, user_id, company_name, job_role, location, experience_level,
        employment_type, guidance, retrieved_context, conn=None,
    ):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO journey_guides
                   (user_id, company_name, job_role, location, experience_level,
                    employment_type, guidance, retrieved_context)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, created_at""",
                (
                    user_id, company_name, job_role, location, experience_level,
                    employment_type, _j(guidance), _j(retrieved_context),
                ),
            ).fetchone()

    def list_user_journey_guides(self, user_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT id, company_name, job_role, location, experience_level,
                          employment_type, guidance, created_at
                   FROM journey_guides WHERE user_id=%s
                   ORDER BY created_at DESC, id DESC LIMIT 50""",
                (user_id,),
            ).fetchall()

    def get_user_journey_guide(self, user_id, guide_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT id, company_name, job_role, location, experience_level,
                          employment_type, guidance, retrieved_context, created_at
                   FROM journey_guides WHERE user_id=%s AND id=%s""",
                (user_id, guide_id),
            ).fetchone()

    def create_journey_roadmap(
        self, user_id, guide_id, daily_study_hours, deadline_days=None, conn=None,
    ):
        if not 1 <= int(daily_study_hours) <= 8:
            raise ValueError("daily_study_hours must be between 1 and 8")
        if deadline_days is not None and int(deadline_days) not in (7, 15, 30, 60):
            raise ValueError("deadline_days must be one of 7, 15, 30, or 60")
        with _c(conn) as c:
            guide = c.execute(
                """SELECT id, company_name, job_role, guidance
                   FROM journey_guides
                   WHERE id=%s AND user_id=%s
                   FOR UPDATE""",
                (guide_id, user_id),
            ).fetchone()
            if not guide:
                return None
            existing = c.execute(
                "SELECT id FROM roadmaps WHERE user_id=%s AND source_guide_id=%s",
                (user_id, guide_id),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "created": False}

            guidance = guide["guidance"] or {}
            advice = guidance.get("ai_guidance") or {}
            tasks = advice.get("preparation_tasks") or []
            if not tasks:
                tasks = [
                    {
                        "title": str(item)[:160],
                        "description": "Review this preparation priority using the supplied role information.",
                        "related_skill": "",
                        "level": "INTERMEDIATE",
                        "estimated_hours": 2,
                        "milestone": "Complete a focused review and practice exercise.",
                    }
                    for item in (advice.get("prioritized_strategy") or [])[:8]
                ]
            if not tasks:
                tasks = [{
                    "title": "Review role fundamentals",
                    "description": "Identify and practice core topics from the saved guide.",
                    "related_skill": "",
                    "level": "BEGINNER",
                    "estimated_hours": 2,
                    "milestone": "Summarize key role concepts and complete a practice task.",
                }]

            roadmap = c.execute(
                """INSERT INTO roadmaps
                   (user_id, jd_id, report_id, source_guide_id, company_name, role_title,
                    daily_study_hours, deadline_days, title)
                   VALUES (%s,NULL,NULL,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (
                    user_id,
                    guide_id,
                    guide["company_name"],
                    guide["job_role"],
                    int(daily_study_hours),
                    int(deadline_days) if deadline_days is not None else None,
                    f"Prep plan: {guide['job_role']} @ {guide['company_name']}"[:250],
                ),
            ).fetchone()
            resources = guidance.get("resources") or []
            with c.cursor() as cursor:
                for position, task in enumerate(tasks[:12], start=1):
                    if not isinstance(task, dict):
                        continue
                    title = str(task.get("title") or task.get("related_skill") or "").strip()[:160]
                    if not title:
                        continue
                    try:
                        hours = float(task.get("estimated_hours", 2))
                    except (TypeError, ValueError):
                        hours = 2.0
                    if not 0 < hours <= 80:
                        hours = 2.0
                    level = str(task.get("level") or "INTERMEDIATE").upper()
                    if level not in {"BEGINNER", "INTERMEDIATE", "ADVANCED"}:
                        level = "INTERMEDIATE"
                    keywords = set(
                        re.findall(
                            r"[a-z0-9]+",
                            f"{title} {task.get('related_skill') or ''}".casefold(),
                        )
                    )
                    resource = next(
                        (
                            item for item in resources
                            if isinstance(item, dict)
                            and isinstance(item.get("url"), str)
                            and item["url"].startswith(("https://", "http://"))
                            and keywords.intersection(
                                set(re.findall(
                                    r"[a-z0-9]+",
                                    f"{item.get('title', '')} {' '.join(item.get('topics', []))}".casefold(),
                                ))
                            )
                        ),
                        None,
                    )
                    cursor.execute(
                        """INSERT INTO roadmap_items
                           (roadmap_id, skill_id, resource_id, title, resource_title,
                            resource_url, position, est_hours, level, practice_task, milestone)
                           VALUES (%s,NULL,NULL,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            roadmap["id"],
                            title,
                            resource.get("title") if resource else None,
                            resource.get("url") if resource else None,
                            position,
                            hours,
                            level,
                            str(task.get("description") or "")[:1000],
                            str(task.get("milestone") or "")[:300],
                        ),
                    )
            return {"id": roadmap["id"], "created": True}

    # ------------------------------------------------------ job discovery
    def upsert_external_job(self, listing, source_name, skill_ids, conn=None):
        source_url = str(listing.source_url)
        retrieved_at = listing_timestamp_now()
        with _c(conn) as c:
            company_name = listing.company_name.strip()
            company = self.get_company_by_name(company_name, c)
            if company is None:
                collision = c.execute(
                    "SELECT 1 FROM companies WHERE lower(name)=lower(%s) LIMIT 1",
                    (company_name,),
                ).fetchone()
                internal_company_name = (
                    f"{company_name} (source: {source_name})" if collision else company_name
                )
                company = self.create_source_company(internal_company_name, c)

            existing_jd = self.find_jd(company["id"], listing.description, c)
            if existing_jd is None:
                skill_pairs = [(skill_id, 3, True) for skill_id in dict.fromkeys(skill_ids)]
                title = self.unique_role_title(company["id"], listing.role_title, c)
                existing_jd = self.create_jd(
                    company["id"],
                    title,
                    listing.description,
                    skill_pairs,
                    c,
                    data_classification="source_reported",
                    source_url=source_url,
                    retrieved_at=retrieved_at,
                    last_verified_at=retrieved_at if listing.is_verified else None,
                )
            elif existing_jd["data_classification"] == "source_reported":
                c.execute(
                    """UPDATE job_descriptions SET source_url=%s, retrieved_at=%s,
                       last_verified_at=CASE WHEN %s THEN %s ELSE last_verified_at END
                       WHERE id=%s""",
                    (
                        source_url, retrieved_at, listing.is_verified,
                        retrieved_at, existing_jd["id"],
                    ),
                )

            status = listing.status
            expires_at = listing.expires_at
            if expires_at:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if retrieved_at >= expires_at:
                    status = "EXPIRED"
            skill_names = c.execute(
                "SELECT name::text AS name FROM skills WHERE id=ANY(%s) ORDER BY name",
                (list(skill_ids),),
            ).fetchall() if skill_ids else []
            skills = list(dict.fromkeys([
                *(item.strip() for item in listing.skills if item.strip()),
                *(item["name"] for item in skill_names),
            ]))
            return c.execute(
                """INSERT INTO external_jobs
                   (source_name, external_id, source_url, company_name, role_title, description,
                    location, is_remote, employment_type, experience_level,
                    experience_requirements, education_requirements, skills, status,
                    is_verified, retrieved_at, last_verified_at, expires_at, jd_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                           CASE WHEN %s THEN %s ELSE NULL END,%s,%s)
                   ON CONFLICT (source_name, external_id) DO UPDATE SET
                    external_id=EXCLUDED.external_id,
                    source_url=EXCLUDED.source_url,
                    company_name=EXCLUDED.company_name,
                    role_title=EXCLUDED.role_title,
                    description=EXCLUDED.description,
                    location=EXCLUDED.location,
                    is_remote=EXCLUDED.is_remote,
                    employment_type=EXCLUDED.employment_type,
                    experience_level=EXCLUDED.experience_level,
                    experience_requirements=EXCLUDED.experience_requirements,
                    education_requirements=EXCLUDED.education_requirements,
                    skills=EXCLUDED.skills,
                    status=EXCLUDED.status,
                    is_verified=EXCLUDED.is_verified,
                    retrieved_at=EXCLUDED.retrieved_at,
                    last_verified_at=COALESCE(EXCLUDED.last_verified_at,external_jobs.last_verified_at),
                    expires_at=EXCLUDED.expires_at,
                    jd_id=COALESCE(external_jobs.jd_id,EXCLUDED.jd_id)
                   RETURNING id""",
                (
                    source_name, listing.external_id, source_url, company_name,
                    listing.role_title, listing.description, listing.location,
                    listing.is_remote, listing.employment_type, listing.experience_level,
                    listing.experience_requirements, listing.education_requirements,
                    _j(skills), status, listing.is_verified, retrieved_at,
                    listing.is_verified, retrieved_at, expires_at, existing_jd["id"],
                ),
            ).fetchone()

    def list_external_jobs(self, filters=None, limit=50, offset=0, verified_only=False, conn=None):
        if filters is not None:
            limit, query = filters.limit, filters.query
            company, location = filters.company, filters.location
            remote, experience, skill = (
                filters.remote, filters.experience_level, filters.skill
            )
        else:
            query = company = location = experience = skill = None
            remote = None
        conditions = ["e.status='ACTIVE'"]
        params = []
        if verified_only:
            conditions.append("e.is_verified")
        for value, expression in (
            (query, "(e.role_title ILIKE %s OR e.description ILIKE %s OR e.company_name ILIKE %s)"),
            (company, "e.company_name ILIKE %s"),
            (location, "e.location ILIKE %s"),
            (experience, "e.experience_level ILIKE %s"),
            (skill, "(e.skills::text ILIKE %s OR e.description ILIKE %s)"),
        ):
            if value:
                pattern = f"%{value.strip()}%"
                conditions.append(expression)
                params.extend([pattern] * expression.count("%s"))
        if remote is not None:
            conditions.append("e.is_remote=%s")
            params.append(remote)
        params.extend([max(1, min(int(limit), 100)), max(0, int(offset))])
        with _c(conn) as c:
            c.execute(
                """UPDATE external_jobs SET status='EXPIRED'
                   WHERE status='ACTIVE' AND expires_at IS NOT NULL AND expires_at <= now()"""
            )
            return c.execute(
                f"""SELECT e.id, e.source_name, e.external_id, e.source_url,
                           e.company_name, e.role_title,
                           e.description, e.location, e.is_remote, e.employment_type, e.experience_level,
                           e.experience_requirements, e.education_requirements, e.skills, e.status,
                           e.is_verified, e.retrieved_at, e.last_verified_at, e.expires_at, e.jd_id,
                           j.company_id
                    FROM external_jobs e
                    LEFT JOIN job_descriptions j ON j.id=e.jd_id
                    WHERE {' AND '.join(conditions)}
                    ORDER BY e.is_verified DESC, e.last_verified_at DESC NULLS LAST, e.id DESC
                    LIMIT %s OFFSET %s""",
                params,
            ).fetchall()

    def get_external_job(self, external_job_id, conn=None):
        with _c(conn) as c:
            c.execute(
                """UPDATE external_jobs SET status='EXPIRED'
                   WHERE id=%s AND status='ACTIVE'
                     AND expires_at IS NOT NULL AND expires_at <= now()""",
                (external_job_id,),
            )
            return c.execute(
                "SELECT * FROM external_jobs WHERE id=%s",
                (external_job_id,),
            ).fetchone()

    # ------------------------------------------------------- preferences
    def get_job_preferences(self, user_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                "SELECT * FROM user_job_preferences WHERE user_id=%s",
                (user_id,),
            ).fetchone()

    def save_job_preferences(self, user_id, preferences, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO user_job_preferences
                   (user_id, preferred_roles, preferred_locations, remote_preference,
                    employment_types, experience_level)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(user_id) DO UPDATE SET
                    preferred_roles=EXCLUDED.preferred_roles,
                    preferred_locations=EXCLUDED.preferred_locations,
                    remote_preference=EXCLUDED.remote_preference,
                    employment_types=EXCLUDED.employment_types,
                    experience_level=EXCLUDED.experience_level,
                    updated_at=now()
                   RETURNING *""",
                (
                    user_id,
                    preferences["preferred_roles"],
                    preferences["preferred_locations"],
                    preferences["remote_preference"],
                    preferences["employment_types"],
                    preferences["experience_level"],
                ),
            ).fetchone()

    # --------------------------------------------------- recommendations
    def save_job_recommendation(self, recommendation, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO job_recommendations
                   (user_id, resume_id, external_job_id, match_score, category, reasons, matching_details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(user_id,resume_id,external_job_id) DO UPDATE SET
                    match_score=EXCLUDED.match_score,
                    category=EXCLUDED.category,
                    reasons=EXCLUDED.reasons,
                    matching_details=EXCLUDED.matching_details,
                    updated_at=now()
                   RETURNING *""",
                (
                    recommendation["user_id"], recommendation["resume_id"],
                    recommendation["external_job_id"], recommendation["match_score"],
                    recommendation["category"], _j(recommendation["reasons"]),
                    _j(recommendation["matching_details"]),
                ),
            ).fetchone()

    def dismiss_job_recommendation(self, user_id, recommendation_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """UPDATE job_recommendations SET dismissed=TRUE, updated_at=now()
                   WHERE id=%s AND user_id=%s RETURNING id""",
                (recommendation_id, user_id),
            ).fetchone()

    def dismissed_recommendation_job_ids(self, user_id, resume_id, conn=None):
        with _c(conn) as c:
            rows = c.execute(
                """SELECT external_job_id FROM job_recommendations
                   WHERE user_id=%s AND resume_id=%s AND dismissed""",
                (user_id, resume_id),
            ).fetchall()
            return {row["external_job_id"] for row in rows}

    # ------------------------------------------------------ applications
    def save_application(self, user_id, external_job_id=None, jd_id=None, conn=None):
        with _c(conn) as c:
            row = c.execute(
                """INSERT INTO applications(user_id, external_job_id, jd_id, status)
                   VALUES (%s,%s,%s,'SAVED')
                   ON CONFLICT DO NOTHING
                   RETURNING *""",
                (user_id, external_job_id, jd_id),
            ).fetchone()
            if row:
                self.add_application_event(
                    row["id"], user_id, "created", None, "SAVED", "", {}, c
                )
                return row
            return c.execute(
                """SELECT * FROM applications
                   WHERE user_id=%s AND
                     ((%s IS NOT NULL AND external_job_id=%s)
                      OR (%s IS NOT NULL AND jd_id=%s))
                   FOR UPDATE""",
                (user_id, external_job_id, external_job_id, jd_id, jd_id),
            ).fetchone()

    def get_owned_application(self, application_id, user_id, conn=None, for_update=False):
        with _c(conn) as c:
            return c.execute(
                "SELECT * FROM applications WHERE id=%s AND user_id=%s"
                + (" FOR UPDATE" if for_update else ""),
                (application_id, user_id),
            ).fetchone()

    def list_user_applications(self, user_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT a.*, COALESCE(e.company_name,c.name) AS company_name,
                          COALESCE(e.role_title,j.role_title) AS role_title,
                          COALESCE(e.source_url,j.source_url) AS source_url,
                          COALESCE(e.location,j.location) AS location
                   FROM applications a
                   LEFT JOIN external_jobs e ON e.id=a.external_job_id
                   LEFT JOIN job_descriptions j ON j.id=a.jd_id
                   LEFT JOIN companies c ON c.id=j.company_id
                   WHERE a.user_id=%s
                   ORDER BY a.next_action_at ASC NULLS LAST, a.updated_at DESC, a.id DESC""",
                (user_id,),
            ).fetchall()

    def update_application(self, application_id, user_id, updates, conn=None):
        allowed = {"status", "applied_at", "next_action_at", "notes", "related_resume_id", "interview_details"}
        if not updates or set(updates) - allowed:
            raise ValueError("invalid application update fields")
        assignments, params = [], []
        for key, value in updates.items():
            assignments.append(f"{key}=%s")
            params.append(_j(value) if key == "interview_details" else value)
        assignments.append("updated_at=now()")
        params.extend([application_id, user_id])
        with _c(conn) as c:
            return c.execute(
                f"""UPDATE applications SET {', '.join(assignments)}
                    WHERE id=%s AND user_id=%s RETURNING *""",
                params,
            ).fetchone()

    def add_application_event(
        self, application_id, user_id, event_type, from_status, to_status,
        notes, details, conn=None,
    ):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO application_events
                   (application_id,user_id,event_type,from_status,to_status,notes,details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    application_id, user_id, event_type, from_status, to_status,
                    notes or "", _j(details or {}),
                ),
            ).fetchone()

    def list_application_events(self, application_id, user_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT * FROM application_events
                   WHERE application_id=%s AND user_id=%s ORDER BY occurred_at,id""",
                (application_id, user_id),
            ).fetchall()

    def placement_dashboard(self, user_id, conn=None):
        with _c(conn) as c:
            stats = c.execute(
                """SELECT count(*) AS total,
                          count(*) FILTER (WHERE status='SAVED') AS saved,
                          count(*) FILTER (WHERE status='APPLIED') AS applied,
                          count(*) FILTER (WHERE status='ONLINE_ASSESSMENT') AS online_assessment,
                          count(*) FILTER (WHERE status='TECHNICAL_INTERVIEW') AS technical_interview,
                          count(*) FILTER (WHERE status='HR_INTERVIEW') AS hr_interview,
                          count(*) FILTER (WHERE status='REJECTED') AS rejected,
                          count(*) FILTER (WHERE status='OFFER_RECEIVED') AS offers
                   FROM applications WHERE user_id=%s""",
                (user_id,),
            ).fetchone()
            upcoming = c.execute(
                """SELECT a.id AS application_id, a.status, a.next_action_at,
                          COALESCE(e.company_name,c.name) AS company_name,
                          COALESCE(e.role_title,j.role_title) AS role_title
                   FROM applications a
                   LEFT JOIN external_jobs e ON e.id=a.external_job_id
                   LEFT JOIN job_descriptions j ON j.id=a.jd_id
                   LEFT JOIN companies c ON c.id=j.company_id
                   WHERE a.user_id=%s AND a.next_action_at >= now()
                   ORDER BY a.next_action_at LIMIT 10""",
                (user_id,),
            ).fetchall()
            return {"statistics": stats, "upcoming_actions": upcoming}

    # ----------------------------------------------------------- resumes
    def add_resume(self, user_id, kind, title, file_path, file_hash, size_bytes, raw_text, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO resumes(user_id, kind, title, file_path, file_hash, size_bytes, raw_text)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id, file_hash) DO UPDATE
                      SET title = EXCLUDED.title, raw_text = COALESCE(EXCLUDED.raw_text, resumes.raw_text)
                   RETURNING *, (xmax = 0) AS inserted""",
                (user_id, kind, title, file_path, file_hash, size_bytes, raw_text)).fetchone()

    def get_resume(self, resume_id, conn=None, for_update=False):
        """for_update=True takes a row lock (a per-resume mutex) until the transaction ends."""
        with _c(conn) as c:
            return c.execute("SELECT * FROM resumes WHERE id=%s" + (" FOR UPDATE" if for_update else ""),
                             (resume_id,)).fetchone()

    def get_owned_resume(self, user_id, resume_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                "SELECT * FROM resumes WHERE id=%s AND user_id=%s",
                (resume_id, user_id),
            ).fetchone()

    def latest_readable_resume(self, user_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT * FROM resumes WHERE user_id=%s
                   AND raw_text IS NOT NULL AND btrim(raw_text)<>''
                   ORDER BY created_at DESC,id DESC LIMIT 1""",
                (user_id,),
            ).fetchone()

    def list_user_resumes(self, user_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT id, title, kind, created_at
                   FROM resumes
                   WHERE user_id=%s AND raw_text IS NOT NULL AND btrim(raw_text) <> ''
                   ORDER BY created_at DESC, id DESC""",
                (user_id,),
            ).fetchall()

    def user_storage_bytes(self, user_id, conn=None):
        with _c(conn) as c:
            return int(c.execute("SELECT COALESCE(SUM(size_bytes),0) AS n FROM resumes WHERE user_id=%s",
                                 (user_id,)).fetchone()["n"])

    def set_resume_skills(self, resume_id, pairs, conn=None):
        """Replace the resume's skill set (idempotent re-analysis). pairs: [(skill_id, confidence)]"""
        with _c(conn) as c:
            c.execute("DELETE FROM resume_skills WHERE resume_id=%s", (resume_id,))
            with c.cursor() as cur:
                cur.executemany("INSERT INTO resume_skills(resume_id, skill_id, confidence) VALUES (%s,%s,%s)",
                                [(resume_id, s, conf) for s, conf in pairs])

    def stale_generated(self, days, conn=None):
        with _c(conn) as c:
            return c.execute("""SELECT id, user_id, file_hash, file_path FROM resumes WHERE kind='GENERATED'
                                AND created_at < now() - make_interval(days => %s)""", (days,)).fetchall()

    def delete_resume(self, resume_id, conn=None):
        with _c(conn) as c:
            c.execute("DELETE FROM resumes WHERE id=%s", (resume_id,))

    # ------------------------------------------------- analysis / roadmaps
    def ats_score(self, resume_id, jd_id, conn=None):
        with _c(conn) as c:
            return float(c.execute("SELECT fn_ats_score(%s,%s) AS s", (resume_id, jd_id)).fetchone()["s"])

    def skill_gap(self, resume_id, jd_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT skill_id, skill_name::text AS skill_name, importance, required, matched "
                             "FROM fn_skill_gap(%s,%s)", (resume_id, jd_id)).fetchall()

    def save_report(
        self, user_id, resume_id, jd_id, job_id, score, suggestions, conn=None,
        matching_details=None,
    ):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO analysis_reports
                   (user_id, resume_id, jd_id, job_id, ats_score, suggestions, matching_details)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    user_id, resume_id, jd_id, job_id, score, _j(suggestions),
                    _j(matching_details or {}),
                ),
            ).fetchone()

    def update_matching_details(self, report_id, matching_details, conn=None):
        with _c(conn) as c:
            c.execute(
                "UPDATE analysis_reports SET matching_details=%s WHERE id=%s",
                (_j(matching_details), report_id),
            )

    def get_report(self, report_id, conn=None, for_update=False):
        with _c(conn) as c:
            return c.execute(
                "SELECT * FROM analysis_reports WHERE id=%s" + (" FOR UPDATE" if for_update else ""),
                (report_id,),
            ).fetchone()

    def get_matching_report(self, user_id, resume_id, jd_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT id, ats_score, matching_details, created_at FROM analysis_reports
                   WHERE user_id=%s AND resume_id=%s AND jd_id=%s
                   ORDER BY created_at DESC, id DESC LIMIT 1""",
                (user_id, resume_id, jd_id),
            ).fetchone()

    def has_analysis_report(self, user_id, resume_id, jd_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM analysis_reports
                       WHERE user_id=%s AND resume_id=%s AND jd_id=%s
                   ) AS available""",
                (user_id, resume_id, jd_id),
            ).fetchone()["available"]

    def pick_resource(self, skill_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM resources WHERE skill_id=%s ORDER BY difficulty, id LIMIT 1",
                             (skill_id,)).fetchone()

    def create_roadmap(self, user_id, jd_id, report_id, title, items, conn=None):
        """Create a roadmap from legacy tuples or structured, evidence-based items."""
        with _c(conn) as c:
            rm = c.execute("INSERT INTO roadmaps(user_id, jd_id, report_id, title) VALUES (%s,%s,%s,%s) RETURNING *",
                           (user_id, jd_id, report_id, title)).fetchone()
            normalized = []
            for item in items:
                if isinstance(item, dict):
                    normalized.append((
                        item["skill_id"], item.get("resource_id"), item["est_hours"],
                        item.get("level", "INTERMEDIATE"),
                        item.get("practice_task", ""),
                        item.get("milestone", ""),
                    ))
                else:
                    skill_id, resource_id, hours = item
                    normalized.append((
                        skill_id, resource_id, hours, "INTERMEDIATE", "", ""
                    ))
            with c.cursor() as cur:
                cur.executemany(
                    """INSERT INTO roadmap_items
                       (roadmap_id,skill_id,resource_id,position,est_hours,level,practice_task,milestone)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [
                        (rm["id"], skill_id, resource_id, position, hours, level, task, milestone)
                        for position, (skill_id, resource_id, hours, level, task, milestone)
                        in enumerate(normalized, start=1)
                    ],
                )
            return rm

    def get_roadmap_for_report(self, user_id, report_id, conn=None):
        with _c(conn) as c:
            return c.execute(
                """SELECT m.id, m.title, COALESCE(SUM(i.est_hours), 0) AS total_hours
                   FROM roadmaps m
                   JOIN analysis_reports requested ON requested.id=%s
                   LEFT JOIN analysis_reports existing ON existing.id=m.report_id
                   LEFT JOIN roadmap_items i ON i.roadmap_id=m.id
                   WHERE m.user_id=%s
                     AND (m.report_id=requested.id OR (
                         existing.user_id=requested.user_id
                         AND existing.resume_id=requested.resume_id
                         AND existing.jd_id=requested.jd_id
                     ))
                   GROUP BY m.id
                     ORDER BY m.id DESC LIMIT 1""",
                (report_id, user_id),
            ).fetchone()

    def list_user_roadmaps(self, user_id, conn=None):
        with _c(conn) as c:
            rows = c.execute(
                """SELECT m.id AS roadmap_id, m.title, m.created_at, m.jd_id,
                          m.source_guide_id,
                          COALESCE(m.role_title, j.role_title) AS role_title,
                          COALESCE(m.company_name, c.name) AS company_name, m.report_id,
                          i.id AS item_id, i.position, i.est_hours, i.status, i.completed_at,
                          i.level, i.practice_task, i.milestone,
                          i.skill_id, COALESCE(NULLIF(i.title,''),s.name) AS item_title,
                          js.importance, js.required,
                          i.resource_id, COALESCE(i.resource_title,r.title) AS resource_title,
                          COALESCE(i.resource_url,r.url) AS resource_url,
                          r.kind AS resource_kind, matched.skills AS matched_skills,
                          COALESCE(m.daily_study_hours,preferences.daily_study_hours) AS daily_study_hours,
                          COALESCE(m.deadline_days,preferences.deadline_days) AS deadline_days,
                          COALESCE(m.deadline_date,preferences.deadline_date) AS deadline_date,
                          preferences.ai_guidance,
                          preferences.ai_notice,
                          COALESCE(preferences.external_research,
                                   guide.guidance->'external_research') AS external_research
                   FROM roadmaps m
                   LEFT JOIN job_descriptions j ON j.id = m.jd_id
                   LEFT JOIN companies c ON c.id = j.company_id
                   LEFT JOIN analysis_reports ar ON ar.id = m.report_id
                   LEFT JOIN roadmap_items i ON i.roadmap_id = m.id
                   LEFT JOIN skills s ON s.id = i.skill_id
                   LEFT JOIN jd_skills js ON js.jd_id = m.jd_id AND js.skill_id = i.skill_id
                   LEFT JOIN resources r ON r.id = i.resource_id
                   LEFT JOIN journey_guides guide ON guide.id=m.source_guide_id
                   LEFT JOIN LATERAL (
                       SELECT jsonb_agg(jsonb_build_object(
                           'skill', sm.name::text,
                           'importance', jsm.importance,
                           'required', jsm.required
                       ) ORDER BY jsm.importance DESC, sm.name) AS skills
                       FROM jd_skills jsm
                       JOIN skills sm ON sm.id = jsm.skill_id
                       JOIN resume_skills rsm ON rsm.skill_id = jsm.skill_id
                       WHERE jsm.jd_id = m.jd_id AND rsm.resume_id = ar.resume_id
                   ) matched ON true
                   LEFT JOIN LATERAL (
                       SELECT (jb.payload->>'daily_study_hours')::integer AS daily_study_hours,
                              (jb.payload->>'deadline_days')::integer AS deadline_days,
                              NULLIF(jb.payload->>'deadline_date', '')::date AS deadline_date,
                              jb.result->'ai_guidance' AS ai_guidance,
                              jb.result->>'ai_notice' AS ai_notice,
                              jb.result->'external_research' AS external_research
                       FROM jobs jb
                       WHERE jb.job_type='PREP_PLAN'
                         AND jb.status='DONE'
                         AND jb.result->>'roadmap_id'=m.id::text
                       ORDER BY (jb.result ? 'ai_guidance') DESC,
                                (jb.result ? 'ai_notice') DESC,
                                jb.finished_at DESC, jb.id DESC LIMIT 1
                   ) preferences ON true
                   WHERE m.user_id=%s
                   ORDER BY m.created_at DESC, m.id DESC, i.position""",
                (user_id,),
            ).fetchall()

        roadmaps = {}
        for row in rows:
            roadmap = roadmaps.get(row["roadmap_id"])
            if roadmap is None:
                roadmap = {
                    "id": row["roadmap_id"],
                    "title": row["title"],
                    "created_at": row["created_at"],
                    "jd_id": row["jd_id"],
                    "source_guide_id": row["source_guide_id"],
                    "role_title": row["role_title"],
                    "company_name": row["company_name"],
                    "report_id": row["report_id"],
                    "items": [],
                    "matched_skills": row["matched_skills"] or [],
                    "daily_study_hours": row["daily_study_hours"],
                    "deadline_days": row["deadline_days"],
                    "deadline_date": row["deadline_date"],
                    "ai_guidance": row["ai_guidance"],
                    "ai_notice": row["ai_notice"],
                    "external_research": row["external_research"],
                }
                roadmaps[row["roadmap_id"]] = roadmap

            if row["item_id"] is not None:
                resource = None
                if row["resource_id"] is not None or row["resource_url"]:
                    resource = {
                        "id": row["resource_id"],
                        "title": row["resource_title"],
                        "url": row["resource_url"],
                        "kind": row["resource_kind"],
                    }
                roadmap["items"].append({
                    "id": row["item_id"],
                    "position": row["position"],
                    "skill_id": row["skill_id"],
                    "title": row["item_title"],
                    "skill": row["item_title"],
                    "importance": row["importance"],
                    "required": row["required"],
                    "est_hours": row["est_hours"],
                    "level": row["level"],
                    "practice_task": row["practice_task"],
                    "milestone": row["milestone"],
                    "status": row["status"],
                    "completed_at": row["completed_at"],
                    "resource": resource,
                })

        for roadmap in roadmaps.values():
            items = roadmap["items"]
            roadmap["total_hours"] = float(sum(item["est_hours"] for item in items))
            roadmap["completed_hours"] = float(sum(
                item["est_hours"] for item in items if item["status"] == "DONE"
            ))
            roadmap["remaining_hours"] = float(sum(
                item["est_hours"] for item in items if item["status"] != "DONE"
            ))
            roadmap["completed_items"] = sum(item["status"] == "DONE" for item in items)
            roadmap["completion_pct"] = round(
                100 * roadmap["completed_items"] / len(items), 1
            ) if items else 0.0
        return list(roadmaps.values())

    def set_item_status(self, item_id, status, conn=None):
        with _c(conn) as c:
            return c.execute("UPDATE roadmap_items SET status=%s WHERE id=%s RETURNING *", (status, item_id)).fetchone()

    def set_user_roadmap_item_status(self, item_id, user_id, status, conn=None):
        with _c(conn) as c:
            return c.execute(
                """UPDATE roadmap_items i
                   SET status=%s
                   WHERE i.id=%s
                     AND EXISTS (
                         SELECT 1 FROM roadmaps m
                         WHERE m.id=i.roadmap_id AND m.user_id=%s
                     )
                   RETURNING i.id, i.roadmap_id, i.status, i.completed_at""",
                (status, item_id, user_id),
            ).fetchone()

    # ------------------------------------------------------- jobs (queue)
    def enqueue_job(self, user_id, job_type, payload, priority=5, max_attempts=3, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO jobs(user_id, job_type, payload, priority, max_attempts)
                   VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                (user_id, job_type, _j(payload), priority, max_attempts)).fetchone()

    def claim_job(self, job_id, worker, conn=None):
        """Atomically READY -> RUNNING. SKIP LOCKED: if another worker holds the row lock
        we do NOT wait; we simply lose the race and get None."""
        with _c(conn) as c:
            return c.execute(
                """UPDATE jobs SET status='RUNNING', locked_by=%s, started_at=now(), attempts=attempts+1
                   WHERE id = (SELECT id FROM jobs WHERE id=%s AND status='READY' FOR UPDATE SKIP LOCKED)
                   RETURNING *""", (worker, job_id)).fetchone()

    def finish_job(self, job_id, result, conn=None):
        with _c(conn) as c:
            return c.execute(
                """UPDATE jobs SET status='DONE', result=%s, error=NULL, locked_by=NULL, finished_at=now()
                   WHERE id=%s AND status='RUNNING' RETURNING *""", (_j(result), job_id)).fetchone()

    def fail_job(self, job_id, error, conn=None):
        """The DB decides retry-vs-fail atomically from attempts / max_attempts."""
        with _c(conn) as c:
            return c.execute(
                """UPDATE jobs SET
                       status = CASE WHEN attempts < max_attempts THEN 'READY'::job_status ELSE 'FAILED'::job_status END,
                       finished_at = CASE WHEN attempts < max_attempts THEN NULL ELSE now() END,
                       error=%s, locked_by=NULL
                   WHERE id=%s AND status='RUNNING' RETURNING *""", (error, job_id)).fetchone()

    def cancel_job(self, job_id, user_id=None, conn=None):
        with _c(conn) as c:
            return c.execute(
                """UPDATE jobs SET status='CANCELLED', finished_at=now()
                   WHERE id=%s AND status='READY' AND (%s::bigint IS NULL OR user_id=%s) RETURNING *""",
                (job_id, user_id, user_id)).fetchone()

    def get_job(self, job_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()

    def recover_jobs(self, conn=None):
        """Crash recovery: anything left RUNNING by a dead process goes back to READY."""
        with _c(conn) as c:
            return c.execute("""UPDATE jobs SET status='READY', locked_by=NULL, error='recovered after restart'
                                WHERE status='RUNNING' RETURNING id""").fetchall()

    def load_ready_jobs(self, limit=10_000, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT id, priority, job_type FROM jobs WHERE status='READY' ORDER BY id LIMIT %s",
                             (limit,)).fetchall()

    def job_counts(self, conn=None):
        with _c(conn) as c:
            return {r["status"]: r["n"] for r in c.execute("SELECT status::text, count(*) AS n FROM jobs GROUP BY 1")}

    def log_activity(self, user_id, entity, entity_id, action, details=None, conn=None):
        with _c(conn) as c:
            c.execute("INSERT INTO activity_logs(user_id, entity, entity_id, action, details) VALUES (%s,%s,%s,%s,%s)",
                      (user_id, entity, entity_id, action, _j(details or {})))
