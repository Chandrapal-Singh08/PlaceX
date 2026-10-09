"""One handler per job type. Signature: handler(job: dict, ctx: JobContext) -> JSON-able dict.
Handlers never touch threads or queues: that is the OS layer's job."""
from .. import db
from ..resume_pdf import generate_resume_pdf

class JobError(Exception):
    """Permanent, non-retryable-in-spirit validation error (still goes through retry policy)."""

def make_handlers(repo, ai, store, cache):

    def catalog():
        return cache.get_or_compute("skill_catalog", repo.list_skills)

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
        return {
            "mode": "GENERAL",
            "report_id": None,
            "ats_score": score,
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
            report = repo.save_report(job["user_id"], resume["id"], jd["id"], job["id"], score, tips, conn)
        return {"report_id": report["id"], "ats_score": score,
                "matched": [g["skill_name"] for g in gap if g["matched"]],
                "missing": [g["skill_name"] for g in gap if not g["matched"]],
            "suggestions": tips,
            "gap": [{"skill": g["skill_name"], "importance": g["importance"],
                 "required": g["required"], "matched": g["matched"]} for g in gap]}

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
        enriched_profile = {**profile, "_supported_skills": supported}
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
                "missing_skills": missing}

    # ------------------------------------------------- 3. journey guide
    def journey_guide(job, ctx):
        p = job["payload"]
        company_id = p.get("company_id")
        if company_id is None and p.get("jd_id"):
            company_id = repo.get_jd(p["jd_id"])["company_id"]
        rounds = repo.get_rounds(company_id)
        if not rounds:
            raise JobError("no hiring-round data for this company yet")
        return {"company_id": company_id, "total_rounds": len(rounds),
                "rounds": [{"no": r["round_no"], "name": r["name"], "type": r["round_type"],
                            "what_to_expect": r["description"], "minutes": r["duration_min"],
                            "tips": r["tips"]} for r in rounds]}

    # ------------------------------------------------ 4. prep planner
    def prep_plan(job, ctx):
        p = job["payload"]
        with db.transaction() as conn:
            report = repo.get_report(p["report_id"], conn)
            if not report or report["user_id"] != job["user_id"]:
                raise JobError("report not found for this user")
            gap = repo.skill_gap(report["resume_id"], report["jd_id"], conn)
            hours = {s["id"]: s["est_hours"] for s in catalog()}
            missing = sorted((g for g in gap if not g["matched"]),
                             key=lambda g: (not g["required"], -g["importance"], g["skill_name"]))
            if not missing:
                return {"roadmap_id": None, "items": [], "note": "No skill gaps: you cover every listed skill."}
            items, picked = [], []
            for g in missing:
                r = repo.pick_resource(g["skill_id"], conn)
                picked.append(r)
                items.append((g["skill_id"], r["id"] if r else None, hours.get(g["skill_id"], 10)))
            jd = repo.get_jd(report["jd_id"], conn)
            rm = repo.create_roadmap(job["user_id"], jd["id"], report["id"],
                             f"Prep plan: {jd['role_title']}" +
                             (f" @ {jd['company_name']}" if jd["company_name"] else ""), items, conn)
        return {"roadmap_id": rm["id"], "total_hours": sum(h for _, _, h in items),
                "items": [{"position": i + 1, "skill": g["skill_name"], "required": g["required"],
                       "est_hours": items[i][2],
                       "resource": ({"title": picked[i]["title"], "url": picked[i]["url"],
                             "kind": picked[i]["kind"]} if picked[i] else None)}
                      for i, g in enumerate(missing)]}

    return {"RESUME_ANALYSIS": resume_analysis, "RESUME_BUILD": resume_build,
            "JOURNEY_GUIDE": journey_guide, "PREP_PLAN": prep_plan}
