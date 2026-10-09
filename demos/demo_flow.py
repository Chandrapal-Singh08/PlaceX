"""End-to-end walk-through of all four modules.  Run:  python demos/demo_flow.py"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from placex.config import Config
from placex.core import PlaceXCore
from placex.initdb import apply_schema

cfg = Config(upload_dir=tempfile.mkdtemp())
apply_schema(cfg.dsn, reset=True)
k = PlaceXCore(cfg).start()
me = k.repo.create_user("demo@geu.ac.in", "Demo Student", "240000000")
cv = b"Demo Student. Skills: Python, SQL, Git. Projects: Flask web app with PostgreSQL."
r = k.upload_resume(me["id"], "cv.txt", cv)
pp = lambda t, o: print(f"\n=== {t} ===\n" + json.dumps(o, indent=2, default=str))

job = k.wait(k.submit(me["id"], "RESUME_ANALYSIS", {"resume_id": r["id"], "jd_id": 1}, priority=8)["id"])
pp("1. Resume Analyzer", job["result"])
pp("2. Resume Builder", k.wait(k.submit(me["id"], "RESUME_BUILD", {"jd_id": 1, "profile": {
    "name": "Demo Student", "email": "demo@geu.ac.in", "skills": ["Python", "Git"],
    "education": ["B.Tech CSE, Graphic Era University"],
    "projects": [{"name": "PlaceX", "description": "Placement guide using Flask and PostgreSQL"}]}})["id"])["result"])
pp("3. Journey Guide", k.wait(k.submit(me["id"], "JOURNEY_GUIDE", {"jd_id": 1})["id"])["result"])
pp("4. Prep Planner", k.wait(k.submit(me["id"], "PREP_PLAN", {"report_id": job["result"]["report_id"]})["id"])["result"])
pp("Dashboard (SQL view)", k.repo.dashboard(me["id"]))
pp("System status", k.system_status())
k.stop()
