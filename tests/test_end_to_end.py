import threading, time
import pytest
from placex.core import Backpressure, PlaceXCore
from placex.os_layer.filestore import UploadRejected

RESUME = b"Chandrapal. Skills: Python, SQL, Postgres, Git and github. Built a REST API with Flask."

def analyze(core, user, data=RESUME, name="cv.txt", jd=1):
    r = core.upload_resume(user["id"], name, data)
    j = core.submit(user["id"], "RESUME_ANALYSIS", {"resume_id": r["id"], "jd_id": jd}, priority=8)
    return r, core.wait(j["id"])

def test_resume_analysis_end_to_end(core, user):
    r, job = analyze(core, user)
    assert job["status"] == "DONE", job["error"]
    res = job["result"]
    assert set(res["matched"]) == {"Python", "SQL", "PostgreSQL", "Git", "REST APIs"}
    assert set(res["missing"]) == {"Data Structures", "Docker", "Linux"}
    assert res["ats_score"] == round(100 * 21 / 29, 2)
    assert float(core.repo.dashboard(user["id"])["best_ats"]) == pytest.approx(72.41, abs=0.01)

def test_full_pipeline_all_four_modules(core, user):
    r, job = analyze(core, user)
    plan = core.wait(core.submit(user["id"], "PREP_PLAN", {"report_id": job["result"]["report_id"]})["id"])
    assert plan["status"] == "DONE", plan["error"]
    assert [i["skill"] for i in plan["result"]["items"]][0] == "Data Structures"   # required + highest importance first
    guide = core.wait(core.submit(user["id"], "JOURNEY_GUIDE", {"jd_id": 1})["id"])
    assert guide["result"]["total_rounds"] == 3
    build = core.wait(core.submit(user["id"], "RESUME_BUILD", {"jd_id": 1, "profile": {
        "name": "Test", "skills": ["Python", "Docker"], "projects": [{"name": "PlaceX", "description": "Flask + PostgreSQL app"}]}})["id"])
    assert build["status"] == "DONE", build["error"]
    gen = core.repo.get_resume(build["result"]["resume_id"])
    assert gen["kind"] == "GENERATED" and "Python" in gen["raw_text"]
    assert "## Professional Summary" in gen["raw_text"]
    assert "## Technical Skills" in gen["raw_text"]
    assert "## Projects" in gen["raw_text"]
    assert (core.store.root / "users" / str(user["id"]) / "generated" /
            f"{gen['file_hash']}.md").is_file()
    assert (core.store.root / "users" / str(user["id"]) / "generated" /
            f"{gen['file_hash']}.pdf").read_bytes().startswith(b"%PDF-")

def test_resume_build_from_uploaded_jd_creates_both_formats(core, user):
    from pathlib import Path
    jd_path = Path(__file__).parent / "fixtures" / "backend-engineer-jd.txt"
    jd = core.resolve_jd(filename=jd_path.name, data=jd_path.read_bytes())
    job = core.wait(core.submit(user["id"], "RESUME_BUILD", {
        "jd_id": jd["id"],
        "profile": {
            "name": "Riley Student",
            "email": "riley@example.test",
            "skills": ["Python", "FastAPI", "PostgreSQL"],
            "projects": [{
                "name": "Job Matcher",
                "description": "Built a job matcher using Python and PostgreSQL. Added REST APIs.",
            }],
            "achievements": ["Won a campus hackathon"],
        },
    })["id"])
    assert job["status"] == "DONE", job["error"]
    resume = core.repo.get_resume(job["result"]["resume_id"])
    assert "Backend Engineer" in resume["raw_text"]
    assert "Python" in resume["raw_text"] and "FastAPI" in resume["raw_text"]
    assert "Docker" not in resume["raw_text"]
    assert "Won a campus hackathon" in resume["raw_text"]
    generated = core.store.root / "users" / str(user["id"]) / "generated"
    assert (generated / f"{resume['file_hash']}.md").is_file()
    assert (generated / f"{resume['file_hash']}.pdf").read_bytes().startswith(b"%PDF-")

def test_duplicate_upload_is_deduplicated(core, user):
    a = core.upload_resume(user["id"], "a.txt", RESUME)
    b = core.upload_resume(user["id"], "renamed.txt", RESUME)
    assert a["id"] == b["id"] and core.repo.user_storage_bytes(user["id"]) == len(RESUME)

def test_wrong_owner_cannot_analyze_someones_resume(core, user):
    other = core.repo.create_user("other@geu.ac.in", "Other")
    r = core.upload_resume(user["id"], "cv.txt", RESUME)
    j = core.wait(core.submit(other["id"], "RESUME_ANALYSIS", {"resume_id": r["id"], "jd_id": 1})["id"], 20)
    assert j["status"] == "FAILED" and "belong" in j["error"] and j["attempts"] == 3

def test_failing_job_retries_then_fails(core, user):
    calls = []
    def flaky(job, ctx):
        calls.append(1); raise ValueError("model exploded")
    core.pool.handlers["JOURNEY_GUIDE"] = flaky
    j = core.wait(core.submit(user["id"], "JOURNEY_GUIDE", {"jd_id": 1})["id"], 20)
    assert j["status"] == "FAILED" and len(calls) == 3 and "model exploded" in j["error"]
    assert core.stats.snapshot()["retried"] == 2

