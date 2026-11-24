"""Deterministic, evidence-backed resume-to-role matching."""
from __future__ import annotations

import logging
import math
import re
import threading
from typing import Protocol, Sequence

logger = logging.getLogger(__name__)

MATCH_WEIGHTS = {
    "exact_skill_match": 0.50,
    "semantic_skill_match": 0.15,
    "experience_match": 0.10,
    "project_relevance": 0.10,
    "responsibility_match": 0.10,
    "eligibility_match": 0.05,
}
SEMANTIC_MATCH_THRESHOLD = 0.72

_REQUIREMENT_COMPONENTS = {
    "experience": "experience_match",
    "project": "project_relevance",
    "responsibility": "responsibility_match",
    "education": "eligibility_match",
    "seniority": "eligibility_match",
}


class SemanticMatchingError(RuntimeError):
    """An embedding provider returned unusable vectors or failed."""


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> Sequence[Sequence[float]]:
        """Return one vector for each input text."""


class EmbeddingSemanticMatcher:
    """Optional cosine-similarity adapter; no embedding model is selected by default."""

    def __init__(
        self, provider: EmbeddingProvider, threshold: float = SEMANTIC_MATCH_THRESHOLD
    ):
        if not 0 < threshold <= 1:
            raise ValueError("semantic similarity threshold must be in (0, 1]")
        self.provider = provider
        self.threshold = threshold

    def match(self, resume_text: str, skill_names: list[str]) -> dict[str, dict]:
        chunks = _text_chunks(resume_text)
        if not chunks or not skill_names:
            return {}
        try:
            vectors = self.provider.embed([*chunks, *skill_names])
        except SemanticMatchingError:
            raise
        if len(vectors) != len(chunks) + len(skill_names):
            raise SemanticMatchingError("embedding provider returned an invalid vector count")

        chunk_vectors = [_validated_vector(vector) for vector in vectors[:len(chunks)]]
        skill_vectors = [_validated_vector(vector) for vector in vectors[len(chunks):]]
        if not chunk_vectors or not skill_vectors:
            return {}
        dimension = len(chunk_vectors[0])
        if any(len(vector) != dimension for vector in [*chunk_vectors, *skill_vectors]):
            raise SemanticMatchingError("embedding provider returned inconsistent vector dimensions")

        matches = {}
        for skill, skill_vector in zip(skill_names, skill_vectors):
            best_score, best_chunk = max(
                (
                    (_cosine_similarity(skill_vector, chunk_vector), chunk)
                    for chunk, chunk_vector in zip(chunks, chunk_vectors)
                ),
                key=lambda pair: pair[0],
            )
            if best_score >= self.threshold:
                matches[skill] = {
                    "similarity": round(best_score, 4),
                    "evidence": best_chunk,
                }
        return matches


