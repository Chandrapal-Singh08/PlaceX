from pypdf import PdfReader
from io import BytesIO

from placex.ai.stub import KeywordBackend
from placex.resume_pdf import generate_resume_pdf


def test_resume_builder_is_truthful_structured_and_jd_targeted():
    profile = {
        "name": "Riley Student",
        "email": "riley@example.test",
        "education": ["B.S. Computer Science, Example University"],
        "skills": ["Python", "Git"],
        "experience": ["Built a small API using Python."],
        "projects": [{
            "name": "Job Matcher",
            "description": "Built a job matcher using Python and PostgreSQL. Added keyword search.",
        }],
        "certifications": ["Python Certificate"],
        "achievements": ["Won a campus hackathon"],
        "coursework": ["Database Systems"],
        "_supported_skills": ["Python", "PostgreSQL"],
    }
    jd = {"role_title": "Backend Developer"}
    markdown = KeywordBackend().build_resume(profile, jd, ["Python", "PostgreSQL"], ["Docker"])

    assert "# Riley Student" in markdown
    assert "riley@example.test" in markdown
    assert "## Professional Summary" in markdown
    assert "## Education" in markdown
    assert "## Technical Skills" in markdown
    assert "## Experience" in markdown
    assert "## Projects" in markdown
    assert "## Certifications" in markdown
    assert "## Achievements" in markdown
    assert "## Relevant Coursework" in markdown
    assert "Python, PostgreSQL" in markdown
    assert "Docker" not in markdown
    assert "Won a campus hackathon" in markdown
    assert "10%" not in markdown


def test_pdf_is_readable_and_preserves_resume_structure():
    markdown = """# Riley Student
riley@example.test | 555-0100

## Professional Summary
Student targeting a Backend Developer position with demonstrated skills in Python and PostgreSQL.

## Technical Skills
- Python, PostgreSQL, Git

## Projects
**Job Matcher**
- Built a job matcher using Python and PostgreSQL.
- Added keyword search.
"""
    pdf = generate_resume_pdf(markdown)
    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for expected in ("Riley Student", "PROFESSIONAL SUMMARY", "TECHNICAL SKILLS", "Job Matcher", "keyword search"):
        assert expected in text


def test_longer_resume_wraps_across_two_readable_pages():
    markdown = "# Riley Student\n\n## Projects\n" + "".join(
        f"- Built project component {index} using Python and PostgreSQL.\n"
        for index in range(60)
    )
    reader = PdfReader(BytesIO(generate_resume_pdf(markdown)))
    assert len(reader.pages) == 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "component 0" in text and "component 59" in text
    for page in reader.pages:
        coordinates = []
        page.extract_text(
            visitor_text=lambda value, ctm, tm, font, size:
                coordinates.append((tm[4], tm[5])) if value.strip() else None
        )
        assert coordinates
        assert all(0 <= y <= float(page.mediabox.height) for _, y in coordinates)
