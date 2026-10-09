"""Data-access layer. All SQL lives here. Methods accept an optional `conn` so several
calls can share ONE transaction (e.g. report + skills are saved atomically)."""
import json
from contextlib import contextmanager
from psycopg.types.json import Jsonb
from . import db
CUSTOM_COMPANY = "Custom job description"

def _j(obj):
    return Jsonb(obj, dumps=lambda o: json.dumps(o, default=str))

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

    def list_job_descriptions(self, conn=None):
        with _c(conn) as c:
            return c.execute(
                     """SELECT j.id, j.role_title, j.company_id,
                                  CASE WHEN c.name = %s THEN '' ELSE c.name END AS company_name, j.location
                   FROM job_descriptions j JOIN companies c ON c.id = j.company_id
                         ORDER BY c.name, j.role_title, j.id""",
                     (CUSTOM_COMPANY,),
            ).fetchall()

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

    def create_jd(self, company_id, role_title, description, skills, conn=None):
        """skills: iterable of (skill_id, importance, required)"""
        with _c(conn) as c:
            jd = c.execute(
                """INSERT INTO job_descriptions(company_id, role_title, description)
                   VALUES (%s,%s,%s) RETURNING *""", (company_id, role_title, description)).fetchone()
            with c.cursor() as cur:
                cur.executemany("INSERT INTO jd_skills(jd_id, skill_id, importance, required) VALUES (%s,%s,%s,%s)",
                                [(jd["id"], s, i, r) for s, i, r in skills])
            return jd

    def get_rounds(self, company_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM hiring_rounds WHERE company_id=%s ORDER BY round_no",
                             (company_id,)).fetchall()

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

    def save_report(self, user_id, resume_id, jd_id, job_id, score, suggestions, conn=None):
        with _c(conn) as c:
            return c.execute(
                """INSERT INTO analysis_reports(user_id, resume_id, jd_id, job_id, ats_score, suggestions)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""",
                (user_id, resume_id, jd_id, job_id, score, _j(suggestions))).fetchone()

    def get_report(self, report_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM analysis_reports WHERE id=%s", (report_id,)).fetchone()

    def pick_resource(self, skill_id, conn=None):
        with _c(conn) as c:
            return c.execute("SELECT * FROM resources WHERE skill_id=%s ORDER BY difficulty, id LIMIT 1",
                             (skill_id,)).fetchone()

    def create_roadmap(self, user_id, jd_id, report_id, title, items, conn=None):
        """items: [(skill_id, resource_id|None, est_hours)] in priority order."""
        with _c(conn) as c:
            rm = c.execute("INSERT INTO roadmaps(user_id, jd_id, report_id, title) VALUES (%s,%s,%s,%s) RETURNING *",
                           (user_id, jd_id, report_id, title)).fetchone()
            with c.cursor() as cur:
                cur.executemany(
                    "INSERT INTO roadmap_items(roadmap_id, skill_id, resource_id, position, est_hours) VALUES (%s,%s,%s,%s,%s)",
                    [(rm["id"], s, r, pos, h) for pos, (s, r, h) in enumerate(items, start=1)])
            return rm

    def set_item_status(self, item_id, status, conn=None):
        with _c(conn) as c:
            return c.execute("UPDATE roadmap_items SET status=%s WHERE id=%s RETURNING *", (status, item_id)).fetchone()

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
