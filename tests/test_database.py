import psycopg
import pytest

def mkuser(conn, email="a@x.in"):
    return conn.execute("INSERT INTO users(email, full_name) VALUES (%s,'A') RETURNING id", (email,)).fetchone()["id"]

def test_seed_loaded(conn):
    assert conn.execute("SELECT count(*) AS n FROM skills").fetchone()["n"] >= 30
    assert conn.execute("SELECT count(*) AS n FROM job_descriptions").fetchone()["n"] == 3

def test_fifteen_tables(conn):
    n = conn.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'").fetchone()["n"]
    assert n == 15

def test_email_unique_case_insensitive(conn):
    mkuser(conn, "Bob@x.in")
    with pytest.raises(psycopg.errors.UniqueViolation):
        mkuser(conn, "bob@X.IN")

def test_check_constraints(conn):
    u = mkuser(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO jobs(user_id, job_type, priority) VALUES (%s,'PREP_PLAN', 99)", (u,))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO jd_skills(jd_id, skill_id, importance) VALUES (1, 3, 9)")

def test_job_state_machine_enforced_by_db(conn):
    u = mkuser(conn)
    jid = conn.execute("INSERT INTO jobs(user_id, job_type) VALUES (%s,'PREP_PLAN') RETURNING id", (u,)).fetchone()["id"]
    with pytest.raises(psycopg.errors.CheckViolation):      # RUNNING without a worker
        conn.execute("UPDATE jobs SET status='RUNNING' WHERE id=%s", (jid,))
    with pytest.raises(psycopg.errors.CheckViolation):      # DONE without finished_at
        conn.execute("UPDATE jobs SET status='DONE' WHERE id=%s", (jid,))

def test_foreign_key_cascade(conn):
    u = mkuser(conn)
    conn.execute("INSERT INTO resumes(user_id, kind, title) VALUES (%s,'UPLOADED','r')", (u,))
    conn.execute("DELETE FROM users WHERE id=%s", (u,))
    assert conn.execute("SELECT count(*) AS n FROM resumes").fetchone()["n"] == 0

def test_trigger_audits_job_lifecycle(conn):
    u = mkuser(conn)
    jid = conn.execute("INSERT INTO jobs(user_id, job_type) VALUES (%s,'PREP_PLAN') RETURNING id", (u,)).fetchone()["id"]
    conn.execute("UPDATE jobs SET status='RUNNING', locked_by='w1' WHERE id=%s", (jid,))
    conn.execute("UPDATE jobs SET status='DONE', locked_by=NULL, finished_at=now() WHERE id=%s", (jid,))
    actions = [r["action"] for r in conn.execute("SELECT action FROM activity_logs WHERE entity_id=%s ORDER BY id", (jid,))]
    assert actions == ["job_created", "job_running", "job_done"]

def _resume_with(conn, u, names):
    rid = conn.execute("INSERT INTO resumes(user_id, kind, title) VALUES (%s,'UPLOADED','r') RETURNING id", (u,)).fetchone()["id"]
    conn.execute("INSERT INTO resume_skills(resume_id, skill_id) SELECT %s, id FROM skills WHERE name = ANY(%s)", (rid, names))
    return rid

def test_ats_score_function_is_importance_weighted(conn):
    u = mkuser(conn)
    # JD 1 weights: Python5 SQL5 REST4 PostgreSQL4 DS4 Git3 Docker2 Linux2 = 29
    rid = _resume_with(conn, u, ["Python", "SQL"])
    assert float(conn.execute("SELECT fn_ats_score(%s,1) AS s", (rid,)).fetchone()["s"]) == round(100 * 10 / 29, 2)
    full = _resume_with(conn, u, ["Python","SQL","REST APIs","PostgreSQL","Data Structures","Git","Docker","Linux"])
    assert float(conn.execute("SELECT fn_ats_score(%s,1) AS s", (full,)).fetchone()["s"]) == 100
    empty = _resume_with(conn, u, [])
    assert float(conn.execute("SELECT fn_ats_score(%s,1) AS s", (empty,)).fetchone()["s"]) == 0

def test_skill_gap_matched_vs_missing(conn):
    u = mkuser(conn)
    rid = _resume_with(conn, u, ["Python"])
    gap = {r["skill_name"]: r["matched"] for r in conn.execute("SELECT * FROM fn_skill_gap(%s,1)", (rid,))}
    assert gap["Python"] is True and gap["SQL"] is False and len(gap) == 8

def test_transaction_rollback_is_atomic(core, user):
    from placex import db
    r = core.repo.add_resume(user["id"], "UPLOADED", "x", None, "a" * 64, 1, "t")
    with pytest.raises(RuntimeError):
        with db.transaction() as c:
            core.repo.set_resume_skills(r["id"], [(1, 1.0)], c)
            raise RuntimeError("boom")
    n = core.repo.get_resume(r["id"])
    from placex import db as d
    with d.transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM resume_skills").fetchone()["n"] == 0

def test_roadmap_item_completion_trigger_and_dashboard_view(core, user):
    r = core.repo.add_resume(user["id"], "UPLOADED", "x", None, "b" * 64, 1, "t")
    rm = core.repo.create_roadmap(user["id"], 1, None, "plan", [(1, None, 5), (7, None, 5)])
    from placex import db
    with db.transaction() as c:
        item = c.execute("SELECT id FROM roadmap_items WHERE roadmap_id=%s ORDER BY position", (rm["id"],)).fetchone()
    done = core.repo.set_item_status(item["id"], "DONE")
    assert done["completed_at"] is not None
    d = core.repo.dashboard(user["id"])
    assert d["roadmap_items_total"] == 2 and float(d["roadmap_progress_pct"]) == 50.0 and d["resumes"] == 1
