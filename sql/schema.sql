-- =====================================================================
-- PlaceX  |  Database layer  |  PostgreSQL 14+
-- Normalized to 3NF. Run through `python -m placex.initdb`.
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE user_role   AS ENUM ('student', 'admin');
CREATE TYPE resume_kind AS ENUM ('UPLOADED', 'GENERATED');
CREATE TYPE job_status  AS ENUM ('READY', 'RUNNING', 'DONE', 'FAILED', 'CANCELLED');
CREATE TYPE job_type    AS ENUM ('RESUME_ANALYSIS', 'RESUME_BUILD', 'JOURNEY_GUIDE', 'PREP_PLAN');
CREATE TYPE item_status AS ENUM ('TODO', 'IN_PROGRESS', 'DONE');

-- ---------------------------------------------------------------- identity
CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    email       CITEXT      NOT NULL UNIQUE,
    full_name   TEXT        NOT NULL CHECK (length(trim(full_name)) > 0),
    roll_no     TEXT        UNIQUE,
    password_hash TEXT,
    role        user_role   NOT NULL DEFAULT 'student',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_sessions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  CHAR(64) NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    revoked_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_user_sessions_user ON user_sessions (user_id, expires_at DESC);

-- ----------------------------------------------------------------- targets
CREATE TABLE companies (
    id        BIGSERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    industry  TEXT,
    website   TEXT
);

CREATE TABLE job_descriptions (
    id           BIGSERIAL PRIMARY KEY,
    company_id   BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    role_title   TEXT   NOT NULL,
    description  TEXT   NOT NULL,
    location     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, role_title)
);

-- ------------------------------------------------------------------ skills
CREATE TABLE skills (
    id         BIGSERIAL PRIMARY KEY,
    name       CITEXT   NOT NULL UNIQUE,
    category   TEXT     NOT NULL CHECK (category IN
               ('language','framework','database','cs_fundamental','tool','soft','ai_ml')),
    aliases    TEXT[]   NOT NULL DEFAULT '{}',
    est_hours  SMALLINT NOT NULL DEFAULT 10 CHECK (est_hours > 0)
);

-- ----------------------------------------------------------------- resumes
CREATE TABLE resumes (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        resume_kind NOT NULL,
    title       TEXT        NOT NULL,
    file_path   TEXT,
    file_hash   CHAR(64),
    size_bytes  BIGINT      NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
    raw_text    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, file_hash)            -- same file twice => one row (dedupe)
);

-- junction tables: make "matched vs missing" a set operation in SQL
CREATE TABLE resume_skills (
    resume_id   BIGINT NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    skill_id    BIGINT NOT NULL REFERENCES skills(id)  ON DELETE CASCADE,
    confidence  NUMERIC(4,3) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    PRIMARY KEY (resume_id, skill_id)
);

CREATE TABLE jd_skills (
    jd_id       BIGINT   NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
    skill_id    BIGINT   NOT NULL REFERENCES skills(id)           ON DELETE CASCADE,
    importance  SMALLINT NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 5),
    required    BOOLEAN  NOT NULL DEFAULT TRUE,
    PRIMARY KEY (jd_id, skill_id)
);

-- -------------------------------------------------- OS layer job queue table
CREATE TABLE jobs (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT REFERENCES users(id) ON DELETE SET NULL,
    job_type      job_type   NOT NULL,
    priority      SMALLINT   NOT NULL DEFAULT 5 CHECK (priority BETWEEN 0 AND 10),
    status        job_status NOT NULL DEFAULT 'READY',
    payload       JSONB      NOT NULL DEFAULT '{}',
    result        JSONB,
    error         TEXT,
    attempts      SMALLINT   NOT NULL DEFAULT 0,
    max_attempts  SMALLINT   NOT NULL DEFAULT 3 CHECK (max_attempts >= 1),
    locked_by     TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- state-machine invariants enforced by the DB itself:
    CHECK ((status = 'RUNNING') = (locked_by IS NOT NULL)),
    CHECK (status NOT IN ('DONE','FAILED','CANCELLED') OR finished_at IS NOT NULL)
);

