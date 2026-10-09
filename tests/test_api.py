import time
import pytest
from fastapi.testclient import TestClient
from placex import api, db
from placex.core import PlaceXCore

@pytest.fixture
def client(cfg):
    from tests.conftest import TEST_DSN
    api.core = PlaceXCore(cfg, probe=lambda: (1, 1, 1)).start()
    yield TestClient(api.app)
    with db.transaction() as conn:
        conn.execute("DELETE FROM job_descriptions WHERE id > 3")
        conn.execute(
            """DELETE FROM companies c WHERE c.name = 'Custom job description'
               AND NOT EXISTS (SELECT 1 FROM job_descriptions j WHERE j.company_id = c.id)"""
        )
    api.core.stop()


def signup(client, email, name):
    response = client.post("/users", json={
        "email": email, "full_name": name, "password": "correct horse battery staple"
    })
    assert response.status_code == 201, response.text
    session = response.json()
    session["id"] = session["user_id"]
    client.headers.update({"Authorization": "Bearer " + session["access_token"]})
    return session


def test_password_auth_and_jd_endpoints(client):
    assert client.get("/auth/me").status_code == 401
    assert "/users/by-email" not in api.app.openapi()["paths"]
    assert client.post("/auth/login", json={
        "email": "missing@example.test", "password": "correct horse battery staple"
    }).status_code == 401

    session = signup(client, "known@example.test", "Known")
    assert client.get("/auth/me").json() == {
        "id": session["user_id"], "email": "known@example.test", "full_name": "Known", "role": "student"
    }
    assert client.post("/auth/login", json={
        "email": "known@example.test", "password": "wrong password"
    }).status_code == 401
    login = client.post("/auth/login", json={
        "email": "known@example.test", "password": "correct horse battery staple"
    })
    assert login.status_code == 200
    client.headers.update({"Authorization": "Bearer " + login.json()["access_token"]})

    duplicate = client.post("/users", json={
        "email": "known@example.test", "full_name": "Changed name", "password": "correct horse battery staple"
    })
    assert duplicate.status_code == 409
    assert client.get(f"/users/{session['user_id']}/dashboard").json()["full_name"] == "Known"

    existing = client.get("/job-descriptions")
    assert existing.status_code == 200 and len(existing.json()) >= 3

    text = "Backend Developer Intern\nAcme Technologies\nPython, SQL, Git"
    created = client.post("/job-descriptions", data={"text": text})
    assert created.status_code == 201
    assert created.json()["company_matched"] is True
    assert {"Python", "SQL", "Git"}.issubset(created.json()["skills"])
    assert client.post("/job-descriptions", data={"text": text}).json()["id"] == created.json()["id"]
    assert created.json()["id"] in {item["id"] for item in client.get("/job-descriptions").json()}

    uploaded = client.post(
        "/job-descriptions",
        files={"file": ("role.txt", b"Backend Developer\nPython and Docker", "text/plain")},
    )
    assert uploaded.status_code == 201 and "Docker" in uploaded.json()["skills"]
    assert uploaded.json()["company_matched"] is False
    rejected = client.post("/job-descriptions", data={"text": "A collaborative and welcoming team."})
    assert rejected.status_code == 422 and "no known skills" in rejected.json()["detail"]
    assert client.post("/job-descriptions", files={"file": ("bad.exe", b"MZ", "application/octet-stream")}).status_code == 415
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_blank_signup_name_is_rejected_before_database_write(client):
    response = client.post("/users", json={
        "email": "blank-name@example.test",
        "full_name": "   ",
        "password": "correct horse battery staple",
    })

    assert response.status_code == 422
    assert "full_name must not be blank" in response.text


def test_cors_headers_are_preserved_on_server_errors(client, monkeypatch):
    def fail_registration(*args, **kwargs):
        raise RuntimeError("simulated registration failure")

    monkeypatch.setattr(api.core.repo, "register_user", fail_registration)
    browser = TestClient(api.app, raise_server_exceptions=False)
    response = browser.post(
        "/users",
        json={
            "email": "cors-error@example.test",
            "full_name": "Test User",
            "password": "correct horse battery staple",
        },
        headers={"Origin": "http://127.0.0.1:5500"},
    )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5500"