def test_retry_can_succeed(core, user):
    state = {"n": 0}
    real = core.pool.handlers["JOURNEY_GUIDE"]
    def flaky(job, ctx):
        state["n"] += 1
        if state["n"] < 2: raise ConnectionError("transient")
        return real(job, ctx)
    core.pool.handlers["JOURNEY_GUIDE"] = flaky
    j = core.wait(core.submit(user["id"], "JOURNEY_GUIDE", {"jd_id": 2})["id"], 20)
    assert j["status"] == "DONE" and j["attempts"] == 2

def test_job_timeout_is_enforced_cooperatively(core, user):
    core.cfg.job_timeout_s = 0.05
    def slow(job, ctx):
        time.sleep(0.15); ctx.check(); return {}
    core.pool.handlers["JOURNEY_GUIDE"] = slow
    j = core.wait(core.submit(user["id"], "JOURNEY_GUIDE", {"jd_id": 1})["id"], 20)
    assert j["status"] == "FAILED" and "time limit" in j["error"]

def test_atomic_claim_exactly_one_winner(core, user):
    core.pool.stop()                       # no workers: we race manually
    j = core.repo.enqueue_job(user["id"], "PREP_PLAN", {})
    wins, barrier = [], threading.Barrier(20)
    def racer(i):
        barrier.wait()
        if core.repo.claim_job(j["id"], f"w{i}"): wins.append(i)
    ts = [threading.Thread(target=racer, args=(i,)) for i in range(20)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(wins) == 1

def test_cancel_ready_job(core, user):
    core.pool.stop()
    j = core.repo.enqueue_job(user["id"], "PREP_PLAN", {})
    assert core.repo.cancel_job(j["id"])["status"] == "CANCELLED"
    assert core.repo.claim_job(j["id"], "w") is None                # cancelled jobs can't be claimed
    assert core.repo.cancel_job(j["id"]) is None                    # already terminal

def test_crash_recovery(cfg, user_factory=None):
    k = PlaceXCore(cfg, probe=lambda: (1, 1, 1))
    u = k.repo.create_user("c@x.in", "Crash")
    ready = k.repo.enqueue_job(u["id"], "JOURNEY_GUIDE", {"jd_id": 1})
    dead = k.repo.enqueue_job(u["id"], "JOURNEY_GUIDE", {"jd_id": 3})
    assert k.repo.claim_job(dead["id"], "worker-that-crashed")       # simulate process dying mid-job
    k.start()                                                        # "restart"
    try:
        assert k.recovered == 1
        assert k.wait(ready["id"])["status"] == "DONE"
        assert k.wait(dead["id"])["status"] == "DONE"
    finally:
        k.stop()

def test_backpressure_when_queue_full(cfg):
    cfg.queue_capacity, cfg.workers = 3, 1
    k = PlaceXCore(cfg, probe=lambda: (1, 1, 1))                     # NOT started: nothing drains the queue
    u = k.repo.create_user("bp@x.in", "BP")
    try:
        for _ in range(3): k.submit(u["id"], "PREP_PLAN", {})
        with pytest.raises(Backpressure): k.submit(u["id"], "PREP_PLAN", {})
        assert k.repo.job_counts().get("CANCELLED", 0) == 0          # rejected BEFORE insert: no orphan row
        assert k.repo.job_counts()["READY"] == 3
    finally:
        k.stop()

def test_admission_control_pauses_workers(cfg, user_factory=None):
    state = {"mem": 95.0}
    k = PlaceXCore(cfg, probe=lambda: (5.0, state["mem"], 10.0)).start()
    try:
        u = k.repo.create_user("m@x.in", "Mem")
        time.sleep(0.3)
        assert k.queue.paused
        j = k.submit(u["id"], "JOURNEY_GUIDE", {"jd_id": 1})
        time.sleep(0.4)
        assert k.repo.get_job(j["id"])["status"] == "READY"          # held back under memory pressure
        state["mem"] = 40.0
        assert k.wait(j["id"], 10)["status"] == "DONE"               # released after recovery
        acts = {r["action"] for r in __import__("placex.db", fromlist=["x"]).transaction().__enter__().execute(
            "SELECT action FROM activity_logs WHERE entity='system'")}
        assert {"admission_paused", "admission_resumed"} <= acts
    finally:
        k.stop()

def test_upload_validation_surface(core, user):
    with pytest.raises(UploadRejected): core.upload_resume(user["id"], "x.exe", b"MZ...")

def test_concurrent_burst_all_complete_correctly(core, user):
    r = core.upload_resume(user["id"], "cv.txt", RESUME)
    ids = [core.submit(user["id"], "RESUME_ANALYSIS", {"resume_id": r["id"], "jd_id": 1 + i % 3})["id"] for i in range(40)]
    jobs = [core.wait(i, 30) for i in ids]
    assert all(j["status"] == "DONE" for j in jobs)
    assert core.stats.snapshot()["completed"] == 40
    assert core.stats.snapshot()["max_concurrent"] >= 2
    with __import__("placex.db", fromlist=["x"]).transaction() as c:
        assert c.execute("SELECT count(*) AS n FROM analysis_reports").fetchone()["n"] == 40