-- ----------------------------------------------------------------- results
CREATE TABLE analysis_reports (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id)             ON DELETE CASCADE,
    resume_id    BIGINT NOT NULL REFERENCES resumes(id)           ON DELETE CASCADE,
    jd_id        BIGINT NOT NULL REFERENCES job_descriptions(id)  ON DELETE CASCADE,
    job_id       BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    ats_score    NUMERIC(5,2) NOT NULL CHECK (ats_score BETWEEN 0 AND 100),
    suggestions  JSONB NOT NULL DEFAULT '[]',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------- guidance
CREATE TABLE hiring_rounds (
    id            BIGSERIAL PRIMARY KEY,
    company_id    BIGINT   NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    round_no      SMALLINT NOT NULL CHECK (round_no > 0),
    name          TEXT     NOT NULL,
    round_type    TEXT     NOT NULL CHECK (round_type IN
                  ('online_test','coding','technical','system_design','hr','group_discussion')),
    description   TEXT,
    duration_min  SMALLINT CHECK (duration_min > 0),
    tips          TEXT,
    UNIQUE (company_id, round_no)
);

CREATE TABLE resources (
    id          BIGSERIAL PRIMARY KEY,
    skill_id    BIGINT   NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    title       TEXT     NOT NULL,
    url         TEXT     NOT NULL,
    kind        TEXT     NOT NULL CHECK (kind IN ('video','article','practice','docs','book')),
    difficulty  SMALLINT NOT NULL DEFAULT 1 CHECK (difficulty BETWEEN 1 AND 3),
    UNIQUE (skill_id, url)
);

CREATE TABLE roadmaps (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id)            ON DELETE CASCADE,
    jd_id       BIGINT NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
    report_id   BIGINT REFERENCES analysis_reports(id)          ON DELETE SET NULL,
    title       TEXT   NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE roadmap_items (
    id            BIGSERIAL PRIMARY KEY,
    roadmap_id    BIGINT   NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
    skill_id      BIGINT   NOT NULL REFERENCES skills(id),
    resource_id   BIGINT   REFERENCES resources(id) ON DELETE SET NULL,
    position      SMALLINT NOT NULL CHECK (position > 0),
    est_hours     NUMERIC(5,1) NOT NULL CHECK (est_hours > 0),
    status        item_status NOT NULL DEFAULT 'TODO',
    completed_at  TIMESTAMPTZ,
    UNIQUE (roadmap_id, position),
    UNIQUE (roadmap_id, skill_id)
);