def test_session_cannot_access_another_users_data(client):
    owner = signup(client, "owner@example.test", "Owner")
    submitted = client.post("/jobs", json={
        "user_id": owner["user_id"], "job_type": "JOURNEY_GUIDE", "payload": {"company_id": 1}
    })
    assert submitted.status_code == 202
    outsider = client.post("/users", json={
        "email": "outsider@example.test", "full_name": "Outsider", "password": "correct horse battery staple"
    }).json()
    client.headers.update({"Authorization": "Bearer " + outsider["access_token"]})
    assert client.get(f"/users/{owner['user_id']}/dashboard").status_code == 403
    assert client.get(f"/jobs/{submitted.json()['id']}").status_code == 404
    assert client.post("/jobs", json={
        "user_id": owner["user_id"], "job_type": "JOURNEY_GUIDE", "payload": {"company_id": 1}
    }).status_code == 403


def test_matched_jd_uses_configured_hiring_rounds(client):
    user = signup(client, "rounds@example.test", "Rounds")
    jd = client.post(
        "/job-descriptions",
        data={"text": "Software Engineer\nAcme Technologies\nPython, SQL and Git"},
    ).json()
    submitted = client.post("/jobs", json={
        "user_id": user["id"], "job_type": "JOURNEY_GUIDE", "payload": {"jd_id": jd["id"]}
    }).json()
    for _ in range(100):
        job = client.get(f"/jobs/{submitted['id']}").json()
        if job["status"] in ("DONE", "FAILED"):
            break
        time.sleep(0.05)
    assert job["status"] == "DONE", job.get("error")
    assert [round_["no"] for round_ in job["result"]["rounds"]] == [1, 2, 3]


def test_jd_analysis_general_mode_and_resume_build(client):
    user = signup(client, "pipeline@example.test", "Pipeline")
    resume = client.post(
        f"/users/{user['id']}/resumes",
        files={"file": ("cv.txt", b"Python SQL Git Docker", "text/plain")},
    ).json()
    jd = client.post("/job-descriptions", data={"text": "Platform Engineer\nPython, SQL and Docker"}).json()

    def wait_for(job_id):
        for _ in range(100):
            current = client.get(f"/jobs/{job_id}").json()
            if current["status"] in ("DONE", "FAILED"):
                return current
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish")

    general = client.post("/jobs", json={
        "user_id": user["id"], "job_type": "RESUME_ANALYSIS",
        "payload": {"resume_id": resume["id"], "mode": "GENERAL"},
    }).json()
    general_result = wait_for(general["id"])
    assert general_result["status"] == "DONE", general_result.get("error")
    assert general_result["result"]["mode"] == "GENERAL"
    assert general_result["result"]["report_id"] is None

    comparison = client.post("/jobs", json={
        "user_id": user["id"], "job_type": "RESUME_ANALYSIS",
        "payload": {"resume_id": resume["id"], "jd_id": jd["id"]},
    }).json()
    analysis = wait_for(comparison["id"])
    assert analysis["status"] == "DONE", analysis.get("error")
    assert analysis["result"]["report_id"]
    assert analysis["result"]["gap"]

    build = client.post("/jobs", json={
        "user_id": user["id"], "job_type": "RESUME_BUILD",
        "payload": {"jd_id": jd["id"], "profile": {"name": "Pipeline", "skills": ["Python"]}},
    }).json()
    generated = wait_for(build["id"])
    assert generated["status"] == "DONE", generated.get("error")
    assert generated["result"]["resume_id"]