class SentenceTransformerProvider:
    """Loads one Sentence Transformers model lazily and reuses it across requests."""

    def __init__(
        self,
        model_name: str,
        device: str | None = None,
        local_files_only: bool = False,
    ):
        if not model_name.strip():
            raise ValueError("embedding model name must not be blank")
        self.model_name = model_name
        self.device = device.strip() if device and device.strip() else None
        self.local_files_only = local_files_only
        self._model = None
        self._load_lock = threading.Lock()

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise SemanticMatchingError(
                    "Sentence Transformers is enabled but not installed; install requirements-ml.txt"
                ) from error
            try:
                self._model = SentenceTransformer(
                    self.model_name,
                    device=self.device,
                    local_files_only=self.local_files_only,
                )
            except (ImportError, OSError, RuntimeError, ValueError, TypeError) as error:
                logger.warning(
                    "Could not load embedding model %s (%s)",
                    self.model_name,
                    type(error).__name__,
                )
                raise SemanticMatchingError("configured embedding model could not be loaded") from error
        return self._model

    def embed(self, texts: list[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        model = self._get_model()
        try:
            encoded = model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except (ImportError, OSError, RuntimeError, ValueError, TypeError) as error:
            logger.warning(
                "Embedding inference failed for model %s (%s)",
                self.model_name,
                type(error).__name__,
            )
            raise SemanticMatchingError("embedding inference failed") from error
        try:
            return encoded.tolist()
        except (AttributeError, TypeError, ValueError) as error:
            raise SemanticMatchingError("embedding model returned an invalid result") from error


def build_matching_report(
    gap: list[dict],
    resume_text: str,
    jd_text: str,
    *,
    skill_evidence: list[dict] | None = None,
    extracted_requirements: list[dict] | None = None,
    semantic_matches: dict[str, dict] | None = None,
    semantic_status: str = "unavailable",
    weights: dict[str, float] | None = None,
) -> dict:
    """Build a score from verified evidence; unassessed dimensions do not reduce it."""
    configured_weights = validate_match_weights(weights)
    evidence_by_skill: dict[str, list[dict]] = {}
    for evidence in skill_evidence or []:
        evidence_by_skill.setdefault(str(evidence.get("skill_id")), []).append(evidence)

    skill_requirements = []
    exact_weight = matched_weight = 0.0
    missing_names = []
    for row in gap:
        importance = _importance(row.get("importance", 1))
        exact_weight += importance
        is_matched = bool(row.get("matched"))
        if is_matched:
            matched_weight += importance
        else:
            missing_names.append(row["skill_name"])
        skill_evidence_items = evidence_by_skill.get(str(row.get("skill_id")), [])
        quote = next(
            (
                item.get("evidence")
                for item in skill_evidence_items
                if _quote_in_source(item.get("evidence"), resume_text)
            ),
            None,
        )
        if is_matched and not quote:
            quote = _find_skill_quote(row["skill_name"], resume_text)
        skill_requirements.append({
            "category": "skill",
            "requirement": row["skill_name"],
            "importance": importance,
            "required": bool(row.get("required")),
            "status": "matched" if is_matched else "not_evidenced",
            "match_type": "exact_or_alias" if is_matched else None,
            "evidence": (
                [_evidence_item("resume", quote, resume_text)] if quote else []
            ),
        })

    components = {}
    if gap:
        components["exact_skill_match"] = _component(
            "assessed",
            100.0 * matched_weight / exact_weight if exact_weight else 0.0,
            "Importance-weighted exact or catalog-alias skill matches.",
        )
    else:
        components["exact_skill_match"] = _component(
            "not_assessed", None, "No structured job-description skills were available."
        )

    semantic_matches = semantic_matches or {}
    semantic_weight = partial_weight = 0.0
    partial_skills = []
    validated_semantics = {}
    canonical_names = {str(row["skill_name"]).casefold(): row for row in gap}
    for name, result in semantic_matches.items():
        row = canonical_names.get(str(name).casefold())
        if row is None or row.get("matched"):
            continue
        try:
            similarity = float(result["similarity"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not math.isfinite(similarity)
            or not SEMANTIC_MATCH_THRESHOLD <= similarity <= 1
        ):
            continue
        quote = result.get("evidence")
        if not _quote_in_source(quote, resume_text):
            continue
        importance = _importance(row.get("importance", 1))
        validated_semantics[row["skill_name"]] = similarity
        semantic_weight += importance
        partial_weight += importance * similarity
        partial_skills.append({
            "skill": row["skill_name"],
            "similarity": round(similarity, 4),
            "evidence": _evidence_item("resume", quote, resume_text),
        })
    missing_rows = [row for row in gap if not row.get("matched")]
    if semantic_status == "available" and missing_rows:
        semantic_weight = sum(_importance(row.get("importance", 1)) for row in missing_rows)
        partial_weight = sum(
            _importance(row.get("importance", 1))
            * validated_semantics[row["skill_name"]]
            for row in missing_rows
            if row["skill_name"] in validated_semantics
        )
        components["semantic_skill_match"] = _component(
            "assessed",
            100.0 * partial_weight / semantic_weight if semantic_weight else 0.0,
            "Importance-weighted embedding similarity for non-exact skills, with quoted resume evidence.",
        )
    elif semantic_weight:
        components["semantic_skill_match"] = _component(
            "assessed",
            100.0 * partial_weight / semantic_weight,
            "Cosine similarity for non-exact skills, with quoted resume evidence.",
        )
    else:
        status = (
            "not_assessed"
            if semantic_status == "available" and not missing_rows
            else semantic_status
        )
        components["semantic_skill_match"] = _component(
            status,
            None,
            "No unmatched skills remained for semantic comparison."
            if not missing_rows
            else "No configured embedding provider produced a validated semantic match."
            if semantic_status == "unavailable"
            else "No semantic skill evidence met the configured threshold.",
        )
    partial_by_name = {
        item["skill"].casefold(): item for item in partial_skills
    }
    for requirement in skill_requirements:
        partial = partial_by_name.get(requirement["requirement"].casefold())
        if partial:
            requirement["status"] = "partially_matched"
            requirement["match_type"] = "semantic"
            requirement["evidence"] = [partial["evidence"]]

    requirement_groups: dict[str, list[dict]] = {}
    for requirement in extracted_requirements or []:
        category = requirement.get("category")
        component_name = _REQUIREMENT_COMPONENTS.get(category)
        requirement_text = str(requirement.get("requirement", "")).strip()
        jd_quote = requirement.get("jd_evidence")
        if not component_name or not requirement_text or not _quote_in_source(jd_quote, jd_text):
            continue
        resume_quotes = [
            str(quote).strip()
            for quote in requirement.get("resume_evidence", [])
            if _quote_in_source(quote, resume_text)
        ]
        requirement_groups.setdefault(component_name, []).append({
            "category": category,
            "requirement": requirement_text,
            "importance": _importance(requirement.get("importance", 1)),
            "required": bool(requirement.get("required")),
            "jd_evidence": jd_quote,
            "resume_evidence": list(dict.fromkeys(resume_quotes)),
        })

    component_labels = {
        "experience_match": "experience",
        "project_relevance": "project",
        "responsibility_match": "responsibility",
        "eligibility_match": "education and seniority",
    }
    for component_name, label in component_labels.items():
        requirements = requirement_groups.get(component_name, [])
        subgroups: dict[str, list[dict]] = {}
        for item in requirements:
            subgroup = (
                item["category"]
                if component_name == "eligibility_match"
                else component_name
            )
            subgroups.setdefault(subgroup, []).append(item)
        assessed_subgroups = {
            subgroup
            for subgroup, items in subgroups.items()
            if any(item["resume_evidence"] for item in items)
        }
        category_has_evidence = bool(assessed_subgroups)
        if not requirements or not category_has_evidence:
            components[component_name] = _component(
                "not_assessed",
                None,
                f"No validated resume evidence was available to assess {label}; this dimension is neutral.",
            )
            for item in requirements:
                item["status"] = "not_assessed"
                item["evidence"] = []
        else:
            assessed_requirements = [
                item
                for subgroup in assessed_subgroups
                for item in subgroups[subgroup]
            ]
            denominator = sum(item["importance"] for item in assessed_requirements)
            numerator = sum(
                item["importance"]
                for item in assessed_requirements
                if item["resume_evidence"]
            )
            components[component_name] = _component(
                "assessed",
                100.0 * numerator / denominator if denominator else 0.0,
                f"Importance-weighted requirements with directly quoted {label} evidence.",
            )
            for item in requirements:
                subgroup = (
                    item["category"]
                    if component_name == "eligibility_match"
                    else component_name
                )
                item["status"] = (
                    "not_assessed"
                    if subgroup not in assessed_subgroups
                    else "matched" if item["resume_evidence"] else "not_evidenced"
                )
                item["evidence"] = [
                    _evidence_item("resume", quote, resume_text)
                    for quote in item.pop("resume_evidence")
                ]
        for item in requirements:
            if "resume_evidence" in item:
                item["evidence"] = [
                    _evidence_item("resume", quote, resume_text)
                    for quote in item.pop("resume_evidence")
                ]
            item["evidence"].insert(
                0,
                _evidence_item(
                    "job_description", item.pop("jd_evidence"), jd_text
                ),
            )
            skill_requirements.append(item)

    assessed = [
        (name, value)
        for name, value in components.items()
        if value["status"] == "assessed" and value["score"] is not None
        and configured_weights[name] > 0
    ]
    total_weight = sum(configured_weights[name] for name, _ in assessed)
    overall_score = (
        round(sum(configured_weights[name] * value["score"] for name, value in assessed) / total_weight, 2)
        if total_weight
        else None
    )
    effective_weights = {
        name: round(configured_weights[name] / total_weight, 6)
        for name, _ in assessed
    } if total_weight else {}
    if overall_score is None:
        explanation = "No matching dimensions could be assessed from the supplied data."
    else:
        parts = [
            f"{name} {value['score']:.2f} (effective weight {effective_weights[name]:.0%})"
            for name, value in assessed
        ]
        explanation = (
            "Overall match is a deterministic weighted average of assessed dimensions: "
            + "; ".join(parts)
            + ". Unassessed dimensions are excluded rather than treated as failures."
        )

    return {
        "version": 1,
        "overall_match_score": overall_score,
        "score_explanation": explanation,
        "configured_weights": configured_weights,
        "effective_weights": effective_weights,
        "components": components,
        "requirements": skill_requirements,
        "exact_missing_skills": missing_names,
        "missing_skills": [
            name for name in missing_names
            if name.casefold() not in partial_by_name
        ],
        "partially_matched_skills": partial_skills,
        "eligibility_state": (
            "evidence_available"
            if components["eligibility_match"]["status"] == "assessed"
            else "not_assessed"
        ),
        "confirmed_ineligibility": False,
        "eligibility_note": (
            "No confirmed ineligibility is inferred from missing resume information."
        ),
        "semantic_status": (
            "assessed" if components["semantic_skill_match"]["status"] == "assessed"
            else components["semantic_skill_match"]["status"]
        ),
    }


def validate_match_weights(weights: dict[str, float] | None) -> dict[str, float]:
    result = dict(MATCH_WEIGHTS)
    if weights is not None:
        if not isinstance(weights, dict):
            raise ValueError("matching weights must be a JSON object")
        unknown = set(weights) - set(MATCH_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown matching weight(s): {', '.join(sorted(unknown))}")
        result.update(weights)
    for name, value in result.items():
        if isinstance(value, bool):
            raise ValueError(f"matching weight {name} must be a non-negative number")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"matching weight {name} must be a non-negative number") from error
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"matching weight {name} must be a non-negative number")
        result[name] = numeric
    if not any(result.values()):
        raise ValueError("at least one matching weight must be positive")
    return result


def _component(status: str, score: float | None, explanation: str) -> dict:
    return {
        "status": status,
        "score": round(score, 2) if score is not None else None,
        "explanation": explanation,
    }


def _importance(value) -> int:
    try:
        return min(5, max(1, int(value)))
    except (TypeError, ValueError):
        return 1


def _quote_in_source(quote, source: str) -> bool:
    if not isinstance(quote, str) or not quote.strip() or not source:
        return False
    normalized_quote = " ".join(quote.casefold().split())
    normalized_source = " ".join(source.casefold().split())
    return normalized_quote in normalized_source


def _evidence_item(source_name: str, quote: str, source_text: str) -> dict:
    item = {"source": source_name, "quote": quote}
    if not quote or not source_text:
        return item
    normalized_quote = " ".join(quote.casefold().split())
    offset = source_text.casefold().find(quote.casefold())
    line_number = None
    if offset >= 0:
        line_number = source_text.count("\n", 0, offset) + 1
        item["start"] = offset
        item["end"] = offset + len(quote)
    else:
        for line_number, line in enumerate(source_text.splitlines(), start=1):
            if normalized_quote in " ".join(line.casefold().split()):
                break
        else:
            line_number = None
    if line_number is not None:
        item["line"] = line_number
    return item


def _find_skill_quote(skill_name: str, resume_text: str) -> str | None:
    if not skill_name or not resume_text:
        return None
    pattern = re.compile(
        r"(?<![\w+#])" + re.escape(skill_name) + r"(?![\w+#])",
        0 if len(skill_name) <= 1 else re.IGNORECASE,
    )
    match = pattern.search(resume_text)
    return match.group(0) if match else None


def _text_chunks(text: str) -> list[str]:
    chunks = []
    for line in (text or "").splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            candidate = " ".join(sentence.split())
            if candidate:
                chunks.append(candidate[:1000])
    return chunks[:200]


def _validated_vector(vector) -> list[float]:
    try:
        result = [float(value) for value in vector]
    except (TypeError, ValueError) as error:
        raise SemanticMatchingError("embedding provider returned an invalid vector") from error
    if not result or any(not math.isfinite(value) for value in result):
        raise SemanticMatchingError("embedding provider returned an invalid vector")
    if not any(result):
        raise SemanticMatchingError("embedding provider returned a zero vector")
    return result


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    denominator = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return max(0.0, min(1.0, numerator / denominator))