CREATE TABLE activity_logs (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    entity      TEXT   NOT NULL,
    entity_id   BIGINT,
    action      TEXT   NOT NULL,
    details     JSONB  NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------- indexes
CREATE INDEX idx_jobs_ready     ON jobs (priority DESC, id) WHERE status = 'READY';
CREATE INDEX idx_jobs_user      ON jobs (user_id, created_at DESC);
CREATE INDEX idx_jobs_running   ON jobs (locked_by)         WHERE status = 'RUNNING';
CREATE INDEX idx_resumes_user   ON resumes (user_id);
CREATE INDEX idx_rs_skill       ON resume_skills (skill_id);
CREATE INDEX idx_jds_skill      ON jd_skills (skill_id);
CREATE INDEX idx_jd_company     ON job_descriptions (company_id);
CREATE INDEX idx_reports_user   ON analysis_reports (user_id, created_at DESC);
CREATE INDEX idx_logs_user      ON activity_logs (user_id, created_at DESC);
CREATE INDEX idx_roadmaps_user  ON roadmaps (user_id);

-- ---------------------------------------------------------------- triggers
CREATE FUNCTION fn_touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_jobs_touch BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

-- every job state change is audited automatically
CREATE FUNCTION fn_job_audit() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO activity_logs(user_id, entity, entity_id, action, details)
        VALUES (NEW.user_id, 'job', NEW.id, 'job_created',
                jsonb_build_object('job_type', NEW.job_type, 'priority', NEW.priority));
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO activity_logs(user_id, entity, entity_id, action, details)
        VALUES (NEW.user_id, 'job', NEW.id, 'job_' || lower(NEW.status::text),
                jsonb_build_object('from', OLD.status, 'to', NEW.status,
                                   'worker', COALESCE(NEW.locked_by, OLD.locked_by),
                                   'attempt', NEW.attempts));
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_jobs_audit AFTER INSERT OR UPDATE OF status ON jobs
    FOR EACH ROW EXECUTE FUNCTION fn_job_audit();

-- roadmap item completion timestamp maintained by the DB
CREATE FUNCTION fn_item_completed() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'DONE' AND OLD.status <> 'DONE' THEN NEW.completed_at := now();
    ELSIF NEW.status <> 'DONE' THEN NEW.completed_at := NULL;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_item_completed BEFORE UPDATE OF status ON roadmap_items
    FOR EACH ROW EXECUTE FUNCTION fn_item_completed();

-- --------------------------------------------------------------- functions
-- Matched vs missing skills for a (resume, JD) pair: one LEFT JOIN.
CREATE FUNCTION fn_skill_gap(p_resume BIGINT, p_jd BIGINT)
RETURNS TABLE (skill_id BIGINT, skill_name CITEXT, importance SMALLINT,
               required BOOLEAN, matched BOOLEAN) AS $$
    SELECT s.id, s.name, j.importance, j.required, (rs.skill_id IS NOT NULL)
    FROM jd_skills j
    JOIN skills s ON s.id = j.skill_id
    LEFT JOIN resume_skills rs ON rs.skill_id = j.skill_id AND rs.resume_id = p_resume
    WHERE j.jd_id = p_jd
    ORDER BY j.importance DESC, s.name;
$$ LANGUAGE sql STABLE;

-- ATS score = importance-weighted share of JD skills found in the resume.
CREATE FUNCTION fn_ats_score(p_resume BIGINT, p_jd BIGINT) RETURNS NUMERIC AS $$
    SELECT COALESCE(ROUND(100.0 * SUM(importance) FILTER (WHERE matched)
                          / NULLIF(SUM(importance), 0), 2), 0)
    FROM fn_skill_gap(p_resume, p_jd);
$$ LANGUAGE sql STABLE;

-- ------------------------------------------------------------------- view
CREATE VIEW v_student_dashboard AS
SELECT d.*,
       CASE WHEN d.roadmap_items_total = 0 THEN 0
            ELSE ROUND(100.0 * d.roadmap_items_done / d.roadmap_items_total, 1) END AS roadmap_progress_pct
FROM (
    SELECT u.id AS user_id, u.full_name,
      (SELECT count(*) FROM resumes r WHERE r.user_id = u.id)            AS resumes,
      (SELECT count(*) FROM analysis_reports a WHERE a.user_id = u.id)   AS reports,
      (SELECT max(ats_score) FROM analysis_reports a WHERE a.user_id = u.id) AS best_ats,
      (SELECT ats_score FROM analysis_reports a WHERE a.user_id = u.id
        ORDER BY created_at DESC, id DESC LIMIT 1)                       AS latest_ats,
      (SELECT count(*) FROM roadmap_items i JOIN roadmaps m ON m.id = i.roadmap_id
        WHERE m.user_id = u.id)                                          AS roadmap_items_total,
      (SELECT count(*) FROM roadmap_items i JOIN roadmaps m ON m.id = i.roadmap_id
        WHERE m.user_id = u.id AND i.status = 'DONE')                    AS roadmap_items_done,
      (SELECT count(*) FROM jobs j WHERE j.user_id = u.id
        AND j.status IN ('READY','RUNNING'))                             AS pending_jobs
    FROM users u WHERE u.role = 'student'
) d;
