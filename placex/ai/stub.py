"""Placeholder AI: deterministic keyword matching. No models, no network.
`latency` simulates model inference time so concurrency benchmarks are meaningful."""
import re
import time
from .base import AIBackend


def _clean(value):
    return " ".join(str(value or "").replace("\x00", "").split())


def _list_values(profile, field):
    values = profile.get(field, [])
    if isinstance(values, str):
        values = values.splitlines()
    return [text for value in values if (text := _clean(value))]


def _project_points(description):
    description = str(description or "").replace("\x00", "").strip()
    if not description:
        return []
    parts = re.split(r"(?:\r?\n|(?<=[.!?])\s+(?=[A-Z0-9])|;\s*)", description)
    bullets = []
    for part in parts:
        sentence = " ".join(part.split()).rstrip()
        if sentence:
            bullets.append(sentence if sentence.endswith((".", "!", "?")) else sentence + ".")
    return bullets


def _markdown_text(value):
    return _clean(value).replace("\\", "\\\\").replace("*", r"\*").replace("#", r"\#").replace("|", r"\|")


def _pattern(term: str):
    flags = 0 if len(term) <= 1 else re.IGNORECASE        # single-letter names ("C") are case-sensitive
    return re.compile(r"(?<![\w+#])" + re.escape(term) + r"(?![\w+#])", flags)

class KeywordBackend(AIBackend):
    def __init__(self, latency: float = 0.0):
        self.latency = latency
        self._compiled: dict[int, list] = {}

    def extract_skills(self, text, catalog):
        if self.latency:
            time.sleep(self.latency)
        found = []
        for s in catalog:
            pats = self._compiled.get(s["id"])
            if pats is None:
                pats = self._compiled[s["id"]] = [_pattern(t) for t in [s["name"], *s["aliases"]]]
            hits = sum(len(p.findall(text)) for p in pats)
            if hits:
                found.append((s["id"], round(min(1.0, 0.6 + 0.1 * hits), 3)))
        return found

    def suggest_improvements(self, gap, score):
        missing = [g for g in gap if not g["matched"]]
        tips = []
        for g in missing[:5]:
            kind = "required" if g["required"] else "preferred"
            tips.append(f"Add evidence of {g['skill_name']} ({kind}, importance {g['importance']}/5): "
                        f"a project, internship task or coursework that uses it.")
        if score < 50:
            tips.append("Match the job description wording: mirror its key terms in your summary and skills section.")
        if not missing:
            tips.append("Strong match. Quantify impact in each bullet (numbers, scale, outcomes).")
        return tips

    def build_resume(self, profile, jd, matched, missing):
        if self.latency:
            time.sleep(self.latency)
        name = _markdown_text(profile.get("name"))
        contact = [_markdown_text(profile.get(key)) for key in ("email", "phone", "location", "linkedin", "website")]
        contact = [value for value in contact if value]
        role = _markdown_text(jd.get("role_title") or "the target role")
        summary = f"Student targeting a {role} position"
        if matched:
            summary += " with demonstrated skills in " + ", ".join(
                _markdown_text(skill) for skill in matched[:5]
            )
        summary += "."

        supplied_skills = _list_values(profile, "skills")
        canonical_supported = [
            _clean(skill) for skill in profile.get("_supported_skills", []) if _clean(skill)
        ]
        relevant = [_clean(skill) for skill in matched if _clean(skill)]
        skills = list(dict.fromkeys(relevant + canonical_supported + supplied_skills))
        education = _list_values(profile, "education")
        experience = _list_values(profile, "experience")
        projects = profile.get("projects", [])
        certifications = _list_values(profile, "certifications")
        achievements = _list_values(profile, "achievements")
        coursework = _list_values(profile, "coursework")

        lines = [f"# {name or 'Resume'}"]
        if contact:
            lines += [" | ".join(contact)]
        lines += ["", "## Professional Summary", summary]
        for title, values in (
            ("Education", education),
            ("Technical Skills", skills),
            ("Experience", experience),
        ):
            if values:
                lines += ["", f"## {title}"]
                lines.extend(f"- {_markdown_text(value)}" for value in values)
        project_lines = []
        for project in projects:
            project_name = _markdown_text(project.get("name"))
            if project_name:
                project_lines.append(f"**{project_name}**")
            project_lines.extend(
                f"- {_markdown_text(point)}" for point in _project_points(project.get("description", ""))
            )
        if project_lines:
            lines += ["", "## Projects", *project_lines]
        for title, values in (
            ("Certifications", certifications),
            ("Achievements", achievements),
            ("Relevant Coursework", coursework),
        ):
            if values:
                lines += ["", f"## {title}"]
                lines.extend(f"- {_markdown_text(value)}" for value in values)
        return "\n".join(lines) + "\n"