def test_generated_resume_download_is_authenticated_and_confined(client, tmp_path):
    owner = signup(client, "download-owner@example.test", "Download Owner")
    owner_token = client.headers["Authorization"]
    markdown = "# Generated Resume\n\n## Professional Summary\nStudent targeting a role.\n"
    generated_file = api.core.store.write_generated(owner["id"], "resume", markdown)
    from placex.resume_pdf import generate_resume_pdf
    generated_pdf = api.core.store.write_generated_pdf(
        owner["id"], generated_file["sha256"], generate_resume_pdf(markdown)
    )
    resume = api.core.repo.add_resume(
        owner["id"], "GENERATED", "Test resume", generated_file["path"],
        generated_file["sha256"], generated_file["size"], markdown,
    )
    other = client.post("/users", json={
        "email": "download-other@example.test",
        "full_name": "Download Other",
        "password": "correct horse battery staple",
    }).json()

    response = client.get(
        f"/resumes/{resume['id']}/download",
        headers={"Authorization": owner_token},
    )
    assert response.status_code == 200
    assert response.content == markdown.encode()
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"].endswith(f'filename="resume-{resume["id"]}.md"')
    assert client.get(
        f"/resumes/{resume['id']}/download/md",
        headers={"Authorization": owner_token},
    ).content == markdown.encode()
    pdf = client.get(
        f"/resumes/{resume['id']}/download/pdf",
        headers={"Authorization": owner_token},
    )
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].endswith(f'filename="resume-{resume["id"]}.pdf"')

    assert TestClient(api.app).get(f"/resumes/{resume['id']}/download").status_code == 401
    assert TestClient(api.app).get(f"/resumes/{resume['id']}/download/pdf").status_code == 401
    assert client.get(
        f"/resumes/{resume['id']}/download",
        headers={"Authorization": "Bearer " + other["access_token"]},
    ).status_code == 404
    assert client.get(
        f"/resumes/{resume['id']}/download/pdf",
        headers={"Authorization": "Bearer " + other["access_token"]},
    ).status_code == 404
    api.core.store.delete(generated_pdf["path"])
    assert client.get(
        "/resumes/99999999/download",
        headers={"Authorization": owner_token},
    ).status_code == 404
    assert client.get(
        f"/resumes/{resume['id']}/download/pdf",
        headers={"Authorization": owner_token},
    ).status_code == 404
    assert client.get(
        f"/resumes/{resume['id']}/download/docx",
        headers={"Authorization": owner_token},
    ).status_code == 404

    outside_file = tmp_path / "outside.md"
    outside_file.write_text("must not be served", encoding="utf-8")
    with db.transaction() as conn:
        conn.execute("UPDATE resumes SET file_path=%s WHERE id=%s", (str(outside_file), resume["id"]))
    traversal = client.get(
        f"/resumes/{resume['id']}/download",
        headers={"Authorization": owner_token},
    )
    assert traversal.status_code == 404
    assert b"must not be served" not in traversal.content
    with db.transaction() as conn:
        conn.execute(
            "UPDATE resumes SET file_hash=%s WHERE id=%s",
            ("../outside", resume["id"]),
        )
    assert client.get(
        f"/resumes/{resume['id']}/download/pdf",
        headers={"Authorization": owner_token},
    ).status_code == 404


def test_http_flow(client):
    u = signup(client, "h@x.in", "H")
    r = client.post(f"/users/{u['id']}/resumes", files={"file": ("cv.txt", b"Python SQL Git", "text/plain")})
    assert r.status_code == 201
    j = client.post("/jobs", json={"user_id": u["id"], "job_type": "RESUME_ANALYSIS",
                                   "payload": {"resume_id": r.json()["id"], "jd_id": 1}, "priority": 7})
    assert j.status_code == 202
    for _ in range(100):
        s = client.get(f"/jobs/{j.json()['id']}").json()
        if s["status"] in ("DONE", "FAILED"): break
        time.sleep(0.05)
    assert s["status"] == "DONE" and s["result"]["ats_score"] > 0
    assert client.get(f"/users/{u['id']}/dashboard").json()["reports"] == 1
    assert client.get("/system").json()["pool"]["completed"] >= 1

def test_http_errors(client):
    u = signup(client, "e@x.in", "E")
    assert client.post(f"/users/{u['id']}/resumes", files={"file": ("a.exe", b"MZ", "x")}).status_code == 415
    assert client.post("/jobs", json={"user_id": u["id"], "job_type": "NOPE"}).status_code == 422
    assert client.get("/jobs/99999").status_code == 404

def test_http_503_under_backpressure(cfg):
    cfg.queue_capacity = 1
    api.core = PlaceXCore(cfg, probe=lambda: (1, 1, 1))        # not started -> queue never drains
    try:
        c = TestClient(api.app)
        u = signup(c, "b@x.in", "B")
        body = {"user_id": u["id"], "job_type": "PREP_PLAN"}
        assert c.post("/jobs", json=body).status_code == 202
        r = c.post("/jobs", json=body)
        assert r.status_code == 503 and r.headers["retry-after"] == "2"
    finally:
        api.core.stop()
